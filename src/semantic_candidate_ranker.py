"""Deterministic, explainable ranking for semantic annotation candidates.

This module consumes candidate metadata and context only.  It never reads
evaluation gold labels and never removes a distinct semantic interpretation.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, asdict
import re

from tex_notation_aliases import canonicalize_tex_notation


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
