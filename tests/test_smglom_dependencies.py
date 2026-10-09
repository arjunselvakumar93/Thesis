import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expression_annotation import match_ast
from notation_catalog import build_expression_config
from expression_parser import ExpressionParser
from expression_parser import NotationRegistry
from notation_catalog import build_catalog, registry_from_catalog
from smglom_dependencies import (
    compare_snapshots,
    dependency_report,
    repository_health,
    repository_snapshot,
)
from smglom_notation_extractor import discover_repositories, scan_available_smglom


class TestSmglomDependencies(unittest.TestCase):
    @staticmethod
    def _write_repository(root, name, macro="sampleop"):
        source = root / name / "source"
        source.mkdir(parents=True)
        (source / "sample.tex").write_text(
            "\\begin{smodule}{sample}\n"
            f"\\symdef{{{macro}}}[args=2]{{\\{macro}{{#1}}{{#2}}}}\n"
            "\\end{smodule}\n", encoding="utf-8")
        return source

    def test_missing_dependencies_are_reported_with_module_and_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "local" / "source"
            source.mkdir(parents=True)
            (source / "module.tex").write_text(
                "\\usemodule[smglom/calculus]{mod?series}\n"
                "% \\usemodule[smglom/ignored]{mod?commented}\n"
                "\\importmodule[smglom/sets]{mod?set}\n", encoding="utf-8")
            report = dependency_report(root)
            self.assertEqual(report["available_repositories"], ["local"])
            self.assertEqual(report["referenced_repositories"], ["calculus", "sets"])
            self.assertEqual(set(report["missing_repositories"]), {"calculus", "sets"})
            calculus = report["missing_repositories"]["calculus"]
            self.assertEqual(calculus["references"][0]["module"], "series")
            self.assertEqual(calculus["references"][0]["line"], 1)

    def test_new_repository_is_discovered_without_a_python_repository_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "testrepo" / "source"
            source.mkdir(parents=True)
            (source / "sample.tex").write_text(
                "\\begin{smodule}{sample}\n"
                "\\symdef{sampleop}[args=2]{\\sampleop{#1}{#2}}\n"
                "\\end{smodule}\n", encoding="utf-8")
            self.assertEqual(list(discover_repositories(root)), ["testrepo"])
            records = scan_available_smglom(root, quiet=True)
            self.assertEqual(records[0]["repository"], "testrepo")
            self.assertEqual(records[0]["macro"], "sampleop")

    def test_health_and_statistics_are_reported_per_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_repository(root, "first")
            health = repository_health(root)
            self.assertEqual(len(health), 1)
            self.assertEqual(health[0]["repository"], "first")
            self.assertEqual(health[0]["tex_files"], 1)
            self.assertEqual(health[0]["symdefs"], 1)
            self.assertEqual(health[0]["parse_errors"], 0)
            self.assertEqual(health[0]["status"], "ok")
            self.assertEqual(health[0]["total_catalogue_records"], 1)

    def test_starred_notation_is_extracted_and_health_remains_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "localized" / "source"
            source.mkdir(parents=True)
            (source / "localized.tex").write_text(
                "\\begin{smodule}{localized}\n"
                "\\notation*{operation}[de]{\\mathrm{op}\\dobrackets{#1}}\n"
                "\\end{smodule}\n", encoding="utf-8")
            records = scan_available_smglom(root, quiet=True)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["declaration_type"], "notation")
            self.assertTrue(records[0]["starred"])
            self.assertEqual(repository_health(root)[0]["parse_errors"], 0)

    def test_snapshot_is_portable_and_preserves_repository_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_repository(root, "first")
            snapshot = repository_snapshot(root, timestamp="2026-01-01T00:00:00+00:00")
            self.assertEqual(snapshot["repositories_installed"], ["first"])
            self.assertEqual(snapshot["records_by_repository"], {"first": 1})
            self.assertIn("first/sample", snapshot["semantic_families"])
            self.assertNotIn(str(root), json.dumps(snapshot))

    def test_comparison_tracks_new_repository_without_regression(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_repository(root, "first", "firstop")
            before = repository_snapshot(root, timestamp="before")
            self._write_repository(root, "second", "secondop")
            after = repository_snapshot(root, timestamp="after")
            comparison = compare_snapshots(before, after)
            self.assertEqual(comparison["repositories_added"], ["second"])
            self.assertEqual(comparison["repositories_removed"], [])
            self.assertEqual(comparison["catalogue_change"]["entries"], 1)
            records = scan_available_smglom(root, quiet=True)
            self.assertEqual({record["repository"] for record in records}, {"first", "second"})

    def test_synthetic_calculus_record_uses_existing_bound_expression_pipeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "calculus" / "source"
            source.mkdir(parents=True)
            (source / "integral.tex").write_text(
                "\\begin{smodule}{synthetic-integral}\n"
                "\\symdef{syntheticIntegral}[args=iiii,name=integral]"
                "{\\mathop{\\comp\\int}_{#1}^{#2}#3\\,d#4}\n"
                "\\end{smodule}\n", encoding="utf-8")
            records = scan_available_smglom(root, quiet=True)
            self.assertEqual(list(discover_repositories(root)), ["calculus"])
            metadata = {
                "grammar_adapters": [{
                    "semantic_macro": "syntheticIntegral",
                    "grammar_kind": "bound_construct",
                    "grammar_adapter": "BoundConstructAdapter",
                    "node_kind": "BoundExpression",
                    "visible": r"\int",
                    "argument_roles": ["lower", "upper", "body", "variable"],
                    "registry_spec": {
                        "node_kind": "BoundExpression",
                        "semantic_macro": "syntheticIntegral",
                        "semantic_argument_roles": ["lower", "upper", "body", "variable"],
                        "semantic_module": "synthetic-integral",
                        "semantic_repository": "calculus",
                        "source": "SMGloM + explicit grammar metadata",
                        "scripts": {"_": "lower", "^": "upper"},
                        "body_role": "body", "body_precedence": 0,
                        "trailing_role": "variable",
                        "trailing_forms": [{"separator": r"\,", "prefix": "d"}],
                    },
                }]
            }
            catalogue = build_catalog(records, metadata)
            entry = next(item for item in catalogue if item["macro"] == "syntheticIntegral")
            self.assertEqual(entry["source"], "SMGloM")
            self.assertEqual(entry["classification"]["pattern_type"], "bound_construct")
            raw = json.loads((ROOT / "integral-config.example.json").read_text(encoding="utf-8"))
            raw["bound_constructs"].pop(r"\int")
            config, diagnostics = registry_from_catalog(catalogue, base=raw)
            self.assertNotIn(r"\int", [item.get("visible") for item in diagnostics])
            config = build_expression_config(config, records)
            registry = NotationRegistry.from_config(config)
            tree = ExpressionParser(registry=registry).parse(r"\int_0^1 f(x)\,dx")
            self.assertEqual((tree.kind, tree.value), ("BoundExpression", r"\int"))
            matches = match_ast(tree, entry["classification"], registry)
            self.assertEqual(matches[0]["arguments"], ["0", "1", "f(x)", "x"])


if __name__ == "__main__":
    unittest.main()
