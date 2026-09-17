"""Incremental reader for complete TeX operands used by notation matching."""

import re

from symbol_scanner import find_matching_brace


STRUCTURAL_COMMANDS = {
    "begin", "documentclass", "end", "label", "usepackage",
}

# Commands whose braced arguments form part of one mathematical operand.
# Unknown control words remain valid zero-argument symbols such as ``\alpha``.
COMMAND_ARITIES = {
    "binom": 2,
    "dfrac": 2,
    "frac": 2,
    "sqrt": 1,
    "tfrac": 2,
}


def skip_horizontal_right(text, position):
    while position < len(text) and text[position] in " \t\r":
        position += 1
    return position


def skip_horizontal_left(text, position):
    while position >= 0 and text[position] in " \t\r":
        position -= 1
    return position


def _balanced_end(text, position, opening, closing):
    if position >= len(text) or text[position] != opening:
        return None
    depth = 0
    for index in range(position, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _control_sequence(text, position):
    if position >= len(text) or text[position] != "\\":
        return None
    end = position + 1
    if end >= len(text):
        return "", end
    if text[end].isalpha():
        while end < len(text) and text[end].isalpha():
            end += 1
    else:
        end += 1
    return text[position + 1:end], end


def _left_right_end(text, position):
    """Read a balanced ``\left ... \right`` expression."""
    cursor = position
    depth = 0
    while cursor < len(text):
        if text.startswith(r"\left", cursor):
            depth += 1
            cursor += len(r"\left")
            continue
        if text.startswith(r"\right", cursor):
            depth -= 1
            cursor += len(r"\right")
            cursor = skip_horizontal_right(text, cursor)
            delimiter = _control_sequence(text, cursor)
            if delimiter:
                cursor = delimiter[1]
            elif cursor < len(text):
                cursor += 1
            if depth == 0:
                return cursor
            continue
        cursor += 1
    return None


def _read_script(text, position):
    position = skip_horizontal_right(text, position)
    if position >= len(text):
        return None
    if text[position] == "{":
        return find_matching_brace(text, position)
    if text[position] == "\\":
        control = _control_sequence(text, position)
        return None if control is None else control[1] - 1
    if text[position] in "\n^_":
        return None
    return position


def _read_base(text, position):
    start = position
    character = text[position]

    if character == "{":
        closing = find_matching_brace(text, position)
        if closing is None:
            return None
        return {
            "value": text[position + 1:closing],
            "raw": text[position:closing + 1],
            "start": start,
            "end": closing + 1,
            "valid": bool(text[position + 1:closing].strip()),
        }

    if character in "([":
        closing_character = ")" if character == "(" else "]"
        end = _balanced_end(text, position, character, closing_character)
        if end is None:
            return None
        raw = text[position:end]
        return {"value": raw, "raw": raw, "start": start, "end": end, "valid": True}

    if character == "\\":
        control = _control_sequence(text, position)
        if control is None:
            return None
        name, end = control
        if name == "left":
            end = _left_right_end(text, position)
            if end is None:
                return None
        elif name in STRUCTURAL_COMMANDS:
            cursor = end
            while True:
                argument = skip_horizontal_right(text, cursor)
                if argument >= len(text) or text[argument] != "{":
                    break
                closing = find_matching_brace(text, argument)
                if closing is None:
                    break
                cursor = closing + 1
            end = cursor
            raw = text[start:end]
            return {"value": raw, "raw": raw, "start": start, "end": end, "valid": False}
        elif name in COMMAND_ARITIES:
            cursor = end
            if name == "sqrt":
                optional = skip_horizontal_right(text, cursor)
                if optional < len(text) and text[optional] == "[":
                    optional_end = _balanced_end(text, optional, "[", "]")
                    if optional_end is None:
                        return None
                    cursor = optional_end
            for _ in range(COMMAND_ARITIES[name]):
                cursor = skip_horizontal_right(text, cursor)
                if cursor >= len(text) or text[cursor] != "{":
                    raw = text[start:end]
                    return {
                        "value": raw, "raw": raw, "start": start,
                        "end": end, "valid": False,
                    }
                closing = find_matching_brace(text, cursor)
                if closing is None:
                    return None
                cursor = closing + 1
            end = cursor
        raw = text[start:end]
        return {
            "value": raw,
            "raw": raw,
            "start": start,
            "end": end,
            "valid": bool(name),
        }

    if character.isalnum():
        end = position + 1
        while end < len(text) and text[end].isalnum():
            end += 1
        raw = text[start:end]
        return {"value": raw, "raw": raw, "start": start, "end": end, "valid": True}

    return {
        "value": character,
        "raw": character,
        "start": start,
        "end": start + 1,
        "valid": False,
    }


def read_tex_operand_right(text, position):
    """Read a complete operand to the right without crossing a source line."""
    position = skip_horizontal_right(text, position)
    if position >= len(text):
        return None
    signed = text[position] in "+-"
    base_position = position + 1 if signed else position
    if signed and (
        base_position >= len(text)
        or text[base_position].isspace()
        or text[base_position] in "+-"
    ):
        return {
            "value": text[position], "raw": text[position],
            "start": position, "end": position + 1, "valid": False,
        }
    operand = _read_base(text, base_position)
    if operand is None:
        return None

    if signed:
        operand["start"] = position

    end = operand["end"]
    has_script = False
    while True:
        script_marker = skip_horizontal_right(text, end)
        if script_marker >= len(text) or text[script_marker] not in "^_":
            break
        script_end = _read_script(text, script_marker + 1)
        if script_end is None:
            operand["valid"] = False
            break
        end = script_end + 1
        has_script = True

    if has_script or signed:
        operand["end"] = end
        operand["raw"] = text[operand["start"]:end]
        operand["value"] = operand["raw"]
    return operand


def _is_structural_argument(text, start, line_start):
    if start >= len(text) or text[start] != "{":
        return False
    prefix = text[line_start:start]
    command = re.search(r"\\([A-Za-z]+)\s*$", prefix)
    return bool(command and command.group(1) in STRUCTURAL_COMMANDS)


def is_structural_position(text, position):
    """Return whether a source position belongs to a structural command."""
    line_start = text.rfind("\n", 0, position) + 1
    cursor = line_start
    while cursor <= position:
        command_start = text.find("\\", cursor, position + 1)
        if command_start == -1:
            return False
        control = _control_sequence(text, command_start)
        if control is None:
            return False
        name, end = control
        if name in STRUCTURAL_COMMANDS:
            structural_end = end
            while True:
                argument = skip_horizontal_right(text, structural_end)
                if argument < len(text) and text[argument] == "[":
                    optional_end = _balanced_end(text, argument, "[", "]")
                    if optional_end is None:
                        break
                    structural_end = optional_end
                    continue
                if argument < len(text) and text[argument] == "{":
                    closing = find_matching_brace(text, argument)
                    if closing is None:
                        break
                    structural_end = closing + 1
                    continue
                break
            if command_start <= position < structural_end:
                return True
            cursor = structural_end
        else:
            cursor = end
    return False


def read_tex_operand_left(text, position):
    """Read the longest complete operand ending to the left of position."""
    position = skip_horizontal_left(text, position)
    if position < 0 or text[position] == "\n":
        return None
    target_end = position + 1
    line_start = text.rfind("\n", 0, target_end) + 1
    best = None
    for start in range(line_start, target_end):
        operand = read_tex_operand_right(text, start)
        if operand is None or operand["start"] != start or operand["end"] != target_end:
            continue
        if not is_valid_operand(operand) or _is_structural_argument(text, start, line_start):
            continue
        if best is None or operand["start"] < best["start"]:
            best = operand
    return best


def is_valid_operand(operand):
    return bool(operand and operand.get("valid") and operand.get("value", "").strip())
