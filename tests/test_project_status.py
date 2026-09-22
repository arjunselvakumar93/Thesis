import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from project_status import collect_status, display_status
from smglom_notation_extractor import scan_available_smglom


class TestProjectStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.status = collect_status()

    def test_status_uses_live_project_data(self):
        self.assertEqual(self.status["catalogue"]["entries"],
                         len(scan_available_smglom(quiet=True)))
        self.assertEqual(self.status["real_paper"]["structural_parser"]["parsed"], 70)
        self.assertIn("arithmetics", self.status["repositories"]["installed"])
        self.assertFalse(self.status["integral"]["real_smglom_semantics_available"])

    def test_human_output_has_required_sections(self):
        with patch("builtins.print") as output:
            display_status(self.status)
        text = "\n".join(" ".join(map(str, call.args)) for call in output.call_args_list)
        for section in ("PROJECT STATUS", "Repositories", "Catalogue", "Real paper",
                        "Decisions", "Application", "Integral"):
            self.assertIn(section, text)


if __name__ == "__main__":
    unittest.main()
