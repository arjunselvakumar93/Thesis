"""Generate traceable, layered evaluation data for the real paper corpus."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil

from expression_annotation import match_candidate
from expression_document import _without_alignment_markers, parse_papers
from expression_metadata import build_expression_config
from expression_registry import NotationRegistry
from notation_catalog import annotation_candidates, build_catalog
from notation_matcher import normalize_math_expression
from notation_scanner import _source_location, mask_latex_comments
from proposal_eligibility import proposal_eligibility, assess_proposal_candidates
from semantic_candidate_ranker import document_context, policy_dict, rank_candidates
from smglom_notation_extractor import scan_available_smglom
from symbol_scanner import PAPERS_DIR


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = ROOT / "evaluation"
GOLD_FILE = EVALUATION_DIR / "gold_annotations.json"
DECISIONS_FILE = ROOT / "decisions" / "review_results.json"
DEFAULT_EVALUATION_PAPERS = ROOT / "evaluation" / "frozen_papers"


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
    if Path(root).is_file():
        try:
            return str(Path(path).relative_to(ROOT / "papers")).replace("\\", "/")
        except ValueError:
            return Path(path).name
    return str(Path(path).relative_to(root)).replace("\\", "/")


def _candidate_matches(region, candidates, registry):
    found = []
    for candidate in candidates:
        rule = candidate["classification"]
        matches = match_candidate(region.tree, candidate, registry)
        for match in matches:
            eligibility = proposal_eligibility(region.tree, match["start"], match["end"])
            if not eligibility["eligible"]:
                continue
            source_start = region.start + match["start"]
            source_end = region.start + match["end"]
            line, column, source_line = _source_location(region.source, source_start)
            found.append({"semantic_macro": candidate.get("macro"),
                          "macro": candidate.get("macro"), "name": candidate.get("name"),
                          "args": candidate.get("args"),
                          "module": candidate.get("module"),
                          "repository": candidate.get("repository"),
                          "source": candidate.get("source"), "classification": dict(rule),
                          "catalogue_visible": candidate.get("visible"),
                          "configured_notation_alias": bool(
                              match.get("matched_visible_notation")
                              and match.get("matched_visible_notation") != candidate.get("visible")
                              and match.get("matched_visible_notation") in candidate.get("variants", ())),
                          "pattern_type": rule.get("pattern_type"),
                          "grammar_adapter": rule.get("grammar_adapter"),
                          "start": match["start"],
                          "end": match["end"], "matched_expression": normalize_math_expression(
                              _without_alignment_markers(mask_latex_comments(
                                  match["matched_expression"]))),
                          "matched_visible_notation": match.get("matched_visible_notation"),
                          "source_start": source_start, "source_end": source_end,
                          "raw_source": region.source[source_start:source_end],
                          "source_expression": region.source[source_start:source_end],
                          "file": str(region.file), "line": line, "column": column,
                          "source_line": source_line, "ast_node": match["ast_node"],
                          "adapter_family": match["adapter_family"],
                          "argument_roles": match["argument_roles"],
                          "arguments": [normalize_math_expression(
                              _without_alignment_markers(mask_latex_comments(argument)))
                                        for argument in match["arguments"]],
                          "annotation_ready": True,
                          "proposal_eligibility": eligibility})
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
        ast_patterns, ast_visible = {}, {}
        if region.tree is not None:
            for node in region.tree.walk():
                if node.kind == "UnaryOperator":
                    pattern = "unary"
                elif node.kind in {"BinaryOperator", "NaryOperator"}:
                    pattern = ("sequence" if node.kind == "NaryOperator"
                               or getattr(node.metadata, "arity", None) == "variable"
                               else "binary_infix")
                elif node.kind in {"Command", "SemanticCommand"}:
                    pattern = "command"
                elif node.kind == "BoundExpression":
                    pattern = "bound_construct"
                elif node.kind == "ScriptedExpression":
                    pattern = "scripted_operator"
                else:
                    pattern = None
                if pattern:
                    ast_patterns.setdefault((node.start, node.end), pattern)
                    ast_visible.setdefault((node.start, node.end), node.notation or node.value)
        ranked, by_scope = [], defaultdict(list)
        for candidate in semantic:
            by_scope[(candidate["start"], candidate["end"],
                      candidate["matched_expression"])].append(candidate)
        context = document_context(region.source, catalogue)
        automatic, proposal_statuses = [], []
        for scope_candidates in by_scope.values():
            first = scope_candidates[0]
            scope_ranked = rank_candidates(scope_candidates, {
                "matched_visible_notation": ast_visible.get(
                    (first["start"], first["end"]), first.get("matched_visible_notation")),
                "pattern_type": ast_patterns.get((first["start"], first["end"])), "file": file,
            }, context, decisions)
            actual = ast_visible.get((first["start"], first["end"]),
                                     first.get("matched_visible_notation"))
            # Multiple independently declared/configured records for one surface form
            # make the spelling context-sensitive, even when only one is currently
            # annotation-ready.  This is derived from catalogue provenance.
            catalogue_surface = first.get("catalogue_visible")
            surface_records = {(entry.get("macro"), entry.get("module"),
                                entry.get("repository"), entry.get("source"),
                                entry.get("fixity"))
                               for entry in catalogue
                               if (actual in entry.get("variants", ())
                                   or catalogue_surface in entry.get("variants", ()))}
            assessment = assess_proposal_candidates(
                scope_ranked, context_sensitive=len(surface_records) > 1)
            proposal_statuses.append({"start": first["start"], "end": first["end"],
                                      "expression": first["matched_expression"], **assessment,
                                      "automatic_candidates": [item.get("semantic_macro")
                                                               for item in assessment["automatic_candidates"]]})
            automatic.extend(assessment["automatic_candidates"])
            ranked.extend(scope_ranked)
        ranked.sort(key=lambda item: (item["start"], item["end"], item["rank"],
                                      item.get("semantic_macro") or ""))
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
                        "raw_candidates": semantic,
                        "semantic_candidates": semantic,
                        "ranked_candidates": ranked,
                        "annotation_candidates": automatic,
                        "proposal_statuses": proposal_statuses,
                        "semantic_ambiguities": ambiguous,
                        "review_decisions": reviewed,
                        "application_result": "not_run"})
    return records


def _gold_metrics(gold, occurrences):
    by_file_line = defaultdict(list)
    for occurrence in occurrences:
        by_file_line[(occurrence["file"], occurrence["line"])].append(occurrence)
    exact, partial, structural_total, candidate_present, candidate_total = 0, 0, 0, 0, 0
    argument_exact, argument_total = 0, 0
    generated_valid, generated_total, ambiguous = 0, 0, 0
    top_hits = {1: 0, 3: 0, 5: 0}
    precision_relevant = {1: 0, 3: 0, 5: 0}
    precision_total = {1: 0, 3: 0, 5: 0}
    reciprocal_ranks = []
    details = []
    for item in gold:
        possible = by_file_line[(item["file"], item["line"])]
        gold_source = normalize_math_expression(item["source_expression"])
        occurrence = next((value for value in possible
                           if gold_source in normalize_math_expression(value["source_expression"])), None)
        root = occurrence and occurrence.get("ast")
        args = [] if not occurrence or not occurrence.get("ast") else _node_arguments_from_json(root)
        structural_labeled = item.get("expected_root") is not None
        arguments_labeled = item.get("expected_arguments") is not None
        root_exact = bool(root and (not structural_labeled or root["value"] == item["expected_root"]))
        arguments_exact = bool(root and (not arguments_labeled or args == item["expected_arguments"]))
        scope_exact = root_exact and arguments_exact
        if structural_labeled:
            structural_total += 1
            exact += scope_exact
            partial += bool(root) and not scope_exact
        if arguments_labeled:
            argument_total += 1
            argument_exact += arguments_exact
        macros = {candidate["semantic_macro"] for candidate in (occurrence or {}).get("semantic_candidates", [])}
        ranked = (occurrence or {}).get("ranked_candidates", [])
        if root:
            scoped = [candidate for candidate in ranked
                      if candidate["start"] == root["start"] and candidate["end"] == root["end"]]
            if scoped:
                ranked = scoped
        ranked_macros = [candidate["semantic_macro"] for candidate in ranked]
        valid = set(item.get("valid_semantic_macros", []))
        if item.get("semantic_status") == "ambiguous":
            ambiguous += 1
        if item.get("should_propose") and valid:
            candidate_total += 1
            candidate_present += bool(macros & valid)
            positions = [index for index, macro in enumerate(ranked_macros, 1) if macro in valid]
            reciprocal_ranks.append(1 / min(positions) if positions else 0)
            for cutoff in top_hits:
                top_hits[cutoff] += bool(valid & set(ranked_macros[:cutoff]))
        if valid:
            generated_total += len(macros)
            generated_valid += len(macros & valid)
            for cutoff in precision_relevant:
                selected = ranked_macros[:cutoff]
                precision_relevant[cutoff] += len(valid & set(selected))
                precision_total[cutoff] += len(selected)
        details.append({"gold": item, "occurrence_id": occurrence and occurrence["id"],
                        "scope_exact": scope_exact, "observed_root": root and root["value"],
                        "observed_arguments": args, "candidate_macros": sorted(macros),
                        "ranked_candidate_macros": ranked_macros,
                        "ranked_candidates": ranked,
                        "correct_candidate_present": bool(macros & valid)})
    size = len(gold)
    return {"size": size, "structurally_labeled": structural_total,
            "semantically_labeled": candidate_total,
            "argument_labeled": argument_total, "correct_arguments": argument_exact,
            "argument_accuracy_percent": round(100 * argument_exact / argument_total, 1) if argument_total else None,
            "exact_scope": exact, "partial_or_incorrect_scope": partial,
            "scope_accuracy_percent": round(100 * exact / structural_total, 1) if structural_total else None,
            "semantic_candidate_recall": {"present": candidate_present, "gold_with_known_candidate": candidate_total,
                "percent": round(100 * candidate_present / candidate_total, 1) if candidate_total else None},
            "semantic_candidate_precision": {"relevant": generated_valid, "generated_on_labeled_gold": generated_total,
                "percent": round(100 * generated_valid / generated_total, 1) if generated_total else None},
            "semantic_ranking": {
                "top_1_recall_percent": round(100 * top_hits[1] / candidate_total, 1) if candidate_total else None,
                "top_3_recall_percent": round(100 * top_hits[3] / candidate_total, 1) if candidate_total else None,
                "top_5_recall_percent": round(100 * top_hits[5] / candidate_total, 1) if candidate_total else None,
                "mean_reciprocal_rank": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4)
                    if reciprocal_ranks else None,
                **{f"precision_at_{cutoff}_percent": round(
                    100 * precision_relevant[cutoff] / precision_total[cutoff], 1)
                    if precision_total[cutoff] else None for cutoff in (1, 3, 5)},
            },
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


def evaluate(papers_dir=DEFAULT_EVALUATION_PAPERS,
             config_path=ROOT / "expression-config.example.json",
             gold_path=GOLD_FILE, apply=False):
    raw_config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    config = build_expression_config(raw_config)
    records = scan_available_smglom(quiet=True)
    catalogue = build_catalog(records, raw_config)
    decisions = json.loads(DECISIONS_FILE.read_text(encoding="utf-8")) if DECISIONS_FILE.exists() else []
    accepted = [item for item in decisions if item.get("decision") == "accepted"]
    from decision_validator import materialize_valid_decisions, validate_decisions
    validation = validate_decisions(accepted, records, papers_dir)
    valid_accepted = materialize_valid_decisions(validation)
    occurrences = _region_records(Path(papers_dir), config, catalogue, valid_accepted)
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
    candidate_counts = [len(item["raw_candidates"]) for item in occurrences]
    ranked_counts = [len(item["ranked_candidates"]) for item in occurrences]
    ordered = sorted(candidate_counts)
    median = ((ordered[len(ordered) // 2] if len(ordered) % 2 else
               (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2)
              if ordered else 0)
    signal_usage = Counter(evidence["signal"] for occurrence in occurrences
                           for candidate in occurrence["ranked_candidates"]
                           for evidence in candidate["evidence"])
    ranking_summary = dict(
        gold_report["semantic_ranking"],
        average_candidate_count=(round(sum(candidate_counts) / len(candidate_counts), 3)
                                 if candidate_counts else 0),
        median_candidate_count=median,
        duplicates_removed=sum(candidate_counts) - sum(ranked_counts),
        occurrences_with_contextual_evidence=sum(
            any(candidate["contextual_evidence"] for candidate in item["ranked_candidates"])
            for item in occurrences),
        fallback_only_occurrences=sum(
            bool(item["ranked_candidates"]) and all(
                not candidate["contextual_evidence"] for candidate in item["ranked_candidates"])
            for item in occurrences),
        signal_usage=dict(sorted(signal_usage.items())), policy=policy_dict())
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
               "semantic_ranking": ranking_summary,
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
    representatives = []
    for detail in (item for item in gold_details if item["ranked_candidates"]):
        representatives.append({
            "file": detail["gold"]["file"], "line": detail["gold"]["line"],
            "expression": detail["gold"]["source_expression"],
            "raw_candidates": detail["candidate_macros"],
            "ranked_candidates": [{key: candidate.get(key) for key in (
                "semantic_macro", "module", "repository", "score", "rank", "evidence")}
                for candidate in detail["ranked_candidates"]],
            "valid_semantic_macros": detail["gold"].get("valid_semantic_macros", []),
            "first_valid_position": next((index for index, macro in enumerate(
                detail["ranked_candidate_macros"], 1)
                if macro in detail["gold"].get("valid_semantic_macros", [])), None),
        })
        if len(representatives) == 4:
            break
    ranking_output = {
        "baseline": {
            "candidate_set_recall": summary["gold"]["semantic_candidate_recall"],
            "candidate_set_precision": summary["gold"]["semantic_candidate_precision"],
            "average_candidate_count": summary["semantic_ranking"]["average_candidate_count"],
            "ambiguous_region_count": summary["semantic_ambiguity_regions"],
        },
        "ranking": summary["semantic_ranking"],
        "representative_examples": representatives,
    }
    (args.output / "semantic_ranking_metrics.json").write_text(
        json.dumps(ranking_output, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _display(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
