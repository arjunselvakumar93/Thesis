import sys
import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


from smglom_notation_extractor import (
    scan_available_smglom,
    search_records,
    read_group,
    skip_whitespace,
)


def read_simple_tex_argument(
    text,
    position
):
    """
    Read one simple TeX argument.

    Supports:

        {+}
        +
        \alpha
    """

    position = skip_whitespace(
        text,
        position
    )

    if position >= len(text):
        return None, position

    # Braced argument
    if text[position] == "{":

        return read_group(
            text,
            position
        )

    # Control sequence
    if text[position] == "\\":

        start = position
        position += 1

        if position >= len(text):
            return "\\", position

        if text[position].isalpha():

            while (
                position < len(text)
                and text[position].isalpha()
            ):
                position += 1

        else:
            position += 1

        return (
            text[start:position],
            position
        )

    # Single character
    return (
        text[position],
        position + 1
    )


def extract_argument_after_command(
    text,
    command
):
    """
    Find a command and read its next TeX argument.

    Examples:

        \maincomp{>}
            -> >

        \maincomp+
            -> +
    """

    position = text.find(
        command
    )

    if position == -1:
        return None

    current = (
        position
        + len(command)
    )

    argument, _ = read_simple_tex_argument(
        text,
        current
    )

    return argument


def extract_visible_operator(
    fragment
):
    """
    Try to extract the visible mathematical operator
    from an sTeX presentation fragment.

    Examples:

        \mathrel{\maincomp{>}}
            -> >

        \mathbin{\maincomp+}
            -> +

        \comp{+}
            -> +
    """

    # Prefer maincomp because SMGloM commonly
    # uses it for the main visible operator.
    value = extract_argument_after_command(
        fragment,
        r"\maincomp"
    )

    if value is not None:
        return value

    # Some declarations use \comp instead.
    value = extract_argument_after_command(
        fragment,
        r"\comp"
    )

    if value is not None:
        return value

    return fragment.strip()


def parse_argsep(notation):
    """
    Parse an argsep sequence.

    Example:

        \argsep{#1}{\mathbin{\maincomp+}}

    returns information about the separator.
    """

    position = notation.find(
        r"\argsep"
    )

    if position == -1:
        return None

    current = (
        position
        + len(r"\argsep")
    )

    current = skip_whitespace(
        notation,
        current
    )

    first_argument, current = read_group(
        notation,
        current
    )

    if first_argument is None:
        return None

    current = skip_whitespace(
        notation,
        current
    )

    separator_argument, current = read_group(
        notation,
        current
    )

    if separator_argument is None:
        return None

    return {
        "argument": first_argument,
        "separator_fragment":
            separator_argument,
        "separator":
            extract_visible_operator(
                separator_argument
            )
    }


def add_operator_metadata(record, classification, fragment):
    """Retain generic binding information carried by an SMGloM operator.

    ``\mathrel`` is presentation metadata, rather than a property of one
    particular visible character.  The matcher can therefore give all
    relations complete expression operands without maintaining a list of
    equality/ordering symbols.
    """

    if r"\mathrel" in fragment:
        classification["operator_role"] = "relation"

    precedence = record.get("options", {}).get("prec")
    if precedence is not None:
        try:
            precedence = int(precedence)
        except (TypeError, ValueError):
            pass
        classification["precedence"] = precedence

    return classification


def classify_notation(record):
    """
    Classify an extracted SMGloM notation.

    Current categories:

        sequence
        command
        binary_infix
        constant
        template
    """

    notation = record.get(
        "notation"
    )

    placeholders = record.get(
        "placeholders",
        []
    )

    if not notation:

        return {
            "pattern_type": "unknown"
        }

    # -------------------------------------------------
    # Case 1:
    # Variable-length sequence
    #
    # Example:
    #
    # \argsep{#1}{\mathbin{\maincomp+}}
    # -------------------------------------------------

    if r"\argsep" in notation:

        information = parse_argsep(
            notation
        )

        if information is not None:

            return add_operator_metadata(record, {
                "pattern_type": "sequence",
                "separator":
                    information["separator"],
                "separator_fragment":
                    information[
                        "separator_fragment"
                    ],
                "variable_arguments": True
            }, information["separator_fragment"])

    # -------------------------------------------------
    # Case 2:
    # No arguments
    #
    # Example:
    #
    # \mathbb{N}^+
    # -------------------------------------------------

    if not placeholders:

        return {
            "pattern_type": "constant"
        }

    # -------------------------------------------------
    # Case 3:
    # Binary infix
    #
    # Example:
    #
    # #1\mathrel{\maincomp{>}}#2
    # -------------------------------------------------

    stripped = notation.strip()

    if (
        placeholders == [1, 2]
        and stripped.startswith("#1")
        and stripped.endswith("#2")
    ):

        middle = stripped[
            len("#1"):
            len(stripped) - len("#2")
        ]

        return add_operator_metadata(record, {
            "pattern_type": "binary_infix",
            "operator_fragment": middle,
            "operator":
                extract_visible_operator(
                    middle
                ),
            "argument_count": 2
        }, middle)

    # -------------------------------------------------
    # Case 4:
    # Command notation
    #
    # Example:
    #
    # \frac{#1}{#2}
    # \sqrt{#1}
    # -------------------------------------------------

    if stripped.startswith("\\"):

        current = 1

        while (
            current < len(stripped)
            and stripped[current].isalpha()
        ):
            current += 1

        command = stripped[:current]

        if (
            command
            and command not in (
                r"\argsep",
                r"\mathrel",
                r"\mathbin"
            )
        ):

            return {
                "pattern_type": "command",
                "command": command,
                "argument_count":
                    len(placeholders)
            }

    # -------------------------------------------------
    # Everything more complicated stays a template
    # for now.
    # -------------------------------------------------

    return {
        "pattern_type": "template"
    }


def get_record_name(record):
    """
    Return the semantic macro/target name.
    """

    if (
        record["declaration_type"]
        == "symdef"
    ):

        return record.get(
            "macro"
        )

    return record.get(
        "target"
    )


def classify_catalog_notation(record):
    """Conservative structural classification for catalogue/registry imports.

    The historical classifier remains available to compatibility callers.
    Recognizing a template category does not claim it is executable grammar.
    """
    body = (record.get("notation") or "").strip()
    placeholders = record.get("placeholders", [])
    signature = str(record.get("args") or record.get("options", {}).get("args", ""))
    components = {f"argument{index}": f"#{index}" for index in placeholders}
    if "b" in signature:
        operator = extract_visible_operator(body)
        if not re.fullmatch(r"\\(?:[A-Za-z]+|[^A-Za-z])", operator or ""):
            operator = None
        return {"pattern_type": "binder", "components": components, "operator": operator,
                "grammar_supported": False,
                "unsupported_reason": "binder_metadata_incomplete"}
    old = classify_notation(record)
    # Strip only presentation wrappers around a single visible token.
    def visible(fragment):
        fragment = fragment.strip()
        wrapper = re.fullmatch(r"\\(?:mathbin|mathrel|mathop|maincomp|comp)\s*\{(.*)\}", fragment, re.S)
        if wrapper:
            return visible(wrapper.group(1))
        wrapper = re.fullmatch(r"\\(?:maincomp|comp)\s*((?:\\[A-Za-z]+)|[^\w\s{}#\\])", fragment)
        if wrapper:
            return wrapper.group(1)
        return fragment if re.fullmatch(r"(?:\\[A-Za-z]+|\\[^A-Za-z]|[^\w\s{}#\\])", fragment) else None
    if old["pattern_type"] == "sequence":
        current = body.find(r"\argsep") + len(r"\argsep")
        first, current = read_group(body, skip_whitespace(body, current))
        second, current = read_group(body, skip_whitespace(body, current))
        token = visible(second or "")
        if body.startswith(r"\argsep") and first == "#1" and not body[current:].strip():
            if token:
                return dict(old, grammar_supported=True, fixity="infix", arity="variable")
            if not (second or "").strip():
                return {"pattern_type": "sequence", "separator": "", "implicit": True,
                        "variable_arguments": True, "grammar_supported": True,
                        "annotation_ready": True, "fixity": "infix", "arity": "variable"}
    if old["pattern_type"] == "binary_infix":
        token = visible(body[2:-2])
        if token:
            return dict(old, operator=token, grammar_supported=True, fixity="infix", arity=2)
    # Exact command argument templates; a leading command alone is insufficient.
    command = re.match(r"\\[A-Za-z]+", body)
    if command and placeholders:
        expected = command.group() + "".join("{#" + str(index) + "}" for index in placeholders)
        if re.sub(r"\s+", "", body) == expected and placeholders == list(range(1, len(placeholders) + 1)):
            return {"pattern_type": "command", "command": command.group(), "argument_count": len(placeholders),
                    "arity": len(placeholders), "fixity": "command", "grammar_supported": True}
    if placeholders == [1]:
        for fixity, pattern in (("prefix", r"(.+?)(?:\{#1\}|#1)"),
                                ("postfix", r"(?:\{#1\}|#1)(.+)")):
            found = re.fullmatch(pattern, body, re.S)
            token = visible(found.group(1)) if found else None
            if token:
                return add_operator_metadata(record, {"pattern_type": "unary", "operator": token,
                    "fixity": fixity, "arity": 1, "argument_count": 1, "grammar_supported": True}, body)
    if not placeholders and body:
        token = visible(body)
        return {"pattern_type": "constant", "visible": token or body,
                "fixity": "constant", "arity": 0, "grammar_supported": bool(token and token.startswith("\\")),
                **({} if token and token.startswith("\\") else {"unsupported_reason": "decorated_constant"})}
    if re.search(r"\([^)]*#\d", body):
        kind = "function_application"
    elif re.search(r"[_^].*#\d", body):
        kind = "scripted_operator"
    elif body.startswith(r"\left"):
        kind = "delimiter_application"
    else:
        kind = "template" if body else "unknown"
    if kind == "function_application":
        reason = "mixfix"
    elif kind == "scripted_operator":
        reason = "big_operator" if r"\mathop" in body else "scripted_template"
    elif kind == "delimiter_application":
        reason = "delimiter_notation"
    elif len(re.findall(r"\\[A-Za-z]+", body)) > 1:
        reason = "multi_part_command"
    elif body.startswith("\\"):
        reason = "unknown_wrapper"
    else:
        reason = "unsupported_template"
    return {"pattern_type": kind, "components": components, "grammar_supported": False,
            "unsupported_reason": reason}


def display_classification(
    record,
    classification,
    number
):
    """
    Display one classified notation.
    """

    print("=" * 70)

    print(
        f"[{number}] "
        f"\\{get_record_name(record)}"
    )

    print(
        f"Module: "
        f"{record.get('module')}"
    )

    print(
        f"Notation: "
        f"{record.get('notation')}"
    )

    print(
        f"Args specification: "
        f"{record.get('args')}"
    )

    print(
        f"Pattern type: "
        f"{classification['pattern_type']}"
    )

    if (
        classification[
            "pattern_type"
        ]
        == "binary_infix"
    ):

        print(
            f"Operator: "
            f"{classification['operator']}"
        )

    elif (
        classification[
            "pattern_type"
        ]
        == "sequence"
    ):

        print(
            f"Separator: "
            f"{classification['separator']}"
        )

        print(
            "Variable arguments: True"
        )

    elif (
        classification[
            "pattern_type"
        ]
        == "command"
    ):

        print(
            f"Command: "
            f"{classification['command']}"
        )

        print(
            f"Arguments: "
            f"{classification['argument_count']}"
        )

    print()


def main():

    print(
        "Scanning and classifying "
        "SMGloM notation"
    )

    print()

    records = scan_available_smglom()

    print()

    if len(sys.argv) > 1:

        search_text = sys.argv[1]

    else:

        search_text = input(
            "Search notation "
            "(example: >, +, \\frac): "
        ).strip()

    if not search_text:
        return

    records = search_records(
        records,
        search_text
    )

    print(
        f"Found {len(records)} "
        f"matching record(s)."
    )

    print()

    for number, record in enumerate(
        records,
        start=1
    ):

        classification = (
            classify_notation(
                record
            )
        )

        display_classification(
            record,
            classification,
            number
        )


if __name__ == "__main__":
    main()
