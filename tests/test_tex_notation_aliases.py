import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))


import review_workflow
from notation_scanner import find_notation_occurrences
from smglom_notation_extractor import SMGLOM_REPOSITORIES, scan_repository
from tex_notation_aliases import (
    canonicalize_tex_notation,
    get_equivalent_tex_notations,
)
from notation_catalog import build_catalog, search_catalog


class TestTexNotationAliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = scan_repository("mv", SMGLOM_REPOSITORIES["mv"])
        cls.canonical_candidate = next(
            candidate
            for candidate in review_workflow.find_generic_candidates(r"\ne", cls.records)
            if candidate["macro"] == "notequal"
        )

    def test_alias_api_is_bidirectional_and_semantics_free(self):
        self.assertEqual(canonicalize_tex_notation(r"\ne"), r"\ne")
        self.assertEqual(canonicalize_tex_notation(r"\neq"), r"\ne")
        self.assertEqual(get_equivalent_tex_notations(r"\ne"), [r"\ne", r"\neq"])
        self.assertEqual(get_equivalent_tex_notations(r"\neq"), [r"\ne", r"\neq"])
        self.assertEqual(get_equivalent_tex_notations("+"), ["+"])

    def test_project_alias_is_generic_bidirectional_and_opt_in(self):
        aliases = {"<custom-op>": r"\times"}
        self.assertEqual(get_equivalent_tex_notations("<custom-op>"), ["<custom-op>"])
        self.assertEqual(set(get_equivalent_tex_notations("<custom-op>", aliases)),
                         {"<custom-op>", r"\times"})
        self.assertEqual(canonicalize_tex_notation("<custom-op>", aliases), r"\times")
        self.assertNotIn("semantic", json.dumps(aliases))

    def test_configured_star_alias_resolves_a_real_smglom_record(self):
        records = scan_repository("arithmetics", SMGLOM_REPOSITORIES["arithmetics"])
        default = build_catalog(records)
        configured = build_catalog(records, {"notation_aliases": {"*": r"\times"}})
        self.assertFalse(any(item.get("macro") and "*" in item.get("variants", ())
                             for item in default))
        matches = [item for item in search_catalog(configured, "*")
                   if item.get("macro") and item.get("visible") == r"\times"]
        self.assertTrue(matches)
        self.assertTrue(all(item["source"] == "SMGloM" and item["record"] is not None
                            for item in matches))

    def test_lookup_from_both_spellings_resolves_the_same_real_record(self):
        canonical = next(
            item for item in review_workflow.find_generic_candidates(r"\ne", self.records)
            if item["macro"] == "notequal"
        )
        alias = next(
            item for item in review_workflow.find_generic_candidates(r"\neq", self.records)
            if item["macro"] == "notequal"
        )
        self.assertIs(canonical["record"], alias["record"])
        self.assertEqual(canonical["classification"], alias["classification"])
        self.assertEqual(canonical["classification"]["operator"], r"\ne")

    def test_scanner_matches_both_spellings_and_preserves_source_notation(self):
        source = (
            r"$a \ne 0$" + "\n"
            r"$a \neq 0$" + "\n"
            r"$A \neq b$" + "\n"
            r"$x^2 \neq y^2$" + "\n"
            r"$\frac{a}{b} \neq 0$" + "\n"
            r"$a+b \neq c-d$" + "\n"
            r"$a \neq$ and $\neq b$" + "\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text(source, encoding="utf-8")
            matches = find_notation_occurrences(
                self.canonical_candidate["classification"], directory
            )

        self.assertEqual(len(matches), 6)
        self.assertEqual(
            [match["matched_visible_notation"] for match in matches],
            [r"\ne", r"\neq", r"\neq", r"\neq", r"\neq", r"\neq"],
        )
        self.assertEqual(
            [match["arguments"] for match in matches],
            [
                ["a", "0"], ["a", "0"], ["A", "b"],
                ["x^2", "y^2"], [r"\frac{a}{b}", "0"],
                ["a+b", "c-d"],
            ],
        )
        self.assertEqual(matches[1]["matched_expression"], r"a \neq 0")
        self.assertEqual(
            len({(match["line"], match["column"], match["matched_expression"])
                 for match in matches}),
            len(matches),
        )

    def test_review_from_alias_stores_actual_spelling_and_semantic_macro(self):
        source = r"$a \neq 0$" + "\n"
        candidate = next(
            item for item in review_workflow.find_generic_candidates(r"\neq", self.records)
            if item["macro"] == "notequal"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.tex"
            paper.write_text(source, encoding="utf-8")
            decision_file = root / "decisions" / "review_results.json"
            with patch.object(review_workflow, "DECISIONS_DIR", decision_file.parent), \
                 patch.object(review_workflow, "DECISION_FILE", decision_file), \
                 patch.object(review_workflow, "choose_candidate", return_value=candidate), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"):
                self.assertEqual(
                    review_workflow.run_generic_review(r"\neq", [candidate], root), 1
                )

            stored = json.loads(decision_file.read_text(encoding="utf-8"))

        self.assertEqual(stored[0]["visible_symbol"], r"\neq")
        self.assertEqual(stored[0]["semantic_macro"], "notequal")
        self.assertEqual(stored[0]["arguments"], ["a", "0"])
        self.assertEqual(stored[0]["matched_expression"], r"a \neq 0")
        self.assertEqual(stored[0]["semantic_suggestion"], r"\notequal{a}{0}")


if __name__ == "__main__":
    unittest.main()
