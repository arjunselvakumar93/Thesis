"""Print source spans and ASTs without selecting a semantic annotation."""

import argparse
import json
import re
import sys
from pathlib import Path
from notation_catalog import build_expression_config
from expression_parser import ExpressionParser
from expression_parser import NotationRegistry
from expression_document import parse_math_regions


def _tree_rows(root):
    """Share the detailed formatter's iterative, role-aware traversal."""
    pending = [(root, 0, "")]
    while pending:
        node, depth, role = pending.pop()
        yield node, depth, role
        child_roles = tuple(node.roles) + ("",) * (len(node.children) - len(node.roles))
        pending.extend((child, depth + 1, child_role)
                       for child, child_role in reversed(tuple(zip(node.children, child_roles))))


def format_tree(root):
    lines = []
    for node, depth, role in _tree_rows(root):
        roles = f" roles={','.join(node.roles)}" if node.roles else ""
        notation = f" notation={node.notation}" if node.notation else ""
        metadata = node.metadata
        source = getattr(metadata, "source", "") if metadata is not None else ""
        semantic = getattr(metadata, "semantic_macro", "") if metadata is not None else ""
        if isinstance(metadata, dict):
            source = metadata.get("source", source)
            semantic = metadata.get("semantic_macro", semantic)
        details = (f" source={source}" if source else "") + (
            f" semantic=\\{semantic}" if semantic else ""
        )
        prefix = f"{role}: " if role else ""
        lines.append(f"{'  ' * depth}{prefix}{node.kind}({node.value}) [{node.start}:{node.end}]"
                     f"{roles}{notation}{details}")
    return "\n".join(lines)


def _readable_label(node):
    """Name syntax already recognized by the AST, without inferring meaning."""
    if node.kind in {"SymbolAtom", "OpaqueMathToken"}:
        return "Unknown Symbol"
    if node.kind == "OpaqueCommandApplication":
        return "Unknown Command (fallback)"
    if node.kind in {"BinaryOperator", "NaryOperator"}:
        return {"+": "Addition", "-": "Subtraction", "−": "Subtraction",
                "*": "Multiplication", r"\times": "Multiplication",
                r"\cdot": "Multiplication", "/": "Division", "=": "Equality"
                }.get(node.value, f"Operator {node.value}")
    if node.kind == "UnaryOperator":
        return {"-": "Negation", "−": "Negation", "+": "Unary Plus"}.get(
            node.value, f"Unary Operator {node.value}")
    if node.kind == "Command" and node.value == r"\frac" and node.roles == ("numerator", "denominator"):
        return "Fraction"
    if node.kind in {"BoundExpression", "BinderApplication", "BigOperatorApplication"}:
        name = getattr(node.metadata, "name", "")
        return name.capitalize() if name else {r"\int": "Integral", "∫": "Integral"}.get(
            node.value, f"Bound Construct {node.value}")
    if node.kind == "ScriptedExpression":
        return "Exponentiation (superscript syntax)" if node.roles == ("base", "^") else "Scripted Expression"
    return {"Atom": "Symbol", "GroupedExpression": "Group",
            "OpaqueArgument": "Uninterpreted Argument",
            "OpaqueEnvironmentBody": "Uninterpreted Environment Body (fallback)",
            "Command": "Command " + node.value,
            "SemanticCommand": "Configured Semantic Command " + node.value}.get(
                node.kind, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", node.kind))


def format_readable(root, view, expression=None):
    """Render source substrings verbatim (quoted/escaped for multiline TeX)."""
    def quote(text):
        return json.dumps(text, ensure_ascii=False)

    lines = ["Expression: " + quote(root.source if expression is None else expression)]
    if view == "tree":
        for node, depth, role in _tree_rows(root):
            prefix = (role.replace("_", " ") + ": ") if role else ""
            lines.append(f"{'  ' * depth}{prefix}{_readable_label(node)}: {quote(node.text)}")
    else:
        top = root
        while top.kind == "GroupedExpression" and len(top.children) == 1:
            top = top.children[0]
        lines.append("Top-level structure: " + _readable_label(top))
        lines.append("Main structural components (first 12, in source order):")
        unknown, count, implicit_count = [], 0, 0
        for node, depth, role in _tree_rows(root):
            if (node.kind == "SymbolAtom" or node.kind.startswith("Opaque")
                    or node.kind == "UncertainOperatorChain"
                    or (isinstance(node.metadata, dict) and node.metadata.get("opaque"))):
                unknown.append(node)
            if node is root or (not node.children and depth > 1) or node.kind == "GroupedExpression":
                continue
            # The parser records implicit adjacency without an operator span.
            # Keep visiting its children, but reserve summary slots for more
            # informative structure. Tree and detailed views retain every node.
            if (node.kind in {"BinaryOperator", "NaryOperator"}
                    and node.operator_start == -1 and not node.notation):
                implicit_count += 1
                continue
            count += 1
            if count <= 12:
                prefix = role.replace("_", " ") + ": " if role else ""
                lines.append(f"  {prefix}{_readable_label(node)}: {quote(node.text)}")
        if not count:
            lines.append("  " + _readable_label(top) + ": " + quote(top.text))
        if count > 12:
            lines.append(f"  ... {count - 12} more components; use --view tree")
        if implicit_count:
            lines.append(f"  {implicit_count} intermediate implicit-operation nodes omitted; use --view tree")
        lines.append(f"Unknown/fallback constructs: {len(unknown)}")
        for node in unknown[:8]:
            lines.append(f"  {_readable_label(node)}: {quote(node.text)}")
        if len(unknown) > 8:
            lines.append(f"  ... {len(unknown) - 8} more; use --view tree")
        if unknown:
            lines.append("Fallback entries are preserved syntax, not parse failures; symbol names or argument roles may be unconfigured.")
    lines.append("Interpretation: structural recognition only; no semantic annotation selected.")
    return "\n".join(lines)


def main(argv=None):
    # Windows terminals often inherit a legacy code page.  Keep Unicode
    # mathematics printable without changing the parsed or stored source.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("expression", nargs="?")
    cli.add_argument("--view", choices=("summary", "tree", "detailed"), default="detailed",
                     help="Display style (default: detailed, for compatibility)")
    cli.add_argument("--file", type=Path, help="Parse every math region in a TeX file")
    cli.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "expression-config.example.json")
    args = cli.parse_args(argv)
    if (args.expression is None) == (args.file is None):
        cli.error("Supply an expression or --file")
    try:
        config = build_expression_config(json.loads(args.config.read_text(encoding="utf-8-sig")))
        registry = NotationRegistry.from_config(config)
        if args.file:
            regions = parse_math_regions(args.file.read_text(encoding="utf-8"), registry, args.file)
            for region in regions:
                print(f"Math region [{region.start}:{region.end}]")
                print("UnsupportedExpression: " + region.error if region.error else (
                    format_tree(region.tree) if args.view == "detailed" else
                    format_readable(region.tree, args.view, region.source[region.start:region.end])))
            return int(any(region.error for region in regions))
        tree = ExpressionParser(registry=registry).parse(args.expression)
        print(format_tree(tree) if args.view == "detailed" else
              format_readable(tree, args.view, args.expression))
        return 0
    except (ValueError, OSError) as error:
        print(f"UnsupportedExpression: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
