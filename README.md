# 🧩 Konsistenz-Filter

**Deterministischer Konsistenz-Vorfilter für EN/DE-Übersetzungskorpora (CSV).**

Findet gleiche oder fast gleiche englische Sätze, die im Deutschen
**unterschiedlich** übersetzt sind, gruppiert sie und schlägt die Fassung
vor, die gelten soll — mit nachvollziehbarer Begründung. So muss die
Werkstatt jede Konsistenz-Entscheidung **nur einmal** treffen.

> **Designprinzip: AI orchestriert, Menschen entscheiden.**
> Das Tool schlägt vor und begründet — die Werkstatt prüft und bestätigt.

## Werkstatt-Regeln (aus Issue #39)

| Regel | Umsetzung |
|---|---|
| `docs/NAMENSVERZEICHNIS.md` maßgeblich, offizielle deutsche Pokémon-Begriffe gehen immer vor | `--glossary`: kanonischer DE-Name gewinnt **vor** der Majorität |
| Gleiches EN kann je nach Sprecher/Situation **bewusst** anders übersetzt sein (`kategorie`/`ort`) | Gruppen über mehrere Orte/Kategorien → `Kontext prüfen`, **kein** Gewinner |
| Dialoge passen zur Figur (Kinder/Freunde locker per Du, Autoritäten förmlicher) | Registerwechsel (formelles „Sie“ vs. „du“) innerhalb einer Gruppe → `Kontext prüfen` |
| Kampf-/Systemtexte: offizielles Gen-3-Wortlaut, Präsens | System-Gruppen (`ort=system_kampf`) erhalten eine Prüfhinweis-Notiz in der Begründung |
| Zeilenumbrüche und Steuerzeichen (`\n`, `\p`, `[…]`) zählen nicht als Unterschied | werden in der Normalisierung zu Leerzeichen normalisiert |

**Ersatz-Vorlagen (Namen/Typen):** Sätze, die sich nur durch ersetzte
Namen oder Typen unterscheiden — z. B. Mega-Stein-Beschreibungen je
Pokémon, Z-Kristalle je Typ — sind übersetzungstechnisch konsistent und
werden **unterdrückt** (werden im Report gezählt). Erkennung strukturell
(keine Namensliste nötig): alle EN-Varianten müssen gleich lang sein,
und eine sich unterscheidende Wort-Position ist nur ein Slot, wenn auch
deren Skelette (kleingeschrieben, ohne Satzzeichen) differieren — reine
Groß-/Kleinschreibung- oder Punktuation-Unterschiede („Aww"/„Aww,")
zählen nicht. EN-Slots müssen namenähnlich sein: Typ-Tokens (alle 18
offiziellen Typen, inkl. Stahl/Unlicht/Fee), großgeschriebene Eigennamen
oder Farben (begrenztes Wortset). Die DE-Fassungen müssen entsprechend
mit gleicher Slot-Anzahl differieren. Pro Zeile müssen Typen und
Glossar-Namen auf beiden Seiten korrespondieren — abweichender oder
fehlender Name/Typ in der DE-Fassung lässt die Gruppe stehen. (Namens-
Korrektheit deckt zusätzlich der automatische Terminologie-Check in der
Pipeline ab.) Typ-Tokens werden vor dem Glossar-Namens-Match ausgeblendet
(„Psychic" als Typ darf nicht den Trainerklassen-Namen „Psychic"
treffen).

## Checks & Pipeline

1. **Normalisierung**: Steuerzeichen/Umbrüche → Leerzeichen, Farbcodes
   (`[#FC…]`) entfernt, Puffer-Platzhalter (`[FDxx]` / `[#FDxx]`)
   kanonisch vereinheitlicht, NFKC + Anführungszeichen vereinheitlicht
2. **Exakt-Gruppen**: gleiche EN-Quelle nach Normalisierung
3. **Nahe-Gruppen**: Multiset-Jaccard ≥ Schwelle (Default 0.85) innerhalb
   von Blocking-Blöcken (Wortzahl, erstes/letztes Wort); Platzhalter
   bleiben unterschiedliche Tokens (andere Puffer = andere Vorlagen)
4. **DE-Varianten**: Unterschiede nur in Groß-/Kleinschreibung oder
   Satzendepunktuation gelten als identisch
5. **Entscheidung pro Gruppe**: Kontext-Modus → `Kontext prüfen`;
   sonst Glossar-Treffer → kanonische Fassung; sonst Majorität
   (Gleichstand: erste Vorkunft in Dateireihenfolge)

## Nutzung

Reines Python 3.10+ (nur Standardbibliothek), offline, deterministisch
(keine LLM, kein Zufall):

```sh
python3 konsistenz_filter.py \
    --csv texte_v0.1.7.2.csv \
    --glossary NAMENSVERZEICHNIS.md \
    --out-dir ./out
```

### Optionen

| Option | Bedeutung |
|---|---|
| `--csv` | Korpus-CSV, `;`-getrennt: `id;en;de;kategorie;ort` (Pflicht) |
| `--glossary` | Namensverzeichnis-Markdown (Tabellen `| DE | EN | Art |`) |
| `--out-dir` | Ausgabeverzeichnis (Default `out/`) |
| `--threshold` | Jaccard-Schwelle für „fast identisch“ (Default 0.85) |
| `--strict` | nur exakt identische EN-Quellen (konservatives Baseline) |
| `--top` | Topliste-Größe im Report (Default 20) |
| `--werkstatt` | zusätzliches `werkstatt_einheitlichkeit.csv` im Dialogwerkstatt-Einleseformat |
| `--kanon` | Kanon-Namensliste-CSV (`typ;en;de;quelle`, z. B. aus dem Repo `kanon-namensliste`): zusätzlich `kanon_check.csv` — prüft pro Zeile, ob in der **DE-Fassung ein offizielles EN-Namen** vorkommt (Fehlübersetzung) oder in der **EN-Quelle ein offizielles DE-Namen** (Kontaminierung) |

### Ausgabe

- `ergebnis.csv` — **Lieferformat**: `gruppe;ids;en_varianten;de_varianten;vorschlag;grund`
  (ids mit `,` getrennt, Varianten mit ` | `; `vorschlag` = `Kontext prüfen`
  oder eine der `de_varianten`, die am häufigsten zuerst steht)
- `werkstatt_einheitlichkeit.csv` (nur mit `--werkstatt`) — **Dialogwerkstatt-
  Einleseformat**: `gruppe;ids;en;de_varianten;vorschlag;grund;kategorie;ort;status`
  (`status` = `vorschlag` / `kontext_pruefen`; `kategorie`/`ort` sind die
  Kontext-Spalnen der Gruppe, kommagetrennt). Eine Karte pro Gruppe für die
  Werkstatt. **Enthält Textinhalte** — privat ablegen.
- `konsistenz_report.md` — **nur Aggregat** (keine Textinhalte, öffentlich sicher)
- `konsistenz_results.json` — maschinenlesbar, **enthält Textinhalte**
- `kanon_check.csv` (nur mit `--kanon`) — `id;en;de;richtung;name;erwartet;typ;quelle`
  (`richtung` = `en_name_in_de` / `de_name_in_en`). Semantik: Befund nur, wenn
  das offizielle EN-Namen in der DE-Fassung vorkommt **und** das zugehörige
  offizielle DE-Namen dort **nicht** — und umgekehrt. Wortgrenzen und
  Groß-/Kleinschreibung werden beachtet (keine Teilwort-Treffer wie
  „Mews" oder „mew"); identische Namen in EN und DE (z. B. Mew) sind nie
  ein Befund; Zeilen, die selbst (nach Normalisierung) nicht übersetzt
  sind, bleiben außen vor — „Name in falscher Sprache" ist dort nicht
  definiert (z. B. Marken-Platzhalter wie „Smogon Gen 7 OU" = Website).
  **Enthält Textinhalte** — privat ablegen.
- **Bekannte Limitation (Vorfilter-Charakter)**: Der Kanon-Check ist
  kontext-blind (Namens-Matching ohne Semantik). Deutsche Wörter, die
  offiziellen EN-Namen entsprechen („Rest", „Pass", „Mist", „Tee",
  „Klang", „Charge"), Marken- und Fachbegriffe („Flash 128k") und
  Namenskollisionen („Blitz" in „Breakneck Blitz") erzeugen
  False-Positives. Absicht: günstige Kandidaten-Liste für die
  Werkstatt-Prüfung (Präzision vor Recall — aber Menschen entscheiden).

## Sicherheit & Datenhaltung

- **Lese-only** — Eingabe wird nicht geändert.
- **Keine Spieltexte in öffentlichen Repos** — Code, Tests (synthetische
  Fixtures), Report-Aggregat und Doku sind public-safe; `ergebnis.csv`,
  `werkstatt_einheitlichkeit.csv`, `konsistenz_results.json` und
  `kanon_check.csv` bleiben privat.
- **Reproduzierbar**: gleicher Input + gleiche Parameter → gleicher Output
  (deterministisch, sortierte Verarbeitung, keine LLM).

## Kalibrierung

Die Werkstatt prüft 50 zufällige Gruppen und meldet die Trefferquote.
Dabei justiert: `--threshold` (nähere Gruppen) und ggf. die
Kontext-Regel. Baseline: `--strict` (nur Exakt-Gruppen, höchste Präzision).

## Versionen

- **v0.5** — Kanon-Check (`--kanon`): offizielle EN/DE-Namen (aus der
  [Kanon-Namensliste](https://github.com/pokemon-unbound-de/kanon-namensliste),
  Gen 1–9 + aktuelle Zusätze) werden pro Zeile in beide Richtungen geprüft —
  offizielles EN-Namen im DE-Text (Fehlübersetzung) bzw. offizielles
  DE-Namen in der EN-Quelle (Kontaminierung) → `kanon_check.csv`.
  Präzisionsregeln: Wortgrenzen, Groß-/Kleinschreibung-treu, kein Befund
  wenn das zugehörige offizielle Namenspaar in derselben Fassung steht,
  der Name in EN/DE identisch ist, oder die Zeile selbst nach
  Normalisierung nicht übersetzt ist (EN == DE, z. B. Marken-
  Platzhalter). Additive Option — Default-Lauf (ohne `--kanon`) bleibt
  unverändert
- **v0.4.1** — Werkstatt-Format: zusätzliches `werkstatt_einheitlichkeit.csv`
  (`--werkstatt`) im Dialogwerkstatt-Einleseformat
  `gruppe;ids;en;de_varianten;vorschlag;grund;kategorie;ort;status`
  (eine Karte pro Gruppe, `status` = `vorschlag`/`kontext_pruefen`), damit die
  Werkstatt die Gruppen direkt einlesen kann
- **v0.4** — strukturelle Ersatz-Vorlagen-Erkennung ohne Namensliste
  (das Namensverzeichnis enthält keine Pokémon-Namen, nur Figuren/
  Trainer/Items): positionsbasiert (gleiche Wortzahl, Slot nur bei
  Skelett-Unterschied — Groß-/Kleinschreibung und Punktuation zählen
  nicht), 18 offizielle Typen (inkl. Stahl/Unlicht/Fee), Farben als
  begrenztes Slot-Wortset, Typ-Tokens beim Glossar-Namens-Match
  ausgeblendet; EN-Varianten mit Punktationsdifferenz werden komplett
  gelistet
- **v0.3** — Ersatz-Vorlagen-Regel: Gruppen, die nur in ersetzten
  Glossar-Namen/Typen differieren, werden unterdrückt (Reduktion der
  Fehlgruppen bei Item-Beschreibungen je Pokémon/Typ)
- **v0.2** — Werkstatt-Regeln: Glossar-Prinzip, Kontext-Modus
  (Orte/Kategorien/Register), Gen-3-Systemhinweis
- **v0.1** — Normalisierung, Exakt-/Nahe-Gruppen, Majoritäts-Vorschlag

## Lizenz

[MIT](LICENSE) — Copyright (c) 2026 pokemon-unbound-de

---

*Teil des Pokémon Unbound DE Projekts. Unoffizielles Fan-Projekt, nicht
mit den Rechteinhabern verbunden. Keine ROMs, Spieltexte oder privaten
Daten in diesem Repository.*
