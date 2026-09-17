"""Scan classified SMGloM notation rules inside LaTeX math regions."""

import re
from pathlib import Path

from notation_classifier import classify_notation
from notation_matcher import match_notation
from symbol_scanner import PAPERS_DIR
from tex_notation_aliases import get_equivalent_tex_notations


MATH_ENVIRONMENTS = {
    "equation", "equation*", "align", "align*",
    "gather", "gather*", "multline", "multline*",
}
BEGIN_ENVIRONMENT = re.compile(r"\\begin\{([^{}]+)\}")


def _is_escaped(text, position):
    """Return whether the character at position has an odd backslash prefix."""
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def uncommented_prefix(line):
    """Keep escaped percent signs and stop at the first TeX comment."""
    for index, character in enumerate(line):
        if character == "%" and not _is_escaped(line, index):
            return line[:index]
    return line


def mask_latex_comments(source):
    """Replace comments with spaces while preserving every source offset."""
    masked = []
    for line in source.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        prefix = uncommented_prefix(content)
        masked.append(prefix + " " * (len(content) - len(prefix)) + ending)
    return "".join(masked)


def _find_unescaped(text, token, start):
    """Find a delimiter that is not escaped by a preceding backslash."""
    position = text.find(token, start)
    while position != -1:
        if not _is_escaped(text, position):
            return position
        position = text.find(token, position + len(token))
    return -1


def extract_math_regions(source):
    """Return math contents and their offsets in the original LaTeX source.

    Comments are masked before delimiter parsing, so commented delimiters never
    create regions. The returned offsets still index the unchanged source.
    """
    text = mask_latex_comments(source)
    regions = []
    position = 0

    while position < len(text):
        if text.startswith(r"\(", position) and not _is_escaped(text, position):
            closing = _find_unescaped(text, r"\)", position + 2)
            if closing != -1:
                regions.append({"text": text[position + 2:closing], "start": position + 2})
                position = closing + 2
                continue

        if text.startswith(r"\[", position) and not _is_escaped(text, position):
            closing = _find_unescaped(text, r"\]", position + 2)
            if closing != -1:
                regions.append({"text": text[position + 2:closing], "start": position + 2})
                position = closing + 2
                continue

        environment = BEGIN_ENVIRONMENT.match(text, position)
        if environment and not _is_escaped(text, position):
            name = environment.group(1)
            if name in MATH_ENVIRONMENTS:
                closing_token = rf"\end{{{name}}}"
                content_start = environment.end()
                closing = text.find(closing_token, content_start)
                if closing != -1:
                    regions.append({"text": text[content_start:closing], "start": content_start})
                    position = closing + len(closing_token)
                    continue

        if text[position] == "$" and not _is_escaped(text, position):
            delimiter = "$$" if text.startswith("$$", position) else "$"
            content_start = position + len(delimiter)
            closing = _find_unescaped(text, delimiter, content_start)
            if closing != -1:
                regions.append({"text": text[content_start:closing], "start": content_start})
                position = closing + len(delimiter)
                continue

        position += 1

    return regions


def _source_location(source, offset):
    """Map an absolute source offset to a one-based line and column."""
    line_start = source.rfind("\n", 0, offset) + 1
    line_end = source.find("\n", offset)
    if line_end == -1:
        line_end = len(source)
    original_line = source[line_start:line_end].rstrip("\r")
    return source.count("\n", 0, offset) + 1, offset - line_start + 1, original_line


def _visible_notation(classification):
    return classification.get({
        "command": "command",
        "binary_infix": "operator",
        "sequence": "separator",
    }.get(classification.get("pattern_type"), ""))


def _rules_for_tex_spellings(classification):
    """Expand one semantic rule into equivalent TeX-syntax matching rules."""
    pattern_type = classification.get("pattern_type")
    field = {
        "command": "command",
        "binary_infix": "operator",
        "sequence": "separator",
    }.get(pattern_type)
    visible = _visible_notation(classification)
    if not field or visible is None:
        return []

    rules = []
    for spelling in get_equivalent_tex_notations(visible):
        rule = dict(classification)
        rule[field] = spelling
        rules.append((spelling, rule))
    return rules


def find_notation_occurrences(rule_or_record, papers_dir=PAPERS_DIR):
    """Search math regions in all paper TeX files with a classified rule."""
    classification = (
        rule_or_record
        if "pattern_type" in rule_or_record
        else classify_notation(rule_or_record)
    )
    if classification.get("pattern_type") not in {
        "command", "binary_infix", "sequence"
    }:
        return []

    occurrences = {}
    matching_rules = _rules_for_tex_spellings(classification)
    for tex_file in sorted(Path(papers_dir).rglob("*.tex")):
        source = tex_file.read_text(encoding="utf-8", errors="replace")
        for region in extract_math_regions(source):
            for spelling, matching_rule in matching_rules:
                for match in match_notation(region["text"], matching_rule):
                    absolute_start = region["start"] + match["start"]
                    absolute_end = region["start"] + match["end"]
                    line, column, original_line = _source_location(source, absolute_start)
                    key = (tex_file, absolute_start, absolute_end, match["pattern_type"])
                    occurrences[key] = {
                        "file": tex_file,
                        "line": line,
                        "column": column,
                        "text": original_line,
                        "matched_expression": match["matched_expression"],
                        "matched_visible_notation": spelling,
                        "arguments": match["arguments"],
                        "pattern_type": match["pattern_type"],
                    }
    return [occurrences[key] for key in sorted(
        occurrences, key=lambda item: (str(item[0]), item[1], item[2], item[3])
    )]
