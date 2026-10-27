#!/usr/bin/env python3
"""
Konsistenz-Filter
=================
Consistency pre-filter for EN/DE localization corpora (CSV).

Finds identical or near-identical ENGLISH source strings that were
translated DIFFERENTLY in German, groups them, and proposes the variant
that should be canonical — with a deterministic, reproducible rationale.
Humans only have to decide each consistency question once.

Project rules (Werkstatt, Issue #39):
  * NAMENSVERZEICHNIS.md is authoritative: official German Pokemon terms
    always win over the majority variant (--glossary).
  * Intentional differences: the same EN may be translated differently on
    purpose (speaker/situation). Groups spanning different locations or
    categories, or mixing formal/informal register (Sie/du), are marked
    "Kontext prüfen" instead of proposing a winner.
  * Battle/system texts: Gen-3 wording, present tense (checked by the
    workshop — the tool adds a reminder to the rationale).
  * Line breaks / control codes never count as a difference.

Pipeline:
  1. Load CSV (;-delimited: id;en;de;kategorie;ort)
  2. Normalize: control codes (\\n, \\p, \\l, ...) to spaces, color codes
     ([#FC...]) removed, buffer placeholders ([FDxx] / [#FDxx])
     canonicalized, NFKC + quote unification, whitespace collapsed
  3. Group A: exact source match after normalization
  4. Group B: near source match (multiset Jaccard >= threshold, blocked
     by (word count, first word, last word)) — disabled with --strict
  5. Decide per group: context mode (Kontext prüfen) vs. winner
     (glossary first, then frequency; ties: file order)
  6. Output: ergebnis.csv (gruppe;ids;en_varianten;de_varianten;
     vorschlag;grund) + konsistenz_report.md (aggregates only)

Safety: input is read-only. Game text must never be committed to public
repositories — only this tool, the report aggregates and the method docs
are public.

Usage:
  python3 konsistenz_filter.py --csv texte.csv --glossary NAMENSVERZEICHNIS.md
  python3 konsistenz_filter.py --csv texte.csv --strict
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

CTRL_RE = re.compile(r"\\[A-Za-z]")
COLOR_RE = re.compile(r"\[#FC[0-9A-Fa-f]+\]")
BUFFER_RE = re.compile(r"\[?#?FD([0-9A-Fa-f]{2})\]")
NONWORD_RE = re.compile(r"[^a-z0-9]+")
FORMAL_SIE_RE = re.compile(r"\bSie\b")
PH_L, PH_R = "\u27e8", "\u27e9"
QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...",
}

KONTEXT = "Kontext prüfen"


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

def norm_text(s: str) -> str:
    """Normalize for source comparison: control codes and color codes are
    layout (-> space), buffer placeholders are canonicalized to the same
    token in both languages ([FD02] == [#FD02]), Unicode and whitespace
    unified. Case and terminal punctuation are kept."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    for key, val in QUOTES.items():
        s = s.replace(key, val)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = CTRL_RE.sub(" ", s)
    s = COLOR_RE.sub(" ", s)
    s = BUFFER_RE.sub(lambda m: f"{PH_L}FD{m.group(1).upper()}{PH_R}", s)
    return re.sub(r"\s+", " ", s).strip()


def de_key(s: str) -> str:
    """Key for DE variant distinctness: same normalization plus case and
    trailing sentence punctuation ignored (documented choice)."""
    return norm_text(s).lower().rstrip(".!?")


def skeleton(s: str) -> str:
    """Aggressive form used for merging case/punctuation variants and for
    blocking: placeholders removed, only lowercase words + digits."""
    t = norm_text(s).lower().replace(PH_L, " ").replace(PH_R, " ")
    return re.sub(r"\s+", " ", NONWORD_RE.sub(" ", t)).strip()


def jaccard_tokens(s: str) -> Counter:
    """Word multiset for similarity: placeholders are KEPT as distinct
    tokens, so different buffer slots stay different templates."""
    t = norm_text(s).lower().replace(PH_L, " ").replace(PH_R, " ")
    return Counter(NONWORD_RE.sub(" ", t).split())


def jaccard(a: Counter, b: Counter) -> float:
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


# --------------------------------------------------------------------------
# Glossary (NAMENSVERZEICHNIS.md: markdown tables | DE | EN | Art |)
# --------------------------------------------------------------------------

def parse_glossary(path: str) -> list[dict]:
    entries: list[dict] = []
    if not path or not os.path.exists(path):
        return entries
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            de, en = cells[0], cells[1]
            if de in ("DE", "---", "") or en in ("EN", "---", ""):
                continue
            if set(de) <= {"-"} or set(en) <= {"-"}:
                continue
            tentative = "*" in de or "*" in en
            de = de.replace("*", "").strip()
            en = en.replace("*", "").strip()
            if de and en:
                entries.append({"de": de, "en": en, "tentative": tentative})
    return entries


def name_in_text(name: str, text: str) -> bool:
    """Case-insensitive word-boundary match (multi-word names supported).
    Allows German possessive/inflection suffixes (Lärchs -> Lärch)."""
    if not name or not text:
        return False
    esc = re.escape(name)
    return re.search(rf"(?<!\w){esc}(?:s|es)?(?!\w)", text, re.IGNORECASE) is not None


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------

def _union_find_join(parent: dict, a: int, b: int) -> None:
    ra, rb = parent[a], parent[b]
    if ra != rb:
        if ra > rb:  # deterministic root: smallest index
            ra, rb = rb, ra
        parent[rb] = ra


def build_groups(rows: list[dict], threshold: float, strict: bool) -> list[dict]:
    """rows: dicts with id/en/de/kategorie/ort + _index. Returns final
    groups sorted by (DE variants desc, entries desc, first id asc)."""
    # -- source groups by exact normalized EN
    by_en: dict[str, dict] = {}
    for r in rows:
        e = norm_text(r["en"])
        if not e:
            continue
        sg = by_en.setdefault(e, {"norm_en": e, "raw_en": r["en"],
                                  "first_index": r["_index"], "rows": []})
        if r["_index"] < sg["first_index"]:
            sg["raw_en"], sg["first_index"] = r["en"], r["_index"]
        sg["rows"].append(r)
    src_groups = list(by_en.values())

    # -- merge case/punctuation variants (identical skeleton)
    by_skel: dict[str, list[dict]] = defaultdict(list)
    for sg in src_groups:
        skel = skeleton(sg["norm_en"])
        by_skel[skel or sg["norm_en"]].append(sg)
    merged: list[dict] = []
    for key in sorted(by_skel):
        parts = sorted(by_skel[key], key=lambda sg: sg["norm_en"])
        base = parts[0]
        for extra in parts[1:]:
            base["rows"].extend(extra["rows"])
            base["first_index"] = min(base["first_index"], extra["first_index"])
        merged.append(base)

    # -- near-source merge (multiset Jaccard within blocking blocks)
    parent = {i: i for i in range(len(merged))}
    edge_min: dict[int, float] = {}
    if not strict:
        blocks: dict[tuple, list[int]] = defaultdict(list)
        for i, sg in enumerate(merged):
            words = skeleton(sg["norm_en"]).split()
            key = (len(words), words[0] if words else "",
                   words[-1] if words else "")
            blocks[key].append(i)
        for key in sorted(blocks):
            idxs = sorted(blocks[key])
            if len(idxs) < 2:
                continue
            for a_i in range(len(idxs)):
                for b_i in range(a_i + 1, len(idxs)):
                    a, b = idxs[a_i], idxs[b_i]
                    sim = jaccard(jaccard_tokens(merged[a]["norm_en"]),
                                  jaccard_tokens(merged[b]["norm_en"]))
                    if sim >= threshold:
                        _union_find_join(parent, a, b)
                        root = min(parent[a], parent[b])
                        edge_min[root] = min(edge_min.get(root, 1.0), sim)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # -- collect clusters
    clusters: dict[int, list[dict]] = defaultdict(list)
    for i, sg in enumerate(merged):
        clusters[find(i)].append(sg)

    groups: list[dict] = []
    for root, parts in clusters.items():
        all_rows = [r for sg in parts for r in sg["rows"]]
        if len(all_rows) < 2:
            continue
        variants: dict[str, list] = {}
        for r in all_rows:
            k = de_key(r["de"])
            v = variants.setdefault(k, [0, r["_index"], r["de"]])
            v[0] += 1
        if len(variants) < 2:
            continue
        # DE variants ordered by (count desc, first seen asc) -> first = Majorität
        de_variants = sorted(variants.values(), key=lambda v: (-v[0], v[1]))
        en_variants = sorted({sg["norm_en"] for sg in parts})
        raw_en = {sg["norm_en"]: sg["raw_en"] for sg in parts}
        sim_min = edge_min.get(root)
        groups.append({
            "en_variants": en_variants,
            "raw_en": raw_en,
            "de_variants": de_variants,
            "rows": all_rows,
            "near": len(en_variants) > 1,
            "sim_min": sim_min,
        })

    groups.sort(key=lambda g: (-len(g["de_variants"]), -len(g["rows"]),
                               sorted(r["id"] for r in g["rows"])[0]))
    for n, g in enumerate(groups, 1):
        g["gruppe"] = f"K{n:04d}"
    return groups


# --------------------------------------------------------------------------
# Decision (Werkstatt-Regeln aus Issue #39)
# --------------------------------------------------------------------------

def decide_group(g: dict, glossary: list[dict]) -> dict:
    """Return {vorschlag, grund, mode, basis}:
    mode  = "kontext"  (bewusster Unterschied möglich — kein Gewinner)
    basis = "glossar" | "majoritaet" (wer gewinnt und warum)
    """
    n = len(g["rows"])
    d = len(g["de_variants"])
    if not g["near"]:
        kind = "exakt identisch"
    elif g.get("sim_min") is not None:
        kind = f"fast identisch (min. Aehnlichkeit {round(100 * g['sim_min'])}%)"
    else:
        kind = "fast identisch"

    orts = sorted({r["ort"] for r in g["rows"] if r["ort"]})
    kates = sorted({r["kategorie"] for r in g["rows"] if r["kategorie"]})
    register = {bool(FORMAL_SIE_RE.search(v[2])) for v in g["de_variants"]}
    register_conflict = len(register) > 1

    notes: list[str] = []
    context_mode = len(orts) > 1 or len(kates) > 1 or register_conflict

    glossary_hit = None
    if not context_mode:
        for entry in glossary:
            if entry["de"].casefold() == entry["en"].casefold():
                continue
            if any(name_in_text(entry["en"], e) for e in g["en_variants"]):
                containing = [v for v in g["de_variants"]
                              if name_in_text(entry["de"], v[2])]
                if containing:
                    glossary_hit = (entry,
                                    max(containing, key=lambda v: v[0]))
                    break

    if context_mode:
        vorschlag = KONTEXT
        basis = "kontext"
        reasons = []
        if len(orts) > 1:
            reasons.append(f"{len(orts)} verschiedene Orte ({', '.join(orts[:4])})")
        if len(kates) > 1:
            reasons.append(f"Kategorien: {', '.join(kates)}")
        if register_conflict:
            reasons.append("Registerunterschied (formelles 'Sie' vs. 'du')")
        notes.append("Kontext: möglicher bewusster Unterschied — " + "; ".join(reasons))
    else:
        if glossary_hit:
            entry, winner = glossary_hit
            vorschlag = winner[2]
            basis = "glossar"
            notes.append(f"Glossar: '{entry['en']}' → kanonisch '{entry['de']}' "
                         f"({winner[0]}×)")
        else:
            winner = g["de_variants"][0]
            vorschlag = winner[2]
            basis = "majoritaet"
            notes.append(f"Majorität: {winner[0]}× ({round(100 * winner[0] / n)}%)")
    if "system_kampf" in orts:
        notes.append("Systemtext: Gen-3-Wortlaut/Präsens prüfen")

    kates_disp = Counter(r["kategorie"] or "Dialog" for r in g["rows"])
    grund = (f"EN {kind} · {n} Einträge · {d} DE-Varianten · "
             + " · ".join(notes) + " · Kategorien: "
             + ", ".join(f"{k} ({v})" for k, v in kates_disp.most_common()))
    return {"vorschlag": vorschlag, "grund": grund, "mode": "kontext"
            if context_mode else "winner", "basis": basis}


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Konsistenz-Filter (EN/DE)")
    p.add_argument("--csv", required=True,
                   help="Korpus-CSV (;-getrennt: id;en;de;kategorie;ort)")
    p.add_argument("--glossary", default=None,
                   help="Namensverzeichnis-Markdown (| DE | EN | Art |)")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--threshold", type=float, default=0.85,
                   help="Jaccard-Schwelle für 'fast identisch' (Default 0.85)")
    p.add_argument("--strict", action="store_true",
                   help="nur exakt identische EN-Quellen")
    p.add_argument("--top", type=int, default=20,
                   help="Gruppen in der Report-Topliste")
    args = p.parse_args()

    with open(args.csv, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        rows = []
        seen_ids = set()
        for i, row in enumerate(reader):
            row_id = (row.get("id") or "").strip()
            en, de = (row.get("en") or ""), (row.get("de") or "")
            if not row_id or not en.strip() or not de.strip():
                continue
            if row_id in seen_ids:
                print(f"! Duplizierte ID {row_id} (Zeile {i + 2}) — ignoriert",
                      file=sys.stderr)
                continue
            seen_ids.add(row_id)
            rows.append({"id": row_id, "en": en, "de": de,
                         "kategorie": (row.get("kategorie") or "").strip(),
                         "ort": (row.get("ort") or "").strip(), "_index": i})
    print(f"→ {len(rows)} Zeilen geladen", file=sys.stderr)

    glossary = parse_glossary(args.glossary)
    if args.glossary and not glossary:
        print(f"⚠ Glossar nicht lesbar: {args.glossary}", file=sys.stderr)
    print(f"→ {len(glossary)} Glossar-Einträge", file=sys.stderr)

    groups = build_groups(rows, threshold=args.threshold, strict=args.strict)
    decisions = {g["gruppe"]: decide_group(g, glossary) for g in groups}
    n_kontext = sum(1 for v in decisions.values() if v["mode"] == "kontext")
    n_glossar = sum(1 for v in decisions.values() if v["basis"] == "glossar")
    exact = sum(1 for g in groups if not g["near"])
    print(f"→ {len(groups)} Gruppen ({exact} exakt, "
          f"{len(groups) - exact} nähere)", file=sys.stderr)
    print(f"→ davon {n_kontext} 'Kontext prüfen', {n_glossar} Glossar-Entscheid",
          file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)

    # -- ergebnis.csv (Spec: gruppe;ids;en_varianten;de_varianten;vorschlag;grund)
    epath = os.path.join(args.out_dir, "ergebnis.csv")
    with open(epath, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow(["gruppe", "ids", "en_varianten", "de_varianten",
                    "vorschlag", "grund"])
        for g in groups:
            dec = decisions[g["gruppe"]]
            ids = ",".join(sorted(r["id"] for r in g["rows"]))
            en_vars = " | ".join(g["raw_en"][e] for e in g["en_variants"])
            de_vars = " | ".join(v[2] for v in g["de_variants"])
            w.writerow([g["gruppe"], ids, en_vars, de_vars,
                        dec["vorschlag"], dec["grund"]])
    print(f"→ {epath}", file=sys.stderr)

    # -- maschinenlesbares Detailergebnis (ENTHÄLT TEXT — nie öffentlich committen)
    jpath = os.path.join(args.out_dir, "konsistenz_results.json")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_by": "konsistenz-filter",
            "csv_rows": len(rows),
            "glossary_entries": len(glossary),
            "threshold": args.threshold,
            "strict": args.strict,
            "groups": [
                {"gruppe": g["gruppe"],
                 "ids": sorted(r["id"] for r in g["rows"]),
                 "en_variants": g["en_variants"],
                 "de_variants": [{"raw": v[2], "count": v[0]}
                                 for v in g["de_variants"]],
                 **decisions[g["gruppe"]]}
                for g in groups],
        }, fh, ensure_ascii=False, indent=1)
    print(f"→ {jpath} (enthält Textinhalte — nicht öffentlich committen)",
          file=sys.stderr)

    # -- Report (NUR Aggregat — öffentlich sicher)
    mpath = os.path.join(args.out_dir, "konsistenz_report.md")
    L: list[str] = []
    add = L.append
    add("# Konsistenz-Filter — Report (nur Aggregat, keine Textinhalte)")
    add("")
    add(f"> Zeilen: **{len(rows)}** · Gruppen: **{len(groups)}** "
        f"({exact} exakt, {len(groups) - exact} nähere) · "
        f"'Kontext prüfen': {n_kontext} · Glossar-Entscheid: {n_glossar} · "
        f"Jaccard-Schwelle: {args.threshold} · strict: {args.strict}")
    add("")
    add("## Top-Gruppen (nach Varianten/Dichte)")
    add("")
    add("| Gruppe | EN-Varianten | Einträge | DE-Varianten | Modus | Kategorien |")
    add("|---|---|---|---|---|---|")
    for g in groups[:args.top]:
        kates = Counter(r["kategorie"] or "Dialog" for r in g["rows"])
        dec = decisions[g["gruppe"]]
        add(f"| {g['gruppe']} | {len(g['en_variants'])} | {len(g['rows'])} | "
            f"{len(g['de_variants'])} | {dec['mode']} | "
            + ", ".join(k for k, _ in kates.most_common(3)) + " |")
    add("")
    add("## Methode (reproduzierbar)")
    add("")
    add("- Normalisierung: Steuerzeichen (\\\\n, \\\\p, \\\\l, ...) und "
        "Zeilenumbrüche zu Leerzeichen, Farbcodes ([#FC...]) entfernt, "
        "Puffer-Platzhalter ([FDxx] / [#FDxx]) kanonisch vereinheitlicht, "
        "NFKC + Anführungszeichen vereinheitlicht, Leerzeichen gestaucht")
    add("- Exakt-Gruppen: gleiche EN-Quelle nach Normalisierung")
    add(f"- Nahe-Gruppen: Multiset-Jaccard ≥ {args.threshold} innerhalb von "
        "Blocking-Blöcken (Wortzahl, erstes, letztes Wort); Platzhalter "
        "bleiben unterschiedliche Tokens (unterschiedliche Puffer = "
        "unterschiedliche Vorlagen)")
    add("- DE-Varianten: Unterschiede nur in Groß-/Kleinschreibung oder "
        "Satzendepunktuation gelten als identisch")
    add("- **Kontext-Regel**: Gruppen über mehrere Orte/Kategorien oder mit "
        "Registerwechsel (formelles 'Sie' vs. 'du') → 'Kontext prüfen', "
        "kein Gewinner (bewusste Unterschiede je Sprecher/Situation)")
    add("- **Glossar-Regel**: kanonischer DE-Name aus dem "
        "Namensverzeichnis gewinnt vor der Majorität (offizielle "
        "Pokémon-Begriffe gehen immer vor)")
    add("- Vorschlag (sonst): häufigste Fassung (Gleichstand: erste Vorkunft "
        "in Dateireihenfolge) — deterministisch, ohne Zufall und ohne LLM")
    add("")
    add("*Konsistenz-Filter — deterministisch und offline. KEINE "
        "Spieltexte in diesem Report (Absicht).*")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"→ {mpath}", file=sys.stderr)

    print(f"\n=== ZUSAMMENFASSUNG ===", file=sys.stderr)
    print(f"Zeilen: {len(rows)} | Gruppen: {len(groups)} "
          f"(exakt {exact}, nähere {len(groups) - exact}) | "
          f"Kontext: {n_kontext} | Glossar: {n_glossar}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
