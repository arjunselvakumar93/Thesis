import re
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]

SMGLOM_REPOSITORIES = {
    "arithmetics": (
        PROJECT_DIR
        / "reference"
        / "arithmetics"
        / "source"
    ),
    "sets": (
        PROJECT_DIR
        / "reference"
        / "sets"
        / "source"
    ),
}


def find_matching_delimiter(
    text,
    opening_position,
    opening_character,
    closing_character
):
    """
    Find the matching closing delimiter.

    Supports nested structures.
    """

    if (
        opening_position >= len(text)
        or text[opening_position] != opening_character
    ):
        return None

    depth = 0

    for index in range(
        opening_position,
        len(text)
    ):
        character = text[index]

        if character == opening_character:
            depth += 1

        elif character == closing_character:
            depth -= 1

            if depth == 0:
                return index

    return None


def read_group(
    text,
    position,
    opening_character="{",
    closing_character="}"
):
    """
    Read one balanced group.
    """

    if (
        position >= len(text)
        or text[position] != opening_character
    ):
        return None, position

    closing_position = find_matching_delimiter(
        text,
        position,
        opening_character,
        closing_character
    )

    if closing_position is None:
        return None, position

    content = text[
        position + 1:
        closing_position
    ]

    return (
        content,
        closing_position + 1
    )


def skip_whitespace(text, position):
    """
    Move past spaces and newlines.
    """

    while (
        position < len(text)
        and text[position].isspace()
    ):
        position += 1

    return position


def remove_latex_comments(text):
    """
    Remove LaTeX comments while keeping line numbers.

    Escaped percent signs such as \\% are preserved.
    """

    cleaned_lines = []

    for line in text.splitlines(
        keepends=True
    ):

        comment_position = None

        for index, character in enumerate(line):

            if character != "%":
                continue

            backslash_count = 0
            previous = index - 1

            while (
                previous >= 0
                and line[previous] == "\\"
            ):
                backslash_count += 1
                previous -= 1

            if backslash_count % 2 == 0:
                comment_position = index
                break

        if comment_position is None:
            cleaned_lines.append(line)

        else:
            ending = ""

            if line.endswith("\r\n"):
                ending = "\r\n"

            elif line.endswith("\n"):
                ending = "\n"

            cleaned_lines.append(
                line[:comment_position]
                + ending
            )

    return "".join(cleaned_lines)


def parse_options(option_text):
    """
    Convert options into a dictionary.

    Example:

    args=ai,prec=700,name=greater than
    """

    options = {}

    if not option_text:
        return options

    parts = option_text.split(",")

    for part in parts:

        part = part.strip()

        if not part:
            continue

        if "=" in part:

            key, value = part.split(
                "=",
                1
            )

            options[
                key.strip()
            ] = value.strip()

        else:
            options[part] = True

    return options


def extract_placeholders(notation):
    """
    Find placeholders such as #1, #2, #3.
    """

    numbers = re.findall(
        r"#(\d+)",
        notation
    )

    return sorted(
        {
            int(number)
            for number in numbers
        }
    )


def parse_symdef(
    declaration,
    start_position=0
):
    """
    Parse a complete \\symdef declaration.
    """

    command_position = declaration.find(
        r"\symdef",
        start_position
    )

    if command_position == -1:
        return None

    current = (
        command_position
        + len(r"\symdef")
    )

    current = skip_whitespace(
        declaration,
        current
    )

    macro, current = read_group(
        declaration,
        current
    )

    if macro is None:
        return None

    current = skip_whitespace(
        declaration,
        current
    )

    option_text = None

    if (
        current < len(declaration)
        and declaration[current] == "["
    ):

        option_text, current = read_group(
            declaration,
            current,
            "[",
            "]"
        )

    current = skip_whitespace(
        declaration,
        current
    )

    notation, current = read_group(
        declaration,
        current
    )

    if notation is None:
        return None

    options = parse_options(
        option_text
    )

    return {
        "declaration_type": "symdef",
        "macro": macro,
        "name": options.get("name"),
        "args": options.get("args"),
        "options": options,
        "notation": notation,
        "placeholders":
            extract_placeholders(notation),
        "end_position": current
    }


def parse_notation(
    declaration,
    start_position=0
):
    """
    Parse a complete \\notation declaration.
    """

    command_position = declaration.find(
        r"\notation",
        start_position
    )

    if command_position == -1:
        return None

    current = (
        command_position
        + len(r"\notation")
    )

    current = skip_whitespace(
        declaration,
        current
    )

    target, current = read_group(
        declaration,
        current
    )

    if target is None:
        return None

    current = skip_whitespace(
        declaration,
        current
    )

    option_text = None

    if (
        current < len(declaration)
        and declaration[current] == "["
    ):

        option_text, current = read_group(
            declaration,
            current,
            "[",
            "]"
        )

    current = skip_whitespace(
        declaration,
        current
    )

    notation, current = read_group(
        declaration,
        current
    )

    if notation is None:
        return None

    options = parse_options(
        option_text
    )

    return {
        "declaration_type": "notation",
        "target": target,
        "options": options,
        "notation": notation,
        "placeholders":
            extract_placeholders(notation),
        "end_position": current
    }


def get_module_before_position(
    text,
    position
):
    """
    Find the closest smodule declaration
    before a notation declaration.
    """

    prefix = text[:position]

    matches = list(
        re.finditer(
            r"\\begin\{smodule\}"
            r"(?:\[[^\]]*\])?"
            r"\{([^}]+)\}",
            prefix
        )
    )

    if not matches:
        return None

    return matches[-1].group(1)


def extract_declarations_from_text(
    text,
    command
):
    """
    Extract all \\symdef or \\notation
    declarations from one TeX document.

    Multiline declarations are supported.
    """

    results = []

    search_position = 0

    while True:

        position = text.find(
            command,
            search_position
        )

        if position == -1:
            break

        if command == r"\symdef":

            parsed = parse_symdef(
                text,
                position
            )

        else:

            parsed = parse_notation(
                text,
                position
            )

        if parsed is None:

            search_position = (
                position + len(command)
            )

            continue

        end_position = parsed[
            "end_position"
        ]

        parsed[
            "start_position"
        ] = position

        parsed[
            "line"
        ] = (
            text.count(
                "\n",
                0,
                position
            )
            + 1
        )

        parsed[
            "module"
        ] = get_module_before_position(
            text,
            position
        )

        parsed[
            "declaration"
        ] = text[
            position:end_position
        ].strip()

        results.append(
            parsed
        )

        search_position = end_position

    return results


def scan_repository(
    repository_name,
    source_directory
):
    """
    Extract all symdef and notation
    declarations from one SMGloM repository.
    """

    records = []

    if not source_directory.exists():
        return records

    for tex_file in source_directory.rglob(
        "*.tex"
    ):

        text = tex_file.read_text(
            encoding="utf-8",
            errors="replace"
        )

        text = remove_latex_comments(
            text
        )

        symdefs = (
            extract_declarations_from_text(
                text,
                r"\symdef"
            )
        )

        notations = (
            extract_declarations_from_text(
                text,
                r"\notation"
            )
        )

        for record in (
            symdefs + notations
        ):

            record[
                "repository"
            ] = repository_name

            record[
                "file"
            ] = tex_file

            records.append(
                record
            )

    return records


def scan_available_smglom():
    """
    Scan every available local SMGloM repository.
    """

    records = []

    for (
        repository_name,
        source_directory
    ) in SMGLOM_REPOSITORIES.items():

        if source_directory.exists():

            print(
                f"Scanning {repository_name}: "
                f"{source_directory}"
            )

            repository_records = (
                scan_repository(
                    repository_name,
                    source_directory
                )
            )

            records.extend(
                repository_records
            )

        else:

            print(
                f"Not found locally: "
                f"{repository_name}"
            )

    return records


def search_records(
    records,
    search_text
):
    """
    Search inside extracted notation,
    macro names, targets and meanings.
    """

    search_lower = (
        search_text.lower()
    )

    matches = []

    for record in records:

        searchable_parts = [
            record.get(
                "notation"
            ),
            record.get(
                "macro"
            ),
            record.get(
                "target"
            ),
            record.get(
                "name"
            )
        ]

        searchable_text = " ".join(
            str(part)
            for part in searchable_parts
            if part is not None
        ).lower()

        if search_lower in searchable_text:
            matches.append(
                record
            )

    return matches


def display_record(
    record,
    number
):
    """
    Print one extracted SMGloM record.
    """

    print("=" * 70)
    print(f"[{number}]")

    print(
        f"Repository: "
        f"{record['repository']}"
    )

    print(
        f"File: "
        f"{record['file'].name}"
    )

    print(
        f"Line: "
        f"{record['line']}"
    )

    print(
        f"Module: "
        f"{record['module']}"
    )

    print(
        f"Type: "
        f"{record['declaration_type']}"
    )

    if (
        record[
            "declaration_type"
        ]
        == "symdef"
    ):

        print(
            f"Macro: "
            f"\\{record['macro']}"
        )

        print(
            f"Meaning: "
            f"{record['name']}"
        )

        print(
            f"Args: "
            f"{record['args']}"
        )

    else:

        print(
            f"Target: "
            f"{record['target']}"
        )

    print(
        f"Notation: "
        f"{record['notation']}"
    )

    print(
        f"Placeholders: "
        f"{record['placeholders']}"
    )

    print()


def main():

    print(
        "Scanning real SMGloM notation declarations"
    )
    print()

    records = scan_available_smglom()

    print()

    symdef_count = sum(
        1
        for record in records
        if (
            record[
                "declaration_type"
            ]
            == "symdef"
        )
    )

    notation_count = sum(
        1
        for record in records
        if (
            record[
                "declaration_type"
            ]
            == "notation"
        )
    )

    print(
        f"Extracted "
        f"{symdef_count} symdef(s)."
    )

    print(
        f"Extracted "
        f"{notation_count} notation(s)."
    )

    print(
        f"Total: {len(records)}"
    )

    print()

    if len(sys.argv) > 1:

        search_text = sys.argv[1]

    else:

        search_text = input(
            "Search extracted notation "
            "(example: \\frac, >, +): "
        ).strip()

    if not search_text:
        return

    matches = search_records(
        records,
        search_text
    )

    print()

    print(
        f"Found {len(matches)} "
        f"record(s) matching "
        f"{search_text!r}."
    )

    print()

    for number, record in enumerate(
        matches[:30],
        start=1
    ):

        display_record(
            record,
            number
        )

    if len(matches) > 30:

        print(
            f"Showing first 30 of "
            f"{len(matches)} matches."
        )


if __name__ == "__main__":
    main()