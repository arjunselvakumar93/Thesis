"""Evaluate a portable manifest of independently labeled TeX documents."""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from evaluate_workflow import evaluate
from notation_catalog import build_catalog
from expression_document import normalize_math_expression
from smglom_dependencies import dependency_report
from smglom_notation_extractor import scan_available_smglom


ROOT = Path(__file__).resolve().parents[1]
ERROR_CATEGORIES = (
    "parser scope error",
    "notation recognition miss",
    "correct semantic candidate absent",
    "correct candidate ranked below top-1",
    "argument extraction error",
    "annotation blocker",
    "application conflict",
    "stale decision",
    "missing repository",
    "missing semantic metadata",
)

SET_NOTATIONS = {r"\cup", r"\cap", r"\in", r"\subseteq"}
STRUCTURAL_COMMANDS = {r"\Box", r"\#"}
GAP_CATEGORY_DEFINITIONS = {
    "A": "no semantic notation present",
    "B": "already-semantic expression",
    "C": "semantic mapping unavailable",
    "D": "missing SMGloM repository",
    "E": "notation record exists but extractor missed it",
    "F": "notation classified but grammar adapter missing",
    "G": "AST matcher failed",
    "H": "argument mapping incomplete",
    "I": "semantic signature contains hidden/non-visible arguments",
    "J": "source notation variant or alias mismatch",
    "K": "unsafe replacement scope",
    "L": "structural-only TeX",
    "M": "intentional no-proposal or review-required case",
    "N": "other genuine implementation defect",
}


def _ast_summary(node):
    """Return a compact, source-preserving structural summary."""
    if not node:
        return None
    children = node.get("children", [])
    head = f"{node.get('kind')}({node.get('value')})"
    return head if not children else head + "[" + ", ".join(
        _ast_summary(child) for child in children) + "]"


def _ast_node_at(node, start, end):
    if not node:
        return None
    if node.get("start") == start and node.get("end") == end:
        return node
    return next((found for child in node.get("children", [])
                 if (found := _ast_node_at(child, start, end)) is not None), None)


def _review_options(occurrence):
    options = []
    for status in occurrence["proposal_statuses"]:
        if status["automatic_candidates"]:
            continue
        candidates = [item for item in occurrence["ranked_candidates"]
                      if item["start"] == status["start"] and item["end"] == status["end"]]
        if not candidates:
            continue
        macros = sorted({item["semantic_macro"] for item in candidates})
        modules = sorted({item.get("module") for item in candidates if item.get("module")})
        reason = ("insufficient local context" if status["status"] == "contextual_support_required"
                  else "multiple domain-specific candidates" if len(modules) > 1
                  else "semantic ambiguity")
        node = _ast_node_at(occurrence["ast"], status["start"], status["end"])
        options.append({
            "matched_expression": status["expression"],
            "start": status["start"], "end": status["end"],
            "ast_node": None if node is None else {
                key: node.get(key) for key in ("kind", "value", "start", "end", "text")},
            "candidate_count": len(macros), "candidate_macros": macros,
            "arguments": candidates[0].get("arguments", []),
            "automatic_selection_blocker": status["status"],
            "classification": reason,
            "explicit_human_selection_is_safe": all(
                item.get("proposal_eligibility", {}).get("eligible") for item in candidates),
            "ranked_candidates": [{
                "macro": item["semantic_macro"], "module": item.get("module"),
                "repository": item.get("repository"), "rank": item.get("rank"),
                "evidence": item.get("evidence", []),
            } for item in candidates],
        })
    return options


def _matching_gold(occurrence, gold):
    expression = normalize_math_expression(occurrence["source_expression"])
    matches = [item for item in gold
               if item.get("file", "").replace("\\", "/") == occurrence["file"]
               and item.get("line") == occurrence["line"]]
    return next((item for item in matches
                 if normalize_math_expression(item["source_expression"]) == expression), None)


def _coverage_gap(document_id, occurrence, gold):
    """Explain the first pipeline stage blocking one non-ready region."""
    nodes = occurrence["recognized_notation_nodes"]
    candidates = occurrence["semantic_candidates"]
    statuses = sorted({status["status"] for status in occurrence["proposal_statuses"]
                       if not status["automatic_candidates"]})
    values = {node["value"] for node in nodes}
    kinds = {node["kind"] for node in nodes}
    already_semantic = "SemanticCommand" in kinds
    negative_gold = bool(gold and not gold.get("should_propose", False))
    requires_repository = None
    recoverable, hidden = [], []

    if already_semantic:
        category, blocker = "B", "already_semantic_correctly_skipped"
        stage = "proposal eligibility"
        possible, should_not = False, True
    elif candidates:
        category = "M"
        blocker = ("intentional_no_proposal_case" if negative_gold else
                   "semantic_ambiguity_requires_human_review" if "ambiguous_candidate_set" in statuses else
                   "contextual_support_required")
        stage = "automatic proposal policy"
        possible, should_not = not negative_gold, negative_gold
    elif values & SET_NOTATIONS:
        category, blocker = "D", "missing_repository"
        stage, requires_repository = "SMGloM repository availability", "sets"
        possible, should_not = True, True
    elif "BoundExpression" in kinds:
        category, blocker = "I", "semantic_signature_has_hidden_arguments"
        stage = "argument mapping"
        recoverable = ["body", "variable"]
        hidden = ["lower", "upper"]
        possible, should_not = True, True
    elif r"\boldsymbol" in values:
        category, blocker = "K", "unsupported_replacement_scope"
        stage = "safe replacement scope"
        possible, should_not = False, True
    elif values and values <= STRUCTURAL_COMMANDS:
        category, blocker = "L", "structural_only_tex"
        stage = "semantic notation recognition"
        possible, should_not = False, True
    elif values & {r"\mathcal", r"\mathfrak"}:
        category, blocker = "C", "semantic_mapping_unavailable"
        stage = "catalogue semantic mapping"
        possible, should_not = False, True
    elif not nodes:
        category, blocker = "A", "no_semantic_notation_present"
        stage = "semantic notation recognition"
        possible, should_not = False, True
    else:
        category, blocker = "C", "semantic_mapping_unavailable"
        stage = "catalogue semantic mapping"
        possible, should_not = False, True

    provenance = sorted({(item.get("semantic_macro"), item.get("repository"),
                          item.get("module"), item.get("source")) for item in candidates})
    return {
        "document": document_id, "file": occurrence["file"],
        "line": occurrence["line"], "column": occurrence["column"],
        "full_expression": occurrence["source_expression"],
        "ast_summary": _ast_summary(occurrence["ast"]),
        "notation_nodes": nodes,
        "semantic_candidates": sorted({item.get("semantic_macro") for item in candidates}),
        "candidate_provenance": [{"macro": macro, "repository": repository,
                                  "module": module, "source": source}
                                 for macro, repository, module, source in provenance],
        "category": category, "blocker": blocker, "pipeline_stage": stage,
        "proposal_policy_statuses": statuses,
        "review_options": _review_options(occurrence),
        "annotation_should_be_possible": possible,
        "annotation_should_not_be_attempted": should_not,
        "requires_repository": requires_repository,
        "argument_mapping": {"recoverable": recoverable, "hidden_or_missing": hidden},
        "gold_policy": (None if gold is None else {
            "should_propose": gold.get("should_propose"),
            "semantic_status": gold.get("semantic_status"),
            "valid_semantic_macros": gold.get("valid_semantic_macros", []),
        }),
    }


def annotation_coverage_audit(documents):
    """Calculate honest annotation denominators and explain every gap."""
    gaps, per_document, supported_candidate_counts, human_candidate_counts = [], [], [], []
    totals = Counter()
    for document in documents:
        occurrences, gold = document["occurrences"], document["gold"]
        document_gaps = []
        ready = sum(bool(item["annotation_candidates"]) for item in occurrences)
        already = sum(any(node["kind"] == "SemanticCommand"
                          for node in item["recognized_notation_nodes"])
                      and not item["annotation_candidates"] for item in occurrences)
        eligible = ready
        supported = ready
        review_ready = ready
        for occurrence in occurrences:
            if occurrence["annotation_candidates"]:
                supported_candidate_counts.append(len({
                    item["semantic_macro"] for item in occurrence["semantic_candidates"]}))
        for occurrence in occurrences:
            if occurrence["annotation_candidates"]:
                continue
            matched_gold = _matching_gold(occurrence, gold)
            gap = _coverage_gap(document["id"], occurrence, matched_gold)
            document_gaps.append(gap)
            if gap["annotation_should_be_possible"]:
                eligible += 1
                if gap["semantic_candidates"]:
                    supported += 1
                    if (gap["review_options"] and all(
                            option["explicit_human_selection_is_safe"]
                            for option in gap["review_options"])):
                        review_ready += 1
                        count = len(gap["semantic_candidates"])
                        supported_candidate_counts.append(count)
                        human_candidate_counts.append(count)
        gaps.extend(document_gaps)
        missing = sum(gap["blocker"] in {"missing_repository", "semantic_mapping_unavailable"}
                      for gap in document_gaps)
        row = {"document": document["id"], "math_regions": len(occurrences),
               "annotation_eligible": eligible, "mapping_available": supported,
               "review_ready": review_ready, "automatic_ready": ready,
               "human_selection_required": review_ready - ready,
               "annotation_ready": ready, "already_semantic": already,
               "unsupported_or_missing_semantic_data": missing}
        per_document.append(row)
        totals.update(math_regions=len(occurrences), annotation_eligible=eligible,
                      mapping_available=supported, annotation_ready=ready,
                      review_ready=review_ready,
                      already_semantic=already, unsupported_or_missing=missing)
    categories = Counter(gap["category"] for gap in gaps)
    blockers = Counter(gap["blocker"] for gap in gaps)
    eligible = totals["annotation_eligible"]
    supported = totals["mapping_available"]
    ready = totals["annotation_ready"]
    review_ready = totals["review_ready"]
    def candidate_buckets(values):
        return {"1": sum(value == 1 for value in values),
                "2-3": sum(2 <= value <= 3 for value in values),
                "4-6": sum(4 <= value <= 6 for value in values),
                "7+": sum(value >= 7 for value in values)}
    ordered_human_counts = sorted(human_candidate_counts)
    human_median = (ordered_human_counts[len(ordered_human_counts) // 2]
                    if len(ordered_human_counts) % 2 else
                    sum(ordered_human_counts[len(ordered_human_counts) // 2 - 1:
                                             len(ordered_human_counts) // 2 + 1]) / 2)
    if not ordered_human_counts:
        human_median = 0
    summary = {
        "readiness_definitions": {
            "structurally_ready": "valid AST with an exact source span",
            "review_ready": ("at least one supported semantic candidate with complete arguments "
                             "and a safe span after explicit human selection"),
            "automatically_ready": ("review-ready and sufficiently determined by semantic evidence "
                                    "without a human semantic choice"),
        },
        "total_math_regions": totals["math_regions"],
        "regions_with_semantic_notation_recognized": sum(
            bool(item["recognized_notation_nodes"])
            for document in documents for item in document["occurrences"]),
        "regions_with_semantic_candidates": sum(
            bool(item["semantic_candidates"])
            for document in documents for item in document["occurrences"]),
        "annotation_eligible_regions": eligible,
        "supported_annotation_eligible_regions": supported,
        "annotation_ready_regions": ready,
        "review_ready_regions": review_ready,
        "automatically_ready_regions": ready,
        "human_selection_required_regions": review_ready - ready,
        "unsupported_annotation_eligible_regions": eligible - supported,
        "already_semantic_regions": totals["already_semantic"],
        "intentionally_no_proposal_regions": sum(
            gap["blocker"] == "intentional_no_proposal_case" for gap in gaps),
        "regions_where_annotation_should_not_be_attempted": sum(
            gap["annotation_should_not_be_attempted"] for gap in gaps),
        "raw_annotation_ready_coverage_percent": round(100 * ready / totals["math_regions"], 1),
        "eligible_annotation_coverage_percent": round(100 * ready / eligible, 1) if eligible else None,
        "supported_annotation_coverage_percent": round(100 * ready / supported, 1) if supported else None,
        "supported_review_coverage_percent": round(100 * review_ready / supported, 1)
        if supported else None,
        "reviewer_effort": {
            "review_ready_candidate_count_distribution": candidate_buckets(supported_candidate_counts),
            "human_selection_candidate_count_distribution": candidate_buckets(human_candidate_counts),
            "average_candidates_per_human_selection_region": round(
                sum(human_candidate_counts) / len(human_candidate_counts), 2)
            if human_candidate_counts else 0,
            "median_candidates_per_human_selection_region": human_median,
        },
        "gap_categories": {key: categories[key] for key in GAP_CATEGORY_DEFINITIONS},
        "gap_category_definitions": GAP_CATEGORY_DEFINITIONS,
        "gap_blockers": dict(sorted(blockers.items())),
        "priority_table": [{
            "blocker": blocker,
            "affected_regions": count,
            "generic_fix_possible": blocker in {
                "unsupported_replacement_scope", "contextual_support_required"},
            "risk": ("high" if blocker in {"semantic_ambiguity_requires_human_review",
                                            "contextual_support_required"}
                     else "medium" if blocker in {"unsupported_replacement_scope",
                                                   "semantic_signature_has_hidden_arguments"}
                     else "none"),
            "recommended_action": {
                "semantic_ambiguity_requires_human_review": "retain candidates for human selection",
                "contextual_support_required": "collect semantic context; do not auto-select",
                "intentional_no_proposal_case": "no change",
                "missing_repository": "install and evaluate the named repository",
                "no_semantic_notation_present": "no change",
                "semantic_mapping_unavailable": "add only evidence-backed semantic metadata",
                "semantic_signature_has_hidden_arguments": "keep blocked until arguments are recoverable",
                "structural_only_tex": "no change",
                "unsupported_replacement_scope": "generalize exact nested AST matching",
                "already_semantic_correctly_skipped": "no change",
            }.get(blocker, "investigate the recorded first failing stage"),
        } for blocker, count in blockers.most_common()],
        "waterfall": [
            {"stage": "total_math_regions", "regions": totals["math_regions"]},
            {"stage": "annotation_eligible", "regions": eligible,
             "excluded": totals["math_regions"] - eligible},
            {"stage": "semantic_mapping_available", "regions": supported,
             "excluded": eligible - supported},
            {"stage": "review_ready", "regions": review_ready,
             "excluded": 0},
            {"stage": "automatically_ready", "regions": ready,
             "human_selection_required": review_ready - ready},
        ],
        "per_document": per_document,
    }
    return {"summary": summary, "gaps": gaps}


def production_review_readiness(documents, records):
    """Prove one explicit candidate per supported region through production paths."""
    from apply_annotations import build_application_plan, render_application_plan
    from apply_annotations import materialize_valid_decisions, validate_decisions
    from review_workflow import build_review_decision

    proofs, per_document = [], []
    for document in documents:
        target, config = document["target"], document["config"]
        decisions, metadata = [], []
        for occurrence in document["occurrences"]:
            gold = _matching_gold(occurrence, document["gold"])
            gap = None if occurrence["annotation_candidates"] else _coverage_gap(
                document["id"], occurrence, gold)
            if gap is not None and not (gap["annotation_should_be_possible"]
                                        and gap["semantic_candidates"]):
                continue
            if occurrence["annotation_candidates"]:
                # Prove the maximal stable scope for a region. Progressive
                # child scopes remain reviewable independently, but the
                # validator deliberately canonicalizes variable sequences.
                selected = max(occurrence["annotation_candidates"],
                               key=lambda item: (item["end"] - item["start"],
                                                 -item.get("rank", 1)))
            else:
                option = gap["review_options"][0]
                selected = next(item for item in occurrence["ranked_candidates"]
                                if item["start"] == option["start"]
                                and item["end"] == option["end"]
                                and item["semantic_macro"] == option["candidate_macros"][0])
            match = {
                "file": selected["file"], "line": selected["line"],
                "column": selected["column"], "text": selected["source_line"],
                "pattern_type": selected["pattern_type"],
                "matched_expression": selected["matched_expression"],
                "matched_visible_notation": selected.get("matched_visible_notation"),
                "arguments": selected["arguments"],
                "source_start": selected["source_start"],
                "source_end": selected["source_end"],
                "source_expression": selected["source_expression"],
            }
            classification = dict(selected["classification"], expression_config=config)
            decision = build_review_decision(match, selected, classification, target)
            if decision is None:
                continue
            decisions.append(decision)
            metadata.append({"decision_id": decision["decision_id"],
                             "document": document["id"], "file": occurrence["file"],
                             "line": occurrence["line"],
                             "source": occurrence["source_expression"],
                             "selected_candidate_for_test_only": decision["semantic_macro"],
                             "automatic_ready": bool(occurrence["annotation_candidates"])})

        validation = validate_decisions(decisions, records, target)
        current = materialize_valid_decisions(validation)
        valid_ids = {item["decision_id"] for item in current}
        safe_ids, renderable_ids = set(), set()
        grouped = defaultdict(list)
        for decision in current:
            grouped[decision["file"]].append(decision)
        for relative, file_decisions in grouped.items():
            source_file = target if target.is_file() else target / relative
            source = source_file.read_text(encoding="utf-8")
            plan = build_application_plan(source, file_decisions, relative)
            safe_ids.update(item.decision_id for item in plan["safe"])
            _, _, failures, _ = render_application_plan(source, plan)
            failed_ids = {item["decision"].get("decision_id") for item in failures}
            renderable_ids.update(item.decision_id for item in plan["safe"]
                                  if item.decision_id not in failed_ids)
        for item in metadata:
            identifier = item["decision_id"]
            item.update(decision_constructible=True,
                        decision_validates=identifier in valid_ids,
                        application_plan_safe=identifier in safe_ids,
                        application_plan_renderable=identifier in renderable_ids)
            item["review_ready"] = all(item[key] for key in (
                "decision_constructible", "decision_validates",
                "application_plan_safe", "application_plan_renderable"))
            proofs.append(item)
        per_document.append({"document": document["id"],
                             "supported_checked": len(metadata),
                             "review_ready": sum(item["review_ready"] for item in metadata)})
    return {"cases": proofs, "per_document": per_document,
            "supported_checked": len(proofs),
            "review_ready": sum(item["review_ready"] for item in proofs)}


def wilson(successes, total, z=1.96):
    if not total:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total
                           + z * z / (4 * total * total)) / denominator
    return {"successes": successes, "total": total,
            "percent": round(100 * proportion, 1),
            "wilson_95_percent": [round(100 * (centre - margin), 1),
                                  round(100 * (centre + margin), 1)]}


def _mean(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 2) if values else None


def _gold_rows(details, occurrences, document_id):
    by_id = {item["id"]: item for item in occurrences}
    rows = []
    for detail in details:
        gold = detail["gold"]
        occurrence = by_id.get(detail["occurrence_id"])
        valid = set(gold.get("valid_semantic_macros", ()))
        ranked = detail.get("ranked_candidates", [])
        macros = [item["semantic_macro"] for item in ranked]
        positions = [index for index, macro in enumerate(macros, 1) if macro in valid]
        rows.append({"document_id": document_id, "gold": gold, "detail": detail,
                     "occurrence": occurrence, "valid": valid, "ranked": ranked,
                     "first_valid_position": min(positions) if positions else None})
    return rows


def _entry_ready(entry):
    rule = entry["classification"]
    return bool(rule.get("grammar_supported") and rule.get(
        "annotation_ready", rule.get("pattern_type") in {
            "binary_infix", "sequence", "unary", "command"}))


def _root_candidates(occurrence):
    ast = (occurrence or {}).get("ast") or {}
    return [candidate for candidate in (occurrence or {}).get("annotation_candidates", [])
            if candidate.get("start") == ast.get("start")
            and candidate.get("end") == ast.get("end")]


def _semantic_eligibility(row, catalog):
    gold, occurrence = row["gold"], row["occurrence"] or {}
    ast = occurrence.get("ast") or {}
    valid = row["valid"]
    already_semantic = (ast.get("kind") == "SemanticCommand"
                        and str(ast.get("value", "")).lstrip("\\") in valid)
    explicit_eligibility = gold.get("proposal_eligible", gold.get("should_propose"))
    proposal_eligible = bool(explicit_eligibility and valid and not already_semantic)
    entries = [entry for entry in catalog if entry.get("macro") in valid]
    visible_forms = {form for entry in entries
                     for form in [entry.get("visible"), *entry.get("variants", [])]
                     if form is not None}
    mapping_available = bool(proposal_eligible and (
        row["detail"]["correct_candidate_present"] or ast.get("value") in visible_forms))
    root_candidates = _root_candidates(occurrence)
    correctly_skipped = bool(already_semantic and not root_candidates)
    no_proposal_expected = bool(already_semantic or not gold.get("should_propose"))
    correctly_suppressed = bool(no_proposal_expected and not root_candidates)
    return {
        "already_semantic": already_semantic,
        "proposal_eligible": proposal_eligible,
        "mapping_available": mapping_available,
        "mapping_unavailable": proposal_eligible and not mapping_available,
        "already_semantic_correctly_skipped": correctly_skipped,
        "no_proposal_expected": no_proposal_expected,
        "correctly_suppressed": correctly_suppressed,
    }


def _candidate_miss_diagnostic(row, catalog):
    gold, occurrence = row["gold"], row["occurrence"] or {}
    ast = occurrence.get("ast") or {}
    valid = row["valid"]
    entries = [entry for entry in catalog if entry.get("macro") in valid]
    visible_forms = sorted({form for entry in entries
                            for form in [entry.get("visible"), *entry.get("variants", [])]
                            if form is not None})
    explicit_semantic = (ast.get("kind") == "SemanticCommand"
                         and str(ast.get("value", "")).lstrip("\\") in valid)
    categories = []
    if not entries:
        categories.extend(["A", "K"])
        stage = "repository/catalogue availability"
        reason = "No local catalogue record has any gold semantic macro."
        resolution = "Install the repository that supplies the gold semantics."
    elif explicit_semantic:
        categories.append("H")
        stage = "candidate generation for an already-semantic AST node"
        reason = ("The AST already contains the gold semantic command; proposal generation "
                  "matches presentation notation and does not propose a second wrapper.")
        resolution = ("Treat the source as already annotated; presentation-notation proposal "
                      "generation should not wrap the semantic command again.")
    elif ast.get("value") not in visible_forms:
        categories.extend(["E", "J"])
        stage = "visible-notation matching"
        reason = (f"AST notation {ast.get('value')!r} is absent from the catalogue spellings "
                  f"{visible_forms!r}; no semantic equivalence is configured.")
        resolution = ("Add an explicit user semantic mapping only after confirming that the "
                      "paper spelling is semantically equivalent to the catalogue notation.")
    else:
        categories.append("G")
        stage = "AST candidate matching"
        reason = "A supported catalogue spelling reached the AST but produced no candidate."
        resolution = "Investigate the generic AST matcher for this supported catalogue rule."
    repositories = sorted({entry.get("repository") for entry in entries if entry.get("repository")})
    modules = sorted({entry.get("module") for entry in entries if entry.get("module")})
    return {
        "document": row["document_id"],
        "file": gold.get("file"),
        "line": gold.get("line"),
        "math_region": gold.get("math_region"),
        "expression": gold["source_expression"],
        "normalized_expression": normalize_math_expression(gold["source_expression"]),
        "gold_semantics": sorted(valid),
        "visible_notation": ast.get("value"),
        "candidate_lookup_key": ast.get("value"),
        "ast_node_type": ast.get("kind"),
        "ast_subtree": ast,
        "raw_candidates_returned": [candidate.get("semantic_macro")
                                    for candidate in (occurrence.get("raw_candidates") or [])],
        "failure_category": categories,
        "pipeline_stage": stage,
        "reason_candidate_is_absent": reason,
        "expected_repositories": repositories,
        "expected_modules": modules,
        "gold_modules": modules,
        "catalogue_visible_forms": visible_forms,
        "catalogue_records_found": len(entries),
        "catalogue_records_annotation_ready": sum(_entry_ready(entry) for entry in entries),
        "annotation_ready_status": any(candidate.get("semantic_macro") in valid
                                       for candidate in occurrence.get("annotation_candidates", [])),
        "detected_scope": {key: ast.get(key) for key in ("start", "end", "text")},
        "recommended_resolution": resolution,
        "requires_code_change": categories == ["G"],
        "requires_repository": not entries,
        "requires_manual_config": "J" in categories,
    }


def evaluate_corpus(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    per_document, all_rows, ambiguity = [], [], []
    semantic_case_analysis, semantic_misses = [], []
    already_semantic_cases, false_proposals = [], []
    coverage_documents = []
    error_counts = Counter({category: 0 for category in ERROR_CATEGORIES})
    family_counts = defaultdict(lambda: Counter(total=0, top1=0))
    candidate_buckets = defaultdict(lambda: Counter(total=0, top1=0, reciprocal_sum=0))
    evidence_buckets = defaultdict(lambda: Counter(total=0, top1=0, reciprocal_sum=0,
                                                     candidate_sum=0))
    totals = Counter()
    macro_percentages = defaultdict(list)
    repository_records = scan_available_smglom()

    for item in manifest["documents"]:
        if not item.get("enabled", True):
            continue
        target = (base / item["path"]).resolve()
        gold_path = (base / item["gold"]).resolve()
        config_path = (base / item.get("config", "../expression-config.example.json")).resolve()
        summary, occurrences, details = evaluate(target, config_path, gold_path, apply=False)
        document_gold = json.loads(gold_path.read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))
        coverage_documents.append({"id": item["id"], "occurrences": occurrences,
                                   "gold": document_gold, "target": target,
                                   "config": config})
        catalog = build_catalog(repository_records, config)
        rows = _gold_rows(details, occurrences, item["id"])
        for row in rows:
            row["evaluation"] = _semantic_eligibility(row, catalog)
        all_rows.extend(rows)
        gold_summary = summary["gold"]
        document_semantic = [row for row in rows if row["valid"]]
        document_proposals = [row for row in document_semantic
                              if row["evaluation"]["proposal_eligible"]]
        document_supported = [row for row in document_proposals
                              if row["evaluation"]["mapping_available"]]
        document_already = [row for row in document_semantic
                            if row["evaluation"]["already_semantic"]]
        document_present = sum(row["detail"]["correct_candidate_present"]
                               for row in document_proposals)
        document_supported_present = sum(row["detail"]["correct_candidate_present"]
                                         for row in document_supported)
        document_top1 = sum(row["first_valid_position"] == 1 for row in document_proposals)
        document_top_hits = {cutoff: sum(bool(row["valid"] & {
            candidate["semantic_macro"] for candidate in row["ranked"][:cutoff]})
            for row in document_proposals) for cutoff in (1, 3, 5)}
        document_reciprocal = sum(1 / row["first_valid_position"]
                                  if row["first_valid_position"] else 0
                                  for row in document_proposals)
        document = {
            "id": item["id"], "path": item["path"], "description": item.get("description"),
            "math_regions": summary["math_region_extraction"]["total_extracted"],
            "parsed": summary["structural_parser"]["parsed"],
            "parser_coverage_percent": summary["structural_parser"]["coverage_percent"],
            "gold_occurrences": gold_summary["size"],
            "structurally_labeled": gold_summary["structurally_labeled"],
            "semantically_labeled": gold_summary["semantically_labeled"],
            "scope_accuracy_percent": gold_summary["scope_accuracy_percent"],
            "notation_recognition": summary["notation_recognition"],
            "legacy_candidate_recall": gold_summary["semantic_candidate_recall"],
            "candidate_recall": wilson(document_present, len(document_proposals)),
            "candidate_precision": gold_summary["semantic_candidate_precision"],
            "legacy_ranking": gold_summary["semantic_ranking"],
            "ranking": {
                **{f"top_{cutoff}_recall": wilson(document_top_hits[cutoff],
                                                   len(document_proposals))
                   for cutoff in (1, 3, 5)},
                "mean_reciprocal_rank": (round(document_reciprocal /
                                                len(document_proposals), 4)
                                         if document_proposals else None),
            },
            "annotation_ready": summary["annotation_ready_coverage"],
            "argument_accuracy_percent": gold_summary["argument_accuracy_percent"],
            "decision_validation": summary["review"],
            "semantic_evaluation": {
                "semantic_gold_cases": len(document_semantic),
                "proposal_eligible_cases": len(document_proposals),
                "mapping_available_cases": len(document_supported),
                "mapping_unavailable_cases": len(document_proposals) - len(document_supported),
                "candidate_present_cases": document_present,
                "proposal_candidate_recall": wilson(document_present, len(document_proposals)),
                "supported_candidate_recall": wilson(document_supported_present,
                                                      len(document_supported)),
                "top_1_recall": wilson(document_top1, len(document_proposals)),
                "already_semantic_cases": len(document_already),
                "already_semantic_correctly_skipped": sum(
                    row["evaluation"]["already_semantic_correctly_skipped"]
                    for row in document_already),
            },
        }
        per_document.append(document)
        totals.update(documents=1, math_regions=document["math_regions"], parsed=document["parsed"],
                      gold=document["gold_occurrences"], structural=gold_summary["structurally_labeled"],
                      scope_exact=gold_summary["exact_scope"], semantic=gold_summary["semantically_labeled"],
                      candidate_present=gold_summary["semantic_candidate_recall"]["present"],
                      precision_relevant=gold_summary["semantic_candidate_precision"]["relevant"],
                      precision_generated=gold_summary["semantic_candidate_precision"]["generated_on_labeled_gold"],
                      recognized=summary["notation_recognition"]["regions"],
                      ready=summary["annotation_ready_coverage"]["regions"],
                      arguments=gold_summary["argument_labeled"], correct_arguments=gold_summary["correct_arguments"],
                      proposal_eligible=len(document_proposals), mapping_available=len(document_supported),
                      proposal_present=document_present,
                      supported_present=document_supported_present,
                      already_semantic=len(document_already),
                      already_semantic_skipped=sum(
                          row["evaluation"]["already_semantic_correctly_skipped"]
                          for row in document_already),
                      no_proposal=sum(row["evaluation"]["no_proposal_expected"] for row in rows),
                      correctly_suppressed=sum(row["evaluation"]["correctly_suppressed"] for row in rows))
        for name, value in (("parser", document["parser_coverage_percent"]),
                            ("scope", document["scope_accuracy_percent"]),
                            ("recall", document["semantic_evaluation"]
                             ["proposal_candidate_recall"]["percent"]
                             if document["semantic_evaluation"]["proposal_candidate_recall"] else None),
                            ("top1", document["semantic_evaluation"]
                             ["top_1_recall"]["percent"]
                             if document["semantic_evaluation"]["top_1_recall"] else None)):
            macro_percentages[name].append(value)
        for reason, count in summary["blockers"].items():
            error_counts["annotation blocker"] += count

        for row in rows:
            gold, detail, occurrence = row["gold"], row["detail"], row["occurrence"]
            root = (occurrence or {}).get("ast") or {}
            root_ranked = [candidate for candidate in (occurrence or {}).get("ranked_candidates", [])
                           if candidate.get("start") == root.get("start")
                           and candidate.get("end") == root.get("end")]
            root_automatic = _root_candidates(occurrence)
            if row["evaluation"]["no_proposal_expected"] and root_automatic:
                tied = len({candidate.get("semantic_macro") for candidate in root_ranked}) > 1
                fallback = all(any(evidence.get("signal") == "fallback_only"
                                   for evidence in candidate.get("evidence", []))
                               for candidate in root_ranked)
                grouped_reason = ("ambiguous_candidate_set" if tied else
                                  "candidate_without_contextual_support" if fallback else
                                  "gold_negative_scope_has_root_candidate")
                false_proposals.append({
                    "document": row["document_id"], "file": gold.get("file"),
                    "line": gold.get("line"), "math_region": gold.get("math_region"),
                    "expression": gold["source_expression"],
                    "normalized_expression": normalize_math_expression(gold["source_expression"]),
                    "context": (occurrence or {}).get("source_line"),
                    "gold_decision": "NO PROPOSAL", "predicted_decision": "PROPOSAL",
                    "generated_candidates": [{key: candidate.get(key) for key in (
                        "semantic_macro", "module", "repository", "score", "rank", "evidence",
                        "matched_expression", "start", "end")}
                        for candidate in root_ranked],
                    "existing_semantic_ancestors": [],
                    "detected_scope": {key: root.get(key) for key in ("kind", "value", "start", "end", "text")},
                    "eligibility_checks": [candidate.get("proposal_eligibility")
                                           for candidate in root_ranked],
                    "reason_proposal_was_allowed": "root scope passed structural eligibility",
                    "failure_category": grouped_reason,
                })
            if row["valid"]:
                analysis = {
                    "document": row["document_id"], "file": gold.get("file"),
                    "line": gold.get("line"), "expression": gold["source_expression"],
                    "gold_semantics": sorted(row["valid"]),
                    "gold_candidate_in_raw_candidate_set": detail["correct_candidate_present"],
                    "rank": row["first_valid_position"],
                    "absence_reason": None,
                    **row["evaluation"],
                }
                if row["evaluation"]["already_semantic"]:
                    classification = _candidate_miss_diagnostic(row, catalog)
                    classification["high_level_classification"] = (
                        "already_semantic_correctly_skipped"
                        if row["evaluation"]["already_semantic_correctly_skipped"]
                        else "already_semantic_not_suppressed")
                    already_semantic_cases.append(classification)
                    analysis["absence_reason"] = classification["reason_candidate_is_absent"]
                elif (row["evaluation"]["proposal_eligible"]
                      and not detail["correct_candidate_present"]):
                    miss = _candidate_miss_diagnostic(row, catalog)
                    miss["high_level_classification"] = (
                        "semantic_mapping_unavailable"
                        if row["evaluation"]["mapping_unavailable"]
                        else "candidate_generation_failure")
                    analysis["absence_reason"] = miss["reason_candidate_is_absent"]
                    semantic_misses.append(miss)
                semantic_case_analysis.append(analysis)
            if gold.get("expected_root") is not None and not detail["scope_exact"]:
                error_counts["parser scope error"] += 1
            if occurrence and not occurrence["recognized_notation_nodes"]:
                error_counts["notation recognition miss"] += 1
            if (row["evaluation"]["proposal_eligible"]
                    and not detail["correct_candidate_present"]):
                error_counts["correct semantic candidate absent"] += 1
            if row["first_valid_position"] and row["first_valid_position"] > 1:
                error_counts["correct candidate ranked below top-1"] += 1
            if gold.get("expected_arguments") is not None and not detail["scope_exact"]:
                error_counts["argument extraction error"] += 1
            if not (row["evaluation"]["proposal_eligible"]
                    and row["evaluation"]["mapping_available"]):
                continue
            count = len(row["ranked"])
            bucket = "1" if count == 1 else "2-3" if count <= 3 else "4-6" if count <= 6 else "7+"
            reciprocal = 1 / row["first_valid_position"] if row["first_valid_position"] else 0
            candidate_buckets[bucket].update(total=1, top1=row["first_valid_position"] == 1,
                                             reciprocal_sum=reciprocal)
            contextual = any(candidate.get("contextual_evidence") for candidate in row["ranked"])
            evidence = "contextual" if contextual else "fallback_only"
            evidence_buckets[evidence].update(total=1, top1=row["first_valid_position"] == 1,
                                              reciprocal_sum=reciprocal, candidate_sum=count)
            top = row["ranked"][0] if row["ranked"] else {}
            family = f"{top.get('repository') or 'unknown'}/{top.get('module') or 'unscoped'}"
            family_counts[family].update(total=1, top1=row["first_valid_position"] == 1)
            if count > 1:
                ambiguity.append({"document_id": row["document_id"], "file": gold.get("file"),
                    "line": gold.get("line"), "expression": gold["source_expression"],
                    "candidate_count": count,
                    "ranked_candidates": [{key: candidate.get(key) for key in (
                        "semantic_macro", "module", "repository", "score", "rank", "evidence")}
                        for candidate in row["ranked"]],
                    "gold_valid_candidates": sorted(row["valid"]),
                    "top_ranked_candidate": top.get("semantic_macro"),
                    "top_ranked_is_valid": top.get("semantic_macro") in row["valid"]})

    coverage_audit = annotation_coverage_audit(coverage_documents)
    readiness_proof = production_review_readiness(coverage_documents, repository_records)
    coverage_audit["production_path_proof"] = readiness_proof
    proof_index = {(item["document"], item["file"], item["line"], item["source"]): item
                   for item in readiness_proof["cases"]}
    for gap in coverage_audit["gaps"]:
        proof = proof_index.get((gap["document"], gap["file"], gap["line"],
                                 gap["full_expression"]))
        if proof:
            gap["production_path_proof"] = proof
    coverage_audit["summary"]["review_ready_regions"] = readiness_proof["review_ready"]
    coverage_audit["summary"]["supported_review_coverage_percent"] = round(
        100 * readiness_proof["review_ready"] /
        coverage_audit["summary"]["supported_annotation_eligible_regions"], 1)
    proof_by_document = {item["document"]: item for item in readiness_proof["per_document"]}
    for item in coverage_audit["summary"]["per_document"]:
        item["review_ready"] = proof_by_document[item["document"]]["review_ready"]
    coverage_by_document = {item["document"]: item
                            for item in coverage_audit["summary"]["per_document"]}
    for document in per_document:
        document["region_annotation_coverage"] = coverage_by_document[document["id"]]
    semantic_rows = [row for row in all_rows if row["evaluation"]["proposal_eligible"]]
    supported_rows = [row for row in semantic_rows if row["evaluation"]["mapping_available"]]
    top_hits = {cutoff: sum(bool(row["valid"] & {item["semantic_macro"]
        for item in row["ranked"][:cutoff]}) for row in semantic_rows) for cutoff in (1, 3, 5)}
    supported_top_hits = {cutoff: sum(bool(row["valid"] & {item["semantic_macro"]
        for item in row["ranked"][:cutoff]}) for row in supported_rows) for cutoff in (1, 3, 5)}
    reciprocal = sum(1 / row["first_valid_position"] if row["first_valid_position"] else 0
                     for row in semantic_rows)
    application_report = ROOT / "evaluation" / "evaluation_summary.json"
    existing_application = (json.loads(application_report.read_text(encoding="utf-8")).get("application", {})
                            if application_report.exists() else {})
    error_counts["application conflict"] += (existing_application.get("same_span_conflicts", 0)
                                              + existing_application.get("partial_overlap_conflicts", 0))
    error_counts["stale decision"] += existing_application.get("stale", 0)
    missing = dependency_report()["missing_repositories"]
    error_counts["missing repository"] += len(missing)
    error_counts.setdefault("missing semantic metadata", 0)
    denominator = len(semantic_rows)
    supported_denominator = len(supported_rows)
    proposal_recall = wilson(totals["proposal_present"], totals["proposal_eligible"])
    supported_recall = wilson(totals["supported_present"], totals["mapping_available"])
    already_semantic_accuracy = wilson(totals["already_semantic_skipped"],
                                       totals["already_semantic"])
    aggregate = {
        "documents": totals["documents"], "math_regions": totals["math_regions"],
        "gold_occurrences": totals["gold"], "structurally_labeled": totals["structural"],
        "semantically_labeled": totals["semantic"],
        "parser_coverage": wilson(totals["parsed"], totals["math_regions"]),
        "scope_accuracy": wilson(totals["scope_exact"], totals["structural"]),
        "notation_recognition_coverage": wilson(totals["recognized"], totals["math_regions"]),
        "candidate_set_recall": proposal_recall,
        "candidate_set_precision_percent": round(100 * totals["precision_relevant"] /
            totals["precision_generated"], 1) if totals["precision_generated"] else None,
        **{f"top_{cutoff}_recall": wilson(top_hits[cutoff], denominator) for cutoff in (1, 3, 5)},
        "mean_reciprocal_rank": round(reciprocal / denominator, 4) if denominator else None,
        "annotation_ready_coverage": wilson(totals["ready"], totals["math_regions"]),
        "correct_argument_extraction": wilson(totals["correct_arguments"], totals["arguments"]),
        "macro_average_percent": {key: _mean(value) for key, value in macro_percentages.items()},
        "application": existing_application,
        "semantic_evaluation": {
            "semantic_gold_cases": totals["semantic"],
            "proposal_eligible_cases": totals["proposal_eligible"],
            "mapping_available_cases": totals["mapping_available"],
            "mapping_unavailable_cases": (totals["proposal_eligible"]
                                          - totals["mapping_available"]),
            "candidate_present_cases": totals["proposal_present"],
            "proposal_candidate_recall": proposal_recall,
            "supported_candidate_recall": supported_recall,
            "semantic_coverage": wilson(totals["mapping_available"],
                                        totals["proposal_eligible"]),
            "already_semantic_cases": totals["already_semantic"],
            "already_semantic_correctly_skipped": totals["already_semantic_skipped"],
            "already_semantic_accuracy": already_semantic_accuracy,
            "no_proposal_gold_cases": totals["no_proposal"],
            "correctly_suppressed": totals["correctly_suppressed"],
            "no_proposal_accuracy": wilson(totals["correctly_suppressed"],
                                           totals["no_proposal"]),
            "ranking": {
                **{f"top_{cutoff}_recall": wilson(top_hits[cutoff], denominator)
                   for cutoff in (1, 3, 5)},
                "mean_reciprocal_rank": (round(reciprocal / denominator, 4)
                                         if denominator else None),
            },
            "supported_only_ranking": {
                **{f"top_{cutoff}_recall": wilson(supported_top_hits[cutoff],
                                                   supported_denominator)
                   for cutoff in (1, 3, 5)},
                "mean_reciprocal_rank": (round(sum(
                    1 / row["first_valid_position"] if row["first_valid_position"] else 0
                    for row in supported_rows) / supported_denominator, 4)
                    if supported_denominator else None),
            },
        },
        "annotation_evaluation": {
            "total_math_regions": totals["math_regions"],
            "gold_semantic_targets": totals["semantic"],
            "proposal_eligible_targets": totals["proposal_eligible"],
            "no_proposal_gold_scopes": totals["no_proposal"],
            "annotation_ready_regions_all_math": totals["ready"],
            "unsupported_semantic_targets": (totals["proposal_eligible"]
                                             - totals["mapping_available"]),
            "regions_without_a_semantic_gold_target": max(
                0, totals["math_regions"] - totals["semantic"]),
            "eligible_annotation_ready_coverage": wilson(
                totals["proposal_present"], totals["proposal_eligible"]),
            "note": ("The original annotation-ready metric uses all extracted math regions; "
                     "the eligible metric uses proposal-eligible semantic gold targets."),
        },
        "region_annotation_coverage": coverage_audit["summary"],
    }
    breakdown = {
        "candidate_count": {key: {"occurrences": value["total"],
            "top_1_percent": round(100 * value["top1"] / value["total"], 1),
            "mrr": round(value["reciprocal_sum"] / value["total"], 4)}
            for key, value in sorted(candidate_buckets.items())},
        "context_evidence": {key: {"occurrences": value["total"],
            "top_1_percent": round(100 * value["top1"] / value["total"], 1),
            "mrr": round(value["reciprocal_sum"] / value["total"], 4),
            "average_candidate_count": round(value["candidate_sum"] / value["total"], 2)}
            for key, value in sorted(evidence_buckets.items())},
        "semantic_family": {key: {"occurrences": value["total"],
            "top_1_percent": round(100 * value["top1"] / value["total"], 1)}
            for key, value in sorted(family_counts.items())},
    }
    miss_category_counts = Counter(category for miss in semantic_misses
                                   for category in miss["failure_category"])
    high_level_counts = Counter(miss["high_level_classification"] for miss in semantic_misses)
    high_level_counts.update(case["high_level_classification"]
                             for case in already_semantic_cases)
    false_proposal_reasons = Counter(item["failure_category"] for item in false_proposals)
    non_semantic = Counter()
    for row in all_rows:
        if row["valid"]:
            continue
        gold = row["gold"]
        ast_kind = ((row["occurrence"] or {}).get("ast") or {}).get("kind")
        if gold.get("semantic_status") == "ambiguous":
            category = "ambiguous notation with no approved semantic mapping"
        elif gold.get("semantic_status") == "structural_only":
            category = "structural container only"
        elif ast_kind == "ScriptedExpression":
            category = "structural script with no semantic proposal expected"
        elif ast_kind == "BoundExpression":
            category = "required semantic arguments unavailable"
        else:
            category = "unsupported semantic family or no candidate expected"
        non_semantic[category] += 1
    return aggregate, per_document, ambiguity, {
        "counts": dict(sorted(error_counts.items())),
        "missing_repositories": sorted(missing),
        "semantic_candidate_summary": {
            "semantic_gold_cases": len(semantic_case_analysis),
            "proposal_eligible_cases": totals["proposal_eligible"],
            "candidate_present": totals["proposal_present"],
            "proposal_candidate_absent": len(semantic_misses),
            "already_semantic_cases": len(already_semantic_cases),
            "already_semantic_correctly_skipped": sum(
                case["high_level_classification"] == "already_semantic_correctly_skipped"
                for case in already_semantic_cases),
            "absent_by_failure_category": dict(sorted(miss_category_counts.items())),
            "high_level_classifications": dict(sorted(high_level_counts.items())),
        },
        "semantic_gold_case_analysis": semantic_case_analysis,
        "semantic_candidate_misses": semantic_misses,
        "already_semantic_cases": already_semantic_cases,
        "false_proposals": false_proposals,
        "false_proposal_summary": {
            "count": len(false_proposals),
            "grouped_reasons": dict(sorted(false_proposal_reasons.items())),
            "examples": [item["expression"] for item in false_proposals],
        },
        "failure_category_definitions": {
            "E": "visible notation mismatch: AST notation is not a declared or configured spelling",
            "J": "missing user configuration: no explicit semantic mapping validates the surface spelling",
        },
        "gold_schema_audit": {
            "migration_applied_to_frozen_gold": False,
            "eligibility_rule": ("proposal_eligible uses explicit proposal_eligible when present, "
                                 "otherwise legacy should_propose, and excludes a verified "
                                 "SemanticCommand root"),
            "legacy_already_semantic_records": [{
                "document": case["document"],
                "file": case["file"],
                "line": case["line"],
                "expression": case["expression"],
                "old_should_propose": True,
                "derived_proposal_eligible": False,
                "reason": "source AST root is already semantic; a second wrapper is incorrect",
            } for case in already_semantic_cases],
        },
        "non_semantic_gold_summary": {
            "total": sum(non_semantic.values()),
            "categories": dict(sorted(non_semantic.items())),
            "label_audit": {
                "structural_only": sum(bool(row["gold"].get("expected_root"))
                                       for row in all_rows if not row["valid"]),
                "already_semantic": 0,
                "negative_or_no_proposal": sum(not row["gold"].get("should_propose", False)
                                               for row in all_rows if not row["valid"]),
                "annotation_only": sum(bool(row["gold"].get("expected_annotation"))
                                       and not row["gold"].get("expected_root")
                                       for row in all_rows if not row["valid"]),
                "other_partial": 0,
            },
        },
        "annotation_coverage_audit": coverage_audit,
    }, breakdown


def _display_verbose_failures(errors):
    for miss in errors["semantic_candidate_misses"]:
        print("\nSEMANTIC MAPPING UNAVAILABLE")
        fields = (
            ("Document", miss["document"]), ("File", miss["file"]),
            ("Line", miss["line"]), ("Math region", miss["math_region"]),
            ("Surface expression", miss["expression"]),
            ("Normalized expression", miss["normalized_expression"]),
            ("Gold semantic macro", ", ".join(miss["gold_semantics"])),
            ("Gold module", ", ".join(miss["gold_modules"])),
            ("Detected scope", json.dumps(miss["detected_scope"], ensure_ascii=False)),
            ("Candidate lookup key", miss["candidate_lookup_key"]),
            ("Relevant indexed symbols", ", ".join(miss["catalogue_visible_forms"])),
            ("Reason mapping was considered unavailable", miss["reason_candidate_is_absent"]),
            ("Failure category", ", ".join(miss["failure_category"])),
        )
        for label, value in fields:
            print(f"{label}: {value}")
    for failure in errors["false_proposals"]:
        print("\nFALSE PROPOSAL")
        fields = (
            ("Document", failure["document"]), ("File", failure["file"]),
            ("Line", failure["line"]), ("Math region", failure["math_region"]),
            ("Expression", failure["expression"]),
            ("Normalized expression", failure["normalized_expression"]),
            ("Context", failure["context"]), ("Gold decision", failure["gold_decision"]),
            ("Predicted decision", failure["predicted_decision"]),
            ("Generated candidates", json.dumps(failure["generated_candidates"], ensure_ascii=False)),
            ("Candidate scores/ranks", ", ".join(
                f"{item['semantic_macro']}={item.get('score')}@{item.get('rank')}"
                for item in failure["generated_candidates"])),
            ("Existing semantic ancestors", failure["existing_semantic_ancestors"]),
            ("Detected scope", json.dumps(failure["detected_scope"], ensure_ascii=False)),
            ("Eligibility checks", json.dumps(failure["eligibility_checks"], ensure_ascii=False)),
            ("Reason proposal was allowed", failure["reason_proposal_was_allowed"]),
            ("Failure category", failure["failure_category"]),
        )
        for label, value in fields:
            print(f"{label}: {value}")
    print("\nFAILURE SUMMARY")
    mapping = errors["semantic_candidate_summary"]
    print(f"Semantic mapping unavailable: {mapping['proposal_candidate_absent']}")
    for reason, count in errors["false_proposal_summary"]["grouped_reasons"].items():
        print(f"False proposals ({reason}): {count}")
    definitions = errors["failure_category_definitions"]
    for category in ("E", "J"):
        count = mapping["absent_by_failure_category"].get(category, 0)
        print(f"Failure {category}: {count} - {definitions[category]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose-failures", action="store_true",
                        help="Print detailed mapping and false-proposal diagnostics")
    args = parser.parse_args(argv)
    summary, documents, ambiguity, errors, breakdown = evaluate_corpus(args.corpus)
    args.output.mkdir(parents=True, exist_ok=True)
    error_output = dict(errors)
    coverage_output = error_output.pop("annotation_coverage_audit")
    outputs = {"corpus_summary.json": {"aggregate": summary, "breakdown": breakdown},
               "per_document_metrics.json": documents,
               "ambiguity_report.json": ambiguity,
               "error_analysis.json": error_output,
               "annotation_coverage_gaps.json": coverage_output}
    for name, content in outputs.items():
        (args.output / name).write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(outputs["corpus_summary.json"], indent=2))
    else:
        print(f"Documents: {summary['documents']}")
        print(f"Math regions: {summary['math_regions']}")
        print(f"Gold occurrences: {summary['gold_occurrences']}")
        def proportion(label, metric):
            print(f"{label}: {metric['percent']}% "
                  f"({metric['successes']}/{metric['total']})")

        semantic = summary["semantic_evaluation"]
        print("\nSTRUCTURE")
        proportion("Structural coverage", summary["parser_coverage"])
        proportion("Scope accuracy", summary["scope_accuracy"])
        print("\nSEMANTIC AVAILABILITY")
        print(f"Semantic gold cases: {semantic['semantic_gold_cases']}")
        print(f"Proposal-eligible semantic cases: {semantic['proposal_eligible_cases']}")
        print(f"Semantic mapping available: {semantic['mapping_available_cases']}")
        print(f"Semantic mapping unavailable: {semantic['mapping_unavailable_cases']}")
        print("\nCANDIDATE GENERATION")
        proportion("Proposal candidate recall", semantic["proposal_candidate_recall"])
        proportion("Supported-semantics candidate recall", semantic["supported_candidate_recall"])
        proportion("Semantic coverage", semantic["semantic_coverage"])
        print("\nRANKING")
        proportion("Top-1", semantic["ranking"]["top_1_recall"])
        proportion("Top-3", semantic["ranking"]["top_3_recall"])
        proportion("Top-5", semantic["ranking"]["top_5_recall"])
        print(f"MRR: {semantic['ranking']['mean_reciprocal_rank']}")
        print("\nNO-PROPOSAL BEHAVIOR")
        print(f"Already-semantic cases: {semantic['already_semantic_cases']}")
        proportion("Already-semantic correctly skipped", semantic["already_semantic_accuracy"])
        proportion("All no-proposal cases correctly suppressed", semantic["no_proposal_accuracy"])
        print("\nANNOTATION")
        proportion("Annotation-ready coverage", summary["annotation_ready_coverage"])
        print("\nAPPLICATION")
        application = summary["application"]
        print(f"Application success: {application.get('applied', 0)}/"
              f"{application.get('eligible_current_decisions', application.get('validated_current', 0))}")
        miss_summary = errors["semantic_candidate_summary"]
        print(f"Candidate present: {miss_summary['candidate_present']}")
        print(f"Proposal candidate absent: {miss_summary['proposal_candidate_absent']}")
        for category, count in miss_summary["absent_by_failure_category"].items():
            print(f"Failure category {category}: {count}")
        if args.verbose_failures:
            _display_verbose_failures(errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
