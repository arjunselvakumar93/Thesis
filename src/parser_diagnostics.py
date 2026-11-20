"""Structural parser coverage and complete unsupported-region reports."""

import argparse
from collections import Counter
import json
from pathlib import Path
from expression_document import parse_papers
from notation_catalog import build_expression_config
PAPERS_DIR = Path(__file__).resolve().parents[1] / "papers"
from expression_parser import canonicalize_tex_notation


def coverage_report(regions, catalogue=None):
    failures = []
    for region in regions:
        if not region.error:
            continue
        details = dict(region.diagnostic or {})
        details.update(file=str(region.file), line=region.source.count("\n", 0, region.start) + 1,
                       start=region.start, end=region.end,
                       math_text=region.source[region.start:region.end], reason=region.error)
        absolute = region.start + (details.get("position") or 0)
        details.update(error_line=region.source.count("\n", 0, absolute) + 1,
                       error_column=absolute - region.source.rfind("\n", 0, absolute),
                       recovery="Scope excluded from review/annotation; check completeness or configure syntax.")
        failures.append(details)
    total, unsupported = len(regions), len(failures)
    notation_nodes = []
    catalogue_spellings = set()
    catalogue_sources = {}
    if catalogue is not None:
        from notation_catalog import annotation_candidates
        for candidate in annotation_candidates(catalogue):
            rule = candidate["classification"]
            visible = rule.get({"sequence": "separator", "binary_infix": "operator",
                                "unary": "operator", "command": "command"}.get(
                                    rule.get("pattern_type"), ""))
            if visible:
                canonical = canonicalize_tex_notation(visible)
                catalogue_spellings.add(canonical)
                catalogue_sources.setdefault(canonical, set()).add(candidate.get("source") or "SMGloM")

    def has_semantics(node):
        metadata = node.metadata
        semantic = getattr(metadata, "semantic_macro", "") if metadata is not None else ""
        if isinstance(metadata, dict):
            semantic = metadata.get("semantic_macro") or (
                node.value.lstrip("\\") if metadata.get("semantic") else ""
            )
        visible = node.notation or node.value
        return bool(semantic) or canonicalize_tex_notation(visible) in catalogue_spellings

    annotation_ready_regions = 0
    source_counts = Counter()
    configured_bound_regions = 0
    for region in regions:
        if region.tree is None:
            continue
        region_ready = False
        for node in region.tree.walk():
            if node.kind not in {"UnaryOperator", "BinaryOperator", "NaryOperator",
                                 "Command", "SemanticCommand", "BoundExpression",
                                 "BinderApplication", "BigOperatorApplication"}:
                continue
            notation_nodes.append(node)
            mapped = has_semantics(node)
            region_ready = region_ready or mapped
            metadata_source = getattr(node.metadata, "source", "") if node.metadata else ""
            semantic_macro = getattr(node.metadata, "semantic_macro", "") if node.metadata else ""
            sources = catalogue_sources.get(canonicalize_tex_notation(node.notation or node.value), set())
            if semantic_macro and metadata_source:
                sources = {*sources, metadata_source}
            for source in sources:
                source_counts["configured" if source == "user configuration" else "smglom"] += 1
            if node.kind in {"BoundExpression", "BinderApplication", "BigOperatorApplication"} \
                    and semantic_macro and metadata_source == "user configuration":
                configured_bound_regions += 1
        annotation_ready_regions += int(region_ready)
    semantic_nodes = sum(has_semantics(node) for node in notation_nodes)
    parsed = total - unsupported
    partial = sum(region.tree is not None and any(
        node.kind == "OpaqueEnvironmentBody" for node in region.tree.walk()) for region in regions)
    fallback = sum(region.tree is not None and any(
        node.kind in {"SymbolAtom", "OpaqueCommandApplication", "OpaqueMathToken"}
        for node in region.tree.walk()) for region in regions)
    return {"structural_parser": {"total": total, "parsed": parsed,
                                   "unsupported": unsupported,
                                   "fully_parsed": parsed - partial, "partially_preserved": partial,
                                   "fallback_regions": fallback,
                                   "coverage_percent": round(100 * parsed / total, 1) if total else None},
            "notation_semantics": {"notation_nodes": len(notation_nodes),
                                     "semantically_mapped": semantic_nodes,
                                     "smglom_mapped": source_counts["smglom"],
                                     "configured_mapped": source_counts["configured"],
                                     "unmapped": len(notation_nodes) - semantic_nodes,
                                     "coverage_percent": round(100 * semantic_nodes / len(notation_nodes), 1)
                                     if notation_nodes else None},
            "annotation": {"total_regions": total,
                            "candidate_regions": annotation_ready_regions,
                            "without_candidates": total - annotation_ready_regions,
                            "coverage_percent": round(100 * annotation_ready_regions / total, 1)
                            if total else None},
            "configured_bound_constructs": {"occurrences": configured_bound_regions},
            "failure_categories": dict(Counter(item.get("category", "unsupported") for item in failures)),
            "unsupported_regions": failures}


def display_report(report, show_unsupported=False):
    counts = report["structural_parser"]
    print(f"Total math regions: {counts['total']}\nParsed successfully: {counts['parsed']}\n"
          f"Unsupported: {counts['unsupported']}\nStructural parser coverage: {counts['coverage_percent']}%")
    print(f"Fully parsed: {counts['fully_parsed']}\nPartially preserved: {counts['partially_preserved']}\n"
          f"Regions with generic symbol/command fallbacks: {counts['fallback_regions']}")
    print("Coverage counts extracted regions with ASTs, including opaque preservation; it does not measure semantic correctness.")
    semantics = report["notation_semantics"]
    annotation = report["annotation"]
    print(f"SMGloM-mapped notation nodes: {semantics['smglom_mapped']}/{semantics['notation_nodes']}\n"
          f"Config-mapped notation nodes: {semantics['configured_mapped']}/{semantics['notation_nodes']}\n"
          f"Combined semantic nodes: {semantics['semantically_mapped']}/"
          f"{semantics['notation_nodes']} ({semantics['coverage_percent']}%)\n"
          f"Regions with annotation-ready nodes: {annotation['candidate_regions']}/"
          f"{annotation['total_regions']} ({annotation['coverage_percent']}%)\n"
          f"Configured bound-construct occurrences: {report['configured_bound_constructs']['occurrences']}")
    if show_unsupported:
        for item in report["unsupported_regions"]:
            print(f"\n{item['file']}:{item['line']} [{item['start']}:{item['end']}]\n"
                  f"Math: {item['math_text']!r}\nCategory: {item.get('category')}\n"
                  f"Token: {item.get('token')!r}; region position: {item.get('position')}\n"
                  f"Error location: {item['error_line']}:{item['error_column']}\n"
                  f"Reason: {item['reason']}\nRecovery: {item['recovery']}")
        print(f"\nFailure categories: {report['failure_categories']}")


def format_region_failure(region):
    """One source-aware diagnostic shared by interactive review entry points."""
    position = (region.diagnostic or {}).get("position") or 0
    absolute = region.start + position
    line = region.source.count("\n", 0, absolute) + 1
    column = absolute - region.source.rfind("\n", 0, absolute)
    source = region.source[region.start:region.end]
    snippet = source[max(0, position - 35):position + 45]
    token = (region.diagnostic or {}).get("token") or "<end>"
    return (f"Unparsed math region in {region.file}:{line}:{column}, "
            f"region [{region.start}:{region.end}], token {token!r}: {region.error}\n"
            f"  Context: {snippet!r}\n"
            "  Scope excluded from review/annotation; check completeness or configure syntax.")


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--papers", type=Path, default=PAPERS_DIR)
    cli.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "expression-config.example.json")
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    config = build_expression_config(json.loads(args.config.read_text(encoding="utf-8-sig")))
    from notation_catalog import build_catalog
    from smglom_notation_extractor import scan_available_smglom
    catalogue = build_catalog(scan_available_smglom(quiet=True), config)
    report = coverage_report(parse_papers(args.papers, config), catalogue)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        display_report(report, True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
