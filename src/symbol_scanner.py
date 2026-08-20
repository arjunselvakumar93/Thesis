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
    Find the closing brace that belongs to an opening brace.

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


def extract_command_with_arguments(
    line,
    position,
    symbol,
    argument_count
):
    """
    Extract a LaTeX command together with its braced arguments.

    One-argument example:

        sqrt-style command with {x}

    Two-argument example:

        frac-style command with {a}{b}

    Returns:

        complete expression
        list of extracted arguments
    """

    start = position
    current = position + len(symbol)

    arguments = []

    for _ in range(argument_count):

        # Allow spaces between the command and an argument
        while (
            current < len(line)
            and line[current].isspace()
        ):
            current += 1

        # Every argument must begin with {
        if (
            current >= len(line)
            or line[current] != "{"
        ):
            return symbol, []

        closing_position = find_matching_brace(
            line,
            current
        )

        if closing_position is None:
            return symbol, []

        # Store the inside of the braces
        argument = line[
            current + 1:
            closing_position
        ]

        arguments.append(argument)

        current = closing_position + 1

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
    Compatibility helper for the old one-argument workflow.
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
    Search all TeX files for occurrences of a LaTeX command.

    argument_count controls how many braced arguments
    are expected.

    Examples:

        sqrt-style command -> 1 argument
        frac-style command -> 2 arguments
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

                # Only keep candidates whose argument
                # structure matches what we expect.
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
    Run the scanner manually from the terminal.
    """

    symbol = input(
        r"Enter LaTeX symbol, for example \sqrt or \frac: "
    ).strip()

    if not symbol:
        print("No symbol was entered.")
        return

    argument_input = input(
        "Number of braced arguments "
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