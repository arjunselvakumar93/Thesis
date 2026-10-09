"""Human-facing projections of internal expression matches.

The parser AST remains the source of spans and structure.  This module only
turns stable shared-matcher records into compact review cards.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewItem:
    file: Path
    line: int
    column: int
    full_math_region: str
    context_text: str
    math_region_start: int
    matched_start: int
    matched_end: int
    node: object
    simple_structure: str
    arguments: tuple
    argument_roles: tuple
    classification: dict
    semantic_candidates: tuple
    already_semantic: bool
    structurally_ready: bool
    semantic_status: str
    review_state: str
    nesting_depth: int
    highlighted: str
    precedence_confidence: str
    ast_debug: str = ""
    match: dict = None

    @property
    def matched_source(self):
        source = self.match.get("document_source")
        if source is not None:
            return source[self.matched_start:self.matched_end]
        region = self.match.get("full_math_region", self.full_math_region)
        return region[self.matched_start - self.math_region_start:
                      self.matched_end - self.math_region_start]

    @property
    def semantic_mapping_available(self):
        return bool(self.semantic_candidates)

    @property
    def review_ready(self):
        return self.structurally_ready and bool(self.semantic_candidates)


def _node_structure(node, configuration):
    return (configuration.get("review_labels", {}).get(node.value)
            or getattr(node.metadata, "name", "")
            or {"Atom": "symbol", "Number": "number", "SymbolAtom": "symbol",
                "ScriptedExpression": "scripted expression",
                "FunctionApplication": "function application",
                "BinaryOperator": "binary operator", "NaryOperator": "operator sequence",
                "UncertainOperatorChain": "uncertain operator chain",
                "BoundExpression": "bound expression",
                "BigOperatorApplication": "big operator application",
                "BinderApplication": "binder application",
                "OpaqueCommandApplication": "opaque command application"}.get(
                    node.kind, node.kind.replace("_", " ").lower()))


def _precedence_confidence(node):
    source = str(getattr(getattr(node, "metadata", None),
                         "precedence_source", "") or "").lower()
    if not source:
        return "not applicable"
    if "smglom" in source:
        return "SMGloM-derived"
    if "unknown" in source:
        return "unknown"
    if "config" in source or "policy" in source:
        return "configuration-dependent"
    if "built" in source:
        return "built-in structural grammar"
    return source


def _cuts_composite_boundary(root, position):
    """Reject slices that remove a required part of an atomic composite."""
    atomic = {"FunctionApplication", "ScriptedExpression", "BoundExpression",
              "BigOperatorApplication", "BinderApplication", "Command",
              "SemanticCommand", "OpaqueCommandApplication"}
    return any(node.kind in atomic and node.start < position < node.end
               for node in root.walk())


def scope_alternatives(scope, configuration, limit=7):
    """Return meaningful exact AST ranges around the suggested target."""
    root = (scope.match or {}).get("region_tree")
    target = (scope.match or {}).get("_ast_node_object")
    if root is None or target is None:
        return ()
    allowed = {"ScriptedExpression", "FunctionApplication", "BinaryOperator", "NaryOperator",
               "UncertainOperatorChain", "UnaryOperator", "Command",
               "SemanticCommand", "OpaqueCommandApplication", "BoundExpression",
               "BigOperatorApplication", "BinderApplication"}
    parents = {id(child): parent for parent in root.walk() for child in parent.children}

    def nearest_meaningful_descendants(node):
        if node.kind in allowed:
            return (node,)
        return tuple(found for child in node.children
                     for found in nearest_meaningful_descendants(child))

    ancestors, parent = [], parents.get(id(target))
    while parent is not None:
        if parent.kind in allowed:
            ancestors.append(parent)
        parent = parents.get(id(parent))
    child_pairs = target.named_children()
    if target.kind == "ScriptedExpression":
        # A script's base can be a meaningful expression target. Script
        # payloads are not alternative ranges for the scripted occurrence.
        child_pairs = tuple(item for item in child_pairs if item[0] == "base")
    descendants = tuple(found for _, child in child_pairs
                        for found in nearest_meaningful_descendants(child))
    related_nodes = (*ancestors[:2], *descendants)

    items, seen = [], set()
    for node in related_nodes:
        if node.end <= node.start or node is target:
            continue
        absolute_start = scope.math_region_start + node.start
        absolute_end = scope.math_region_start + node.end
        key = (absolute_start, absolute_end)
        if key in seen:
            continue
        seen.add(key)
        if ancestors and node is ancestors[0]:
            relationship = 'parent'
        elif node in ancestors:
            relationship = 'ancestor'
        else:
            relationship = 'child'
        items.append(dict(start=absolute_start, end=absolute_end,
                          node=node, relationship=relationship))
    # Ambiguous precedence can make a plausible range absent from the chosen
    # tree shape. Reparse only complete source-boundary combinations formed by
    # existing nodes; invalid or partial TeX never becomes an option.
    from expression_parser import ExpressionParser, ParseError
    from expression_parser import NotationRegistry
    parser = ExpressionParser(registry=NotationRegistry.from_config(configuration))
    boundaries = sorted({node.start for node in root.walk()} | {node.end for node in root.walk()})
    for left in boundaries:
        for right in boundaries:
            if right <= left or not (left <= target.start < target.end <= right
                                     or target.start <= left < right <= target.end):
                continue
            absolute_start = scope.math_region_start + left
            absolute_end = scope.math_region_start + right
            if ((absolute_start, absolute_end) in seen
                    or (absolute_start, absolute_end) ==
                       (scope.matched_start, scope.matched_end)):
                continue
            if _cuts_composite_boundary(root, left) or _cuts_composite_boundary(root, right):
                continue
            raw = root.source[left:right]
            try:
                parsed = parser.parse(raw)
            except ParseError:
                continue
            if parsed.text != raw or parsed.kind in {"Atom", "Number"}:
                continue
            seen.add((absolute_start, absolute_end))
            items.append(dict(start=absolute_start, end=absolute_end,
                              node=parsed, relationship='reparsed_boundary'))
    def quality(item):
        same_head = (item['node'].value == target.value
                     or bool(target.notation and item['node'].notation == target.notation))
        relation_order = {'parent': 1, 'child': 2,
                          'ancestor': 3,
                          'reparsed_boundary': 4}
        relation = 0 if same_head else relation_order[item['relationship']]
        distance = abs(item['start'] - scope.matched_start) + abs(item['end'] - scope.matched_end)
        secondary = item['start'] if item['relationship'] == 'child' else distance
        return relation, secondary, item['end'] - item['start'], item['start']
    items.sort(key=quality)
    return tuple(items[:limit])


def match_for_alternative(scope, alternative):
    """Create the actual source-preserving review target for a chosen range."""
    match = dict(scope.match or {})
    node = alternative['node']
    arguments = tuple(child.text[1:-1] if child.kind == "GroupedExpression"
                      and child.value in {"{", "[", "("} else child.text
                      for child in node.children)
    roles = node.roles or (("left", "right") if node.kind == "BinaryOperator" else ())
    document = match.get("document_source", "")
    if document:
        before = document[:alternative['start']]
        match["line"] = before.count("\n") + 1
        match["column"] = alternative['start'] - before.rfind("\n")
    match.update(
        source_start=alternative['start'], source_end=alternative['end'],
        source_expression=alternative['node'].text,
        matched_expression=alternative['node'].text,
        arguments=list(arguments), argument_roles=list(roles),
        pattern_type="structural", structural_node=True,
        ast_node={"kind": node.kind, "value": node.value,
                  "start": node.start, "end": node.end, "text": node.text,
                  "roles": list(node.roles)},
        _ast_node_object=node,
        classification={"pattern_type": "structural", "node_kind": node.kind,
                        "structural_name": _node_structure(node, {}),
                        "semantic_unavailable_status":
                            "No semantic mapping is available in the loaded catalogue or configuration."},
        proposal_eligibility={"eligible": True},
        review_target_selection="user_corrected")
    return match


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
    matched = match.get("source_expression", match.get("matched_expression", ""))
    structurally_ready = bool(matched and end > start and
                              (arguments or match.get("structural_node")) and
                              match.get("proposal_eligibility", {}).get("eligible", True))
    semantic_mapping_available = bool(compatible)
    semantic_status = rule_status = match.get("classification", {}).get(
        "semantic_unavailable_status")
    if not semantic_status:
        semantic_status = ("semantic candidates available" if semantic_mapping_available
                           else "semantic metadata unavailable locally")
    uncertain = bool(match.get("_ast_node_object") is not None and
                     (match["_ast_node_object"].kind == "UncertainOperatorChain"
                      or getattr(match["_ast_node_object"].metadata,
                                 "uncertain_chain", False)))
    if uncertain and semantic_mapping_available:
        semantic_status = ("semantic candidates available; grouping uses structural policy "
                           "and remains semantically uncertain")
    review_ready = bool(semantic_mapping_available and structurally_ready)
    if not structurally_ready:
        review_state = "UNSUPPORTED"
    elif not semantic_mapping_available:
        review_state = "STRUCTURAL_ONLY"
    elif uncertain:
        review_state = "SEMANTIC_UNCERTAIN"
    elif review_ready:
        review_state = "ANNOTATION_READY"
    else:
        review_state = "SEMANTIC_CANDIDATES_AVAILABLE"
    return ReviewItem(
        file=Path(match.get("file") or ""), line=match.get("line", 1),
        column=match.get("column", 1), full_math_region=full_region,
        context_text=match.get("text", ""), math_region_start=region_start,
        matched_start=start, matched_end=end, node=match.get("_ast_node_object"),
        simple_structure=_structure_label(match, compatible, len(arguments)),
        arguments=arguments, argument_roles=roles,
        classification=dict(match.get("classification", {})),
        semantic_candidates=compatible,
        precedence_confidence=_precedence_confidence(match.get("_ast_node_object")),
        already_semantic=bool(match.get("already_semantic", False)),
        structurally_ready=structurally_ready,
        semantic_status=semantic_status, review_state=review_state,
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
                  "", "Suggested scope:", scope.matched_source,
                  "", "Highlighted:", scope.highlighted,
                  "", "Structure:", scope.simple_structure,
                  "", "Review state:", scope.review_state))
    if scope.precedence_confidence in {"configuration-dependent", "unknown"}:
        lines.extend(("", "Precedence confidence:",
                      scope.precedence_confidence))
    lines.extend(("", "Arguments:"))
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
