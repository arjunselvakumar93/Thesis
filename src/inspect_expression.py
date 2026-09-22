"""Print source spans and ASTs without selecting a semantic annotation."""

import argparse
import json
from pathlib import Path
from expression_metadata import build_expression_config
from expression_parser import ExpressionParser, ParseError
from expression_registry import NotationRegistry
from expression_document import parse_math_regions


def format_tree(root):
    lines, pending = [], [(root, 0, "")]
    while pending:
        node, depth, role = pending.pop()
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
        child_roles = tuple(node.roles) + ("",) * (len(node.children) - len(node.roles))
        pending.extend((child, depth + 1, child_role)
                       for child, child_role in reversed(tuple(zip(node.children, child_roles))))
    return "\n".join(lines)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("expression", nargs="?")
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
                print("UnsupportedExpression: " + region.error if region.error else format_tree(region.tree))
            return int(any(region.error for region in regions))
        print(format_tree(ExpressionParser(registry=registry).parse(args.expression)))
        return 0
    except (ValueError, OSError) as error:
        print(f"UnsupportedExpression: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
