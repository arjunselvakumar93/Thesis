"""Evaluate structural generalization without changing the gold corpus."""

import argparse
from collections import Counter
import json
from pathlib import Path

from expression_annotation import match_candidate, match_classification
from expression_document import parse_math_regions
from expression_metadata import build_expression_config
from expression_registry import NotationRegistry
from notation_catalog import annotation_candidates, build_catalog
from smglom_notation_extractor import scan_available_smglom


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "generalization_stress.tex"


def _failure_category(region, expression):
    diagnostic = region.diagnostic or {}
    token = diagnostic.get("token", "")
    category = diagnostic.get("category", "")
    if r"\begin{" in expression or r"\end{" in expression:
        return "unsupported TeX environment"
    if category == "alignment_syntax" or token == "&":
        return "unsupported TeX environment"
    if category == "delimiter_or_group":
        return "delimiter issue"
    if category == "unknown_control_sequence":
        return "unknown command issue"
    if category == "incomplete_expression":
        return "binder issue"
    if token in {"^", "_"}:
        return "script issue"
    if token:
        return "tokenization issue"
    return "other"


def _has_opaque(tree):
    return any(node.kind in {"OpaqueCommandApplication", "OpaqueEnvironmentBody"}
               for node in tree.walk())


def _candidate_matches(tree, registry, candidates):
    matches = []
    for candidate in candidates:
        try:
            matches.extend(match_candidate(tree, candidate, registry))
        except (KeyError, TypeError, ValueError):
            # Catalogue entries whose adapter metadata is incomplete are a
            # semantic availability signal, but cannot claim an AST match.
            continue
    return matches


def evaluate(fixture=DEFAULT_FIXTURE):
    fixture = Path(fixture)
    source = fixture.read_text(encoding="utf-8")
    raw_config = json.loads((ROOT / "expression-config.example.json").read_text(
        encoding="utf-8-sig"))
    records = scan_available_smglom(quiet=True)
    config = build_expression_config(raw_config, records)
    registry = NotationRegistry.from_config(config)
    catalogue = build_catalog(records, config)
    ready_candidates = annotation_candidates(catalogue)
    mapped_candidates = [
        {"macro": entry.get("macro"), "classification": dict(entry["classification"]),
         "record": entry.get("record"), "args": entry.get("args")}
        for entry in catalogue
        if entry.get("macro") and entry["classification"].get("grammar_supported")]
    regions = parse_math_regions(source, registry, fixture)
    items = []
    for index, region in enumerate(regions, 1):
        expression = source[region.start:region.end]
        if region.tree is None:
            items.append({
                "index": index, "expression": expression,
                "structural_status": "UNSUPPORTED",
                "structural_parse_success": False,
                "exact_span_preserved": False,
                "review_scope_available": False,
                "semantic_mapping_available": False,
                "annotation_ready": False,
                "failure_category": _failure_category(region, expression),
                "error": region.error,
            })
            continue
        semantic_matches = _candidate_matches(region.tree, registry, mapped_candidates)
        ready_matches = _candidate_matches(region.tree, registry, ready_candidates)
        structural_matches = []
        for token, label in config.get("review_labels", {}).items():
            operator = registry.operators.get((token, "infix"))
            if operator is None:
                continue
            pattern = "sequence" if operator.arity == "variable" else "binary_infix"
            rule = {"pattern_type": pattern,
                    "separator" if pattern == "sequence" else "operator": operator.token,
                    "structural_name": label}
            structural_matches.extend(match_classification(region.tree, rule, registry))
        opaque = _has_opaque(region.tree)
        items.append({
            "index": index, "expression": expression,
            "structural_status": "OPAQUE_PRESERVED" if opaque else "FULLY_STRUCTURED",
            "structural_parse_success": True,
            "exact_span_preserved": region.tree.text == expression,
            "review_scope_available": bool(ready_matches or structural_matches),
            "semantic_mapping_available": bool(semantic_matches),
            "annotation_ready": bool(ready_matches),
            "opaque_nodes": sum(node.kind in {"OpaqueCommandApplication", "OpaqueEnvironmentBody"}
                                for node in region.tree.walk()),
        })
    statuses = Counter(item["structural_status"] for item in items)
    result = {
        "fixture": str(fixture.relative_to(ROOT)),
        "separate_from_gold_corpus": True,
        "total_expressions": len(items),
        "fully_structured": statuses["FULLY_STRUCTURED"],
        "opaque_preserved": statuses["OPAQUE_PRESERVED"],
        "structurally_partial": statuses["STRUCTURALLY_PARTIAL"],
        "unsupported": statuses["UNSUPPORTED"],
        "exact_source_span": {
            "success": sum(item["exact_span_preserved"] for item in items),
            "total": len(items),
        },
        "review_scope_available": sum(item["review_scope_available"] for item in items),
        "semantic_mapping_available": sum(item["semantic_mapping_available"] for item in items),
        "annotation_ready": sum(item["annotation_ready"] for item in items),
        "failure_categories": dict(sorted(Counter(
            item["failure_category"] for item in items if item.get("failure_category")).items())),
        "expressions": items,
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.fixture)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "expressions"},
                     indent=2, ensure_ascii=False))
    for item in result["expressions"]:
        if item["structural_status"] == "UNSUPPORTED":
            print(f"UNSUPPORTED #{item['index']}: {item['failure_category']}: "
                  f"{item['expression']} -- {item['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
