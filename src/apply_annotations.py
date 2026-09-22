"""Plan and apply reviewed semantic annotations against immutable source spans."""

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil

PROJECT_DIR = Path(__file__).resolve().parents[1]
PAPERS_DIR = PROJECT_DIR / "papers"
DECISIONS_FILE = PROJECT_DIR / "decisions" / "review_results.json"
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
    if not DECISIONS_FILE.exists():
        print("Decision file does not exist.")
        return []
    data = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
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


def _line_starts(source):
    return [0] + [index + 1 for index, char in enumerate(source) if char == "\n"]


def _decision_span(decision, source, line_starts):
    original = decision.get("source_expression", decision.get("matched_expression", ""))
    if type(decision.get("source_start")) is int:
        start = decision["source_start"]
        end = decision.get("source_end", start + len(original))
    else:
        line = int(decision.get("line", 0)) - 1
        column = int(decision.get("column", 0)) - 1
        if not 0 <= line < len(line_starts) or column < 0:
            return None
        start = line_starts[line] + column
        end = start + len(original)
    if (type(end) is not int or not 0 <= start < end <= len(source)
            or source[start:end] != original):
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
    starts = _line_starts(source)
    stale, candidates = [], []
    for index, decision in enumerate(decisions):
        span = _decision_span(decision, source, starts)
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
    from decision_validator import materialize_valid_decisions, validate_decisions
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
