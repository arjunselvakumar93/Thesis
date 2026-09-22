"""Layer B: select annotation scopes from an existing AST, without parsing."""

from expression_registry import Operator
from tex_notation_aliases import canonicalize_tex_notation


def match_ast(root, classification, registry):
    pattern = classification.get("pattern_type")
    visible = classification.get({"sequence": "separator", "binary_infix": "operator", "unary": "operator",
                                  "command": "command"}.get(pattern, ""))
    matches = []
    for node in root.walk():
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
            if classification.get("fixity") and node.metadata.fixity != classification["fixity"]:
                continue
            if pattern == "binary_infix" and len(node.children) != 2:
                continue
            children = node.children
            if implicit and pattern == "sequence":
                def flatten(current):
                    if (current.kind == "BinaryOperator" and current.operator_start == -1
                            and current.metadata == registry.implicit):
                        return (*flatten(current.children[0]), *flatten(current.children[1]))
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
            if pattern == "command" and child.kind == "GroupedExpression" and child.value == "{":
                arguments.append(child.source[child.start + 1:child.end - 1])
            else:
                arguments.append(child.text)
        matches.append({"pattern_type": pattern, "matched_expression": node.text,
                        "arguments": arguments, "start": node.start, "end": node.end,
                        "matched_visible_notation": node.notation or visible or classification.get("token")})
    return sorted(matches, key=lambda match: (match["start"], match["end"]))
