import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluate_workflow import DEFAULT_EVALUATION_PAPERS, GOLD_FILE, _output_sanity, evaluate


class TestWorkflowEvaluation(unittest.TestCase):
    def test_manual_gold_set_is_real_and_not_parser_generated(self):
        gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(gold), 20)
        self.assertTrue(all(item.get("expected_root") and "semantic_status" in item for item in gold))
        self.assertTrue(any(item["semantic_status"] == "ambiguous" for item in gold))
        self.assertTrue(all((DEFAULT_EVALUATION_PAPERS / item["file"]).exists()
                            for item in gold))

    def test_real_evaluation_keeps_layers_separate(self):
        summary, occurrences, gold = evaluate()
        self.assertEqual(summary["math_region_extraction"]["total_extracted"], 70)
        self.assertEqual(summary["structural_parser"]["parsed"], 70)
        self.assertEqual(summary["gold"]["size"], 20)
        self.assertEqual(len(occurrences), 70)
        self.assertEqual(len(gold), 20)
        self.assertIn("semantic_candidate_coverage", summary)
        self.assertIn("annotation_ready_coverage", summary)
        self.assertIn("application", summary)
        self.assertIn("semantic_ranking", summary)
        self.assertIn("top_1_recall_percent", summary["semantic_ranking"])
        self.assertTrue(all("raw_candidates" in item and "ranked_candidates" in item
                            for item in occurrences))
        self.assertTrue(all(item["raw_candidates"] == item["semantic_candidates"]
                            for item in occurrences))
        self.assertEqual(len(summary["examples"]["successful"]), 5)
        self.assertEqual(len(summary["examples"]["ambiguous"]), 5)

    def test_output_sanity_detects_balanced_and_broken_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.tex"
            paper.write_text("$a+b$\n\\usemodule[smglom/arithmetics]{mod?arithmetics}\n",
                             encoding="utf-8")
            self.assertTrue(_output_sanity(root)["passed"])
            paper.write_text("$a+b\n{", encoding="utf-8")
            report = _output_sanity(root)
            self.assertFalse(report["passed"])
            self.assertTrue(report["unbalanced_brace_files"])
            self.assertTrue(report["unbalanced_math_delimiter_files"])

    def test_adapter_candidates_are_found_inside_structural_wrappers(self):
        _, occurrences, _ = evaluate()
        styled = next(item for item in occurrences
                      if item["source_expression"] == r"\boldsymbol{x^2}")
        power = [candidate for candidate in styled["semantic_candidates"]
                 if candidate["semantic_macro"] in
                 {"power", "comppower", "intpower", "ratpower", "realpower"}]
        self.assertTrue(power)
        self.assertTrue(all(candidate["matched_expression"] == "x^2" for candidate in power))
        self.assertTrue(all(styled["ast"]["start"] < candidate["start"]
                            and candidate["end"] < styled["ast"]["end"]
                            for candidate in power))


if __name__ == "__main__":
    unittest.main()
