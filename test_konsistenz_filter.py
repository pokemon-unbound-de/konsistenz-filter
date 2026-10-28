"""Offline tests for the Konsistenz-Filter (synthetic fixtures only)."""

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
import konsistenz_filter as kf


def make_csv(path, rows):
    """rows: (id, en, de, kategorie, ort)"""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["id", "en", "de", "kategorie", "ort"])
        for row in rows:
            w.writerow(row)


def make_glossary(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Testglossar\n\n| DE | EN | Art |\n| --- | --- | --- |\n")
        for de, en in entries:
            fh.write(f"| {de} | {en} | Test |\n")


def run_tool():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = kf.main()
    return rc


def patched_argv(args):
    old = sys.argv
    sys.argv = ["konsistenz_filter.py"] + args
    try:
        return run_tool()
    finally:
        sys.argv = old


class KonsistenzFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.csv_path = os.path.join(self.temp.name, "texte.csv")
        self.gloss_path = os.path.join(self.temp.name, "glossar.md")
        self.out_dir = os.path.join(self.temp.name, "out")

    def load_results(self):
        with open(os.path.join(self.out_dir, "ergebnis.csv"), encoding="utf-8",
                  newline="") as fh:
            return list(csv.DictReader(fh, delimiter=";"))

    def test_werkstatt_csv_format(self):
        # Werkstatt-Einleseformat: 9 Spalten, status vorschlag/kontext_pruefen,
        # kategorie/ort als Kontext-Spalten gefüllt
        make_csv(self.csv_path, [
            ("0x0001", "You found a key!", "Du hast einen Schluessel gefunden!", "scripts", "Dorf"),
            ("0x0002", "You found a key!", "Du hast einen Schluessel gefunden!", "scripts", "Dorf"),
            ("0x0003", "You found a key!", "Du hast den Schluessel gefunden.", "scripts", "Dorf"),
            ("0x0004", "The door is locked.", "Die Tuer ist verschlossen.", "scripts", "Dorf"),
            ("0x0005", "The door is locked.", "Die Tuer ist zu.", "system_kampf", "Stadt"),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir,
                      "--werkstatt"])
        with open(os.path.join(self.out_dir, "werkstatt_einheitlichkeit.csv"),
                  encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0].keys()),
                         ["gruppe", "ids", "en", "de_varianten", "vorschlag",
                          "grund", "kategorie", "ort", "status"])
        by_status = {r["status"]: r for r in rows}
        self.assertEqual(set(by_status), {"vorschlag", "kontext_pruefen"})
        # Gruppen-Mitglieder als script:<adresse> (Issue #39 Korrekturrunde)
        self.assertEqual(by_status["vorschlag"]["ids"],
                         "script:0x0001,script:0x0002,script:0x0003")
        self.assertEqual(by_status["vorschlag"]["kategorie"], "scripts")
        self.assertEqual(by_status["vorschlag"]["ort"], "Dorf")
        self.assertEqual(by_status["kontext_pruefen"]["ids"],
                         "script:0x0004,script:0x0005")
        self.assertEqual(by_status["kontext_pruefen"]["vorschlag"],
                         "Kontext prüfen")
        # Default-Lauf erzeugt KEIN Werkstatt-CSV
        patched_argv(["--csv", self.csv_path,
                      "--out-dir", os.path.join(self.temp.name, "out2")])
        self.assertFalse(os.path.exists(
            os.path.join(self.temp.name, "out2",
                         "werkstatt_einheitlichkeit.csv")))

    def test_exact_en_different_de_majority_wins(self):
        make_csv(self.csv_path, [
            ("0x0001", "You found a key!", "Du hast einen Schluessel gefunden!", "scripts", ""),
            ("0x0002", "You found a key!", "Du hast einen Schluessel gefunden!", "scripts", ""),
            ("0x0003", "You found a key!", "Du hast den Schluessel gefunden.", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ids"],
                         "script:0x0001,script:0x0002,script:0x0003")
        self.assertIn("Majorität", rows[0]["grund"])
        self.assertEqual(rows[0]["vorschlag"], "Du hast einen Schluessel gefunden!")

    def test_same_en_same_de_is_not_a_group(self):
        make_csv(self.csv_path, [
            ("0x0001", "Good morning!", "Guten Morgen!", "scripts", ""),
            ("0x0002", "Good morning!", "Guten Morgen!", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])

    def test_case_and_punctuation_only_differences_are_identical(self):
        self.assertEqual(kf.skeleton("It's here."), kf.skeleton("It’s here!"))
        self.assertEqual(kf.de_key("Gut."), kf.de_key("gut?"))
        make_csv(self.csv_path, [
            ("0x0001", "It's here.", "Es ist hier.", "scripts", ""),
            ("0x0002", "It’s here!", "Es ist hier!", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])

    def test_near_en_is_merged_above_threshold(self):
        make_csv(self.csv_path, [
            ("0x0001",
             "We have walked a very long road through the forest to find this place.",
             "Wir sind einen sehr langen Weg durch den Wald zu diesem Ort gegangen.",
             "scripts", ""),
            ("0x0002",
             "We have walked a very long road through the woods to find this place.",
             "Wir sind einen sehr langen Weg durch die Waelder zu diesem Ort gegangen.",
             "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir,
                      "--threshold", "0.85"])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertIn("fast identisch", rows[0]["grund"])
        self.assertEqual(len(rows[0]["ids"].split(",")), 2)
        # Jaccard-merge (EN nur ähnlich, Inhalt unterscheidet sich) ->
        # 'Kontext prüfen' statt Vorschlag (Issue #39 Korrekturrunde:
        # Einheitsfassungen werden nie blind vervielfältigt)
        self.assertEqual(rows[0]["vorschlag"], "Kontext prüfen")
        self.assertIn("Jaccard-merge", rows[0]["grund"])
        # --strict muss diese nahe Gruppe ausschliessen
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir,
                      "--strict"])
        self.assertEqual(self.load_results(), [])

    def test_color_codes_do_not_change_identity(self):
        make_csv(self.csv_path, [
            ("0x0001", "[#FC0106]You found it!", "Du hast es gefunden!", "gap_misc", ""),
            ("0x0002", "You found it!", "Du hast es entdeckt!", "gap_misc", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        # identisch nach Normalisierung — angezeigte EN-Form = erste Vorkunft
        self.assertEqual(rows[0]["en_varianten"], "[#FC0106]You found it!")

    def test_buffer_slots_stay_distinct_templates(self):
        make_csv(self.csv_path, [
            ("0x0001", "You earned [FD02] points!", "Du hast [FD02] Punkte erhalten!", "gap_misc", ""),
            ("0x0002", "You earned [FD03] points!", "Du hast [FD03] Punkte verdient!", "gap_misc", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir,
                      "--threshold", "0.5"])
        # [FD02] vs [FD03]: Jaccard 2/4 = 0.5 — bei 0.5 wird gemerged,
        # die Platzhalter bleiben aber unterschiedliche DE-Varianten.
        # Ohne Merge (Default-Schwelle) gibt es keine Gruppe.
        self.assertTrue(self.load_results())
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])

    def test_glossary_canonical_name_beats_majority(self):
        make_csv(self.csv_path, [
            ("0x0001", "The Blubio is here!", "Hier ist das Blubio!", "scripts", ""),
            ("0x0002", "The Blubio is here!", "Hier ist das Blubio!", "scripts", ""),
            ("0x0003", "The Blubio is here!", "Hier ist der Blubbio!", "scripts", ""),
        ])
        make_glossary(self.gloss_path, [("Blubbio", "Blubio")])
        patched_argv(["--csv", self.csv_path, "--glossary", self.gloss_path,
                      "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Hier ist der Blubbio!")
        self.assertIn("Glossar", rows[0]["grund"])

    def test_different_locations_mean_context_check(self):
        make_csv(self.csv_path, [
            ("0x0001", "I have to go now.", "Ich muss jetzt los.", "scripts", "Hafenstadt"),
            ("0x0002", "I have to go now.", "Ich muss mich verabschieden.", "scripts", "Antisia"),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Kontext prüfen")
        self.assertIn("Kontext", rows[0]["grund"])

    def test_mixed_categories_mean_context_check(self):
        make_csv(self.csv_path, [
            ("0x0001", "Yes, of course.", "Ja, natuerlich.", "scripts", ""),
            ("0x0002", "Yes, of course.", "Ja, selbstverstaendlich.", "ui_rc", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Kontext prüfen")

    def test_register_switch_means_context_check(self):
        make_csv(self.csv_path, [
            ("0x0001", "Thank you for helping me.", "Danke, dass du mir geholfen hast.", "scripts", "Hafenstadt"),
            ("0x0002", "Thank you for helping me.", "Danke, dass Sie mir geholfen haben.", "scripts", "Hafenstadt"),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Kontext prüfen")
        self.assertIn("Register", rows[0]["grund"])

    def test_system_template_keeps_winner_and_adds_note(self):
        make_csv(self.csv_path, [
            ("0x0001", "A wild thing appeared!", "Ein wildes Wesen ist aufgetaucht!", "gap_kampf", "system_kampf"),
            ("0x0002", "A wild thing appeared!", "Ein wildes Wesen tauchte auf!", "gap_kampf", "system_kampf"),
            ("0x0003", "A wild thing appeared!", "Ein wildes Wesen ist aufgetaucht!", "gap_kampf", "system_kampf"),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Ein wildes Wesen ist aufgetaucht!")
        self.assertIn("Gen-3", rows[0]["grund"])

    def test_output_csv_format(self):
        make_csv(self.csv_path, [
            ("0x0001", "Hello there!", "Hallo!", "scripts", ""),
            ("0x0002", "Hello there!", "Hallo da!", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        with open(os.path.join(self.out_dir, "ergebnis.csv"), encoding="utf-8",
                  newline="") as fh:
            header = next(csv.reader(fh, delimiter=";"))
        self.assertEqual(header, ["gruppe", "ids", "en_varianten", "de_varianten",
                                  "vorschlag", "grund"])
        rows = self.load_results()
        # DE-Varianten sortiert: Anzahl desc, dann erste Vorkunft asc
        self.assertEqual(rows[0]["de_varianten"], "Hallo! | Hallo da!")
        self.assertIn(rows[0]["vorschlag"], rows[0]["de_varianten"].split(" | "))
        with open(os.path.join(self.out_dir, "konsistenz_results.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["groups"]), 1)
        self.assertIn("vorschlag", data["groups"][0])

    def test_name_substitution_template_is_suppressed(self):
        make_csv(self.csv_path, [
            ("0x0001",
             "Have the Blubio hold this stone and it will enable it to Mega Evolve during battle.",
             "Trägt der Blubbio diesen Stein, kann er die Mega-Entwicklung vollziehen.",
             "gap_item_desc", ""),
            ("0x0002",
             "Have the Glubio hold this stone and it will enable it to Mega Evolve during battle.",
             "Trägt der Glubbio diesen Stein, kann er die Mega-Entwicklung vollziehen.",
             "gap_item_desc", ""),
        ])
        make_glossary(self.gloss_path, [("Blubbio", "Blubio"),
                                        ("Glubbio", "Glubio")])
        patched_argv(["--csv", self.csv_path, "--glossary", self.gloss_path,
                      "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])

    def test_type_substitution_template_is_suppressed(self):
        make_csv(self.csv_path, [
            ("0x0001",
             "This is a crystallized form of Z-Power. It upgrades Bug-type moves to Z-Moves.",
             "Eine kristallisierte Form der Z-Kraft. Sie wandelt Attacken vom Typ Käfer in Z-Moves.",
             "gap_item_desc", ""),
            ("0x0002",
             "This is a crystallized form of Z-Power. It upgrades Flying-type moves to Z-Moves.",
             "Eine kristallisierte Form der Z-Kraft. Sie wandelt Attacken vom Typ Flug in Z-Moves.",
             "gap_item_desc", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])
        # Grass-Typ: EN "Grass-type" == DE "Pflanze" (offizieller DE-Name)
        self.assertIn(kf.PH_L + "T:GRASS" + kf.PH_R, kf.norm_text("Grass-type moves"))
        self.assertIn(kf.PH_L + "t:grass" + kf.PH_R,
                      kf.de_key("Pflanze-Attacken"))

    def test_postgen3_types_are_tokenized(self):
        self.assertIn(kf.PH_L + "T:STEEL" + kf.PH_R, kf.norm_text("Steel-type moves"))
        self.assertIn(kf.PH_L + "T:DARK" + kf.PH_R, kf.norm_text("Dark-type data"))
        self.assertIn(kf.PH_L + "T:PSYCHIC" + kf.PH_R,
                      kf.norm_text("Psychic-type moves"))
        self.assertIn(kf.PH_L + "t:steel" + kf.PH_R, kf.de_key("Stahl-Attacken"))
        self.assertIn(kf.PH_L + "t:dark" + kf.PH_R, kf.de_key("Typ Unlicht"))
        self.assertIn(kf.PH_L + "t:psychic" + kf.PH_R, kf.de_key("Typ Psycho"))

    def test_color_template_is_suppressed(self):
        # Farben: begrenztes Wortset, das als Slot zählt (kleingeschrieben)
        make_csv(self.csv_path, [
            ("0x0001",
             "This jewel was found in the large room on the first floor beneath a red crystal.",
             "Dieser Schmuckstein lag im großen Raum im Erdgeschoss unter einem roten Kristall.",
             "scripts", ""),
            ("0x0002",
             "This jewel was found in the large room on the first floor beneath a blue crystal.",
             "Dieser Schmuckstein lag im großen Raum im Erdgeschoss unter einem blauen Kristall.",
             "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])

    def test_structural_name_template_without_glossary_is_suppressed(self):
        # Namen NICHT im Glossar: Struktur-Erkennung muss reichen
        # (großgeschriebene EN-Wörter als Namen, gleiche Slot-Anzahl DE)
        make_csv(self.csv_path, [
            ("0x0001",
             "Have Zorba hold this, and this stone will enable it to Mega Evolve during battle.",
             "Trägt Zorbla diesen Stein, kann es im Kampf die Mega-Entwicklung vollziehen.",
             "gap_item_desc", ""),
            ("0x0002",
             "Have Quibla hold this, and this stone will enable it to Mega Evolve during battle.",
             "Trägt Quiblo diesen Stein, kann es im Kampf die Mega-Entwicklung vollziehen.",
             "gap_item_desc", ""),
            ("0x0003",
             "Have Mufflo hold this, and this stone will enable it to Mega Evolve during battle.",
             "Trägt Muffla diesen Stein, kann es im Kampf die Mega-Entwicklung vollziehen.",
             "gap_item_desc", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])
        with open(os.path.join(self.out_dir, "konsistenz_results.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["suppressed_templates"]), 1)
        self.assertEqual(data["suppressed_templates"][0]["ids"],
                         ["0x0001", "0x0002", "0x0003"])

    def test_lowercase_en_variation_is_not_suppressed(self):
        # forest/woods: kleine Wörter sind KEINE Namen -> Gruppe bleibt stehen
        make_csv(self.csv_path, [
            ("0x0001",
             "We walked a very long road through the forest all day to reach this place.",
             "Wir gingen den ganzen Tag einen sehr langen Weg durch den Wald, um diesen Ort zu erreichen.",
             "scripts", ""),
            ("0x0002",
             "We walked a very long road through the woods all day to reach this place.",
             "Wir gingen den ganzen Tag einen sehr langen Weg durch die Wälder, um diesen Ort zu erreichen.",
             "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        # Die Gruppe bleibt stehen (kleine Wörter sind KEINE Namen ->
        # keine Unterdrückung); die Entscheidung ist nach der
        # #39-Korrekturrunde 'Kontext prüfen' (Jaccard-merge), nicht
        # Majorität: Der Inhalt unterscheidet sich (Wald/Wälder) und
        # wird nicht blind vereinheitlicht.
        self.assertEqual(rows[0]["vorschlag"], "Kontext prüfen")
        self.assertIn("Jaccard-merge", rows[0]["grund"])

    def test_type_mismatch_row_keeps_group(self):
        # EN Fire-type, DE aber "Typ Wasser" -> Typ-Korrespondenz pro Zeile
        # schlägt fehl -> Gruppe bleibt (echte Inkonsistenz)
        make_csv(self.csv_path, [
            ("0x0001",
             "This is a crystallized form of Z-Power. It upgrades Bug-type moves to Z-Moves.",
             "Eine kristallisierte Form der Z-Kraft. Sie wandelt Attacken vom Typ Käfer in Z-Moves.",
             "gap_item_desc", ""),
            ("0x0002",
             "This is a crystallized form of Z-Power. It upgrades Fire-type moves to Z-Moves.",
             "Eine kristallisierte Form der Z-Kraft. Sie wandelt Attacken vom Typ Wasser in Z-Moves.",
             "gap_item_desc", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(len(self.load_results()), 1)

    def test_glossary_name_mismatch_row_keeps_group(self):
        # Zeile 3: EN sagt Candice, DE aber Lärch -> korrespondiert nicht
        # -> Gruppe bleibt stehen (falscher Name sichtbar)
        make_csv(self.csv_path, [
            ("0x0001",
             "Today Log says hello to everyone who is gathered in the big castle.",
             "Heute sagt Lärch allen, die sich im großen Schloss versammelt haben, Hallo.",
             "scripts", ""),
            ("0x0002",
             "Today Candice says hello to everyone who is gathered in the big castle.",
             "Heute sagt Frida allen, die sich im großen Schloss versammelt haben, Hallo.",
             "scripts", ""),
            ("0x0003",
             "Today Candice says hello to everyone who is gathered in the big castle.",
             "Heute sagt Lärch allen, die sich im großen Schloss versammelt haben, Hallo.",
             "scripts", ""),
        ])
        make_glossary(self.gloss_path, [("Lärch", "Log"), ("Frida", "Candice")])
        patched_argv(["--csv", self.csv_path, "--glossary", self.gloss_path,
                      "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ids"],
                         "script:0x0001,script:0x0002,script:0x0003")

    def test_first_last_position_diff_is_kept(self):
        # Differenz nur im ersten Wort (Punktuation) ist KEIN Slot —
        # Blocking fixiert erstes/letztes Wort -> Gruppe bleibt
        make_csv(self.csv_path, [
            ("0x0001", "Aww no!", "Aww, nein!", "scripts", ""),
            ("0x0002", "Aww, no!", "Aww, weh!", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        self.assertEqual(len(self.load_results()), 1)

    def test_punctuation_en_variants_are_both_listed(self):
        # EN-Varianten, die nur in Punktuation differieren, werden gemerged
        # und beide werden in en_varianten gelistet (Transparenz)
        make_csv(self.csv_path, [
            ("0x0001", "The Def and Sp. Def stats are swapped in the room!",
             "Vert. und Sp.-Vert. sind getauscht!", "scripts", ""),
            ("0x0002", "The Def. and Sp. Def stats are swapped in the room!",
             "Die Werte von Verteidigung und Sp.-Verteidigung sind vertauscht.",
             "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertIn("The Def and Sp. Def stats are swapped in the room!",
                      rows[0]["en_varianten"])
        self.assertIn("The Def. and Sp. Def stats are swapped in the room!",
                      rows[0]["en_varianten"])

    def test_type_token_does_not_match_glossary_name(self):
        # Glossar-Eintrag "Seher | Psychic" (Trainerklasse) darf durch den
        # Typ-Token <T:PSYCHIC> NICHT als Namens-Treffer gezählt werden —
        # sonst würde die (korrekte) Typ-Vorlage nicht unterdrückt.
        make_csv(self.csv_path, [
            ("0x0001",
             "This is a crystallized form of Z-Power. It upgrades Psychic-type moves to Z-Moves.",
             "Eine kristallisierte Form der Z-Kraft. Sie wandelt Attacken vom Typ Psycho in Z-Attacken um.",
             "gap_item_desc", ""),
            ("0x0002",
             "This is a crystallized form of Z-Power. It upgrades Bug-type moves to Z-Moves.",
             "Eine kristallisierte Form der Z-Kraft. Sie wandelt Attacken vom Typ Käfer in Z-Attacken um.",
             "gap_item_desc", ""),
        ])
        make_glossary(self.gloss_path, [("Seher", "Psychic")])
        patched_argv(["--csv", self.csv_path, "--glossary", self.gloss_path,
                      "--out-dir", self.out_dir])
        self.assertEqual(self.load_results(), [])

    def test_same_en_with_name_swap_kept_and_glossary_decides(self):
        make_csv(self.csv_path, [
            ("0x0001", "The Blubio is here!", "Hier ist der Blubbio!", "scripts", ""),
            ("0x0002", "The Blubio is here!", "Hier ist das Blubio!", "scripts", ""),
        ])
        make_glossary(self.gloss_path, [("Blubbio", "Blubio")])
        patched_argv(["--csv", self.csv_path, "--glossary", self.gloss_path,
                      "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Hier ist der Blubbio!")
        self.assertIn("Glossar", rows[0]["grund"])

    def test_run_is_deterministic(self):
        make_csv(self.csv_path, [
            ("0x0001", "We have walked a very long road through the forest to find this place.",
             "Wir sind einen sehr langen Weg durch den Wald zu diesem Ort gegangen.", "scripts", ""),
            ("0x0002", "We have walked a very long road through the woods to find this place.",
             "Wir sind einen sehr langen Weg durch die Waelder zu diesem Ort gegangen.", "scripts", ""),
            ("0x0003", "Good morning!", "Guten Morgen!", "scripts", ""),
            ("0x0004", "Good morning!", "Hallo! Guten Morgen!", "scripts", ""),
        ])
        out_a = os.path.join(self.temp.name, "out_a")
        out_b = os.path.join(self.temp.name, "out_b")
        patched_argv(["--csv", self.csv_path, "--out-dir", out_a])
        patched_argv(["--csv", self.csv_path, "--out-dir", out_b])
        a = Path(out_a, "ergebnis.csv").read_text(encoding="utf-8")
        b = Path(out_b, "ergebnis.csv").read_text(encoding="utf-8")
        self.assertEqual(a, b)

    def test_card_id_helper(self):
        # Werkstatt-Format script:<adresse>, idempotent (Issue #39)
        self.assertEqual(kf.card_id("0x1A2B3C4"), "script:0x1A2B3C4")
        self.assertEqual(kf.card_id("script:0x1A2B3C4"), "script:0x1A2B3C4")

    def test_ids_script_prefix_in_csvs_and_raw_in_json(self):
        make_csv(self.csv_path, [
            ("0x0001", "Hello there!", "Hallo!", "scripts", ""),
            ("0x0002", "Hello there!", "Hallo da!", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir,
                      "--werkstatt"])
        rows = self.load_results()
        self.assertEqual(rows[0]["ids"], "script:0x0001,script:0x0002")
        with open(os.path.join(self.out_dir, "werkstatt_einheitlichkeit.csv"),
                  encoding="utf-8", newline="") as fh:
            wrows = list(csv.DictReader(fh, delimiter=";"))
        self.assertEqual(wrows[0]["ids"], "script:0x0001,script:0x0002")
        # JSON: Einzelzeilen-Referenzen behalten die Roh-ID
        with open(os.path.join(self.out_dir, "konsistenz_results.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["groups"][0]["ids"], ["0x0001", "0x0002"])

    def test_similar_en_group_gets_context_check(self):
        # Jaccard-merged Gruppe (EN nur ähnlich, Inhalt anders) ->
        # 'Kontext prüfen' auch bei gleichem Ort und gleicher Kategorie
        # (#39: Einheitsfassung wird nie blind vervielfältigt)
        make_csv(self.csv_path, [
            ("0x0001",
             "Please bring me the old treasure map right now.",
             "Bringe mir bitte die alte Schatzkarte.",
             "scripts", "Hafenstadt"),
            ("0x0002",
             "Please bring me the new treasure map right now.",
             "Bringe mir bitte die neue Schatzkarte.",
             "scripts", "Hafenstadt"),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir,
                      "--threshold", "0.80"])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Kontext prüfen")
        self.assertIn("Jaccard-merge", rows[0]["grund"])
        self.assertEqual(rows[0]["ids"], "script:0x0001,script:0x0002")

    def test_case_punctuation_variants_keep_winner(self):
        # Gleiches EN-Skelett (nur Groß-/Kleinschreibung + Punktuation,
        # kein Jaccard-merge) -> Vorschlag bleibt gültig (Majorität)
        make_csv(self.csv_path, [
            ("0x0001", "It's here.", "Es ist hier.", "scripts", ""),
            ("0x0002", "IT'S HERE.", "Es ist hier.", "scripts", ""),
            ("0x0003", "it's here!", "Hier ist es.", "scripts", ""),
        ])
        patched_argv(["--csv", self.csv_path, "--out-dir", self.out_dir])
        rows = self.load_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vorschlag"], "Es ist hier.")
        self.assertIn("Majorität", rows[0]["grund"])


def make_kanon(path, rows):
    """rows: (typ, en, de, quelle) — Kanon-Namensliste-Format"""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["typ", "en", "de", "quelle"])
        for row in rows:
            w.writerow(row)


# Synthetische Kanon-Fixture (keine echten Spieltexte):
#   Mew = EN==DE (darf nie ein Befund sein)
#   10,000,000 Volt Thunderbolt = Kommas (CSV-Quoting + Regex)
#   Let's Snuggle Forever = Apostroph (Regex-Sonderzeichen)
#   G-Max Vine Lash → Giga-Geißel = Umlaut/ß + Bindestrich
KANON_FIXTURE = [
    ("pokémon", "Mew", "Mew", "https://example.org/mew"),
    ("pokémon", "Charizard", "Glurak", "https://example.org/glurak"),
    ("attacke", "10,000,000 Volt Thunderbolt",
     "Tausendfacher Donnerblitz", "https://example.org/donnerblitz"),
    ("attacke", "Let's Snuggle Forever", "Herzliche Knuddelkloppe",
     "https://example.org/knuddelkloppe"),
    ("attacke", "G-Max Vine Lash", "Giga-Geißel",
     "https://example.org/geissel"),
]


class KanonCheckTests(unittest.TestCase):
    """v0.5: --kanon prüft offizielle EN-Namen im DE-Text und umgekehrt."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.csv_path = os.path.join(self.temp.name, "texte.csv")
        self.kanon_path = os.path.join(self.temp.name, "kanon.csv")
        self.out_dir = os.path.join(self.temp.name, "out")

    def run_with_kanon(self, extra=None):
        args = ["--csv", self.csv_path, "--kanon", self.kanon_path,
                "--out-dir", self.out_dir]
        if extra:
            args += extra
        return patched_argv(args)

    def read_kanon(self):
        with open(os.path.join(self.out_dir, "kanon_check.csv"),
                  encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh, delimiter=";"))

    def read_json(self):
        with open(os.path.join(self.out_dir, "konsistenz_results.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def test_en_name_in_de_text_is_a_finding(self):
        make_csv(self.csv_path, [
            ("0x0001", "Charizard uses Flamethrower!",
             "Charizard setzt Flamethrower ein!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        rows = self.read_kanon()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "0x0001")
        self.assertEqual(rows[0]["richtung"], "en_name_in_de")
        self.assertEqual(rows[0]["name"], "Charizard")
        self.assertEqual(rows[0]["erwartet"], "Glurak")
        self.assertEqual(rows[0]["typ"], "pokémon")
        self.assertEqual(rows[0]["quelle"], "https://example.org/glurak")

    def test_de_name_present_suppresses_en_finding(self):
        # Offizielles EN-Namen im DE-Text, ABER das offizielle
        # DE-Namen ist auch da -> kein Befund (Präzision vor Recall)
        make_csv(self.csv_path, [
            ("0x0001", "Charizard is here!",
             "Charizard und Glurak sind hier!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        self.assertEqual(self.read_kanon(), [])

    def test_de_name_alone_is_not_a_finding(self):
        # DE-Fassung enthält nur den offiziellen DE-Namen -> korrekt
        make_csv(self.csv_path, [
            ("0x0001", "Charizard is here!", "Glurak ist hier!",
             "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        self.assertEqual(self.read_kanon(), [])

    def test_de_name_in_en_text_is_a_finding(self):
        # Offizielles DE-Namen in der EN-Quelle -> Kontaminierung.
        # DE-Text enthält bewusst KEINEN Kanon-Namen, damit genau der
        # de_name_in_en-Befund entsteht.
        make_csv(self.csv_path, [
            ("0x0001", "Glurak is here!", "Der Drache ist hier!",
             "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        rows = self.read_kanon()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["richtung"], "de_name_in_en")
        self.assertEqual(rows[0]["name"], "Glurak")
        self.assertEqual(rows[0]["erwartet"], "Charizard")

    def test_identical_names_in_both_languages_never_a_finding(self):
        # Mew == Mew in EN und DE: Name in beiden Fassungen ist korrekt
        make_csv(self.csv_path, [
            ("0x0001", "Mew is here!", "Mew ist hier!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        self.assertEqual(self.read_kanon(), [])

    def test_word_boundary_and_case_sensitive_matching(self):
        # Mews (Plural), Mewtu (anderes Pokémon) und kleingeschriebenes
        # 'mew' dürfen NICHT mit dem Kanon-Namen 'Mew' matchen
        make_csv(self.csv_path, [
            ("0x0001", "The Mews play.", "Die Mews spielen.", "scripts", ""),
            ("0x0002", "Mewtwo waits.", "Mewtu wartet dort.", "scripts", ""),
            ("0x0003", "Mew is here!", "mew ist hier!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        self.assertEqual(self.read_kanon(), [])

    def test_names_inside_longer_words_do_not_match(self):
        # 'Charizardite' enthält 'Charizard', aber Wortgrenz-Check:
        # nach 'Charizard' folgt 'i' -> kein Match
        make_csv(self.csv_path, [
            ("0x0001", "Use the Charizardite X.",
             "Verwende das Gluraknit X.", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        self.assertEqual(self.read_kanon(), [])

    def test_special_characters_comma_and_apostrophe(self):
        # '10,000,000 Volt Thunderbolt' (Kommas) und 'Let's Snuggle
        # Forever' (Apostroph): Regex-Escape + CSV-Quoting müssen halten
        make_csv(self.csv_path, [
            ("0x0001", "It learns 10,000,000 Volt Thunderbolt.",
             "Es lernt 10,000,000 Volt Thunderbolt.", "scripts", ""),
            ("0x0002", "Let's Snuggle Forever!",
             "Herzliche Knuddelkloppe!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        rows = self.read_kanon()
        # Zeile 1: EN-Name im DE-Text ohne DE-Name -> Befund
        # Zeile 2: korrekte DE-Übersetzung -> kein Befund
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "0x0001")
        self.assertEqual(rows[0]["name"], "10,000,000 Volt Thunderbolt")
        self.assertEqual(rows[0]["erwartet"], "Tausendfacher Donnerblitz")

    def test_umlaut_and_hyphen_name(self):
        # 'Giga-Geißel' (ß + Bindestrich) muss exakt matchen
        make_csv(self.csv_path, [
            ("0x0001", "It uses G-Max Vine Lash!",
             "Es nutzt die Giga-Geißel!", "scripts", ""),
            ("0x0002", "It uses G-Max Vine Lash!",
             "Es nutzt G-Max Vine Lash!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        rows = self.read_kanon()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "0x0002")
        self.assertEqual(rows[0]["name"], "G-Max Vine Lash")
        self.assertEqual(rows[0]["erwartet"], "Giga-Geißel")

    def test_kanon_csv_format_and_json_section(self):
        # Zeile 1: EN-Name 'Charizard' im DE-Text -> en_name_in_de
        # Zeile 2: DE-Name 'Glurak' in der EN-Quelle -> de_name_in_en
        make_csv(self.csv_path, [
            ("0x0001", "Charizard is here!", "Charizard ist hier!",
             "scripts", ""),
            ("0x0002", "Glurak waits.", "Glurak wartet.", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        with open(os.path.join(self.out_dir, "kanon_check.csv"),
                  encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh, delimiter=";"))
        self.assertEqual(header, ["id", "en", "de", "richtung", "name",
                                  "erwartet", "typ", "quelle"])
        data = self.read_json()
        self.assertEqual(data["kanon"]["names"], len(KANON_FIXTURE))
        self.assertEqual(data["kanon"]["findings"], 2)
        self.assertEqual(data["kanon"]["rows_affected"], 2)
        self.assertEqual(data["kanon"]["by_direction"],
                         {"en_name_in_de": 1, "de_name_in_en": 1})
        report = open(os.path.join(self.out_dir, "konsistenz_report.md"),
                      encoding="utf-8").read()
        self.assertIn("## Kanon-Check", report)

    def test_untranslated_rows_are_not_kanon_findings(self):
        # EN == DE (oder nur Formatierungs-/Farbcod-Unterschiede) ->
        # Zeile effektiv nicht übersetzt (z. B. Marken-Platzhalter
        # "Smogon Gen 7 OU" = Website-Name, nicht das Pokémon) ->
        # "Name in falscher Sprache" ist dort nicht definiert
        make_csv(self.csv_path, [
            ("0x0001", "Glurak Gen 7 OU", "Glurak Gen 7 OU", "scripts", ""),
            ("0x0002", "[#FC0106]Charizard", "Charizard", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        self.run_with_kanon()
        self.assertEqual(self.read_kanon(), [])

    def test_default_run_without_kanon(self):
        # Default-Verhalten unverändert: ohne --kanon kein kanon_check.csv
        make_csv(self.csv_path, [
            ("0x0001", "Charizard is here!", "Charizard ist hier!",
             "scripts", ""),
        ])
        out2 = os.path.join(self.temp.name, "out2")
        patched_argv(["--csv", self.csv_path, "--out-dir", out2])
        self.assertFalse(os.path.exists(
            os.path.join(out2, "kanon_check.csv")))
        with open(os.path.join(out2, "konsistenz_results.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsNone(data["kanon"])

    def test_kanon_is_deterministic(self):
        make_csv(self.csv_path, [
            ("0x0001", "Charizard is here!", "Charizard ist hier!",
             "scripts", ""),
            ("0x0002", "Charizard and Mew battle!",
             "Charizard und Mew kämpfen!", "scripts", ""),
        ])
        make_kanon(self.kanon_path, KANON_FIXTURE)
        out_a = os.path.join(self.temp.name, "out_a")
        out_b = os.path.join(self.temp.name, "out_b")
        patched_argv(["--csv", self.csv_path, "--kanon", self.kanon_path,
                      "--out-dir", out_a])
        patched_argv(["--csv", self.csv_path, "--kanon", self.kanon_path,
                      "--out-dir", out_b])
        a = Path(out_a, "kanon_check.csv").read_text(encoding="utf-8")
        b = Path(out_b, "kanon_check.csv").read_text(encoding="utf-8")
        self.assertEqual(a, b)

    def test_empty_kanon_file_warns_but_runs(self):
        make_csv(self.csv_path, [
            ("0x0001", "Charizard is here!", "Charizard ist hier!",
             "scripts", ""),
        ])
        make_kanon(self.kanon_path, [])  # nur Header
        # bewusst NICHT via patched_argv/run_tool: deren interner
        # redirect_stderr würde die Warnung verschlucken
        old = sys.argv
        sys.argv = ["konsistenz_filter.py", "--csv", self.csv_path,
                    "--kanon", self.kanon_path, "--out-dir", self.out_dir]
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                rc = kf.main()
        finally:
            sys.argv = old
        self.assertEqual(rc, 0)
        self.assertIn("⚠", err.getvalue())
        rows = self.read_kanon()
        self.assertEqual(rows, [])
        self.assertEqual(self.read_json()["kanon"]["names"], 0)


if __name__ == "__main__":
    unittest.main()
