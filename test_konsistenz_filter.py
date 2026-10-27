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
        self.assertEqual(by_status["vorschlag"]["ids"],
                         "0x0001,0x0002,0x0003")
        self.assertEqual(by_status["vorschlag"]["kategorie"], "scripts")
        self.assertEqual(by_status["vorschlag"]["ort"], "Dorf")
        self.assertEqual(by_status["kontext_pruefen"]["ids"], "0x0004,0x0005")
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
        self.assertEqual(rows[0]["ids"], "0x0001,0x0002,0x0003")
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
        self.assertEqual(rows[0]["vorschlag"],
                         "Wir gingen den ganzen Tag einen sehr langen Weg durch den Wald, um diesen Ort zu erreichen.")
        self.assertIn("Majorität", rows[0]["grund"])

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
        self.assertEqual(rows[0]["ids"], "0x0001,0x0002,0x0003")

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


if __name__ == "__main__":
    unittest.main()
