import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


from symbol_scanner import (
    remove_latex_comment,
    extract_braced_expression,
)

from review_workflow import (
    generate_semantic_suggestion,
)

from apply_annotations import (
    build_module_declaration,
    insert_required_modules,
)

from smglom_lookup import (
    find_symbol_definitions,
)


class TestSymbolScanner(unittest.TestCase):

    def test_remove_comment(self):

        line = r"x = \sqrt{4} % comment"

        result = remove_latex_comment(line)

        self.assertEqual(
            result,
            r"x = \sqrt{4} "
        )

    def test_simple_sqrt_expression(self):

        line = r"x = \sqrt{3}"

        position = line.find(r"\sqrt")

        result = extract_braced_expression(
            line,
            position,
            r"\sqrt"
        )

        self.assertEqual(
            result,
            r"\sqrt{3}"
        )

    def test_nested_sqrt_expression(self):

        line = r"\sqrt{\frac{B^2}{4} - C}"

        position = line.find(r"\sqrt")

        result = extract_braced_expression(
            line,
            position,
            r"\sqrt"
        )

        self.assertEqual(
            result,
            r"\sqrt{\frac{B^2}{4} - C}"
        )

    def test_two_sqrt_expressions(self):

        line = r"\sqrt{3} + \sqrt{2}"

        first_position = line.find(r"\sqrt")

        second_position = line.find(
            r"\sqrt",
            first_position + 1
        )

        first = extract_braced_expression(
            line,
            first_position,
            r"\sqrt"
        )

        second = extract_braced_expression(
            line,
            second_position,
            r"\sqrt"
        )

        self.assertEqual(
            first,
            r"\sqrt{3}"
        )

        self.assertEqual(
            second,
            r"\sqrt{2}"
        )


class TestSemanticSuggestion(unittest.TestCase):

    def test_asqrt_suggestion(self):

        result = generate_semantic_suggestion(
            r"\sqrt",
            r"\sqrt{3}",
            "asqrt"
        )

        self.assertEqual(
            result,
            r"\asqrt{3}"
        )

    def test_realsqrt_suggestion(self):

        result = generate_semantic_suggestion(
            r"\sqrt",
            r"\sqrt{x}",
            "realsqrt"
        )

        self.assertEqual(
            result,
            r"\realsqrt{x}"
        )


class TestModuleHandling(unittest.TestCase):

    def test_build_module_declaration(self):

        result = build_module_declaration(
            "arithmetics"
        )

        self.assertEqual(
            result,
            r"\usemodule[smglom/arithmetics]{mod?arithmetics}"
        )

    def test_insert_module_only_once(self):

        original = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\end{document}\n"
        )

        result = insert_required_modules(
            original,
            ["arithmetics", "arithmetics"]
        )

        declaration = (
            r"\usemodule[smglom/arithmetics]{mod?arithmetics}"
        )

        self.assertEqual(
            result.count(declaration),
            1
        )


class TestSMGloMLookup(unittest.TestCase):

    def test_find_one_argument_sqrt_candidates(self):

        results = find_symbol_definitions(
            r"\sqrt",
            required_args=1
        )

        macros = {
            result["macro"]
            for result in results
        }

        self.assertIn(
            "asqrt",
            macros
        )

        self.assertIn(
            "realsqrt",
            macros
        )

    def test_two_argument_root_is_filtered_out(self):

        results = find_symbol_definitions(
            r"\sqrt",
            required_args=1
        )

        macros = {
            result["macro"]
            for result in results
        }

        self.assertNotIn(
            "aroot",
            macros
        )

        self.assertNotIn(
            "realroot",
            macros
        )


if __name__ == "__main__":
    unittest.main()