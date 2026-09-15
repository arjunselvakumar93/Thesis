import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


from symbol_scanner import (
    extract_command_with_arguments,
    read_tex_argument,
)


class TestGenericTexArguments(unittest.TestCase):

    def test_two_unbraced_arguments(self):

        text = r"\frac a b"

        expression, arguments = (
            extract_command_with_arguments(
                text,
                0,
                r"\frac",
                2
            )
        )

        self.assertEqual(
            arguments,
            ["a", "b"]
        )


    def test_first_argument_braced(self):

        text = r"\frac {a} b"

        expression, arguments = (
            extract_command_with_arguments(
                text,
                0,
                r"\frac",
                2
            )
        )

        self.assertEqual(
            arguments,
            ["a", "b"]
        )


    def test_second_argument_braced(self):

        text = r"\frac a {b}"

        expression, arguments = (
            extract_command_with_arguments(
                text,
                0,
                r"\frac",
                2
            )
        )

        self.assertEqual(
            arguments,
            ["a", "b"]
        )


    def test_both_arguments_braced(self):

        text = r"\frac {a} {b}"

        expression, arguments = (
            extract_command_with_arguments(
                text,
                0,
                r"\frac",
                2
            )
        )

        self.assertEqual(
            arguments,
            ["a", "b"]
        )


    def test_control_sequence_arguments(self):

        text = r"\frac \alpha \beta"

        expression, arguments = (
            extract_command_with_arguments(
                text,
                0,
                r"\frac",
                2
            )
        )

        self.assertEqual(
            arguments,
            [
                r"\alpha",
                r"\beta"
            ]
        )


if __name__ == "__main__":
    unittest.main()