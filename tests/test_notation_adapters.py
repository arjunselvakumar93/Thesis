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

    def test_adapter_scopes_stay_inside_rows_and_sequence_items(self):
        from expression_annotation import match_candidate
        from expression_document import parse_math_regions, _without_alignment_markers
        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        registry = NotationRegistry.from_config(config)
        candidate = next(entry for entry in annotation_candidates(self.entries)
                         if entry["macro"] == "defeq")
        for source in (r"\begin{align}a&:=b\\c&:=d\end{align}", "$a:=b,c:=d$"):
            with self.subTest(source=source):
                region, = parse_math_regions(source, registry)
                self.assertIsNotNone(region.tree, region.error)
                matches = match_candidate(region.tree, candidate, registry)
                self.assertEqual([[_without_alignment_markers(arg).strip() for arg in match["arguments"]]
                                  for match in matches], [["a", "b"], ["c", "d"]])
                for match in matches:
                    self.assertEqual(match["matched_expression"],
                                     source[region.start + match["start"]:region.start + match["end"]])
                    self.assertNotIn(r"\\", match["matched_expression"])
                    self.assertNotIn(",", match["matched_expression"])

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

    def test_exponentiation_and_root_semantic_matching(self):
        """Regression test for scripted_operator and template pattern support.

        Verifies that:
        - Exponentiation (x^2, 2^3) matches power semantic macro
        - Subscripts (x_i) do NOT match power
        - Nth-root (\sqrt[3]{x}) matches aroot semantic macro
        - Single-arg square root (\sqrt{x}) matches asqrt semantic macro
        """
        raw_config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        config = raw_config  # Already has proper structure
        registry = NotationRegistry.from_config(config)
        parser = ExpressionParser(registry=registry)
        candidates = annotation_candidates(self.entries)

        test_cases = [
            ("x^2", "power", "Exponentiation: base x, exponent 2"),
            ("2^3", "power", "Exponentiation: base 2, exponent 3"),
            ("a^{2}", "power", "Exponentiation with braces: base a, exponent {2}"),
            (r"\sqrt{x}", "asqrt", "Single-arg square root"),
            (r"\sqrt[3]{x}", "aroot", "Nth-root with explicit index"),
            (r"\sqrt[2]{x}", "aroot", "Square root with explicit index 2"),
        ]

        for expr, expected_macro, description in test_cases:
            with self.subTest(expr=expr, macro=expected_macro):
                ast = parser.parse(expr)
                expected_cands = [c for c in candidates if c.get("macro") == expected_macro]
                self.assertTrue(expected_cands, f"No candidates found for {expected_macro}")

                matches = []
                for cand in expected_cands:
                    m = match_ast(ast, cand.get("classification"), registry)
                    if m:
                        matches.extend(m)

                self.assertTrue(matches, f"Failed to match {expr} to {expected_macro}: {description}")
                self.assertEqual(matches[0].get("pattern_type"),
                                expected_cands[0].get("classification", {}).get("pattern_type"),
                                f"Pattern type mismatch for {expr}")

    def test_subscripts_do_not_match_exponentiation(self):
        """Verify that subscripts are not interpreted as exponentiation.

        This is a critical safety check to prevent x_i from being matched as x^i.
        """
        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        registry = NotationRegistry.from_config(config)
        parser = ExpressionParser(registry=registry)
        candidates = annotation_candidates(self.entries)

        # Parse subscript expression
        ast = parser.parse("x_i")

        # Get power candidates
        power_cands = [c for c in candidates if c.get("macro") == "power"]
        self.assertTrue(power_cands, "No power candidates found")

        # Verify it does NOT match power
        for cand in power_cands:
            matches = match_ast(ast, cand.get("classification"), registry)
            self.assertFalse(matches, f"Subscript x_i should NOT match power pattern, but got: {matches}")

    def test_template_handler_not_overly_broad(self):
        """Verify that the template handler doesn't match unrelated commands.

        This ensures that commands with the same argument count as templates
        don't get false positive matches.
        """
        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        registry = NotationRegistry.from_config(config)
        parser = ExpressionParser(registry=registry)
        candidates = annotation_candidates(self.entries)

        # Test that unrelated 2-argument commands don't match aroot
        aroot_cands = [c for c in candidates if c.get("macro") == "aroot"]
        if aroot_cands:
            aroot_cand = aroot_cands[0]

            # Parse a different 2-argument command
            ast = parser.parse(r"\frac{a}{b}")
            matches = match_ast(ast, aroot_cand.get("classification"), registry)

            # Should NOT match because it's \frac, not \sqrt
            self.assertFalse(matches,
                           r"\frac{a}{b} should NOT match aroot (nth-root) pattern")


if __name__ == "__main__":
    unittest.main()
