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
