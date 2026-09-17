"""Small generic reader for complete operands around relation operators.

This is deliberately narrower than a full TeX parser.  A relation splits the
containing balanced math group into expression sides; nested groups, arithmetic
operators, scripts and command expressions consequently remain part of those
sides.  Structural commands and TeX row breaks form hard boundaries.
"""

import re


STRUCTURAL_COMMAND = re.compile(
    r"\\(?:begin|documentclass|end|label|usepackage)\b"
)
OPENING = {"(": ")", "[": "]", "{": "}"}
CLOSING = {value: key for key, value in OPENING.items()}


def _is_escaped(text, position):
    count = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        count += 1
        position -= 1
    return count % 2 == 1


def _balanced_pairs(text):
    """Return balanced delimiter pairs as ``(open, close)`` offsets."""
    stack = []
    pairs = []
    for position, character in enumerate(text):
        if _is_escaped(text, position):
            continue
        if character in OPENING:
            stack.append((character, position))
        elif character in CLOSING:
            if stack and stack[-1][0] == CLOSING[character]:
                _, opening = stack.pop()
                pairs.append((opening, position))
    return pairs


def _containing_scope(position, length, pairs):
    containing = [pair for pair in pairs if pair[0] < position < pair[1]]
    if not containing:
        return 0, length
    opening, closing = max(containing, key=lambda pair: pair[0])
    return opening + 1, closing


def _control_word_end(text, position):
    end = position + 1
    while end < len(text) and text[end].isalpha():
        end += 1
    return end


def _group_end(text, position, opening="{", closing="}"):
    if position >= len(text) or text[position] != opening:
        return None
    depth = 0
    for current in range(position, len(text)):
        if _is_escaped(text, current):
            continue
        if text[current] == opening:
            depth += 1
        elif text[current] == closing:
            depth -= 1
            if depth == 0:
                return current + 1
    return None


def _structural_spans(text):
    """Return complete spans occupied by structural LaTeX commands."""
    spans = []
    for found in STRUCTURAL_COMMAND.finditer(text):
        end = _control_word_end(text, found.start())
        while True:
            cursor = end
            while cursor < len(text) and text[cursor] in " \t\r":
                cursor += 1
            if cursor < len(text) and text[cursor] in "[{":
                opening = text[cursor]
                closing = "]" if opening == "[" else "}"
                group_end = _group_end(text, cursor, opening, closing)
                if group_end is None:
                    break
                end = group_end
                continue
            break
        spans.append((found.start(), end))
    return spans


def _row_breaks(text):
    """Return TeX display-row separators (``\\`` in the source)."""
    breaks = []
    position = 0
    while position + 1 < len(text):
        if text[position:position + 2] == "\\\\":
            breaks.append((position, position + 2))
            position += 2
        else:
            position += 1
    return breaks


def _bounded_scope(position, base_scope, hard_boundaries):
    start, end = base_scope
    for boundary_start, boundary_end in hard_boundaries:
        if start <= boundary_start < position:
            start = max(start, boundary_end)
        elif position < boundary_end <= end:
            end = min(end, boundary_start)
    return start, end


def _trim_span(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    # Punctuation placed after a displayed formula belongs to the surrounding
    # sentence.  It is safe to remove only at an outer expression edge; the
    # same characters inside groups or between terms remain untouched.
    while end > start and text[end - 1] in ",;:.":
        end -= 1
        while end > start and text[end - 1].isspace():
            end -= 1
    return start, end


def _valid_expression(text, start, end):
    if start >= end:
        return False
    expression = text[start:end]
    if not expression.strip() or STRUCTURAL_COMMAND.search(expression):
        return False
    # Alignment marks are layout, not mathematical operands.  Supporting them
    # safely requires preserving them outside any eventual replacement.
    if "&" in expression:
        return False
    return any(character.isalnum() or character == "\\" for character in expression)


def _operator_positions(text, operator, operator_at, structural_position):
    positions = []
    search = 0
    while search < len(text):
        position = text.find(operator, search)
        if position == -1:
            break
        if operator_at(text, operator, position) and not structural_position(text, position):
            positions.append(position)
        search = position + max(1, len(operator))
    return positions


def match_relation_notation(text, classification, operator_at, structural_position):
    """Match one SMGloM ``\mathrel`` rule using complete expression sides."""
    pattern_type = classification["pattern_type"]
    operator = classification.get("separator", classification.get("operator"))
    positions = _operator_positions(text, operator, operator_at, structural_position)
    if not positions:
        return []

    pairs = _balanced_pairs(text)
    boundaries = _structural_spans(text) + _row_breaks(text)
    groups = {}
    for position in positions:
        base = _containing_scope(position, len(text), pairs)
        scope = _bounded_scope(position, base, boundaries)
        if scope[0] <= position < scope[1]:
            groups.setdefault(scope, []).append(position)

    matches = []
    for (scope_start, scope_end), separators in sorted(groups.items()):
        separators.sort()
        cuts = [scope_start]
        cuts.extend(position + len(operator) for position in separators)
        raw_ends = separators + [scope_end]
        spans = [
            _trim_span(text, start, end)
            for start, end in zip(cuts, raw_ends)
        ]

        if pattern_type == "sequence":
            if len(spans) < 2 or not all(
                _valid_expression(text, start, end) for start, end in spans
            ):
                continue
            start, end = spans[0][0], spans[-1][1]
            matches.append({
                "pattern_type": pattern_type,
                "matched_expression": text[start:end],
                "separator": operator,
                "arguments": [text[left:right] for left, right in spans],
                "start": start,
                "end": end,
            })
            continue

        # Binary relation chains yield one adjacent pair per visible operator.
        for index in range(len(separators)):
            left, right = spans[index], spans[index + 1]
            if not (_valid_expression(text, *left) and _valid_expression(text, *right)):
                continue
            matches.append({
                "pattern_type": pattern_type,
                "matched_expression": text[left[0]:right[1]],
                "operator": operator,
                "arguments": [text[left[0]:left[1]], text[right[0]:right[1]]],
                "start": left[0],
                "end": right[1],
            })

    return sorted(matches, key=lambda match: (match["start"], match["end"]))
