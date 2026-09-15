from pathlib import Path


# Folder containing the LaTeX papers
PAPERS_DIR = Path(__file__).resolve().parents[1] / "papers"


def remove_latex_comment(line):
    """
    Remove the comment part of a LaTeX line.
    """

    comment_position = line.find("%")

    if comment_position != -1:
        return line[:comment_position]

    return line


def find_matching_brace(text, opening_position):
    """
    Find the closing brace belonging to an opening brace.

    Nested braces are supported.
    """

    if (
        opening_position >= len(text)
        or text[opening_position] != "{"
    ):
        return None

    depth = 0

    for index in range(
        opening_position,
        len(text)
    ):

        if text[index] == "{":
            depth += 1

        elif text[index] == "}":
            depth -= 1

            if depth == 0:
                return index

    return None


def read_tex_argument(text, position):
    """
    Read one TeX argument.

    A TeX argument can be:

    1. A braced group:
       {a+b}

    2. A single character/token:
       a

    3. A control sequence:
       \\alpha

    Returns:

        argument
        next position

    If no valid argument exists:

        None
        original position
    """

    current = position

    # Ignore whitespace before the argument
    while (
        current < len(text)
        and text[current].isspace()
    ):
        current += 1

    if current >= len(text):
        return None, position

    # Case 1:
    # Braced argument
    #
    # Example:
    # {a+b}
    if text[current] == "{":

        closing_position = find_matching_brace(
            text,
            current
        )

        if closing_position is None:
            return None, position

        argument = text[
            current + 1:
            closing_position
        ]

        return (
            argument,
            closing_position + 1
        )

    # Case 2:
    # LaTeX control sequence
    #
    # Example:
    # \alpha
    if text[current] == "\\":

        start = current
        current += 1

        if current >= len(text):
            return "\\", current

        # Control word:
        # \alpha
        if text[current].isalpha():

            while (
                current < len(text)
                and text[current].isalpha()
            ):
                current += 1

        # Control symbol:
        # \,
        # \%
        else:
            current += 1

        argument = text[
            start:current
        ]

        return argument, current

    # Case 3:
    # Single TeX token
    #
    # Example:
    # a
    argument = text[current]

    return (
        argument,
        current + 1
    )


def extract_command_with_arguments(
    line,
    position,
    symbol,
    argument_count
):
    """
    Extract a LaTeX command and its arguments.

    Arguments do not have to use braces.

    These are all supported:

        \\frac{a}{b}
        \\frac a b
        \\frac {a} b
        \\frac a {b}
        \\frac {a} {b}

    This logic is generic and is not specific to \\frac.
    """

    start = position
    current = position + len(symbol)

    arguments = []

    for _ in range(argument_count):

        (
            argument,
            next_position
        ) = read_tex_argument(
            line,
            current
        )

        if argument is None:
            return symbol, []

        arguments.append(argument)

        current = next_position

    expression = line[
        start:current
    ]

    return expression, arguments


def extract_braced_expression(
    line,
    position,
    symbol
):
    """
    Compatibility helper for the old
    one-argument workflow.
    """

    expression, _ = extract_command_with_arguments(
        line,
        position,
        symbol,
        1
    )

    return expression


def find_symbol_occurrences(
    symbol,
    argument_count=1
):
    """
    Search all TeX files for occurrences
    of a LaTeX command.

    The argument reader supports both
    grouped and ungrouped TeX arguments.
    """

    matches = []

    for tex_file in PAPERS_DIR.rglob("*.tex"):

        lines = tex_file.read_text(
            encoding="utf-8",
            errors="replace"
        ).splitlines()

        for line_number, original_line in enumerate(
            lines,
            start=1
        ):

            line = remove_latex_comment(
                original_line
            )

            search_position = 0

            while True:

                position = line.find(
                    symbol,
                    search_position
                )

                if position == -1:
                    break

                (
                    matched_expression,
                    arguments
                ) = extract_command_with_arguments(
                    line,
                    position,
                    symbol,
                    argument_count
                )

                if len(arguments) == argument_count:

                    matches.append(
                        {
                            "file": tex_file,
                            "line": line_number,
                            "column": position + 1,
                            "text": line.rstrip(),
                            "matched_expression":
                                matched_expression,
                            "arguments":
                                arguments
                        }
                    )

                search_position = (
                    position + len(symbol)
                )

    return matches


def main():
    """
    Run the scanner manually
    from the terminal.
    """

    symbol = input(
        r"Enter LaTeX symbol, for example \sqrt or \frac: "
    ).strip()

    if not symbol:
        print("No symbol was entered.")
        return

    argument_input = input(
        "Number of arguments "
        "(press Enter for 1): "
    ).strip()

    if not argument_input:
        argument_count = 1

    else:

        try:
            argument_count = int(
                argument_input
            )

        except ValueError:
            print(
                "Argument count must be a number."
            )
            return

    matches = find_symbol_occurrences(
        symbol,
        argument_count
    )

    print()
    print(
        f"Found {len(matches)} "
        "matching occurrence(s)."
    )
    print()

    for number, match in enumerate(
        matches,
        start=1
    ):

        print("=" * 60)

        print(
            f"Candidate {number} "
            f"of {len(matches)}"
        )

        print()

        print(
            f"File: {match['file'].name}"
        )

        print(
            f"Line: {match['line']}"
        )

        print(
            f"Column: {match['column']}"
        )

        print()

        print("Full text:")
        print(
            match["text"]
        )

        print()

        print("Matched expression:")
        print(
            match["matched_expression"]
        )

        print()

        print("Extracted arguments:")

        for argument_number, argument in enumerate(
            match["arguments"],
            start=1
        ):

            print(
                f"Argument {argument_number}: "
                f"{argument}"
            )

        print()


if __name__ == "__main__":
    main()