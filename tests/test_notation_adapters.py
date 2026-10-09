import sys
import tempfile
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from notation_catalog import template_parts
from expression_annotation import match_ast
from expression_parser import ExpressionParser
from expression_parser import NotationRegistry
from notation_catalog import (adapter_examples, annotation_candidates, build_catalog,
                              catalog_statistics, adapter_summary)
from ast_test_helpers import scan_rule as find_notation_occurrences
from review_workflow import generate_classified_suggestion
from smglom_notation_extractor import scan_available_smglom


class TestGenericNotationAdapters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = scan_available_smglom(quiet=True)
        cls.entries = build_catalog(cls.records)

    def by_adapter(self, name):
        return [entry for entry in self.entries
                if entry["classification"].get("grammar_adapter") == name]

    def test_multi_part_adapter_handles_unrelated_real_records(self):
        entries = self.by_adapter("MultiPartAdapter")
        self.assertGreaterEqual(len({entry["module"] for entry in entries}), 3)
        self.assertTrue({"argmin", "ceiling", "realpart"} <= {entry["macro"] for entry in entries})
        self.assertTrue(all(entry["classification"]["grammar_interpretation"]["parts"]
                            for entry in entries))

    def test_decorated_and_scripted_adapters_each_cover_multiple_records(self):
        decorated = self.by_adapter("DecoratedSymbolAdapter")
        scripted = self.by_adapter("ScriptedTemplateAdapter")
        self.assertTrue({"ComplexNumbers", "ExtendedReals", "IntegerNumbers"}
                        <= {entry["macro"] for entry in decorated})
        self.assertTrue({"power", "arithmean", "geomean"}
                        <= {entry["macro"] for entry in scripted})
        self.assertTrue(all(entry["classification"]["grammar_interpretation"]["node_kind"]
                            == "ScriptedExpression" for entry in scripted))

    def test_mixfix_and_delimiter_adapters_are_pattern_driven(self):
        self.assertGreaterEqual(len(self.by_adapter("MixfixAdapter")), 3)
        self.assertGreaterEqual(len(self.by_adapter("DelimiterAdapter")), 3)
        self.assertEqual(template_parts(r"\left(#1\right)^{#2}"), [
            {"kind": "fixed", "text": r"\left("}, {"kind": "argument", "slot": 1},
            {"kind": "fixed", "text": r"\right)^{"}, {"kind": "argument", "slot": 2},
            {"kind": "fixed", "text": "}"},
        ])

    def test_pattern_adapter_preserves_reordered_and_repeated_slots(self):
        multi = self.by_adapter("MultiPartAdapter")
        scripted = self.by_adapter("ScriptedTemplateAdapter")
        self.assertIn("imply", {entry["macro"] for entry in multi})
        self.assertIn("infsequi", {entry["macro"] for entry in scripted})

    def test_empty_argsep_is_generic_implicit_sequence(self):
        records = [entry for entry in self.entries
                   if entry.get("notation") == r"\argsep{#1}{}"]
        self.assertEqual(len(records), 6)
        self.assertTrue(all(entry["classification"].get("implicit") for entry in records))
        self.assertTrue(all(entry["classification"].get("annotation_ready") for entry in records))

        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        registry = NotationRegistry.from_config(config)
        parser = ExpressionParser(registry=registry)
        rule = records[0]["classification"]
        for source, expected in (("ab", ["a", "b"]), ("2x", ["2", "x"]),
                                 (r"2\alpha", ["2", r"\alpha"]),
                                 ("x(y+1)", ["x", "(y+1)"]),
                                 ("(a+b)(c-d)", ["(a+b)", "(c-d)"])):
            matches = match_ast(parser.parse(source), rule, registry)
            self.assertEqual(matches[-1]["arguments"], expected)
        addition = parser.parse("a+bc")
        self.assertEqual(addition.value, "+")
        self.assertEqual(addition.children[1].value, "juxtaposition")

    def test_generic_constant_wrapped_sequence_and_binder_are_ready(self):
        constants = self.by_adapter("SemanticConstantAdapter")
        self.assertEqual(len(constants), 5)
        self.assertTrue({"infinity", "pinumber", "discriminant", "trueval", "falseval"}
                        <= {entry["macro"] for entry in constants})
        self.assertEqual(len(self.by_adapter("SequencePatternAdapter")), 2)
        binders = self.by_adapter("BinderAdapter")
        self.assertEqual(len(binders), 33)
        self.assertEqual(sum(entry["classification"].get("annotation_ready", False)
                             for entry in binders), 13)
        self.assertTrue(all(entry["classification"].get("annotation_blocker")
                            == "missing_argument_mapping"
                            for entry in binders
                            if not entry["classification"].get("annotation_ready")))

    def test_two_binder_families_use_one_user_metadata_adapter(self):
        configuration = {"grammar_adapters": [
            {"semantic_macro": "argminover", "grammar_kind": "binder",
             "roles": {"bound_variable": 1, "body": 2}},
            {"semantic_macro": "SumOver", "grammar_kind": "binder",
             "roles": {"bound_variable": 2, "body": 3}},
        ]}
        entries = build_catalog(self.records, configuration)
        selected = [entry for entry in entries if entry["macro"] in {"argminover", "SumOver"}]
        self.assertEqual({entry["classification"].get("grammar_adapter") for entry in selected},
                         {"BinderAdapter"})
        self.assertTrue(all(entry["classification"]["grammar_supported"] for entry in selected))
        self.assertTrue(all(entry["classification"]["annotation_ready"] for entry in selected))
        self.assertEqual({item["macro"] for item in annotation_candidates(selected)},
                         {"argminover", "SumOver"})

    def test_incomplete_binder_override_stays_unsupported(self):
        configuration = {"grammar_adapters": [{"semantic_macro": "SumOver",
            "grammar_kind": "binder", "roles": {"body": 3}}]}
        entry = next(item for item in build_catalog(self.records, configuration)
                     if item["macro"] == "SumOver")
        self.assertFalse(entry["classification"]["grammar_supported"])
        self.assertIn("bound_variable", entry["classification"]["unsupported_reason"])

    def test_catalogue_exposes_status_and_representative_examples(self):
        statistics = catalog_statistics(self.entries)
        self.assertGreater(statistics["grammar_supported"], 200)
        self.assertGreater(statistics["annotation_ready_entries"], 200)
        examples = adapter_examples(self.entries)
        for adapter in ("MultiPartAdapter", "ScriptedTemplateAdapter", "DelimiterAdapter",
                        "MixfixAdapter", "DecoratedSymbolAdapter"):
            self.assertGreaterEqual(len(examples[adapter]), 3)
        summary = adapter_summary(self.entries)
        self.assertIn("no_deterministic_source_pattern", summary["blockers"])

    def test_real_adapter_records_complete_annotation_path(self):
        cases = [
            ("argmin", r"\maincomp{\mathrm{argmin}}\;#1", r"\mathrm{argmin} x", ["x"]),
            ("argmax", None, r"\mathrm{argmax} y", ["y"]),
            ("aroot", r"\sqrt[#1]{#2}", r"\sqrt[3]{x}", ["3", "x"]),
            ("power", r"{#1}^{#2}", "x^2", ["x", "2"]),
            ("arithmean", None, "A_i", ["i"]),
            ("seqsel", None, "x_i", ["x", "i"]),
            ("absolutevalue", None, r"\left|x+y\right|", ["x+y"]),
            ("floor", r"[#1]", "[x]", ["x"]),
            ("ComplexNumbers", None, r"\mathbb{C}", []),
            ("trueval", r"\comp{\mathsf{T}}", r"\mathsf{T}", []),
            ("power", r"\left(#1\right)^{#2}", r"\left(x\right)^2", ["x", "2"]),
            ("dedecut", None, "(a,b)", ["a", "b"]),
            ("pinumber", None, r"\pi", []),
            ("infinity", None, r"\infty", []),
            ("conj", None, r"a\mathrm{and}b", ["a", "b"]),
            ("disj", None, r"a\mathrm{or}b", ["a", "b"]),
            ("argminover", None, r"\underset{x}{\mathrm{argmin}} f(x)", ["x", "f(x)"]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number, (macro, notation, source, arguments) in enumerate(cases):
                candidates = [entry for entry in self.entries
                              if entry.get("macro") == macro
                              and entry["classification"].get("grammar_adapter")
                              and (notation is None or entry.get("notation") == notation)]
                entry = candidates[0]
                paper = root / f"case{number}.tex"
                paper.write_text("$" + source + "$", encoding="utf-8")
                matches = [item for item in find_notation_occurrences(
                    entry["classification"], root) if item["file"] == paper]
                with self.subTest(macro=macro, source=source):
                    if source == r"\left|x+y\right|":
                        # The presentation template is known, but this sized
                        # delimiter is outside the supported AST grammar.
                        self.assertEqual(matches, [])
                        continue
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(matches[0]["arguments"], arguments)
                    candidate = {key: entry.get(key) for key in (
                        "macro", "name", "args", "module", "repository", "record")}
                    candidate["classification"] = entry["classification"]
                    expected = ("\\" + macro + "{" + ",".join(arguments) + "}"
                                if entry["classification"].get("pattern_type") == "sequence"
                                else "\\" + macro + "".join("{" + arg + "}" for arg in arguments))
                    self.assertEqual(generate_classified_suggestion(matches[0], candidate), expected)


if __name__ == "__main__":
    unittest.main()
