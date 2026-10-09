import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


from notation_catalog import (
    classify_notation,
)


class TestNotationClassifier(unittest.TestCase):

    def test_binary_infix_greater_than(self):

        record = {
            "notation":
                r"#1\mathrel{\maincomp{>}}#2",
            "placeholders": [1, 2]
        }

        result = classify_notation(
            record
        )

        self.assertEqual(
            result["pattern_type"],
            "binary_infix"
        )

        self.assertEqual(
            result["operator"],
            ">"
        )

        self.assertEqual(
            result["argument_count"],
            2
        )


    def test_sequence_plus(self):

        record = {
            "notation":
                r"\argsep{#1}{\mathbin{\maincomp+}}",
            "placeholders": [1]
        }

        result = classify_notation(
            record
        )

        self.assertEqual(
            result["pattern_type"],
            "sequence"
        )

        self.assertEqual(
            result["separator"],
            "+"
        )

        self.assertTrue(
            result["variable_arguments"]
        )


    def test_fraction_command(self):

        record = {
            "notation":
                r"\frac{#1}{#2}",
            "placeholders": [1, 2]
        }

        result = classify_notation(
            record
        )

        self.assertEqual(
            result["pattern_type"],
            "command"
        )

        self.assertEqual(
            result["command"],
            r"\frac"
        )

        self.assertEqual(
            result["argument_count"],
            2
        )


    def test_sqrt_command(self):

        record = {
            "notation":
                r"\sqrt{#1}",
            "placeholders": [1]
        }

        result = classify_notation(
            record
        )

        self.assertEqual(
            result["pattern_type"],
            "command"
        )

        self.assertEqual(
            result["command"],
            r"\sqrt"
        )

        self.assertEqual(
            result["argument_count"],
            1
        )


    def test_constant_not_confused_with_plus(self):

        record = {
            "notation":
                r"\mathbb{N}^+",
            "placeholders": []
        }

        result = classify_notation(
            record
        )

        self.assertEqual(
            result["pattern_type"],
            "constant"
        )


    def test_complex_template(self):

        record = {
            "notation":
                r"#1\comp{+}#2\comp{i}",
            "placeholders": [1, 2]
        }

        result = classify_notation(
            record
        )

        self.assertEqual(
            result["pattern_type"],
            "template"
        )


if __name__ == "__main__":
    unittest.main()