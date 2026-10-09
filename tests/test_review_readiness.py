import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apply_annotations import build_application_plan, render_application_plan
from apply_annotations import materialize_valid_decisions, validate_decisions
from evaluate_corpus import evaluate_corpus
from evaluate_workflow import evaluate
from expression_document import find_ast_occurrences, parse_papers
from notation_catalog import build_expression_config
from expression_document import _source_location
from notation_catalog import annotation_candidates, build_catalog
from review_workflow import build_review_decision
from smglom_notation_extractor import scan_available_smglom


class TestReviewReadiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest_path = ROOT / "evaluation" / "corpus.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.aggregate, _, _, cls.errors, _ = evaluate_corpus(cls.manifest_path)
        cls.audit = cls.errors["annotation_coverage_audit"]
        cls.records = scan_available_smglom(quiet=True)

    def test_readiness_levels_and_human_effort_are_separate(self):
        coverage = self.aggregate["region_annotation_coverage"]
        self.assertEqual(coverage["supported_annotation_eligible_regions"], 99)
        self.assertEqual(coverage["review_ready_regions"], 99)
        self.assertEqual(coverage["automatically_ready_regions"], 88)
        self.assertEqual(coverage["human_selection_required_regions"], 11)
        self.assertEqual(coverage["unsupported_annotation_eligible_regions"], 8)
        self.assertEqual(coverage["supported_review_coverage_percent"], 100.0)
        effort = coverage["reviewer_effort"]
        self.assertEqual(sum(effort["human_selection_candidate_count_distribution"].values()), 11)
        self.assertGreater(effort["average_candidates_per_human_selection_region"], 1)

    def test_all_human_selection_regions_validate_and_render(self):
        base = self.manifest_path.parent
        gaps = [gap for gap in self.audit["gaps"]
                if gap["annotation_should_be_possible"] and gap["semantic_candidates"]]
        self.assertEqual(len(gaps), 11)
        decisions_by_document = {}

        for document in self.manifest["documents"]:
            target = (base / document["path"]).resolve()
            config_path = (base / document.get(
                "config", "../expression-config.example.json")).resolve()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            gold_path = (base / document["gold"]).resolve()
            _, occurrences, _ = evaluate(target, config_path, gold_path)
            document_gaps = [gap for gap in gaps if gap["document"] == document["id"]]
            decisions = []
            for gap in document_gaps:
                occurrence = next(item for item in occurrences
                                  if item["file"] == gap["file"]
                                  and item["line"] == gap["line"]
                                  and item["source_expression"] == gap["full_expression"])
                option = gap["review_options"][0]
                # This is an explicit controlled reviewer choice.  The test does
                # not assert that the first candidate is mathematically correct.
                selected = next(item for item in occurrence["ranked_candidates"]
                                if item["start"] == option["start"]
                                and item["end"] == option["end"]
                                and item["semantic_macro"] == option["candidate_macros"][0])
                source_file = target if target.is_file() else target / occurrence["file"]
                source = source_file.read_text(encoding="utf-8")
                start = occurrence["source_start"] + selected["start"]
                end = occurrence["source_start"] + selected["end"]
                line, column, full_text = _source_location(source, start)
                match = {
                    "file": source_file, "line": line, "column": column, "text": full_text,
                    "pattern_type": selected["pattern_type"],
                    "matched_expression": selected["matched_expression"],
                    "matched_visible_notation": selected["matched_visible_notation"],
                    "arguments": selected["arguments"],
                    "source_start": start, "source_end": end,
                    "source_expression": source[start:end],
                }
                classification = dict(selected["classification"], expression_config=config)
                decision = build_review_decision(match, selected, classification, target)
                self.assertIsNotNone(decision, gap["full_expression"])
                self.assertTrue({"file", "start", "end",
                                 "semantic_macro", "arguments", "argument_roles", "source_hash",
                                 "decision_id", "configuration_source",
                                 "candidate_classification", "adapter_family",
                                 } <= decision.keys())
                decisions.append(decision)

            validation = validate_decisions(decisions, self.records, target)
            self.assertEqual(len(validation["valid"]), len(decisions),
                             (validation["stale"], validation["unverifiable"]))
            self.assertFalse(validation["stale"])
            self.assertFalse(validation["unverifiable"])
            current = materialize_valid_decisions(validation)
            decisions_by_document[document["id"]] = current

            grouped = {}
            for decision in current:
                grouped.setdefault(decision["file"], []).append(decision)
            rendered_count = 0
            for relative, file_decisions in grouped.items():
                source_file = target if target.is_file() else target / relative
                source = source_file.read_text(encoding="utf-8")
                plan = build_application_plan(source, file_decisions, relative)
                self.assertFalse(plan["stale"])
                self.assertFalse(plan["same_span_conflicts"])
                self.assertFalse(plan["partial_overlap_conflicts"])
                _, _, failures, count = render_application_plan(source, plan)
                self.assertFalse(failures)
                rendered_count += count
            self.assertEqual(rendered_count, len(current))

        self.assertEqual(sum(map(len, decisions_by_document.values())), 11)

    def test_evaluator_review_and_validator_share_nested_adapter_scope(self):
        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8"))
        _, occurrences, _ = evaluate()
        styled = next(item for item in occurrences
                      if item["source_expression"] == r"\boldsymbol{x^2}")
        candidate = next(item for item in styled["ranked_candidates"]
                         if item["semantic_macro"] == "comppower")
        compiled = build_expression_config(config, self.records)
        review_matches = find_ast_occurrences(
            parse_papers(ROOT / "evaluation" / "frozen_papers", compiled),
            candidate["classification"], compiled, candidate)
        reviewed = next(item for item in review_matches
                        if item["source_start"] == candidate["source_start"])
        self.assertEqual((reviewed["source_start"], reviewed["source_end"],
                          reviewed["source_expression"]),
                         (candidate["source_start"], candidate["source_end"], "x^2"))
        self.assertEqual(reviewed["ast_node"]["kind"], "ScriptedExpression")
        self.assertEqual(reviewed["semantic_macro"], "comppower")
        self.assertEqual(reviewed["adapter_family"], "ScriptedTemplateAdapter")

    def test_annotation_ready_adapter_families_round_trip_through_validator(self):
        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8"))
        config["commands"].update({r"\mathbb": {"arity": 1}, r"\underset": {"arity": 2}})
        entries = annotation_candidates(build_catalog(self.records, config))
        cases = (
            ("aplus", None, "a+b"),
            ("power", "ScriptedTemplateAdapter", "x^2"),
            ("argmin", "MultiPartAdapter", r"\mathrm{argmin} x"),
            ("floor", "DelimiterAdapter", "[x]"),
            ("conj", "SequencePatternAdapter", r"a\mathrm{and}b"),
            ("ComplexNumbers", "DecoratedSymbolAdapter", r"\mathbb{C}"),
            ("argminover", "BinderAdapter", r"\underset{x}{\mathrm{argmin}} f(x)"),
        )
        compiled = build_expression_config(config, self.records)
        with tempfile.TemporaryDirectory() as directory:
            papers = Path(directory)
            decisions = []
            for index, (macro, adapter, source) in enumerate(cases):
                matching = [item for item in entries if item.get("macro") == macro
                            and (adapter is None or item["classification"].get(
                                "grammar_adapter") == adapter)]
                self.assertTrue(matching, (macro, adapter))
                candidate = matching[0]
                paper = papers / f"case{index}.tex"
                paper.write_text("$" + source + "$", encoding="utf-8")
                matches = find_ast_occurrences(
                    parse_papers(paper, compiled), candidate["classification"],
                    compiled, candidate)
                self.assertTrue(matches, (macro, source))
                match = matches[0]
                classification = dict(candidate["classification"], expression_config=config)
                decision = build_review_decision(match, candidate, classification, papers)
                self.assertIsNotNone(decision, (macro, source))
                decisions.append(decision)
            validation = validate_decisions(decisions, self.records, papers)
            self.assertEqual(len(validation["valid"]), len(cases),
                             (validation["stale"], validation["unverifiable"]))


if __name__ == "__main__":
    unittest.main()
