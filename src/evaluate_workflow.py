"""Generate traceable, layered evaluation data for the real paper corpus."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil

from expression_annotation import match_ast
from expression_document import parse_papers
from expression_metadata import build_expression_config
from expression_registry import NotationRegistry
from notation_adapters import match_adapter_expression
from notation_catalog import annotation_candidates, build_catalog
from notation_matcher import normalize_math_expression
from notation_scanner import _source_location
from smglom_notation_extractor import scan_available_smglom
from symbol_scanner import PAPERS_DIR


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = ROOT / "evaluation"
GOLD_FILE = EVALUATION_DIR / "gold_annotations.json"
DECISIONS_FILE = ROOT / "decisions" / "review_results.json"


def _node_json(node):
    return {"kind": node.kind, "value": node.value, "start": node.start, "end": node.end,
            "text": node.text, "roles": list(node.roles),
            "children": [_node_json(child) for child in node.children]}


def _node_arguments(node):
    if node.kind == "Alignment":
        return [child.text.strip() for child in node.children]
    result = []
    for child in node.children:
        text = child.text.strip()
        if child.kind == "GroupedExpression" and child.value == "{" and text.startswith("{"):
            text = text[1:-1]
        result.append(normalize_math_expression(text))
    return result


def _relative(path, root):
    return str(Path(path).relative_to(root)).replace("\\", "/")


def _candidate_matches(region, candidates, registry):
    found = []
    for candidate in candidates:
        rule = candidate["classification"]
        if rule.get("grammar_adapter"):
            match = match_adapter_expression(region.tree.text, rule)
            matches = [match] if match else []
        else:
            matches = match_ast(region.tree, rule, registry)
        for match in matches:
            found.append({"semantic_macro": candidate.get("macro"),
                          "module": candidate.get("module"),
                          "repository": candidate.get("repository"),
                          "pattern_type": rule.get("pattern_type"),
                          "grammar_adapter": rule.get("grammar_adapter"),
                          "arguments": match["arguments"], "start": match["start"],
                          "end": match["end"], "matched_expression": match["matched_expression"],
                          "annotation_ready": True})
    unique = {(item["semantic_macro"], item["start"], item["end"], tuple(item["arguments"])):
              item for item in found}
    return sorted(unique.values(), key=lambda item: (item["start"], item["end"], item["semantic_macro"] or ""))


def _region_records(papers_dir, config, catalogue, decisions):
    registry = NotationRegistry.from_config(config)
    candidates = annotation_candidates(catalogue)
    decision_map = defaultdict(list)
    for decision in decisions:
        decision_map[(str(decision.get("file", "")).replace("\\", "/"),
                      int(decision.get("line", 0)))].append(decision)
    records = []
    for number, region in enumerate(parse_papers(papers_dir, config), 1):
        line, column, source_line = _source_location(region.source, region.start)
        file = _relative(region.file, papers_dir)
        source_expression = region.source[region.start:region.end]
        nodes = [] if region.tree is None else [
            {"kind": node.kind, "value": node.value, "start": node.start, "end": node.end,
             "text": node.text, "roles": list(node.roles)}
            for node in region.tree.walk()
            if node.kind in {"UnaryOperator", "BinaryOperator", "NaryOperator", "Command",
                             "SemanticCommand", "ScriptedExpression", "BigOperatorApplication",
                             "BinderApplication", "BoundExpression"}]
        semantic = [] if region.tree is None else _candidate_matches(region, candidates, registry)
        grouped = defaultdict(set)
        for item in semantic:
            grouped[(item["start"], item["end"], item["matched_expression"])].add(item["semantic_macro"])
        ambiguous = [{"start": key[0], "end": key[1], "expression": key[2],
                      "semantic_macros": sorted(value)} for key, value in grouped.items() if len(value) > 1]
        end_line = region.source.count("\n", 0, region.end) + 1
        reviewed = [decision for key, values in decision_map.items()
                    if key[0] == file and line <= key[1] <= end_line for decision in values]
        records.append({"id": number, "paper": Path(file).parts[0] if Path(file).parts else "",
                        "file": file, "line": line, "column": column,
                        "source_start": region.start, "source_end": region.end,
                        "source_line": source_line, "source_expression": source_expression,
                        "parse_success": region.tree is not None, "parse_error": region.error,
                        "ast_type": region.tree.kind if region.tree else None,
                        "ast": _node_json(region.tree) if region.tree else None,
                        "recognized_notation_nodes": nodes,
                        "semantic_candidates": semantic,
                        "annotation_candidates": semantic,
                        "semantic_ambiguities": ambiguous,
                        "review_decisions": reviewed,
                        "application_result": "not_run"})
    return records


def _gold_metrics(gold, occurrences):
    by_file_line = defaultdict(list)
    for occurrence in occurrences:
        by_file_line[(occurrence["file"], occurrence["line"])].append(occurrence)
    exact, partial, candidate_present, candidate_total = 0, 0, 0, 0
    generated_valid, generated_total, ambiguous = 0, 0, 0
    details = []
    for item in gold:
        possible = by_file_line[(item["file"], item["line"])]
        gold_source = normalize_math_expression(item["source_expression"])
        occurrence = next((value for value in possible
                           if gold_source in normalize_math_expression(value["source_expression"])), None)
        root = occurrence and occurrence.get("ast")
        args = [] if not occurrence or not occurrence.get("ast") else _node_arguments_from_json(root)
        scope_exact = bool(root and root["value"] == item["expected_root"]
                           and args == item["expected_arguments"])
        exact += scope_exact
        partial += bool(root) and not scope_exact
        macros = {candidate["semantic_macro"] for candidate in (occurrence or {}).get("semantic_candidates", [])}
        valid = set(item.get("valid_semantic_macros", []))
        if item.get("semantic_status") == "ambiguous":
            ambiguous += 1
        if item.get("should_propose") and valid:
            candidate_total += 1
            candidate_present += bool(macros & valid)
        if valid:
            generated_total += len(macros)
            generated_valid += len(macros & valid)
        details.append({"gold": item, "occurrence_id": occurrence and occurrence["id"],
                        "scope_exact": scope_exact, "observed_root": root and root["value"],
                        "observed_arguments": args, "candidate_macros": sorted(macros),
                        "correct_candidate_present": bool(macros & valid)})
    size = len(gold)
    return {"size": size, "exact_scope": exact, "partial_or_incorrect_scope": partial,
            "scope_accuracy_percent": round(100 * exact / size, 1) if size else None,
            "semantic_candidate_recall": {"present": candidate_present, "gold_with_known_candidate": candidate_total,
                "percent": round(100 * candidate_present / candidate_total, 1) if candidate_total else None},
            "semantic_candidate_precision": {"relevant": generated_valid, "generated_on_labeled_gold": generated_total,
                "percent": round(100 * generated_valid / generated_total, 1) if generated_total else None},
            "ambiguous_cases": ambiguous,
            "details": details}


def _node_arguments_from_json(node):
    result = []
    for child in node.get("children", []):
        text = normalize_math_expression(child["text"].strip())
        if child["kind"] == "GroupedExpression" and child["value"] == "{" and text.startswith("{"):
            text = text[1:-1]
        result.append(text)
    return result


def _hash_papers(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob("*.tex"))}


def _output_sanity(root):
    files, unreadable, unbalanced_braces, unbalanced_math, duplicate_modules = 0, [], [], [], []
    for path in sorted(Path(root).rglob("*.tex")):
        files += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            unreadable.append(str(path))
            continue
        depth = 0
        for index, character in enumerate(text):
            if character in "{}" and (index == 0 or text[index - 1] != "\\"):
                depth += 1 if character == "{" else -1
                if depth < 0:
                    break
        if depth:
            unbalanced_braces.append(str(path))
        dollars = len(re.findall(r"(?<!\\)\$", text))
        if dollars % 2:
            unbalanced_math.append(str(path))
        modules = re.findall(r"\\usemodule\[[^]]+\]\{[^}]+\}", text)
        if len(modules) != len(set(modules)):
            duplicate_modules.append(str(path))
    return {"files_checked": files, "unreadable_files": unreadable,
            "unbalanced_brace_files": unbalanced_braces,
            "unbalanced_math_delimiter_files": unbalanced_math,
            "duplicate_module_files": duplicate_modules,
            "passed": not any((unreadable, unbalanced_braces, unbalanced_math, duplicate_modules))}


def evaluate(papers_dir=PAPERS_DIR, config_path=ROOT / "expression-config.example.json",
             gold_path=GOLD_FILE, apply=False):
    config = build_expression_config(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))
    records = scan_available_smglom(quiet=True)
    catalogue = build_catalog(records)
    decisions = json.loads(DECISIONS_FILE.read_text(encoding="utf-8")) if DECISIONS_FILE.exists() else []
    occurrences = _region_records(Path(papers_dir), config, catalogue, decisions)
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    blockers = Counter()
    for entry in catalogue:
        rule = entry["classification"]
        if not rule.get("grammar_supported"):
            blockers[rule.get("unsupported_reason", "parser unsupported")] += 1
        elif not rule.get("annotation_ready", rule.get("pattern_type") in {
                "binary_infix", "sequence", "unary", "command"}):
            blockers[rule.get("annotation_blocker", "missing user metadata")] += 1
    parsed = sum(item["parse_success"] for item in occurrences)
    recognized = sum(bool(item["recognized_notation_nodes"]) for item in occurrences)
    semantic = sum(bool(item["semantic_candidates"]) for item in occurrences)
    ready = sum(bool(item["annotation_candidates"]) for item in occurrences)
    ambiguous = sum(bool(item["semantic_ambiguities"]) for item in occurrences)
    accepted = [item for item in decisions if item.get("decision") == "accepted"]
    from decision_validator import materialize_valid_decisions, validate_decisions
    validation = validate_decisions(accepted, records, papers_dir)
    valid_accepted = materialize_valid_decisions(validation)
    application = {"requested": apply, "accepted": len(accepted), "applied": 0,
                   "failed": 0, "original_sources_unchanged": True,
                   "validated_current": len(valid_accepted),
                   "stale": len(validation["stale"]),
                   "unverifiable": len(validation["unverifiable"]),
                   "compilation": "not run; no usable compiler was invoked"}
    if apply:
        import apply_annotations
        before = _hash_papers(papers_dir)
        apply_annotations.PAPERS_DIR = Path(papers_dir)
        apply_annotations.prepare_output_folder()
        application_report = apply_annotations.apply_annotations(valid_accepted, return_report=True)
        application["applied"] = application_report["successfully_rendered_decisions"]
        application["failed"] = application_report["failed_decisions"]
        for key in ("eligible_current_decisions", "unique_source_nodes", "same_span_conflicts",
                    "partial_overlap_conflicts", "nested_relationships", "nested_compositions",
                    "independent_replacements", "successfully_rendered_decisions",
                    "successfully_written_files", "failure_reasons", "modules_inserted"):
            application[key] = application_report[key]
        application["original_sources_unchanged"] = before == _hash_papers(papers_dir)
        application["sanity"] = _output_sanity(apply_annotations.OUTPUT_DIR)
        application["compiler_available"] = bool(shutil.which("pdflatex") or shutil.which("latexmlc"))
        for occurrence in occurrences:
            occurrence["application_result"] = "evaluated_in_output_copy"
    gold_report = _gold_metrics(gold, occurrences)
    successful_examples = [{"file": detail["gold"]["file"], "line": detail["gold"]["line"],
                            "expression": detail["gold"]["source_expression"],
                            "root": detail["observed_root"],
                            "candidate_macros": detail["candidate_macros"]}
                           for detail in gold_report["details"]
                           if detail["scope_exact"] and detail["correct_candidate_present"]][:5]
    ambiguous_examples = [{"file": detail["gold"]["file"], "line": detail["gold"]["line"],
                           "expression": detail["gold"]["source_expression"],
                           "valid_semantic_macros": detail["gold"]["valid_semantic_macros"],
                           "reason": "multiple concepts share the visible notation"}
                          for detail in gold_report["details"]
                          if detail["gold"].get("semantic_status") == "ambiguous"][:5]
    blocked_examples = [{"semantic_macro": entry.get("macro"), "module": entry.get("module"),
                         "notation": entry.get("notation"),
                         "reason": (entry["classification"].get("unsupported_reason")
                                    or entry["classification"].get("annotation_blocker"))}
                        for entry in catalogue
                        if (not entry["classification"].get("grammar_supported")
                            or (entry["classification"].get("grammar_supported")
                                and not entry["classification"].get("annotation_ready",
                                    entry["classification"].get("pattern_type") in {
                                        "binary_infix", "sequence", "unary", "command"})))]
    total = len(occurrences)
    summary = {"math_region_extraction": {"total_extracted": total,
                "gold_regions_located": sum(detail["occurrence_id"] is not None for detail in gold_report["details"]),
                "gold_regions": len(gold)},
               "structural_parser": {"parsed": parsed, "unsupported": total - parsed,
                "coverage_percent": round(100 * parsed / total, 1) if total else None},
               "notation_recognition": {"regions": recognized,
                "coverage_percent": round(100 * recognized / total, 1) if total else None},
               "semantic_candidate_coverage": {"regions": semantic,
                "coverage_percent": round(100 * semantic / total, 1) if total else None},
               "annotation_ready_coverage": {"regions": ready,
                "coverage_percent": round(100 * ready / total, 1) if total else None},
               "semantic_ambiguity_regions": ambiguous,
               "gold": {key: value for key, value in gold_report.items() if key != "details"},
               "review": {"stored_decisions": len(decisions), "accepted": len(accepted),
                          "validated_current": len(valid_accepted),
                          "stale": len(validation["stale"]),
                          "unverifiable": len(validation["unverifiable"])},
               "application": application, "blockers": dict(sorted(blockers.items())),
               "examples": {"successful": successful_examples,
                            "ambiguous": ambiguous_examples,
                            "blocked": blocked_examples}}
    return summary, occurrences, gold_report["details"]


def _display(summary):
    for title, key in (("Math regions", "math_region_extraction"),
                       ("Structural parser", "structural_parser"),
                       ("Notation recognition", "notation_recognition"),
                       ("Semantic candidates", "semantic_candidate_coverage"),
                       ("Annotation readiness", "annotation_ready_coverage"),
                       ("Application", "application")):
        print(f"\n{title}\n{'-' * len(title)}")
        for name, value in summary[key].items():
            print(f"{name.replace('_', ' ').title()}: {value}")
    print("\nBlockers\n--------")
    for reason, count in summary["blockers"].items():
        print(f"{reason}: {count}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, default=PAPERS_DIR)
    parser.add_argument("--config", type=Path, default=ROOT / "expression-config.example.json")
    parser.add_argument("--gold", type=Path, default=GOLD_FILE)
    parser.add_argument("--output", type=Path, default=EVALUATION_DIR)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply", action="store_true",
                        help="Apply accepted stored decisions to a copied output tree")
    args = parser.parse_args(argv)
    summary, occurrences, gold_details = evaluate(args.papers, args.config, args.gold, args.apply)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "evaluation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.output / "evaluation_occurrences.json").write_text(
        json.dumps(occurrences, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.output / "evaluation_gold_results.json").write_text(
        json.dumps(gold_details, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _display(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
