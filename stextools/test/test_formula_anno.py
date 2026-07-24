import unittest

from stextools.snify.formula_anno.notations import get_notation_match


class TestFormulaAnnotation(unittest.TestCase):
    def test_subset_match(self):
        string = r'A \subset B'
        ranges = get_notation_match(r'#1\subset#2', string)

        self.assertIsNotNone(ranges)
        self.assertEqual(string[ranges[0][0]:ranges[0][1]].strip(), 'A')
        self.assertEqual(string[ranges[1][0]:ranges[1][1]].strip(), 'B')

    def test_subseteq_match(self):
        string = r'A \subseteq B'
        ranges = get_notation_match(r'#1\subseteq#2', string)

        self.assertIsNotNone(ranges)
        self.assertEqual(string[ranges[0][0]:ranges[0][1]].strip(), 'A')
        self.assertEqual(string[ranges[1][0]:ranges[1][1]].strip(), 'B')


class TestFormulaAnnotationAdditionalShapes(unittest.TestCase):
    def test_additional_formula_shapes(self):
        cases = [
            (r'#1\subset#2', r'X \subset Y', ['X', 'Y']),
            (r'#1\subset#2', r'A_1 \subset B_2', ['A_1', 'B_2']),
            (
                r'#1\subseteq#2',
                r'\mathcal{A} \subseteq \mathcal{B}',
                [r'\mathcal{A}', r'\mathcal{B}'],
            ),
            (
                r'#1\subseteq#2',
                r'(A \cup B) \subseteq C',
                [r'(A \cup B)', 'C'],
            ),
        ]

        for notation, string, expected_arguments in cases:
            with self.subTest(notation=notation, string=string):
                ranges = get_notation_match(notation, string)

                self.assertIsNotNone(ranges)
                arguments = [string[a:b].strip() for a, b in ranges]
                self.assertEqual(arguments, expected_arguments)


if __name__ == '__main__':
    unittest.main()
