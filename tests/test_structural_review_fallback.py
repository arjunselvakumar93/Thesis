import io
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import review_workflow
import apply_annotations
from expression_document import find_structural_occurrences, parse_papers
from notation_catalog import build_expression_config
from review_scope import build_review_scopes, render_review_scope


class TestStructuralReviewFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paper = ROOT / "tests" / "fixtures" / "structural_fallback_cases.tex"
        cls.raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        cls.config = build_expression_config(cls.raw, [])
        cls.regions = parse_papers(cls.paper, cls.config)

    def scopes(self, query):
        matches = find_structural_occurrences(self.regions, query, self.config)
        return build_review_scopes(matches, (), include_structural=True)

    def test_symbol_and_scripted_symbol_are_structural_only(self):
        symbol = next(scope for scope in self.scopes(r"\omega")
                      if scope.full_math_region.strip() == r"\omega")
        self.assertEqual((symbol.matched_source, symbol.simple_structure),
                         (r"\omega", "symbol"))
        self.assertEqual(symbol.highlighted, r"[\omega]")
        self.assertTrue(symbol.structurally_ready)
        self.assertFalse(symbol.semantic_mapping_available)
        self.assertFalse(symbol.review_ready)
        script = next(scope for scope in self.scopes(r"\omega_i")
                      if scope.full_math_region.strip() == r"\omega_i")
        self.assertEqual(script.simple_structure, "scripted expression")
        self.assertEqual(script.arguments, (r"\omega", "i"))
        rendered = render_review_scope(script)
        self.assertIn("base: \\omega", rendered)
        self.assertIn("subscript: i", rendered)

    def test_relation_function_sum_opaque_and_raw_product_are_discoverable(self):
        cases = {
            r"\omega_i\in A": "membership relation",
            r"P(\omega_i)": "function application",
            r"\sum": "summation",
            r"\foo{x}": "opaque command application",
            "*": "multiplication",
        }
        for query, structure in cases.items():
            with self.subTest(query=query):
                scopes = self.scopes(query)
                self.assertTrue(scopes)
                self.assertIn(structure, {scope.simple_structure for scope in scopes})
                self.assertTrue(all(not scope.semantic_candidates for scope in scopes))

    def test_opaque_arguments_and_exact_highlights_are_preserved(self):
        scope = self.scopes(r"\foo{x}")[0]
        self.assertEqual(scope.arguments, ("x",))
        self.assertEqual(scope.argument_roles, ("arg1",))
        self.assertIn(r"[\foo{x}]+y", scope.highlighted)
        rendered = render_review_scope(scope)
        self.assertIn("Annotation status:\nNot annotation-ready.", rendered)

    def test_cli_omega_uses_structural_fallback_instead_of_unmatched_message(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["review_workflow.py", "--paper", str(self.paper),
                                        "--preview"]), \
             patch("builtins.input", return_value=r"\omega"), redirect_stdout(output):
            review_workflow.main()
        text = output.getvalue()
        self.assertIn("Found ", text)
        self.assertIn("structural review scope", text)
        self.assertIn("Suggested scope:\n\\omega", text)
        self.assertNotIn("No matching notation was found", text)
        self.assertNotIn("Semantic candidates:", text)

    def test_raw_star_is_structural_without_config_and_semantic_with_test_config(self):
        output = io.StringIO()
        with redirect_stdout(output):
            count = review_workflow.run_structural_preview(
                "*", self.paper, self.raw, self.regions)
        self.assertEqual(count, 1)
        self.assertIn("Structure:\nmultiplication", output.getvalue())
        self.assertIn("Suggested scope:\np(x) * q(x)", output.getvalue())
        self.assertNotIn(r"\fooTimes", output.getvalue())
        test_config_path = ROOT / "tests" / "fixtures" / "configs/test_raw_star_semantics.json"
        test_config = build_expression_config(json.loads(test_config_path.read_text(encoding="utf-8")), [])
        candidates = review_workflow.find_expression_candidates("*", test_config, [])
        self.assertEqual({candidate["macro"] for candidate in candidates}, {"fooTimes"})

    def test_existing_semantic_review_keeps_priority(self):
        candidate = {"macro": "realCandidate", "classification": {
            "pattern_type": "sequence", "separator": "+"}}
        with redirect_stdout(io.StringIO()), patch.object(sys, "argv", ["review_workflow.py", "--preview"]), \
             patch("builtins.input", return_value="+"), \
             patch.object(review_workflow, "find_generic_candidates", return_value=[candidate]), \
             patch.object(review_workflow, "run_generic_review") as semantic, \
             patch.object(review_workflow, "run_structural_preview") as structural:
            review_workflow.main()
        semantic.assert_called_once()
        structural.assert_not_called()


class TestUnresolvedMeaningWorkflow(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.papers = self.root / "papers"
        self.papers.mkdir()
        self.paper = self.papers / "paper.tex"
        self.decisions = self.root / "decisions" / "review_results.json"
        self.output = self.root / "output"
        self.config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        patches = ExitStack()
        self.addCleanup(patches.close)
        for module, name, value in (
                (review_workflow, "DECISIONS_DIR", self.decisions.parent),
                (review_workflow, "DECISION_FILE", self.decisions),
                (apply_annotations, "DECISION_FILE", self.decisions),
                (apply_annotations, "PAPERS_DIR", self.papers),
                (apply_annotations, "OUTPUT_DIR", self.output)):
            patches.enter_context(patch.object(module, name, value))
        patches.enter_context(patch.object(review_workflow, "scan_available_smglom", return_value=[]))
        patches.enter_context(patch.object(apply_annotations, "scan_available_smglom", return_value=[]))

    def cli(self, query, *answers):
        output = io.StringIO()
        with patch.object(sys, "argv", ["review_workflow.py", "--paper", str(self.paper)]), \
             patch("builtins.input", side_effect=[query, *answers]), redirect_stdout(output):
            review_workflow.main()
        return output.getvalue()

    def stored(self):
        return json.loads(self.decisions.read_text(encoding="utf-8"))

    def test_cli_user_meaning_has_provenance_and_never_becomes_an_annotation(self):
        original = r"Before $\unseenOperator{x}$ after."
        self.paper.write_text(original, encoding="utf-8")
        self.cli(r"\unseenOperator{x}", "m", "A meaning supplied by the reviewer")
        record, = self.stored()
        self.assertEqual(record["decision"], "unresolved")
        self.assertEqual(record["semantic_status"], "user_provided_meaning")
        self.assertEqual(record["semantic_meaning"], "A meaning supplied by the reviewer")
        self.assertEqual(record["meaning_provenance"]["source"], "human review")
        self.assertIn("recorded_at", record["meaning_provenance"])
        self.assertIsNone(record["semantic_macro"])
        self.assertIsNone(record["semantic_suggestion"])
        self.assertFalse(record["annotation_ready"])
        fragment = original[record["start"]:record["end"]]
        self.assertEqual(fragment, r"\unseenOperator{x}")
        self.assertEqual(record["source_hash"], hashlib.sha256(fragment.encode("utf-8")).hexdigest())
        validation = apply_annotations.validate_decisions(self.stored(), [], self.papers)
        self.assertFalse(validation["valid"])
        self.assertIn("no executable semantic definition", validation["unverifiable"][0][2])
        self.assertEqual(apply_annotations.load_accepted_decisions(), [])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(apply_annotations.main([]), 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.paper.read_text(encoding="utf-8"), original)

    def test_exact_atoms_and_numbers_can_be_left_unresolved_and_rereviewed(self):
        self.paper.write_text("$r+7$", encoding="utf-8")
        self.cli("r", "u")
        self.cli("7", "u")
        self.assertEqual(len(self.stored()), 2)
        self.assertTrue(all(item["semantic_meaning"] is None for item in self.stored()))
        self.cli("r", "m", "A locally chosen parameter")
        self.assertEqual(len(self.stored()), 2)
        record = next(item for item in self.stored() if item["visible_symbol"] == "r")
        self.assertEqual(record["semantic_meaning"], "A locally chosen parameter")

    def test_offered_candidates_can_be_declined_without_selecting_a_macro(self):
        self.paper.write_text("$a*b$", encoding="utf-8")
        config = json.loads((ROOT / "tests/fixtures/configs/test_raw_star_semantics.json").read_text(encoding="utf-8"))
        config = build_expression_config(config, [])
        candidates = review_workflow.find_expression_candidates("*", config, [])
        for action in ("u", "m"):
            with self.subTest(action=action), redirect_stdout(io.StringIO()), \
                 patch("builtins.input", side_effect=[action, "My intended meaning"]), \
                 patch.object(review_workflow, "ask_for_decision") as accept:
                self.assertEqual(review_workflow.run_generic_review(
                    "*", candidates, self.papers, config), 1)
                accept.assert_not_called()
                record, = self.stored()
                self.assertEqual(record["decision"], "unresolved")
                self.assertIsNone(record["semantic_macro"])

    def test_preview_and_unsupported_syntax_do_not_request_or_store_meanings(self):
        self.paper.write_text(r"$\unseen$", encoding="utf-8")
        with redirect_stdout(io.StringIO()), patch("builtins.input") as ask:
            self.assertEqual(review_workflow.run_generic_review(
                r"\unseen", [], self.papers, self.config, preview=True), 1)
            ask.assert_not_called()
        self.assertFalse(self.decisions.exists())
        self.paper.write_text(r"$\unseen{$", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input") as ask:
            self.assertEqual(review_workflow.run_structural_review(
                r"\unseen", self.papers, self.config), 0)
            ask.assert_not_called()
        self.assertIn("Unparsed math region", output.getvalue())
        self.assertFalse(self.decisions.exists())

    def test_nonmatching_catalogue_candidates_still_allow_structural_review(self):
        self.paper.write_text(r"$\unseen{x}$", encoding="utf-8")
        config = json.loads((ROOT / "tests/fixtures/configs/test_raw_star_semantics.json").read_text(encoding="utf-8"))
        config = build_expression_config(config, [])
        candidates = review_workflow.find_expression_candidates("*", config, [])
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=["u"]):
            self.assertEqual(review_workflow.run_generic_review(
                r"\unseen", candidates, self.papers, config), 1)
        record, = self.stored()
        self.assertEqual(record["matched_expression"], r"\unseen{x}")
        self.assertEqual(record["decision"], "unresolved")

    def test_later_configured_mapping_requires_acceptance_and_safe_source(self):
        original = "$a*b$"
        self.paper.write_text(original, encoding="utf-8")
        self.cli("*", "m", "A local operation, pending a definition")
        # Reuse an existing explicitly synthetic user-configuration fixture.
        # This supplies no invented SMGloM catalogue record.
        config = json.loads((ROOT / "tests/fixtures/configs/test_raw_star_semantics.json").read_text(encoding="utf-8"))
        config = build_expression_config(config, [])
        candidates = review_workflow.find_expression_candidates("*", config, [])
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=["1", "a"]):
            self.assertEqual(review_workflow.run_generic_review(
                "*", candidates, self.papers, config), 1)
        accepted, = self.stored()
        self.assertEqual(accepted["decision"], "accepted")
        self.assertIn("synthetic test configuration", accepted["configuration_source"])
        validation = apply_annotations.validate_decisions(self.stored(), [], self.papers)
        self.assertEqual(len(validation["valid"]), 1)
        apply_annotations.prepare_output_folder()
        current = apply_annotations.materialize_valid_decisions(validation)
        self.assertEqual(apply_annotations.apply_annotations(current), 1)
        self.assertEqual((self.output / "paper.tex").read_text(encoding="utf-8"), r"$\fooTimes{a}{b}$")
        self.assertEqual(self.paper.read_text(encoding="utf-8"), original)
        self.paper.write_text("$a*c$", encoding="utf-8")
        self.assertEqual(len(apply_annotations.validate_decisions(self.stored(), [], self.papers)["stale"]), 1)


if __name__ == "__main__":
    unittest.main()
