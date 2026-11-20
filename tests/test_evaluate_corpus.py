import json
import hashlib
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluate_corpus import _display_verbose_failures, evaluate_corpus, main, wilson


class TestCorpusEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = ROOT / "evaluation" / "corpus.json"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.aggregate, cls.documents, cls.ambiguity, cls.errors, cls.breakdown = (
                evaluate_corpus(cls.manifest)
            )

    def test_portable_manifest_has_multiple_documents_and_enough_gold(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        enabled = [item for item in manifest["documents"] if item.get("enabled", True)]
        self.assertGreaterEqual(len(enabled), 3)
        self.assertTrue(all(not Path(item["path"]).is_absolute() for item in enabled))
        self.assertTrue(all(not Path(item["gold"]).is_absolute() for item in enabled))
        gold_size = sum(len(json.loads((self.manifest.parent / item["gold"])
                                      .resolve().read_text(encoding="utf-8")))
                        for item in enabled)
        self.assertGreaterEqual(gold_size, 50)

    def test_corpus_reports_micro_macro_breakdowns_and_partial_gold(self):
        self.assertEqual(self.aggregate["documents"], 3)
        self.assertEqual(self.aggregate["math_regions"], 141)
        self.assertEqual(self.aggregate["gold_occurrences"], 58)
        self.assertEqual(self.aggregate["parser_coverage"]["successes"], 141)
        self.assertIn("macro_average_percent", self.aggregate)
        self.assertIn("candidate_count", self.breakdown)
        self.assertIn("context_evidence", self.breakdown)
        self.assertIn("semantic_family", self.breakdown)
        self.assertTrue(any(document["argument_accuracy_percent"] is None
                            for document in self.documents))

    def test_semantic_candidate_misses_are_evidence_backed(self):
        summary = self.errors["semantic_candidate_summary"]
        self.assertEqual(summary["semantic_gold_cases"], 46)
        self.assertEqual(summary["proposal_eligible_cases"], 44)
        self.assertEqual(summary["candidate_present"], 44)
        self.assertEqual(summary["proposal_candidate_absent"], 0)
        misses = self.errors["semantic_candidate_misses"]
        self.assertEqual(len(misses), 0)
        self.assertTrue(all(miss["ast_subtree"] and miss["pipeline_stage"]
                            and miss["recommended_resolution"] for miss in misses))
        self.assertEqual(sum("E" in miss["failure_category"] for miss in misses), 0)
        self.assertEqual(len(self.errors["already_semantic_cases"]), 2)
        self.assertTrue(all(not miss["requires_code_change"] for miss in misses))
        self.assertEqual(self.errors["non_semantic_gold_summary"]["total"], 12)
        self.assertEqual(sum(self.errors["non_semantic_gold_summary"]["categories"].values()), 12)

    def test_already_semantic_cases_are_correctly_excluded_and_suppressed(self):
        semantic = self.aggregate["semantic_evaluation"]
        self.assertEqual(semantic["semantic_gold_cases"], 46)
        self.assertEqual(semantic["proposal_eligible_cases"], 44)
        self.assertEqual(semantic["already_semantic_cases"], 2)
        self.assertEqual(semantic["already_semantic_correctly_skipped"], 2)
        self.assertEqual(semantic["already_semantic_accuracy"]["percent"], 100.0)
        cases = self.errors["already_semantic_cases"]
        self.assertEqual({case["expression"] for case in cases},
                         {r"\notequal{a}{0}", r"\eq{x,y}"})

    def test_availability_generation_and_ranking_have_distinct_denominators(self):
        semantic = self.aggregate["semantic_evaluation"]
        self.assertEqual(semantic["mapping_available_cases"], 44)
        self.assertEqual(semantic["mapping_unavailable_cases"], 0)
        self.assertEqual(semantic["proposal_candidate_recall"]["total"], 44)
        self.assertEqual(semantic["supported_candidate_recall"]["total"], 44)
        self.assertEqual(semantic["supported_candidate_recall"]["successes"], 44)
        self.assertEqual(semantic["supported_only_ranking"]["top_1_recall"]["total"], 44)
        self.assertTrue(all(miss["high_level_classification"] ==
                            "semantic_mapping_unavailable"
                            for miss in self.errors["semantic_candidate_misses"]))

    def test_star_is_not_fabricated_as_a_times_alias(self):
        from notation_catalog import build_catalog
        from smglom_notation_extractor import scan_available_smglom
        with contextlib.redirect_stdout(io.StringIO()):
            default = build_catalog(scan_available_smglom())
        self.assertFalse(any(item.get("macro") and "*" in item.get("variants", ())
                             for item in default))

    def test_frozen_gold_snapshot_is_unchanged_and_records_old_intent(self):
        original = (ROOT / "evaluation" / "gold_annotations.json").read_bytes()
        frozen = (ROOT / "evaluation" / "gold_v1.json").read_bytes()
        self.assertEqual(hashlib.sha256(original).digest(), hashlib.sha256(frozen).digest())
        expanded = json.loads((ROOT / "evaluation" / "gold_general_v2.json")
                              .read_text(encoding="utf-8"))
        semantic_commands = [item for item in expanded
                             if item["source_expression"] in
                             {r"\notequal{a}{0}", r"\eq{x,y}"}]
        self.assertEqual(len(semantic_commands), 2)
        self.assertTrue(all(item["should_propose"] is True for item in semantic_commands))

    def test_false_proposals_are_diagnosed_without_gold_driven_suppression(self):
        report = self.errors["false_proposal_summary"]
        self.assertEqual(report["count"], 0)
        self.assertEqual(report["grouped_reasons"], {})
        self.assertEqual(self.aggregate["annotation_evaluation"]
                         ["eligible_annotation_ready_coverage"]["successes"], 44)

    def test_verbose_failure_output_contains_actionable_fields(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            _display_verbose_failures(self.errors)
        output = stream.getvalue()
        self.assertIn("FAILURE SUMMARY", output)
        self.assertNotIn("FALSE PROPOSAL", output)
        self.assertIn("Failure E: 0", output)
        self.assertIn("Failure J: 0", output)

    def test_wilson_interval_is_bounded_and_handles_empty_samples(self):
        self.assertIsNone(wilson(0, 0))
        interval = wilson(10, 10)
        self.assertEqual(interval["percent"], 100.0)
        self.assertGreaterEqual(interval["wilson_95_percent"][0], 0)
        self.assertLessEqual(interval["wilson_95_percent"][1], 100)

    def test_command_writes_all_machine_readable_reports(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["--corpus", str(self.manifest),
                                   "--output", directory]), 0)
            expected = {"corpus_summary.json", "per_document_metrics.json",
                        "ambiguity_report.json", "error_analysis.json",
                        "annotation_coverage_gaps.json"}
            self.assertEqual({path.name for path in Path(directory).glob("*.json")}, expected)
        self.assertIn("Documents:", output.getvalue())
        self.assertIn("SEMANTIC AVAILABILITY", output.getvalue())

    def test_every_non_ready_region_has_a_pipeline_explanation(self):
        audit = self.errors["annotation_coverage_audit"]
        summary, gaps = audit["summary"], audit["gaps"]
        self.assertEqual(summary["total_math_regions"], 141)
        # A bounded integral is no longer counted as a power-template match.
        self.assertEqual(summary["annotation_ready_regions"], 87)
        self.assertEqual(len(gaps), 141 - 87)
        self.assertEqual(sum(summary["gap_categories"].values()), len(gaps))
        self.assertEqual(len(summary["per_document"]), 3)
        required = {"document", "file", "line", "full_expression", "ast_summary",
                    "notation_nodes", "semantic_candidates", "candidate_provenance",
                    "blocker", "pipeline_stage", "annotation_should_be_possible",
                    "annotation_should_not_be_attempted"}
        self.assertTrue(all(required <= gap.keys() for gap in gaps))
        self.assertEqual(sum(gap["blocker"] == "already_semantic_correctly_skipped"
                             for gap in gaps), 2)
        self.assertTrue(any(gap["requires_repository"] == "sets" for gap in gaps))
        bounded_integral = next(gap for gap in gaps
                                if gap["full_expression"] == r"\int_0^1 f(x)\,dx")
        self.assertEqual(bounded_integral["semantic_candidates"], [])
        integral = next(gap for gap in gaps if gap["full_expression"] == r"\int f(x)\,dx")
        self.assertEqual(integral["argument_mapping"]["hidden_or_missing"],
                         ["lower", "upper"])

    def test_production_ranker_has_no_gold_or_evaluation_dependency(self):
        source = (ROOT / "src" / "expression_annotation.py").read_text(encoding="utf-8")
        self.assertNotIn("gold_annotations", source)
        self.assertNotIn("gold_v1", source)
        self.assertNotIn("evaluate_corpus", source)
        self.assertNotIn("evaluation_summary", source)


if __name__ == "__main__":
    unittest.main()
