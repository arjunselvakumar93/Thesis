import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import apply_annotations


class TestApplyIntegration(unittest.TestCase):
    def test_arithmetic_suggestions_apply_only_to_copied_paper(self):
        replacements = (
            ("A+B", r"\natplus{A,B}", "natarith", "arithmetics"),
            ("A-b-C", r"\natminus{A,b,C}", "natarith", "arithmetics"),
            ("A>B", r"\natmorethan{A}{B}", "natarith", "arithmetics"),
            ("x<Y", r"\natlessthan{x}{Y}", "natarith", "arithmetics"),
            ("A=b", r"\eq{A,b}", "equal", "mv"),
            (r"A\ne B", r"\notequal{A}{B}", "equal", "mv"),
            ("A/b", r"\adivide{A}{b}", "arithmetics", "arithmetics"),
            (r"A\times b", r"\nattimes{A,b}", "natarith", "arithmetics"),
            (r"A\cdot b", r"\nattimes{A,b}", "natarith", "arithmetics"),
            (r"\frac A B", r"\adivide{A}{B}", "arithmetics", "arithmetics"),
        )
        original = "\\documentclass{article}\n" + "\n".join(
            expression for expression, _, _, _ in replacements
        ) + "\n"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            output = root / "output" / "annotated_papers"
            papers.mkdir()
            source = papers / "paper.tex"
            source.write_text(original, encoding="utf-8")
            decisions = []
            for line, (expression, suggestion, module, repository) in enumerate(
                replacements, start=2
            ):
                decisions.append({
                    "file": "paper.tex", "line": line, "column": 1,
                    "matched_expression": expression,
                    "start": original.index(expression),
                    "end": original.index(expression) + len(expression),
                    "source_hash": hashlib.sha256(expression.encode("utf-8")).hexdigest(),
                    "semantic_suggestion": suggestion,
                    "semantic_module": module,
                    "semantic_repository": repository,
                })

            with patch.object(apply_annotations, "PAPERS_DIR", papers), \
                 patch.object(apply_annotations, "OUTPUT_DIR", output):
                apply_annotations.prepare_output_folder()
                count = apply_annotations.apply_annotations(decisions)

            annotated = (output / "paper.tex").read_text(encoding="utf-8")
            self.assertEqual(count, len(replacements))
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            for _, suggestion, _, _ in replacements:
                self.assertIn(suggestion, annotated)
            self.assertIn(r"\usemodule[smglom/mv]{mod?equal}", annotated)
            self.assertIn(r"\usemodule[smglom/arithmetics]{mod?natarith}", annotated)
            self.assertIn(r"\usemodule[smglom/arithmetics]{mod?arithmetics}", annotated)


if __name__ == "__main__":
    unittest.main()
