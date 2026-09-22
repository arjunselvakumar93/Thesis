"""Parse every complete math region before annotation selection (Layer A)."""

from dataclasses import dataclass, replace
from pathlib import Path
from expression_parser import ExpressionParser, ParseError
from expression_registry import NotationRegistry
from expression_annotation import match_ast
from notation_scanner import extract_math_regions, _source_location, mask_latex_comments
from notation_matcher import normalize_math_expression


def _without_alignment_markers(text):
    """Remove unescaped TeX layout tabs from display text, never from source spans."""
    import re
    return re.sub(r"(?<!\\)&", "", text)


@dataclass(frozen=True)
class ParsedRegion:
    source: str
    start: int
    end: int
    tree: object = None
    error: str = ""
    file: object = None
    diagnostic: object = None


def parse_math_regions(source, registry, file=None):
    """Keep original source and document offsets, including comments/newlines."""
    results = []
    parser = ExpressionParser(registry=registry)
    for region in extract_math_regions(source):
        start, end = region["start"], region["start"] + len(region["text"])
        try:
            content = source[start:end]
            if region.get("environment") in registry.alignment_environments:
                tree = _parse_alignment(content, parser, region["environment"])
            else:
                tree = parser.parse(content)
            results.append(ParsedRegion(source, start, end, tree, file=file))
        except ParseError as error:
            results.append(ParsedRegion(source, start, end, error=str(error), file=file,
                                        diagnostic={"category": error.category,
                                                    "position": error.position, "token": error.token}))
    return results


def _rebase_tree(node, source, offset):
    return replace(node, start=node.start + offset, end=node.end + offset,
                   operator_start=(node.operator_start + offset if node.operator_start >= 0 else -1),
                   source=source,
                   children=tuple(_rebase_tree(child, source, offset) for child in node.children))


def _alignment_row_spans(text):
    """Split top-level TeX rows without assigning semantics to the separator."""
    start, depth, index = 0, 0, 0
    while index < len(text):
        if text[index] in "{([":
            depth += 1
        elif text[index] in "})]" and depth:
            depth -= 1
        elif depth == 0 and text.startswith(r"\\", index):
            yield start, index
            index += 2
            start = index
            continue
        index += 1
    yield start, len(text)


def _parse_alignment(text, parser, environment):
    rows = []
    for raw_start, raw_end in _alignment_row_spans(text):
        left = raw_start
        while left < raw_end and text[left].isspace():
            left += 1
        right = raw_end
        while right > left and text[right - 1].isspace():
            right -= 1
        if left == right:
            continue
        masked = list(text[left:right])
        for index, character in enumerate(masked):
            if character == "&" and (index == 0 or masked[index - 1] != "\\"):
                masked[index] = " "
        expression = _rebase_tree(parser.parse("".join(masked)), text, left)
        row = ExpressionParser._node(parser, "Row", left, right, "row", (expression,),
                                     roles=("expression",))
        rows.append(replace(row, source=text))
    if not rows:
        raise ParseError("Alignment environment contains no mathematical rows")
    container = ExpressionParser._node(parser, "Alignment", 0, len(text), environment, rows,
                                       roles=tuple("row" for _ in rows),
                                       metadata={"environment": environment})
    return replace(container, source=text)


def parse_papers(papers_dir, configuration):
    registry = NotationRegistry.from_config(configuration)
    target = Path(papers_dir)
    paths = [target] if target.is_file() else sorted(target.rglob("*.tex"))
    return [region for path in paths
            for region in parse_math_regions(path.read_text(encoding="utf-8"), registry, path)]


def find_ast_occurrences(regions, classification, configuration):
    registry = NotationRegistry.from_config(configuration)
    occurrences = []
    for region in regions:
        if region.tree is None:
            continue
        matches = match_ast(region.tree, classification, registry)
        for match in matches:
            start, end = region.start + match["start"], region.start + match["end"]
            line, column, text = _source_location(region.source, start)
            match["source_start"], match["source_end"] = start, end
            match["source_expression"] = region.source[start:end]
            match["matched_expression"] = normalize_math_expression(
                _without_alignment_markers(mask_latex_comments(match["matched_expression"])))
            match["arguments"] = [normalize_math_expression(
                _without_alignment_markers(mask_latex_comments(arg))) for arg in match["arguments"]]
            occurrences.append(dict(match, file=region.file, line=line, column=column, text=text))
    return occurrences
