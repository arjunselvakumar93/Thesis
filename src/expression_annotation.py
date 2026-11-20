"""Match AST scopes, check eligibility and rank semantic candidates."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, asdict
import re
from expression_parser import canonicalize_tex_notation
from expression_parser import Operator
from notation_catalog import match_adapter_expression

# Structural proposal eligibility independent of semantic ranking and gold data.


SEMANTIC_CONTEXT_SIGNALS = {
    "module_imported_in_document", "local_semantic_module",
    "same_document_macro_history", "same_document_module_history",
    "session_preferred_module", "session_recent_family",
}
NOTATION_SIGNALS = {"exact_visible_notation", "notation_alias_match",
                    "configured_notation_alias"}


def semantic_ancestors(root, start, end):
    """Return semantic AST nodes that fully contain a proposed source span."""
    if root is None:
        return []
    return [node for node in root.walk()
            if node.kind == "SemanticCommand"
            and node.start <= start and end <= node.end]


def proposal_eligibility(root, start, end):
    """Reject invalid scopes and annotations nested in existing semantic markup."""
    if root is None:
        return {"eligible": False, "reason": "unparsed_scope", "semantic_ancestors": []}
    if start is None or end is None or start < 0 or end <= start or end > len(root.source):
        return {"eligible": False, "reason": "invalid_scope", "semantic_ancestors": []}
    ancestors = semantic_ancestors(root, start, end)
    if ancestors:
        return {"eligible": False, "reason": "inside_existing_semantic_annotation",
                "semantic_ancestors": [node.text for node in ancestors]}
    return {"eligible": True, "reason": "structurally_eligible", "semantic_ancestors": []}


def assess_proposal_candidates(ranked_candidates, context_sensitive=False):
    """Classify auto-proposal support while retaining all retrieved candidates."""
    if not ranked_candidates:
        return {"status": "unsupported_notation", "automatic_candidates": [],
                "evidence": []}
    best_rank = min(item.get("rank", 1) for item in ranked_candidates)
    best = [item for item in ranked_candidates if item.get("rank", 1) == best_rank]
    signals = {e["signal"] for item in best for e in item.get("evidence", ())}
    contextual = bool(signals & SEMANTIC_CONTEXT_SIGNALS)
    notation = bool(signals & NOTATION_SIGNALS)
    if not notation and not contextual:
        reason = "ambiguous_candidate_set" if len(best) > 1 else "insufficient_evidence"
        return {"status": reason, "automatic_candidates": [],
                "evidence": sorted(signals)}
    if len(best) > 1 and not contextual:
        return {"status": "ambiguous_candidate_set", "automatic_candidates": [],
                "evidence": sorted(signals)}
    if context_sensitive and not contextual and "configured_notation_alias" not in signals:
        return {"status": "contextual_support_required", "automatic_candidates": [],
                "evidence": sorted(signals)}
    return {"status": "supported", "automatic_candidates": best,
            "evidence": sorted(signals)}


# Deterministic, explainable ranking for semantic annotation candidates.
#
# This module consumes candidate metadata and context only.  It never reads
# evaluation gold labels and never removes a distinct semantic interpretation.


@dataclass(frozen=True)
class RankingPolicy:
    exact_visible: float = 1.0
    alias_visible: float = 0.75
    structural_compatibility: float = 0.5
    imported_module: float = 3.0
    local_semantic_module: float = 2.0
    same_document_macro_history: float = 3.5
    same_document_module_history: float = 1.5
    preferred_module: float = 2.5
    recent_family: float = 1.0


DEFAULT_POLICY = RankingPolicy()
MODULE_REFERENCE = re.compile(
    r"\\(?:usemodule|importmodule|requiremodule)\s*"
    r"(?:\[\s*smglom/(?P<repository>[^\]/\s]+)\s*\])?\s*"
    r"\{\s*(?:mod\?)?(?P<module>[^}\s]+)\s*\}"
)
SEMANTIC_COMMAND = re.compile(r"\\([A-Za-z]+)")


def semantic_family(candidate):
    """Describe a candidate using declared meaning/module/repository metadata."""
    classification = candidate.get("classification", {})
    return {
        "operation": candidate.get("name") or classification.get("pattern_type") or "unknown",
        "module": candidate.get("module"),
        "repository": candidate.get("repository"),
    }


def document_context(source, catalogue=()):
    """Extract module imports and modules of explicit semantic commands."""
    imports = [{"repository": match.group("repository"), "module": match.group("module")}
               for match in MODULE_REFERENCE.finditer(source or "")]
    macros = {entry.get("macro"): entry for entry in catalogue if entry.get("macro")}
    semantic_modules = []
    for macro in SEMANTIC_COMMAND.findall(source or ""):
        entry = macros.get(macro)
        if entry and entry.get("module"):
            semantic_modules.append(entry["module"])
    return {"imports": imports, "semantic_modules": semantic_modules}


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _identity(candidate):
    rule = candidate.get("classification", {})
    mapping = tuple(rule.get("argument_roles", ())) or _freeze(rule.get("components") or {})
    return (candidate.get("macro", candidate.get("semantic_macro")),
            candidate.get("module"), candidate.get("repository"),
            rule.get("pattern_type"), mapping)


def deduplicate_candidates(candidates):
    """Collapse presentation variants of one meaning while retaining provenance."""
    result, positions = [], {}
    for candidate in candidates:
        key = _identity(candidate)
        provenance = candidate.get("provenance") or candidate.get("source") or "unknown"
        if key in positions:
            stored = result[positions[key]]
            if provenance not in stored["provenance"]:
                stored["provenance"].append(provenance)
            stored["presentation_variants"] += 1
            continue
        stored = deepcopy(candidate)
        stored["provenance"] = [provenance]
        stored["presentation_variants"] = 1
        positions[key] = len(result)
        result.append(stored)
    return result


def _visible(candidate):
    rule = candidate.get("classification", {})
    return (candidate.get("matched_visible_notation") or candidate.get("visible")
            or rule.get("operator") or rule.get("separator") or rule.get("command")
            or rule.get("token"))


def rank_candidates(candidates, occurrence=None, context=None, accepted_history=(),
                    session=None, policy=DEFAULT_POLICY):
    """Return deduplicated ranked copies; the supplied raw list is never mutated."""
    occurrence, context, session = occurrence or {}, context or {}, session or {}
    actual = occurrence.get("matched_visible_notation") or occurrence.get("visible_symbol")
    pattern = occurrence.get("pattern_type")
    imported_modules = {item.get("module") for item in context.get("imports", ())}
    local_modules = Counter(context.get("semantic_modules", ()))
    occurrence_file = str(occurrence.get("file", "")).replace("\\", "/")
    history = [item for item in accepted_history
               if item.get("decision") == "accepted"
               and (not occurrence_file
                    or str(item.get("file", "")).replace("\\", "/") == occurrence_file)]
    if actual:
        history = [item for item in history if canonicalize_tex_notation(
            item.get("visible_symbol", "")) == canonicalize_tex_notation(actual)]
    ranked = []
    for order, candidate in enumerate(deduplicate_candidates(candidates)):
        score, evidence = 0.0, []
        visible = _visible(candidate)
        if candidate.get("configured_notation_alias"):
            score += policy.alias_visible
            evidence.append({"signal": "configured_notation_alias", "weight": policy.alias_visible})
        elif actual and visible == actual:
            score += policy.exact_visible
            evidence.append({"signal": "exact_visible_notation", "weight": policy.exact_visible})
        elif actual and visible and canonicalize_tex_notation(visible) == canonicalize_tex_notation(actual):
            score += policy.alias_visible
            evidence.append({"signal": "notation_alias_match", "weight": policy.alias_visible})
        rule_pattern = candidate.get("classification", {}).get("pattern_type") or candidate.get("pattern_type")
        if pattern and rule_pattern == pattern:
            score += policy.structural_compatibility
            evidence.append({"signal": "structural_compatibility", "weight": policy.structural_compatibility})
        module = candidate.get("module")
        macro = candidate.get("macro", candidate.get("semantic_macro"))
        if module and module in imported_modules:
            score += policy.imported_module
            evidence.append({"signal": "module_imported_in_document", "weight": policy.imported_module})
        if module and local_modules[module]:
            contribution = policy.local_semantic_module
            score += contribution
            evidence.append({"signal": "local_semantic_module", "weight": contribution})
        if macro and any(item.get("semantic_macro") == macro for item in history):
            score += policy.same_document_macro_history
            evidence.append({"signal": "same_document_macro_history",
                             "weight": policy.same_document_macro_history})
        elif module and any(item.get("semantic_module") == module for item in history):
            score += policy.same_document_module_history
            evidence.append({"signal": "same_document_module_history",
                             "weight": policy.same_document_module_history})
        if module and module == session.get("preferred_module"):
            score += policy.preferred_module
            evidence.append({"signal": "session_preferred_module", "weight": policy.preferred_module})
        family = semantic_family(candidate)
        if session.get("recent_family") and family == session["recent_family"]:
            score += policy.recent_family
            evidence.append({"signal": "session_recent_family", "weight": policy.recent_family})
        contextual = any(item["signal"] not in {
            "exact_visible_notation", "notation_alias_match", "structural_compatibility"
        } for item in evidence)
        if not contextual:
            evidence.append({"signal": "fallback_only", "weight": 0.0})
        candidate.update(score=score, evidence=evidence, semantic_family=family,
                         contextual_evidence=contextual, _stable_order=order)
        ranked.append(candidate)
    ranked.sort(key=lambda item: (-item["score"],
                                  item.get("macro", item.get("semantic_macro")) or "",
                                  item.get("module") or "", item["_stable_order"]))
    previous_score, rank = None, 0
    for position, candidate in enumerate(ranked, 1):
        if candidate["score"] != previous_score:
            rank = position
            previous_score = candidate["score"]
        candidate["rank"] = rank
        candidate.pop("_stable_order", None)
    return ranked


def policy_dict(policy=DEFAULT_POLICY):
    return asdict(policy)


# Layer B: select annotation scopes from an existing AST, without parsing.


def match_classification(root, classification, registry):
    """Match core grammar or a generic adapter at compatible AST scopes."""
    if not classification.get("grammar_adapter"):
        return match_ast(root, classification, registry)
    if root.kind in {"Alignment", "Row", "MathSequence"}:
        return [match for child in root.children
                for match in match_classification(child, classification, registry)]
    expected_kind = classification.get("grammar_interpretation", {}).get("node_kind")
    # A caret inside a larger expression does not make that scope a script.
    targets = ([root] if expected_kind != "ScriptedExpression"
               or root.kind == expected_kind else [])
    targets.extend(node for node in root.walk()
                   if node is not root and node.kind == expected_kind)
    matches = []
    for target in targets:
        match = match_adapter_expression(target.text, classification)
        if not match:
            continue
        match = dict(match)
        match["start"] += target.start
        match["end"] += target.start
        match["matched_expression"] = root.source[match["start"]:match["end"]]
        match["matched_visible_notation"] = match["matched_expression"]
        matches.append(match)
    return list({(item["start"], item["end"], tuple(item["arguments"])): item
                 for item in matches}.values())


def match_candidate(root, candidate, registry):
    """Return stable structural matches enriched with semantic identity."""
    classification = candidate["classification"]
    result = []
    for match in match_classification(root, classification, registry):
        node = next((node for node in root.walk()
                     if node.start == match["start"] and node.end == match["end"]), None)
        result.append({
            **match,
            "ast_node": None if node is None else {
                "kind": node.kind, "value": node.value,
                "start": node.start, "end": node.end, "text": node.text,
                "roles": list(node.roles),
            },
            "classification": dict(classification),
            "adapter_family": classification.get("grammar_adapter"),
            "semantic_macro": candidate.get("macro", candidate.get("semantic_macro")),
            "module": candidate.get("module"), "repository": candidate.get("repository"),
            "argument_roles": list(classification.get("argument_roles", [])),
        })
    return result


def match_ast(root, classification, registry):
    pattern = classification.get("pattern_type")
    visible = classification.get({"sequence": "separator", "binary_infix": "operator", "unary": "operator",
                                  "command": "command"}.get(pattern, ""))
    matches = []
    nodes = list(root.walk())
    parents = {id(child): parent for parent in nodes for child in parent.children}
    for node in nodes:
        if pattern == "bound_construct":
            if (node.kind != classification.get("node_kind")
                    or node.value != classification.get("token")):
                continue
            role_map = dict(zip(node.roles, node.children))
            required = classification.get("argument_roles", ())
            if any(role not in role_map for role in required):
                continue
            children = [role_map[role] for role in required]
        elif pattern in {"sequence", "binary_infix", "unary"}:
            kinds = {"UnaryOperator"} if pattern == "unary" else {"BinaryOperator", "NaryOperator"}
            implicit = bool(classification.get("implicit"))
            operator_matches = (implicit and node.operator_start == -1
                                and registry.implicit is not None and node.metadata == registry.implicit)
            if (node.kind not in kinds or not isinstance(node.metadata, Operator)
                    or not (operator_matches or registry.equivalent(visible, node.metadata))):
                continue
            parent = parents.get(id(node))
            if (pattern == "sequence" and classification.get("maximal_sequence")
                    and parent is not None
                    and parent.kind in {"BinaryOperator", "NaryOperator"}
                    and parent.metadata == node.metadata):
                continue
            if classification.get("fixity") and node.metadata.fixity != classification["fixity"]:
                continue
            if pattern == "binary_infix" and len(node.children) != 2:
                continue
            children = node.children
            if pattern == "sequence" and (implicit or node.metadata.arity == "variable"):
                def flatten(current):
                    same_operator = (current.kind in {"BinaryOperator", "NaryOperator"}
                                     and current.metadata == node.metadata)
                    if same_operator and (not implicit or current.operator_start == -1):
                        return tuple(part for child in current.children for part in flatten(child))
                    return (current,)
                children = flatten(node)
        elif pattern == "command":
            if (node.kind != "Command" or canonicalize_tex_notation(node.value)
                    != canonicalize_tex_notation(visible)):
                continue
            # Optional presentation arguments cannot be silently discarded.
            if "optional" in node.roles or len(node.children) != classification.get("argument_count"):
                continue
            children = node.children
        elif pattern == "scripted_operator":
            if node.kind != "ScriptedExpression":
                continue
            if "^" not in node.roles:
                continue
            if len(node.children) != 2:
                continue
            children = node.children
        elif pattern == "template":
            grammar_interp = classification.get("grammar_interpretation", {})
            node_kind = grammar_interp.get("node_kind")
            if node_kind == "MultiPartApplication" and node.kind == "Command":
                parts = grammar_interp.get("parts", [])
                arguments_expected = sum(1 for p in parts if p.get("kind") == "argument")
                if arguments_expected == len(node.children):
                    first_part = parts[0] if parts else {}
                    first_fixed_text = first_part.get("text", "")
                    if first_fixed_text.startswith(r"\sqrt") and canonicalize_tex_notation(node.value) == canonicalize_tex_notation(r"\sqrt"):
                        children = node.children
                    else:
                        continue
                else:
                    continue
            else:
                continue
        else:
            continue
        arguments = []
        for child in children:
            if (pattern in {"command", "bound_construct"}
                    and child.kind == "GroupedExpression" and child.value == "{"):
                arguments.append(child.source[child.start + 1:child.end - 1])
            else:
                arguments.append(child.text)
        matches.append({"pattern_type": pattern, "matched_expression": node.text,
                        "arguments": arguments, "start": node.start, "end": node.end,
                        "matched_visible_notation": node.notation or visible or classification.get("token")})
    return sorted(matches, key=lambda match: (match["start"], match["end"]))
