from contextlib import redirect_stdout
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from expression_parser import ExpressionParser, Operator, ParseError
from ast_test_helpers import match_rule as match_notation
from ast_test_helpers import scan_rule as find_notation_occurrences
from smglom_notation_extractor import parse_symdef
from review_workflow import find_generic_candidates, generate_classified_suggestion
import review_workflow
import apply_annotations
from apply_annotations import validate_decisions


def config():
    return {"operators": [
        {"token": token, "precedence": power, "associativity": "left"}
        for token, power in (("+", 10), ("-", 10), ("*", 20))
    ], "commands": {r"\aplus": {"arity": 1, "semantic": True},
                     r"\frac": {"arity": 2}}}


def shape(node):
    return (node.value, *(shape(child) for child in node.children))


class TestExpressionParser(unittest.TestCase):
    def parse(self, source):
        data = config()
        return ExpressionParser(data["operators"], data["commands"]).parse(source)

    def test_precedence_and_operand_order(self):
        self.assertEqual(shape(self.parse("a+b*c")), ("+", ("a",), ("*", ("b",), ("c",))))
        self.assertEqual(shape(self.parse("a*b+c")), ("+", ("*", ("a",), ("b",)), ("c",)))

    def test_left_associativity_preserves_mixed_and_repeated_operators(self):
        for source in ("a+b-c", "a-b-c"):
            with self.subTest(source=source):
                root = self.parse(source)
                self.assertEqual(root.value, "-")
                self.assertEqual(root.children[0].value, source[1])
                self.assertEqual(root.children[1].value, "c")

    def test_explicit_and_nested_grouping(self):
        for source in ("(a+b)*c", "a+(b*c)", "((a+b))*c"):
            with self.subTest(source=source):
                root = self.parse(source)
                self.assertEqual((root.start, root.end), (0, len(source)))
                self.assertTrue(any(node.kind == "GroupedExpression" for node in root.walk()))
        self.assertEqual(self.parse("(a+b)*c").children[0].children[0].value, "+")

    def test_nested_semantics_are_opaque_complete_operands(self):
        source = r"\aplus{a,\aplus{b,c}}*d"
        root = self.parse(source)
        semantic = root.children[0]
        self.assertEqual(semantic.kind, "SemanticCommand")
        self.assertEqual(source[semantic.start:semantic.end], r"\aplus{a,\aplus{b,c}}")
        self.assertEqual(semantic.children[0].kind, "OpaqueArgument")
        self.assertEqual(self.parse(r"\aplus{a,b}*c").value, "*")

    def test_command_arguments_are_recursive(self):
        root = self.parse(r"\frac{a+b*c}{d}-e")
        self.assertEqual(root.children[0].kind, "Command")
        self.assertEqual(root.children[0].children[0].children[0].value, "+")

    def test_right_and_nonassociative_configuration(self):
        parser = ExpressionParser([Operator("@", 5, "right")])
        self.assertEqual(shape(parser.parse("a@b@c")), ("@", ("a",), ("@", ("b",), ("c",))))
        parser = ExpressionParser([Operator("@", 5, "none")])
        with self.assertRaises(ParseError):
            parser.parse("a@b@c")
        self.assertEqual(parser.parse("(a@b)@c").value, "@")

    def test_prefix_postfix_and_variable_arity_do_not_flatten(self):
        parser = ExpressionParser([Operator("~", 30, "right", "prefix", 1),
                                   Operator("!", 40, "left", "postfix", 1),
                                   Operator("@", 10, "left", "infix", "variable")])
        root = parser.parse("~a!@b@c")
        self.assertEqual(root.children[0].kind, "BinaryOperator")
        self.assertEqual(shape(root.children[0].children[0]), ("~", ("!", ("a",))))

    def test_invalid_or_unconfigured_syntax_fails_closed(self):
        for source in ("a+", "a?b+c", "a+(b*c", r"\aplus{a,b", "a b+c", r"\frac{a}{b", "a++b"):
            with self.subTest(source=source), self.assertRaises(ParseError):
                self.parse(source)

    def test_operator_control_words_and_aliases(self):
        parser = ExpressionParser([Operator(r"\ne", 1, "none")])
        self.assertEqual(parser.parse(r"a\neq b").value, r"\ne")
        with self.assertRaises(ParseError):
            parser.parse(r"a\neqsomething b")

    def test_scope_matches_and_source_offsets(self):
        rule = {"pattern_type": "sequence", "separator": "+", "expression_config": config()}
        matches = match_notation(" a+\n b*c ", rule)
        self.assertEqual(matches[0]["arguments"], ["a", "b*c"])
        self.assertEqual((matches[0]["start"], matches[0]["end"]), (1, 8))
        rule["separator"] = "-"
        matches = match_notation("a-b-c", rule)
        self.assertEqual([item["arguments"] for item in matches], [["a", "b"], ["a-b", "c"]])

    def test_invalid_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            Operator("@", 3, "associative")
        with self.assertRaises(ValueError):
            ExpressionParser([Operator("@", 3, "left"), Operator("@", 4, "left")])


class TestExpressionPipeline(unittest.TestCase):
    def setUp(self):
        self.record = parse_symdef(r"\symdef{aplus}[args=a,prec=500,name=addition]{\argsep{#1}{\mathbin{\maincomp+}}}")
        self.record.update(repository="test", module="arithmetic")
        self.candidate = find_generic_candidates("+", [self.record])[0]


    def test_math_only_scanning_and_suggestion(self):
        rule = dict(self.candidate["classification"], expression_config=config())
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "paper.tex").write_text("a+b*c outside\n$a+\n b*c$", encoding="utf-8")
            matches = find_notation_occurrences(rule, directory)
        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0]["line"], matches[0]["column"]), (2, 2))
        self.assertEqual(generate_classified_suggestion(matches[0], self.candidate), r"\aplus{a,b*c}")

    def test_review_and_validation_reuse_parser_configuration(self):
        settings = config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            papers.mkdir()
            Path(papers, "paper.tex").write_text("$a+b*c$", encoding="utf-8")
            with redirect_stdout(io.StringIO()), patch.object(review_workflow, "DECISIONS_DIR", root / "decisions"), \
                 patch.object(review_workflow, "DECISION_FILE", root / "decisions" / "review.json"), \
                 patch.object(review_workflow, "choose_candidate", return_value=self.candidate), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"):
                review_workflow.run_generic_review("+", [self.candidate], papers, settings)
                decisions = review_workflow.load_existing_decisions()
            result = validate_decisions(decisions, [self.record], papers)
            self.assertEqual(len(result["valid"]), 1)
            self.assertEqual(decisions[0]["semantic_suggestion"], r"\aplus{a,b*c}")
            output = root / "annotated"
            with patch.object(apply_annotations, "PAPERS_DIR", papers), \
                 patch.object(apply_annotations, "OUTPUT_DIR", output):
                apply_annotations.prepare_output_folder()
                self.assertEqual(apply_annotations.apply_annotations(decisions), 1)
            self.assertIn(r"\aplus{a,b*c}", (output / "paper.tex").read_text(encoding="utf-8"))
            self.assertEqual((papers / "paper.tex").read_text(encoding="utf-8"), "$a+b*c$")


if __name__ == "__main__":
    unittest.main()
