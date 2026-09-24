import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import semantic_candidate_ranker as ranker


def candidate(macro, module, source="SMGloM"):
    return {"macro": macro, "name": "operation", "module": module,
            "repository": "repo", "source": source,
            "classification": {"pattern_type": "binary_infix", "operator": "+"}}


class TestSemanticCandidateRanker(unittest.TestCase):
    def setUp(self):
        self.raw = [candidate("one", "module-one"), candidate("two", "module-two")]
        self.occurrence = {"matched_visible_notation": "+", "pattern_type": "binary_infix",
                           "file": "paper.tex"}

    def test_deterministic_explained_neutral_fallback_preserves_raw_input(self):
        before = repr(self.raw)
        first = ranker.rank_candidates(self.raw, self.occurrence)
        second = ranker.rank_candidates(self.raw, self.occurrence)
        self.assertEqual(first, second)
        self.assertEqual(repr(self.raw), before)
        self.assertEqual(first[0]["score"], first[1]["score"])
        self.assertEqual(first[0]["rank"], first[1]["rank"])
        self.assertTrue(all(item["evidence"] for item in first))
        self.assertTrue(all(any(e["signal"] == "fallback_only" for e in item["evidence"])
                            for item in first))

    def test_module_context_changes_ranking(self):
        context = {"imports": [{"repository": "repo", "module": "module-two"}]}
        ranked = ranker.rank_candidates(self.raw, self.occurrence, context)
        self.assertEqual(ranked[0]["macro"], "two")
        self.assertIn("module_imported_in_document",
                      {item["signal"] for item in ranked[0]["evidence"]})

    def test_same_document_history_and_session_preferences_change_ranking(self):
        history = [{"decision": "accepted", "file": "paper.tex", "visible_symbol": "+",
                    "semantic_macro": "two", "semantic_module": "module-two"}]
        ranked = ranker.rank_candidates(self.raw, self.occurrence, accepted_history=history)
        self.assertEqual(ranked[0]["macro"], "two")
        session = {"preferred_module": "module-one"}
        ranked = ranker.rank_candidates(self.raw, self.occurrence, session=session)
        self.assertEqual(ranked[0]["macro"], "one")

    def test_duplicate_variants_collapse_and_preserve_provenance(self):
        raw = [candidate("one", "module-one", "notation A"),
               candidate("one", "module-one", "notation B")]
        ranked = ranker.rank_candidates(raw, self.occurrence)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["presentation_variants"], 2)
        self.assertEqual(ranked[0]["provenance"], ["notation A", "notation B"])

    def test_production_ranker_has_no_gold_dependency(self):
        source = inspect.getsource(ranker).lower()
        self.assertNotIn("gold_annotations", source)
        self.assertNotIn("evaluation/gold", source)


if __name__ == "__main__":
    unittest.main()
