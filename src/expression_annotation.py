"""Layer B: select annotation scopes from an existing AST, without parsing."""

from expression_registry import Operator
from notation_adapters import match_adapter_expression
from tex_notation_aliases import canonicalize_tex_notation


def match_classification(root, classification, registry):
    """Match core grammar or a generic adapter at compatible AST scopes."""
    if not classification.get("grammar_adapter"):
        return match_ast(root, classification, registry)
    expected_kind = classification.get("grammar_interpretation", {}).get("node_kind")
    targets = [root]
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


def _node_at(root, start, end):
    return next((node for node in root.walk()
                 if node.start == start and node.end == end), None)


def match_candidate(root, candidate, registry):
    """Return stable structural matches enriched with semantic identity."""
    classification = candidate["classification"]
    result = []
    for match in match_classification(root, classification, registry):
        node = _node_at(root, match["start"], match["end"])
        result.append({
            **match,
            "raw_source": root.source[match["start"]:match["end"]],
            "ast_node": None if node is None else {
                "kind": node.kind, "value": node.value,
                "start": node.start, "end": node.end, "text": node.text,
                "roles": list(node.roles),
            },
            "classification": dict(classification),
            "adapter_family": classification.get("grammar_adapter"),
            "semantic_candidate": {
                "macro": candidate.get("macro", candidate.get("semantic_macro")),
                "name": candidate.get("name"), "module": candidate.get("module"),
                "repository": candidate.get("repository"),
            },
            "semantic_macro": candidate.get("macro", candidate.get("semantic_macro")),
            "module": candidate.get("module"), "repository": candidate.get("repository"),
            "argument_roles": list(classification.get("argument_roles", [])),
            "provenance": list(candidate.get("provenance", (candidate.get("source"),))
                               if candidate.get("source") else candidate.get("provenance", ())),
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
