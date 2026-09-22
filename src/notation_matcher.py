import re

from symbol_scanner import (
    extract_command_with_arguments,
    find_matching_brace,
)
from tex_operand import (
    is_structural_position,
    is_valid_operand as is_valid_tex_operand,
    read_tex_operand_left,
    read_tex_operand_right,
)
from relation_expression import match_relation_notation


PHYSICAL_LINE_BREAK = re.compile(r"[ \t\r]*\n[ \t\r]*")


def normalize_math_expression(value):
    """Replace source-formatting line breaks with one mathematical space."""
    return PHYSICAL_LINE_BREAK.sub(" ", value)


def normalize_match_expressions(matches):
    """Normalize displayed values without changing their source offsets."""
    for match in matches:
        match["matched_expression"] = normalize_math_expression(
            match["matched_expression"]
        )
        match["arguments"] = [
            normalize_math_expression(argument)
            for argument in match.get("arguments", [])
        ]
    return matches


def skip_whitespace_right(text, position):
    """
    Skip whitespace moving to the right.
    """

    while (
        position < len(text)
        and text[position] in " \t\r"
    ):
        position += 1

    return position


def skip_whitespace_left(text, position):
    """
    Skip whitespace moving to the left.
    """

    while (
        position >= 0
        and text[position] in " \t\r"
    ):
        position -= 1

    return position


def read_tex_atom_right(text, position):
    """
    Read one simple TeX atom from left to right.

    Supports:

        a
        12
        variable123
        {a+b}
        \\alpha
    """

    position = skip_whitespace_right(
        text,
        position
    )

    if position >= len(text):
        return None

    start = position

    # --------------------------------------
    # Braced group
    # --------------------------------------

    if text[position] == "{":

        closing = find_matching_brace(
            text,
            position
        )

        if closing is None:
            return None

        return {
            "value": text[
                position + 1:
                closing
            ],
            "raw": text[
                position:
                closing + 1
            ],
            "start": position,
            "end": closing + 1
        }

    # --------------------------------------
    # LaTeX control sequence
    #
    # \alpha
    # --------------------------------------

    if text[position] == "\\":

        position += 1

        if position >= len(text):

            return {
                "value": "\\",
                "raw": "\\",
                "start": start,
                "end": position
            }

        if text[position].isalpha():

            while (
                position < len(text)
                and text[position].isalpha()
            ):
                position += 1

        else:
            position += 1

        raw = text[
            start:position
        ]

        return {
            "value": raw,
            "raw": raw,
            "start": start,
            "end": position
        }

    # --------------------------------------
    # Normal word / number
    #
    # x
    # abc
    # 123
    # --------------------------------------

    if (
        text[position].isalnum()
        or text[position] == "_"
    ):

        while (
            position < len(text)
            and (
                text[position].isalnum()
                or text[position] == "_"
            )
        ):
            position += 1

        raw = text[
            start:position
        ]

        return {
            "value": raw,
            "raw": raw,
            "start": start,
            "end": position
        }

    # --------------------------------------
    # Single symbol
    # --------------------------------------

    return {
        "value": text[position],
        "raw": text[position],
        "start": position,
        "end": position + 1
    }


def find_matching_open_brace(
    text,
    closing_position
):
    """
    Find the opening brace belonging to a closing brace.

    Nested braces are supported.
    """

    if (
        closing_position < 0
        or text[closing_position] != "}"
    ):
        return None

    depth = 0

    for index in range(
        closing_position,
        -1,
        -1
    ):

        if text[index] == "}":
            depth += 1

        elif text[index] == "{":
            depth -= 1

            if depth == 0:
                return index

    return None


def read_tex_atom_left(text, position):
    """
    Read one simple TeX atom moving from right to left.

    Supports:

        a
        12
        abc
        {a+b}
        \\alpha
    """

    position = skip_whitespace_left(
        text,
        position
    )

    if position < 0:
        return None

    end = position + 1

    # --------------------------------------
    # Braced group
    # --------------------------------------

    if text[position] == "}":

        opening = find_matching_open_brace(
            text,
            position
        )

        if opening is None:
            return None

        return {
            "value": text[
                opening + 1:
                position
            ],
            "raw": text[
                opening:
                position + 1
            ],
            "start": opening,
            "end": position + 1
        }

    # A control word is one atom even when read backwards.
    if text[position].isalpha():
        start = position
        while start - 1 >= 0 and text[start - 1].isalpha():
            start -= 1
        if start > 0 and text[start - 1] == "\\":
            raw = text[start - 1:end]
            return {"value": raw, "raw": raw, "start": start - 1, "end": end}

    # --------------------------------------
    # Normal word / number
    # --------------------------------------

    if (
        text[position].isalnum()
        or text[position] == "_"
    ):

        start = position

        while (
            start - 1 >= 0
            and (
                text[start - 1].isalnum()
                or text[start - 1] == "_"
            )
        ):
            start -= 1

        raw = text[
            start:end
        ]

        return {
            "value": raw,
            "raw": raw,
            "start": start,
            "end": end
        }

    # --------------------------------------
    # Single symbol
    # --------------------------------------

    return {
        "value": text[position],
        "raw": text[position],
        "start": position,
        "end": position + 1
    }


def is_valid_operand(atom):
    """Accept complete simple atoms, not TeX delimiters or operators."""
    if atom is None:
        return False
    raw = atom["raw"]
    if raw.startswith("{"):
        return bool(atom["value"].strip())
    if raw.startswith("\\"):
        return len(raw) > 1 and raw[1].isalpha()
    return bool(raw) and (raw[0].isalnum() or raw[0] == "_")


def operator_at(text, operator, position):
    """Do not match a TeX control word inside another control word."""
    if not operator or not text.startswith(operator, position):
        return False
    if operator.startswith("\\") and operator[-1].isalpha():
        end = position + len(operator)
        if end < len(text) and text[end].isalpha():
            return False
    return True


def match_command_notation(
    text,
    classification
):
    """
    Match command-style notation.

    Example rule:

        command = \\frac
        argument_count = 2

    Matches:

        \\frac a b
        \\frac{a}{b}
        \\frac {a} b
    """

    command = classification[
        "command"
    ]

    argument_count = classification[
        "argument_count"
    ]

    matches = []

    search_position = 0

    while True:

        position = text.find(
            command,
            search_position
        )

        if position == -1:
            break

        (
            expression,
            arguments
        ) = extract_command_with_arguments(
            text,
            position,
            command,
            argument_count
        )

        if (
            len(arguments) == argument_count
            and operator_at(text, command, position)
            and all(argument.strip() for argument in arguments)
        ):

            matches.append(
                {
                    "pattern_type": "command",
                    "matched_expression":
                        expression,
                    "arguments":
                        arguments,
                    "start": position,
                    "end":
                        position
                        + len(expression)
                }
            )

        search_position = (
            position + len(command)
        )

    return matches


def match_binary_infix_notation(
    text,
    classification
):
    """
    Match simple binary infix notation.

    Example:

        a > b

    becomes:

        arguments = ["a", "b"]
    """

    operator = classification[
        "operator"
    ]

    matches = []

    search_position = 0

    while True:

        position = text.find(
            operator,
            search_position
        )

        if position == -1:
            break

        left = read_tex_operand_left(
            text,
            position - 1
        )

        right = read_tex_operand_right(
            text,
            position + len(operator)
        )

        if (
            operator_at(text, operator, position)
            and not is_structural_position(text, position)
            and is_valid_tex_operand(left)
            and is_valid_tex_operand(right)
        ):

            expression = text[
                left["start"]:
                right["end"]
            ]

            matches.append(
                {
                    "pattern_type":
                        "binary_infix",
                    "matched_expression":
                        expression,
                    "operator":
                        operator,
                    "arguments": [
                        left["value"],
                        right["value"]
                    ],
                    "start":
                        left["start"],
                    "end":
                        right["end"]
                }
            )

        search_position = (
            position + len(operator)
        )

    return matches


def match_sequence_notation(
    text,
    classification
):
    """
    Match simple variable-length sequences.

    Example:

        a + b + c

    becomes:

        ["a", "b", "c"]

    A sequence requires at least two arguments.
    """

    separator = classification[
        "separator"
    ]

    matches = []

    search_position = 0

    while search_position < len(text):
        separator_position = text.find(separator, search_position)
        if separator_position == -1:
            break

        if (
            not operator_at(text, separator, separator_position)
            or is_structural_position(text, separator_position)
        ):
            search_position = separator_position + len(separator)
            continue

        first = read_tex_operand_left(text, separator_position - 1)
        second = read_tex_operand_right(
            text, separator_position + len(separator)
        )
        if not (
            is_valid_tex_operand(first)
            and is_valid_tex_operand(second)
        ):
            search_position = separator_position + len(separator)
            continue

        arguments = [first["value"], second["value"]]
        sequence_start = first["start"]
        sequence_end = second["end"]
        current = second["end"]
        incomplete = False

        while True:
            next_separator = skip_whitespace_right(text, current)
            if not operator_at(text, separator, next_separator):
                break
            next_argument = read_tex_operand_right(
                text, next_separator + len(separator)
            )
            if not is_valid_tex_operand(next_argument):
                incomplete = True
                current = next_separator + len(separator)
                break
            arguments.append(next_argument["value"])
            sequence_end = next_argument["end"]
            current = next_argument["end"]

        if not incomplete:
            matches.append({
                "pattern_type": "sequence",
                "matched_expression": text[sequence_start:sequence_end],
                "separator": separator,
                "arguments": arguments,
                "start": sequence_start,
                "end": sequence_end,
            })
        search_position = separator_position + len(separator)

    unique = {
        (match["start"], match["end"]): match
        for match in matches
    }
    candidates = list(unique.values())
    return sorted(
        [
            match for match in candidates
            if not any(
                other["start"] <= match["start"]
                and match["end"] <= other["end"]
                and (other["start"], other["end"])
                    != (match["start"], match["end"])
                for other in candidates
            )
        ],
        key=lambda match: (match["start"], match["end"]),
    )


def match_notation(
    text,
    classification
):
    """
    Generic entry point.

    The matcher chooses its behaviour
    from the classification generated
    from SMGloM.
    """

    if classification.get("expression_config") is not None:
        from expression_parser import match_expression_scopes
        return normalize_match_expressions(match_expression_scopes(
            text, classification, classification["expression_config"]
        ))

    pattern_type = classification.get(
        "pattern_type"
    )

    if (
        classification.get("operator_role") == "relation"
        and pattern_type in {"binary_infix", "sequence"}
    ):
        return normalize_match_expressions(match_relation_notation(
            text,
            classification,
            operator_at,
            is_structural_position,
        ))

    if pattern_type == "command":

        return normalize_match_expressions(match_command_notation(
            text,
            classification
        ))

    if pattern_type == "binary_infix":

        return normalize_match_expressions(match_binary_infix_notation(
            text,
            classification
        ))

    if pattern_type == "sequence":

        return normalize_match_expressions(match_sequence_notation(
            text,
            classification
        ))

    return []


def main():
    """
    Small manual demonstration.
    """

    examples = [

        (
            r"\frac a b",
            {
                "pattern_type": "command",
                "command": r"\frac",
                "argument_count": 2
            }
        ),

        (
            "a > b",
            {
                "pattern_type":
                    "binary_infix",
                "operator": ">"
            }
        ),

        (
            "a + b + c",
            {
                "pattern_type":
                    "sequence",
                "separator": "+",
                "variable_arguments": True
            }
        )
    ]

    for text, classification in examples:

        print("=" * 60)

        print(
            f"Text: {text}"
        )

        print(
            f"Rule: {classification}"
        )

        print(
            "Matches:"
        )

        print(
            match_notation(
                text,
                classification
            )
        )

        print()


if __name__ == "__main__":
    main()
