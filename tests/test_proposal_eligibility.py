import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expression_document import find_ast_occurrences, parse_math_regions
from notation_catalog import build_expression_config
from expression_parser import NotationRegistry
from expression_annotation import proposal_eligibility, assess_proposal_candidates


class TestProposalEligibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8"))
        cls.config = build_expression_config(raw)
        cls.registry = NotationRegistry.from_config(cls.config)
        cls.plus = {"pattern_type": "sequence", "separator": "+", "fixity": "infix",
                    "arity": "variable"}

    def test_already_semantic_subtree_is_not_proposed_again(self):
        regions = parse_math_regions(r"$\aplus{a+b,c}$", self.registry)
        self.assertEqual(find_ast_occurrences(regions, self.plus, self.config), [])

    def test_nested_semantic_content_is_suppressed_but_outer_raw_notation_remains(self):
        regions = parse_math_regions(r"$\aplus{a+b,c}+d$", self.registry)
        matches = find_ast_occurrences(regions, self.plus, self.config)
        self.assertEqual([match["matched_expression"] for match in matches],
                         [r"\aplus{a+b,c}+d"])
        self.assertEqual(matches[0]["proposal_eligibility"]["reason"],
                         "structurally_eligible")

    def test_invalid_scope_is_ineligible(self):
        region = parse_math_regions("$a+b$", self.registry)[0]
        result = proposal_eligibility(region.tree, -1, 2)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "invalid_scope")

    def test_structural_only_retrieval_is_retained_but_not_auto_proposed(self):
        ranked = [{"semantic_macro": "alpha", "rank": 1,
                   "evidence": [{"signal": "structural_compatibility", "weight": .5},
                                {"signal": "fallback_only", "weight": 0}]}]
        result = assess_proposal_candidates(ranked)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["automatic_candidates"], [])
        self.assertEqual(ranked[0]["semantic_macro"], "alpha")

    def test_tied_structural_candidates_are_ambiguous(self):
        ranked = [{"semantic_macro": macro, "rank": 1,
                   "evidence": [{"signal": "structural_compatibility", "weight": .5}]}
                  for macro in ("alpha", "beta")]
        self.assertEqual(assess_proposal_candidates(ranked)["status"],
                         "ambiguous_candidate_set")

    def test_context_can_resolve_context_sensitive_notation(self):
        ranked = [{"semantic_macro": "alpha", "rank": 1,
                   "evidence": [{"signal": "exact_visible_notation", "weight": 1},
                                {"signal": "module_imported_in_document", "weight": 3}]}]
        result = assess_proposal_candidates(ranked, context_sensitive=True)
        self.assertEqual(result["status"], "supported")
        self.assertEqual(result["automatic_candidates"], ranked)

    def test_configured_alias_is_explicit_proposal_evidence(self):
        ranked = [{"semantic_macro": "alpha", "rank": 1,
                   "evidence": [{"signal": "configured_notation_alias", "weight": .75}]}]
        self.assertEqual(assess_proposal_candidates(
            ranked, context_sensitive=True)["status"], "supported")


if __name__ == "__main__":
    unittest.main()
