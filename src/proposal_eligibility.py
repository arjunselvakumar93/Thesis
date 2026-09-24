"""Structural proposal eligibility independent of semantic ranking and gold data."""


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
