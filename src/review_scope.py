"""Human-facing projections of internal expression matches.

The parser AST remains the source of spans and structure.  This module only
turns stable shared-matcher records into compact review cards.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewScope:
    file: Path
    line: int
    column: int
    full_math_region: str
    context_text: str
    matched_start: int
    matched_end: int
    math_region_start: int
    matched_source: str
    classification: dict
    simple_structure: str
    arguments: tuple
    argument_roles: tuple
    semantic_candidates: tuple
    ranking_evidence: tuple
    already_semantic: bool
    structurally_ready: bool
    semantic_mapping_available: bool
    semantic_status: str
    review_ready: bool
    nesting_depth: int
    highlighted: str
    ast_debug: str = ""
    match: dict = None


def _candidate_identity(candidate):
    rule = candidate.get("classification", {})
    return (candidate.get("macro", candidate.get("semantic_macro")),
            candidate.get("module"), candidate.get("repository"),
            rule.get("pattern_type"), rule.get("grammar_adapter"))


def _compatible_candidates(match, candidates):
    rule = match.get("classification", {})
    pattern = match.get("pattern_type") or rule.get("pattern_type")
    adapter = match.get("adapter_family") or rule.get("grammar_adapter")
    visible = tuple(rule.get(key) for key in
                    ("operator", "separator", "command", "token", "components"))
    compatible = []
    seen = set()
    for candidate in candidates:
        candidate_rule = candidate.get("classification", {})
        if candidate_rule.get("pattern_type") != pattern:
            continue
        if adapter and candidate_rule.get("grammar_adapter") != adapter:
            continue
        candidate_visible = tuple(candidate_rule.get(key) for key in
                                  ("operator", "separator", "command", "token", "components"))
        if candidate_visible != visible:
            continue
        identity = _candidate_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        compatible.append(candidate)
    return tuple(compatible)


def _roles(match, argument_count):
    roles = tuple(match.get("argument_roles") or
                  match.get("classification", {}).get("argument_roles") or ())
    if len(roles) == argument_count:
        return roles
    pattern = match.get("pattern_type") or match.get("classification", {}).get("pattern_type")
    if argument_count == 2 and pattern in {"binary_infix", "sequence"}:
        return ("left", "right")
    return tuple(f"argument_{index}" for index in range(1, argument_count + 1))


def _structure_label(match, candidates, argument_count):
    rule = match.get("classification", {})
    label = rule.get("structural_name") or next(
        (candidate.get("name") for candidate in candidates if candidate.get("name")), None)
    label = label or rule.get("pattern_type") or "expression"
    label = str(label).replace("_", " ")
    if rule.get("pattern_type") == "sequence" and argument_count > 2 and "sequence" not in label:
        label += " sequence"
    return label


def highlight_source(full_math_region, region_start, match_start, match_end,
                     open_marker="[", close_marker="]"):
    """Highlight one exact document span without reconstructing source text."""
    left, right = match_start - region_start, match_end - region_start
    if left < 0 or right < left or right > len(full_math_region):
        return full_math_region
    return (full_math_region[:left] + open_marker + full_math_region[left:right]
            + close_marker + full_math_region[right:])


def build_review_scope(match, candidates):
    """Project a stable shared match into a terminal-friendly review scope."""
    compatible = _compatible_candidates(match, candidates)
    arguments = tuple(match.get("arguments", ()))
    roles = _roles(match, len(arguments))
    start = match.get("source_start", match.get("start", 0))
    end = match.get("source_end", match.get("end", start))
    region_start = match.get("math_region_start", start)
    raw_region = match.get("full_math_region", match.get("source_expression",
                                                          match.get("matched_expression", "")))
    leading = len(raw_region) - len(raw_region.lstrip())
    full_region = raw_region.strip()
    display_region_start = region_start + leading
    matched = match.get("source_expression", match.get("raw_source",
                                                         match.get("matched_expression", "")))
    evidence = tuple({"macro": candidate.get("macro", candidate.get("semantic_macro")),
                      "evidence": tuple(candidate.get("evidence", ()))}
                     for candidate in compatible)
    structurally_ready = bool(matched and end > start and
                              (arguments or match.get("structural_node")) and
                              match.get("proposal_eligibility", {}).get("eligible", True))
    semantic_mapping_available = bool(compatible)
    semantic_status = rule_status = match.get("classification", {}).get(
        "semantic_unavailable_status")
    if not semantic_status:
        semantic_status = ("semantic candidates available" if semantic_mapping_available
                           else "semantic metadata unavailable locally")
    return ReviewScope(
        file=Path(match.get("file") or ""), line=match.get("line", 1),
        column=match.get("column", 1), full_math_region=full_region,
        context_text=match.get("text", ""), matched_start=start, matched_end=end,
        math_region_start=region_start,
        matched_source=matched, classification=dict(match.get("classification", {})),
        simple_structure=_structure_label(match, compatible, len(arguments)),
        arguments=arguments, argument_roles=roles, semantic_candidates=compatible,
        ranking_evidence=evidence,
        already_semantic=bool(match.get("already_semantic", False)),
        structurally_ready=structurally_ready,
        semantic_mapping_available=semantic_mapping_available,
        semantic_status=semantic_status,
        review_ready=bool(semantic_mapping_available and structurally_ready),
        nesting_depth=int(match.get("ast_depth", 0)),
        highlighted=highlight_source(full_region, display_region_start, start, end),
        ast_debug=match.get("ast_debug", ""), match=match)


def build_review_scopes(matches, candidates, order="innermost", include_structural=False):
    """Build, deduplicate and deterministically order reviewable scopes."""
    scopes, seen = [], set()
    for match in matches:
        scope = build_review_scope(match, candidates)
        if scope.already_semantic or not (scope.review_ready or
                                          (include_structural and scope.structurally_ready)):
            continue
        structural = tuple(scope.classification.get(key) for key in
                           ("pattern_type", "grammar_adapter", "operator", "separator",
                            "command", "token"))
        key = (str(scope.file), scope.matched_start, scope.matched_end, structural)
        if key in seen:
            continue
        seen.add(key)
        scopes.append(scope)
    reverse_depth = order != "outermost"
    if reverse_depth:
        scopes.sort(key=lambda item: (str(item.file), item.math_region_start,
                                     -item.nesting_depth, item.matched_start,
                                     item.matched_end - item.matched_start))
    else:
        scopes.sort(key=lambda item: (str(item.file), item.math_region_start,
                                     item.nesting_depth, item.matched_start,
                                     -(item.matched_end - item.matched_start)))
    return scopes


def render_review_scope(scope, number=None, total=None, show_ast=False):
    """Return a normal-mode review card; full AST output is opt-in."""
    lines = ["=" * 60]
    if number is not None and total is not None:
        lines.append(f"Review {number} of {total}")
    lines.extend((f"File: {scope.file.name}", f"Line: {scope.line}  Column: {scope.column}",
                  "", "Original formula:", scope.full_math_region,
                  "", "Matched scope:", scope.matched_source,
                  "", "Highlighted:", scope.highlighted,
                  "", "Structure:", scope.simple_structure, "", "Arguments:"))
    role_labels = {"^": "exponent", "_": "subscript",
                   **scope.classification.get("review_argument_labels", {})}
    for role, argument in zip(scope.argument_roles, scope.arguments):
        label = role_labels.get(role, role)
        if label.startswith("argument_") and label[9:].isdigit():
            lines.append(f"  {label[9:]}. {argument}")
        else:
            lines.append(f"  {label}: {argument}")
    if scope.semantic_mapping_available:
        lines.extend(("", "Semantic candidates:"))
        for index, candidate in enumerate(scope.semantic_candidates, 1):
            macro = candidate.get("macro", candidate.get("semantic_macro"))
            lines.append(f"[{index}] \\{macro}")
            lines.append(f"    module: {candidate.get('module')}")
            source = candidate.get("semantic_source", candidate.get("source"))
            if source:
                lines.append(f"    source: {source}")
            evidence = ", ".join(item.get("signal", "") for item in candidate.get("evidence", ()))
            if evidence:
                lines.append(f"    evidence: {evidence}")
    else:
        lines.extend(("", "Semantic status:", scope.semantic_status))
        lines.extend(("", "Annotation status:", "Not annotation-ready."))
    if show_ast and scope.ast_debug:
        lines.extend(("", "Internal AST (debug):", scope.ast_debug))
    return "\n".join(lines)
