from pathlib import Path


# Folder containing the LaTeX papers
PAPERS_DIR = Path(__file__).resolve().parents[1] / "papers"


def remove_latex_comment(line):
    """
    Remove the comment part of a LaTeX line.

    Example:
        x = \\sqrt{4} % comment

    becomes:
        x = \\sqrt{4}
    """

    comment_position = line.find("%")

    if comment_position != -1:
        return line[:comment_position]

    return line


def extract_braced_expression(line, position, symbol):
    """
    Extract the complete LaTeX expression belonging to a symbol.

    Example:

        \\sqrt{3}

    becomes:

        \\sqrt{3}

    It also works with nested expressions such as:

        \\sqrt{\\frac{B^2}{4} - C}
    """

    start = position

    # Move to the character immediately after the symbol
    current = position + len(symbol)

    # Ignore spaces after the symbol
    while current < len(line) and line[current].isspace():
        current += 1

    # If the symbol is not followed by {
    # return only the symbol
    if current >= len(line) or line[current] != "{":
        return symbol

    brace_depth = 0

    # Read characters until the matching closing brace is found
    for index in range(current, len(line)):

        if line[index] == "{":
            brace_depth += 1

        elif line[index] == "}":
            brace_depth -= 1

            # We found the closing brace belonging to the symbol
            if brace_depth == 0:
                return line[start:index + 1]

    # If something is incomplete, return only the symbol
    return symbol


def find_symbol_occurrences(symbol):
    """
    Search all .tex files inside the papers folder.

    Return information about every occurrence of
    the selected LaTeX symbol.
    """

    matches = []

    # Search all .tex files, including files inside subfolders
    for tex_file in PAPERS_DIR.rglob("*.tex"):

        lines = tex_file.read_text(
            encoding="utf-8",
            errors="replace"
        ).splitlines()

        # Check every line
        for line_number, original_line in enumerate(lines, start=1):

            # Ignore LaTeX comments
            line = remove_latex_comment(original_line)

            search_position = 0

            # A line can contain the same symbol more than once
            while True:

                position = line.find(symbol, search_position)

                # No more matches on this line
                if position == -1:
                    break

                # Extract the complete expression
                matched_expression = extract_braced_expression(
                    line,
                    position,
                    symbol
                )

                matches.append(
                    {
                        "file": tex_file,
                        "line": line_number,
                        "column": position + 1,
                        "text": line.rstrip(),
                        "matched_expression": matched_expression
                    }
                )

                # Continue searching after this occurrence
                search_position = position + len(symbol)

    return matches


def main():
    """
    Run the symbol scanner from the terminal.
    """

    symbol = input(
        r"Enter a LaTeX symbol, for example \sqrt: "
    ).strip()

    # Stop if the user enters nothing
    if not symbol:
        print("No symbol was entered.")
        return

    matches = find_symbol_occurrences(symbol)

    print()
    print(f"Found {len(matches)} occurrence(s).")
    print()

    # Display every result
    for number, match in enumerate(matches, start=1):

        print("=" * 60)
        print(f"Candidate {number} of {len(matches)}")
        print()
        print(f"File: {match['file'].name}")
        print(f"Line: {match['line']}")
        print(f"Column: {match['column']}")
        print()
        print(f"Full text:")
        print(match["text"])
        print()
        print("Matched expression:")
        print(match["matched_expression"])
        print()


if __name__ == "__main__":
    main()