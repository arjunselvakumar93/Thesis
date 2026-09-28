"""Parse every complete math region before annotation selection (Layer A)."""

from dataclasses import dataclass, replace
from pathlib import Path
from expression_parser import ExpressionParser, ParseError
from expression_registry import NotationRegistry
from expression_annotation import match_candidate, match_classification
from notation_scanner import extract_math_regions, _source_location, mask_latex_comments
from notation_matcher import normalize_math_expression
from proposal_eligibility import proposal_eligibility


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
    spans = list(_alignment_row_spans(text))
    index = 0
    while index < len(spans):
        raw_start, raw_end = spans[index]
        left = raw_start
        while left < raw_end and text[left].isspace():
            left += 1
        right = raw_end
        while right > left and text[right - 1].isspace():
            right -= 1
        if left == right:
            index += 1
            continue
        masked = list(text[left:right])
        for position, character in enumerate(masked):
            if character == "&" and (position == 0 or masked[position - 1] != "\\"):
                masked[position] = " "
        try:
            expression = _rebase_tree(parser.parse("".join(masked)), text, left)
        except ParseError as error:
            if error.category != "incomplete_expression" or index + 1 >= len(spans):
                raise
            # A TeX row break may format one logical expression across lines.
            # Preserve offsets by masking the separator instead of deleting it.
            next_end = spans[index + 1][1]
            masked = list(text[left:next_end])
            for position, character in enumerate(masked):
                if character == "&" and (position == 0 or masked[position - 1] != "\\"):
                    masked[position] = " "
            for position in range(len(masked) - 1):
                if masked[position] == "\\" and masked[position + 1] == "\\":
                    masked[position] = masked[position + 1] = " "
            expression = _rebase_tree(parser.parse("".join(masked)), text, left)
            right = next_end
            index += 1
        row = ExpressionParser._node(parser, "Row", left, right, "row", (expression,),
                                     roles=("expression",))
        rows.append(replace(row, source=text))
        index += 1
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


def find_ast_occurrences(regions, classification, configuration, candidate=None):
    registry = NotationRegistry.from_config(configuration)
    occurrences = []
    for region in regions:
        if region.tree is None:
            continue
        rule = dict(classification)
        if rule.get("pattern_type") == "sequence":
            rule["maximal_sequence"] = True
        if candidate is None:
            matches = match_classification(region.tree, rule, registry)
        else:
            structural_candidate = dict(candidate, classification=rule)
            matches = match_candidate(region.tree, structural_candidate, registry)
        for match in matches:
            eligibility = proposal_eligibility(region.tree, match["start"], match["end"])
            if not eligibility["eligible"]:
                continue
            start, end = region.start + match["start"], region.start + match["end"]
            line, column, text = _source_location(region.source, start)
            match["source_start"], match["source_end"] = start, end
            match["source_expression"] = region.source[start:end]
            match["raw_source"] = match["source_expression"]
            match["math_region_start"], match["math_region_end"] = region.start, region.end
            match["full_math_region"] = region.source[region.start:region.end]
            containing = [node for node in region.tree.walk()
                          if node.start <= match["start"] and node.end >= match["end"]]
            match["ast_depth"] = max(0, len(containing) - 1)
            node = min(containing, key=lambda item: item.end - item.start) if containing else None
            if node is not None:
                if (not match.get("argument_roles")
                        and len(node.roles) == len(match.get("arguments", ()))):
                    match["argument_roles"] = list(node.roles)
                def describe(item, indent=0):
                    value = f"({item.value})" if item.value else ""
                    lines = ["  " * indent + item.kind + value]
                    for child in item.children:
                        lines.extend(describe(child, indent + 1))
                    return lines
                match["ast_debug"] = "\n".join(describe(node))
            match["matched_expression"] = normalize_math_expression(
                _without_alignment_markers(mask_latex_comments(match["matched_expression"])))
            match["arguments"] = [normalize_math_expression(
                _without_alignment_markers(mask_latex_comments(arg))) for arg in match["arguments"]]
            occurrences.append(dict(match, file=region.file, line=line, column=column, text=text,
                                    proposal_eligibility=eligibility))
    return occurrences


def find_structural_occurrences(regions, query, configuration, semantic_status=None):
    """Find exact source/head AST nodes without assigning semantic meaning."""
    registry = NotationRegistry.from_config(configuration)
    query = query.strip()
    occurrences, seen = [], set()

    def ungroup(node):
        if node.kind == "GroupedExpression" and node.value in {"{", "[", "("}:
            return node.text[1:-1]
        return node.text

    def structure_name(node):
        configured = configuration.get("review_labels", {}).get(node.value)
        if configured:
            label = configured
        elif getattr(node.metadata, "name", ""):
            label = node.metadata.name
        else:
            label = {
                "SymbolAtom": "symbol",
                "ScriptedExpression": "scripted expression",
                "OpaqueCommandApplication": "opaque command application",
                "FunctionApplication": "function application",
                "BigOperatorApplication": "big operator application",
                "BinderApplication": "binder application",
                "BoundExpression": "bound expression",
                "EnvironmentExpression": "environment",
                "BinaryOperator": "binary operator",
                "NaryOperator": "operator sequence",
                "UnaryOperator": "unary operator",
                "Command": "command application",
            }.get(node.kind, node.kind.replace("_", " ").lower())
        if node.kind == "NaryOperator" and len(node.children) > 2 and "sequence" not in label:
            label += " sequence"
        return label

    for region in regions:
        if region.tree is None:
            continue
        for node in region.tree.walk():
            if node.kind in {"Row", "Cell", "GroupedExpression", "Atom", "Number",
                             "OpaqueEnvironmentBody"}:
                continue
            exact = node.text == query
            head = node.value == query or node.notation == query
            name = structure_name(node).casefold() == query.casefold()
            if not (exact or head or name):
                continue
            start, end = region.start + node.start, region.start + node.end
            key = (str(region.file), start, end, node.kind, node.value)
            if key in seen:
                continue
            seen.add(key)
            roles = list(node.roles)
            arguments = [ungroup(child) for child in node.children]
            if node.kind in {"BinaryOperator", "NaryOperator"} and not roles:
                roles = (["left", "right"] if len(arguments) == 2 else
                         [f"argument_{index}" for index in range(1, len(arguments) + 1)])
            line, column, text = _source_location(region.source, start)
            rule = {
                "pattern_type": "structural",
                "node_kind": node.kind,
                "structural_name": structure_name(node),
                "semantic_unavailable_status": (semantic_status
                    or configuration.get("review_unavailable_status", {}).get(node.value)
                    or "No semantic mapping is available in the loaded catalogue or configuration."),
            }
            if node.kind == "ScriptedExpression":
                rule["review_argument_labels"] = {"_": "subscript", "^": "exponent"}
            occurrences.append({
                "file": region.file, "line": line, "column": column, "text": text,
                "source_start": start, "source_end": end,
                "source_expression": region.source[start:end],
                "raw_source": region.source[start:end],
                "matched_expression": region.source[start:end],
                "arguments": arguments, "argument_roles": roles,
                "pattern_type": "structural", "classification": rule,
                "math_region_start": region.start, "math_region_end": region.end,
                "full_math_region": region.source[region.start:region.end],
                "ast_depth": max(0, sum(1 for parent in region.tree.walk()
                                         if parent.start <= node.start and parent.end >= node.end) - 1),
                "ast_debug": f"{node.kind}({node.value})",
                "structural_node": True,
                "proposal_eligibility": {"eligible": True},
            })
    return occurrences
