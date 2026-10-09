"""Validate accepted decisions and safely apply them to output copies."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from expression_document import parse_papers, find_ast_occurrences
from review_workflow import find_generic_candidates, find_expression_candidates
from smglom_notation_extractor import scan_available_smglom
from collections import defaultdict
from dataclasses import dataclass, field

# Report stored review decisions that no longer match the current scanner.


PAPERS_DIR = Path(__file__).resolve().parents[1] / "papers"


PROJECT_DIR = Path(__file__).resolve().parents[1]
DECISION_FILE = PROJECT_DIR / "decisions" / "review_results.json"


def _normalise_file(path):
    return str(path).replace("\\", "/")


def decision_occurrence_key(decision):
    return (_normalise_file(decision.get("file", "")), decision.get("start"), decision.get("end"))


def match_occurrence_key(match, papers_dir):
    base = Path(papers_dir)
    relative = match["file"].name if base.is_file() else match["file"].relative_to(base)
    return (_normalise_file(relative), match["source_start"], match["source_end"])


def _select_candidate(decision, candidates):
    matching = [
        candidate for candidate in candidates
        if candidate.get("macro") == decision.get("semantic_macro")
    ]
    repository = decision.get("semantic_repository")
    module = decision.get("semantic_module")
    if repository:
        matching = [item for item in matching if item.get("repository") == repository]
    if module:
        matching = [item for item in matching if item.get("module") == module]
    if len(matching) == 1:
        return matching[0]
    stored = decision.get("candidate_classification")
    if stored and matching:
        keys = ("pattern_type", "grammar_adapter", "implicit", "separator", "operator",
                "command", "node_kind", "argument_roles")
        matching = [item for item in matching if all(
            item.get("classification", {}).get(key) == stored.get(key) for key in keys)]
        # Multiple catalogue records with the same semantic identity and the
        # same structural interpretation are presentation provenance variants,
        # not distinct reviewer choices.
        if matching:
            signatures = {json.dumps({key: item["classification"].get(key) for key in keys},
                                     sort_keys=True) for item in matching}
            if len(signatures) == 1:
                return matching[0]
    return None


def validate_decisions(decisions, records=None, papers_dir=PAPERS_DIR):
    """Partition decisions without changing the stored decision file."""
    if records is None:
        records = scan_available_smglom()
    candidates_by_symbol = {}
    occurrences_by_candidate = {}
    valid = []
    valid_matches = []
    stale = []
    unverifiable = []
    default_config = json.loads(
        (PROJECT_DIR / "expression-config.example.json").read_text(encoding="utf-8-sig"))

    for index, decision in enumerate(decisions):
        symbol = decision.get("visible_symbol")
        macro = decision.get("semantic_macro")
        if symbol is None or not macro:
            unverifiable.append((index, decision, "missing notation metadata"))
            continue

        direct_classification = decision.get("candidate_classification")
        search_key = (symbol, json.dumps(decision.get("expression_config"), sort_keys=True),
                      bool(direct_classification))
        if search_key not in candidates_by_symbol:
            if direct_classification:
                from notation_catalog import annotation_candidates, build_catalog
                candidates_by_symbol[search_key] = annotation_candidates(build_catalog(
                    records, decision.get("expression_config", default_config)))
            else:
                candidates_by_symbol[search_key] = (
                    find_expression_candidates(symbol, decision["expression_config"], records)
                    if "expression_config" in decision else find_generic_candidates(symbol, records))
        candidate = _select_candidate(decision, candidates_by_symbol[search_key])
        if candidate is None:
            unverifiable.append((index, decision, "semantic candidate not found uniquely"))
            continue

        candidate_key = (
            candidate.get("repository"), candidate.get("module"),
            candidate.get("macro"), symbol,
            json.dumps(decision.get("expression_config"), sort_keys=True),
        )
        classification = dict(candidate["classification"])
        if "expression_config" in decision:
            classification["expression_config"] = decision["expression_config"]
        if candidate_key not in occurrences_by_candidate:
            from notation_catalog import build_expression_config
            expression_config = build_expression_config(
                decision.get("expression_config", default_config), records)
            matches = find_ast_occurrences(
                parse_papers(papers_dir, expression_config), classification,
                expression_config, candidate)
            occurrences_by_candidate[candidate_key] = {
                match_occurrence_key(match, Path(papers_dir)): match
                for match in matches
            }

        item = (index, decision)
        matched = occurrences_by_candidate[candidate_key].get(decision_occurrence_key(decision))
        source_matches = matched is not None and decision.get("source_hash") == hashlib.sha256(
            matched["source_expression"].encode("utf-8")).hexdigest()
        if source_matches:
            valid.append(item)
            valid_matches.append((index, decision, matched))
        else:
            stale.append(item)

    return {"valid": valid, "valid_matches": valid_matches,
            "stale": stale, "unverifiable": unverifiable}


def materialize_valid_decisions(validation):
    """Return independently usable copies of the validated current decisions."""
    return [dict(decision) for _, decision, _ in validation.get("valid_matches", [])]


def remove_stale_decisions(decisions, stale_indexes, decision_file, confirmation):
    """Remove reported entries only after exact confirmation and make a backup."""
    if confirmation != "REMOVE":
        return False
    decision_file = Path(decision_file)
    backup = decision_file.with_suffix(decision_file.suffix + ".bak")
    shutil.copy2(decision_file, backup)
    stale_indexes = set(stale_indexes)
    retained = [item for index, item in enumerate(decisions) if index not in stale_indexes]
    temporary = decision_file.with_suffix(decision_file.suffix + ".tmp")
    temporary.write_text(
        json.dumps(retained, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(decision_file)
    return True


def _describe(decision):
    return (
        f"{decision.get('decision', 'unknown'):8} "
        f"{decision.get('semantic_macro', '?'):12} "
        f"{decision.get('file', '?')}:{decision.get('line', '?')}:"
        f"{decision.get('column', '?')}  {decision.get('matched_expression', '')!r}"
    )


def validate_main():
    parser = argparse.ArgumentParser(
        description="Dry-run validation of stored notation review decisions."
    )
    parser.add_argument(
        "--symbol", help="Only report one visible notation; use --symbol=- for minus."
    )
    parser.add_argument(
        "--remove-stale", action="store_true",
        help="Offer to remove reported stale entries after explicit confirmation.",
    )
    arguments = parser.parse_args()

    if not DECISION_FILE.exists():
        print(f"Decision file does not exist: {DECISION_FILE}")
        return
    decisions = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    selected = [
        (index, decision) for index, decision in enumerate(decisions)
        if arguments.symbol is None or decision.get("visible_symbol") == arguments.symbol
    ]
    selected_decisions = [decision for _, decision in selected]
    report = validate_decisions(selected_decisions)

    print(f"Checked: {len(selected_decisions)}")
    print(f"Current: {len(report['valid'])}")
    print(f"Stale: {len(report['stale'])}")
    print(f"Unverifiable: {len(report['unverifiable'])}")
    if report["stale"]:
        print("\nStale decisions:")
        for _, decision in report["stale"]:
            print(
                _describe(decision)
                + "  reason=current scanner no longer returns this occurrence"
            )

    if not arguments.remove_stale or not report["stale"]:
        print("\nDry run only. No decisions were changed.")
        return

    original_indexes = [selected[index][0] for index, _ in report["stale"]]
    confirmation = input(
        f"Type REMOVE to delete {len(original_indexes)} stale decision(s) "
        "after creating a .bak file: "
    ).strip()
    if remove_stale_decisions(decisions, original_indexes, DECISION_FILE, confirmation):
        print(f"Removed {len(original_indexes)} stale decision(s).")
        print(f"Backup: {DECISION_FILE.with_suffix(DECISION_FILE.suffix + '.bak')}")
    else:
        print("No decisions were changed.")


# Plan and apply reviewed semantic annotations against immutable source spans.


OUTPUT_DIR = PROJECT_DIR / "output" / "annotated_papers"


@dataclass
class PlannedReplacement:
    start: int
    end: int
    original: str
    replacement: str
    decision: dict
    decision_id: str
    children: list = field(default_factory=list)
    parent: object = None

    @property
    def macro(self):
        return self.decision.get("semantic_macro", "")


def build_module_declaration(module_name, repository="arithmetics"):
    return f"\\usemodule[smglom/{repository}]{{mod?{module_name}}}"


def load_accepted_decisions():
    if not DECISION_FILE.exists():
        print("Decision file does not exist.")
        return []
    data = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    return [item for item in data
            if item.get("decision") == "accepted" and item.get("semantic_suggestion")]


def prepare_output_folder():
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    shutil.copytree(PAPERS_DIR, OUTPUT_DIR)


def insert_required_modules(text, module_names):
    specs = {item if isinstance(item, tuple) else ("arithmetics", item)
             for item in module_names}
    declarations = [build_module_declaration(module, repository)
                    for repository, module in sorted(specs)
                    if build_module_declaration(module, repository) not in text]
    if not declarations:
        return text
    insertion = "\n".join(declarations) + "\n"
    first_line = text.find("\n")
    return insertion + text if first_line == -1 else text[:first_line + 1] + insertion + text[first_line + 1:]


def _decision_span(decision, source):
    start, end = decision.get("start"), decision.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
        return None
    original = source[start:end]
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != decision.get("source_hash"):
        return None
    return start, end, original


def _decision_id(decision, index):
    return decision.get("decision_id") or (
        f"{decision.get('file', '?')}:{decision.get('line', '?')}:"
        f"{decision.get('column', '?')}:{decision.get('semantic_macro', '?')}:{index}")


def _relationship(left, right):
    if left.start == right.start and left.end == right.end:
        return "same_span"
    if left.end <= right.start or right.end <= left.start:
        return "disjoint"
    if left.start <= right.start and right.end <= left.end:
        return "contains"
    if right.start <= left.start and left.end <= right.end:
        return "nested_in"
    return "partial_overlap"


def build_application_plan(source, decisions, relative_file=""):
    """Build a deterministic replacement forest without changing *source*."""
    stale, candidates = [], []
    for index, decision in enumerate(decisions):
        span = _decision_span(decision, source)
        if span is None:
            stale.append({"decision": decision, "reason": "source_span_mismatch"})
            continue
        start, end, original = span
        candidates.append(PlannedReplacement(
            start, end, original, decision["semantic_suggestion"], decision,
            _decision_id(decision, index)))

    unique = {}
    for item in candidates:
        unique.setdefault((item.start, item.end, item.macro, item.replacement), item)
    candidates = sorted(unique.values(), key=lambda item: (item.start, -item.end, item.macro, item.decision_id))

    same_span_conflicts, partial_conflicts, conflicted = [], [], set()
    by_span = defaultdict(list)
    for item in candidates:
        by_span[(item.start, item.end)].append(item)
    for span, items in by_span.items():
        if len({(item.macro, item.replacement) for item in items}) > 1:
            same_span_conflicts.append({"file": relative_file, "start": span[0], "end": span[1],
                "source_expression": source[span[0]:span[1]], "decisions": items,
                "relationship": "same_span"})
            conflicted.update(id(item) for item in items)
    for position, left in enumerate(candidates):
        for right in candidates[position + 1:]:
            if right.start >= left.end:
                break
            if _relationship(left, right) == "partial_overlap":
                partial_conflicts.append({"file": relative_file,
                    "start": max(left.start, right.start), "end": min(left.end, right.end),
                    "source_expression": source[max(left.start, right.start):min(left.end, right.end)],
                    "decisions": [left, right], "relationship": "partial_overlap"})
                conflicted.update((id(left), id(right)))

    safe = [item for item in candidates if id(item) not in conflicted]
    nested_relationships = 0
    for child in safe:
        containers = [parent for parent in safe if parent is not child
                      and parent.start <= child.start and child.end <= parent.end
                      and (parent.start, parent.end) != (child.start, child.end)]
        if containers:
            parent = min(containers, key=lambda item: (item.end - item.start, item.start))
            child.parent = parent
            parent.children.append(child)
            nested_relationships += 1
    for item in safe:
        item.children.sort(key=lambda child: (child.start, child.end))
    roots = [item for item in safe if item.parent is None]
    return {"roots": roots, "safe": safe, "stale": stale,
            "same_span_conflicts": same_span_conflicts,
            "partial_overlap_conflicts": partial_conflicts,
            "nested_relationships": nested_relationships,
            "independent_replacements": sum(not item.children for item in roots),
            "unique_source_nodes": len(by_span),
            "nodes_with_one_semantic_decision": sum(
                len({(x.macro, x.replacement) for x in values}) == 1 for values in by_span.values()),
            "nodes_with_competing_semantic_decisions": len(same_span_conflicts)}


def _render_node(node):
    rendered, cursor = node.replacement, 0
    for child in node.children:
        child_rendered = _render_node(child)
        position = rendered.find(child.original, cursor)
        if position == -1:
            position = rendered.find(child.original)
        if position == -1:
            raise ValueError(f"child source is absent from parent template: {child.original!r}")
        rendered = rendered[:position] + child_rendered + rendered[position + len(child.original):]
        cursor = position + len(child_rendered)
    return rendered


def render_application_plan(source, plan):
    rendered, failed = [], []
    for root in plan["roots"]:
        try:
            rendered.append((root.start, root.end, _render_node(root), root))
        except ValueError as error:
            failed.append({"decision": root.decision, "reason": "nested_render_failure", "detail": str(error)})
    result = source
    for start, end, replacement, _ in sorted(rendered, key=lambda item: item[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    rendered_count = 0
    def count(node):
        nonlocal rendered_count
        rendered_count += 1
        for child in node.children:
            count(child)
    for _, _, _, root in rendered:
        count(root)
    return result, rendered, failed, rendered_count


def apply_annotations(decisions, return_report=False):
    """Apply an already validated set of decisions to copied documents."""
    grouped = defaultdict(list)
    for decision in decisions:
        grouped[decision["file"]].append(decision)
    report = {"eligible_current_decisions": len(decisions), "unique_source_nodes": 0,
              "same_span_conflicts": 0, "partial_overlap_conflicts": 0,
              "nested_relationships": 0, "nested_compositions": 0,
              "independent_replacements": 0, "successfully_rendered_decisions": 0,
              "successfully_written_files": 0, "failed_decisions": 0,
              "failure_reasons": defaultdict(int), "modules_inserted": [], "files": []}
    inserted = set()
    for relative_file in sorted(grouped):
        output_file = OUTPUT_DIR / Path(relative_file)
        if not output_file.exists():
            count = len(grouped[relative_file])
            report["failed_decisions"] += count
            report["failure_reasons"]["file_not_found"] += count
            continue
        source = output_file.read_text(encoding="utf-8")
        plan = build_application_plan(source, grouped[relative_file], relative_file)
        transformed, roots, render_failures, rendered_count = render_application_plan(source, plan)
        successful = []
        def visit(node):
            successful.append(node)
            for child in node.children:
                visit(child)
        for _, _, _, root in roots:
            visit(root)
        modules = {(item.decision.get("semantic_repository", "arithmetics"),
                    item.decision.get("semantic_module")) for item in successful
                   if item.decision.get("semantic_module")}
        final = insert_required_modules(transformed, modules)
        if final != source:
            output_file.write_text(final, encoding="utf-8")
        report["successfully_written_files"] += 1
        report["unique_source_nodes"] += plan["unique_source_nodes"]
        report["same_span_conflicts"] += len(plan["same_span_conflicts"])
        report["partial_overlap_conflicts"] += len(plan["partial_overlap_conflicts"])
        report["nested_relationships"] += plan["nested_relationships"]
        report["nested_compositions"] += sum(bool(item.children) for item in successful)
        report["independent_replacements"] += plan["independent_replacements"]
        report["successfully_rendered_decisions"] += rendered_count
        same_failed = sum(len(item["decisions"]) for item in plan["same_span_conflicts"])
        overlap_failed = len({id(x) for item in plan["partial_overlap_conflicts"] for x in item["decisions"]})
        failures = {"source_span_mismatch": len(plan["stale"]),
                    "semantic_conflict_same_span": same_failed,
                    "overlapping_replacement_conflict": overlap_failed,
                    "nested_render_failure": len(render_failures)}
        for reason, count in failures.items():
            report["failure_reasons"][reason] += count
            report["failed_decisions"] += count
        inserted.update(modules)
        report["files"].append({"file": relative_file, "plan": plan,
                                "render_failures": render_failures})
    report["modules_inserted"] = sorted(
        [{"repository": repo, "module": module} for repo, module in inserted],
        key=lambda item: (item["repository"], item["module"]))
    report["failure_reasons"] = {key: value for key, value in report["failure_reasons"].items() if value}
    return report if return_report else report["successfully_rendered_decisions"]


def _group(decisions):
    grouped = defaultdict(list)
    for decision in decisions:
        grouped[decision["file"]].append(decision)
    return grouped


def _print_conflict(item):
    print(f"  {item['file']} [{item['start']}:{item['end']}] {item['source_expression']!r}")
    print(f"    relationship: {item['relationship']}")
    for decision in item["decisions"]:
        print(f"    {decision.decision_id}: \\{decision.macro}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-conflicts", action="store_true",
                        help="Validate and report the immutable-source plan without writing output")
    args = parser.parse_args(argv)
    accepted = load_accepted_decisions()
    if not accepted:
        print("No accepted annotations were found.")
        return 0
    validation = validate_decisions(accepted)
    current = materialize_valid_decisions(validation)
    print(f"Accepted total: {len(accepted)}")
    print(f"Validated current: {len(current)}")
    print(f"Stale: {len(validation['stale'])}")
    print(f"Unverifiable: {len(validation['unverifiable'])}")
    print(f"Eligible for application: {len(current)}")
    if args.check_conflicts:
        for relative_file, file_decisions in sorted(_group(current).items()):
            source = (PAPERS_DIR / relative_file).read_text(encoding="utf-8")
            plan = build_application_plan(source, file_decisions, relative_file)
            print(f"\n{relative_file}: unique nodes={plan['unique_source_nodes']}, "
                  f"nested={plan['nested_relationships']}, safe={len(plan['safe'])}")
            for item in plan["same_span_conflicts"] + plan["partial_overlap_conflicts"]:
                _print_conflict(item)
        return 0
    prepare_output_folder()
    report = apply_annotations(current, return_report=True)
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, indent=2))
    print(f"Annotated documents: {OUTPUT_DIR}")
    print("Original documents were not modified.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
