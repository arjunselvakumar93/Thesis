"""Print a concise, reproducible status report for the annotation project."""

import argparse
import json
from pathlib import Path
import unittest

from evaluate_workflow import EVALUATION_DIR, evaluate
from expression_metadata import build_expression_config
from notation_catalog import build_catalog, catalog_statistics, search_catalog
from smglom_dependencies import dependency_report
from smglom_notation_extractor import scan_available_smglom


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = EVALUATION_DIR / "evaluation_summary.json"


def _test_count():
    return unittest.defaultTestLoader.discover(str(ROOT / "tests")).countTestCases()


def collect_status():
    dependencies = dependency_report()
    records = scan_available_smglom(quiet=True)
    catalogue = build_catalog(records)
    stats = catalog_statistics(catalogue)
    evaluation, _, _ = evaluate(apply=False)
    previous = (json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
                if SUMMARY_FILE.exists() else {})
    application = previous.get("application", evaluation["application"])
    integral_config_path = ROOT / "integral-config.example.json"
    configured_integral = False
    if integral_config_path.exists():
        raw = json.loads(integral_config_path.read_text(encoding="utf-8-sig"))
        configured = build_expression_config(raw, records)
        configured_integral = any(
            spec.get("semantic_macro")
            for spec in configured.get("bound_constructs", {}).values()
            if spec.get("node_kind") == "BoundExpression"
        )
    real_integrals = [entry for entry in search_catalog(catalogue, "integral")
                      if entry.get("source") == "SMGloM"]
    return {
        "tests": {"discovered": _test_count()},
        "repositories": {
            "installed": dependencies["available_repositories"],
            "referenced": dependencies["referenced_repositories"],
            "missing": sorted(dependencies["missing_repositories"]),
        },
        "catalogue": stats,
        "real_paper": {
            "math_regions": evaluation["math_region_extraction"]["total_extracted"],
            "structural_parser": evaluation["structural_parser"],
            "notation_recognition": evaluation["notation_recognition"],
            "semantic_candidate_coverage": evaluation["semantic_candidate_coverage"],
            "annotation_ready_coverage": evaluation["annotation_ready_coverage"],
            "gold": evaluation["gold"],
        },
        "decisions": evaluation["review"],
        "application": application,
        "integral": {
            "structural_support": True,
            "configured_semantic_support": configured_integral,
            "real_smglom_semantic_records": len(real_integrals),
            "real_smglom_semantics_available": bool(real_integrals),
        },
    }


def display_status(status):
    paper, decisions, application = status["real_paper"], status["decisions"], status["application"]
    total = paper["math_regions"]
    print("PROJECT STATUS\n==============")
    print(f"\nTests\n-----\nDiscovered: {status['tests']['discovered']}")
    print("\nRepositories\n------------")
    print("Installed: " + ", ".join(status["repositories"]["installed"]))
    print("Missing referenced: " + ", ".join(status["repositories"]["missing"]))
    print("\nCatalogue\n---------")
    print(f"Records: {status['catalogue']['entries']}")
    print(f"Classified: {status['catalogue']['classified']}")
    print(f"Grammar-supported: {status['catalogue']['grammar_supported']}")
    print(f"Annotation-ready: {status['catalogue']['annotation_ready_entries']}")
    print("\nReal paper\n----------")
    parsed = paper["structural_parser"]
    print(f"Math regions: {total}")
    print(f"Parsed: {parsed['parsed']}/{total} ({parsed['coverage_percent']}%)")
    for label, key in (("Notation recognition", "notation_recognition"),
                       ("Semantic candidates", "semantic_candidate_coverage"),
                       ("Annotation-ready", "annotation_ready_coverage")):
        item = paper[key]
        print(f"{label}: {item['regions']}/{total} ({item['coverage_percent']}%)")
    print("\nDecisions\n---------")
    print(f"Stored: {decisions['stored_decisions']}")
    print(f"Current: {decisions['validated_current']}")
    print(f"Stale: {decisions['stale']}")
    print(f"Unverifiable: {decisions['unverifiable']}")
    print("\nApplication\n-----------")
    print(f"Eligible: {application.get('eligible_current_decisions', application.get('validated_current', 0))}")
    print(f"Conflicts: {application.get('same_span_conflicts', 0) + application.get('partial_overlap_conflicts', 0)}")
    print(f"Rendered: {application.get('successfully_rendered_decisions', application.get('applied', 0))}")
    print(f"Failures: {application.get('failed', 0)}")
    print("Original sources unchanged: " + ("yes" if application.get("original_sources_unchanged") else "no"))
    print("\nIntegral\n--------")
    print("Structural support: yes")
    print("Configured semantic support: " + ("yes" if status["integral"]["configured_semantic_support"] else "no"))
    available = status["integral"]["real_smglom_semantics_available"]
    print("Real SMGloM integral semantics: " + ("available" if available else "unavailable locally"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--metrics", type=Path, help="Write the same current status as JSON")
    args = parser.parse_args(argv)
    status = collect_status()
    if args.metrics:
        args.metrics.parent.mkdir(parents=True, exist_ok=True)
        args.metrics.write_text(json.dumps(status, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        display_status(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
