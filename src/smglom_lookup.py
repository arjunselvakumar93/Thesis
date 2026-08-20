import re
from pathlib import Path


# Main project folder
PROJECT_DIR = Path(__file__).resolve().parents[1]

# SMGloM arithmetic source files
SMGLOM_DIR = (
    PROJECT_DIR
    / "reference"
    / "arithmetics"
    / "source"
)


def get_module_name(line):
    """
    Read the name of an sTeX module.

    Example:

    \\begin{smodule}[title={General Arithmetics}]{arithmetics}

    returns:

    arithmetics
    """

    match = re.search(
        r"\\begin\{smodule\}(?:\[[^\]]*\])?\{([^}]+)\}",
        line
    )

    if match:
        return match.group(1)

    return None


def get_symdef_information(line):
    """
    Extract information from a simple \\symdef declaration.

    We try to obtain:

    - semantic macro name
    - number of arguments
    - human-readable meaning
    """

    macro_match = re.search(
        r"\\symdef\{([^}]+)\}",
        line
    )

    if not macro_match:
        return None

    macro = macro_match.group(1)

    args_match = re.search(
        r"args=([^,\]]+)",
        line
    )

    name_match = re.search(
        r"name=([^,\]]+)",
        line
    )

    args = (
        args_match.group(1).strip()
        if args_match
        else None
    )

    name = (
        name_match.group(1).strip()
        if name_match
        else None
    )

    return {
        "macro": macro,
        "args": args,
        "name": name
    }


def find_symbol_definitions(symbol, required_args=None):
    """
    Search SMGloM for semantic definitions that contain
    the selected visible LaTeX symbol.

    required_args can be used to filter by argument count.

    Example:

        symbol = \\sqrt
        required_args = 1

    keeps square-root definitions such as \\asqrt
    but removes two-argument root definitions such as \\aroot.
    """

    results = []

    for tex_file in SMGLOM_DIR.rglob("*.tex"):

        lines = tex_file.read_text(
            encoding="utf-8",
            errors="replace"
        ).splitlines()

        current_module = None

        for line_number, line in enumerate(
            lines,
            start=1
        ):

            module = get_module_name(line)

            if module:
                current_module = module

            if "\\symdef" not in line:
                continue

            if symbol not in line:
                continue

            information = get_symdef_information(line)

            if information is None:
                continue

            # Filter by number of arguments
            if required_args is not None:

                if information["args"] != str(required_args):
                    continue

            results.append(
                {
                    "file": tex_file,
                    "line": line_number,
                    "module": current_module,
                    "macro": information["macro"],
                    "args": information["args"],
                    "name": information["name"],
                    "declaration": line.strip()
                }
            )

    return results


def choose_candidate(results):
    """
    Let the user choose one semantic definition.
    """

    if not results:
        return None

    while True:

        print()
        choice = input(
            "Choose a semantic candidate by number, "
            "or Q to quit: "
        ).strip()

        if choice.lower() == "q":
            return None

        if not choice.isdigit():
            print("Please enter a number or Q.")
            continue

        number = int(choice)

        if number < 1 or number > len(results):
            print(
                f"Please choose a number between "
                f"1 and {len(results)}."
            )
            continue

        return results[number - 1]


def main():

    symbol = input(
        r"Enter LaTeX symbol, for example \sqrt: "
    ).strip()

    if not symbol:
        print("No symbol entered.")
        return

    print()
    print(
        "For the current prototype we are looking "
        "for one-argument notation."
    )

    # Our current expressions look like:
    #
    # \sqrt{3}
    #
    # so they have one argument.
    required_args = 1

    results = find_symbol_definitions(
        symbol,
        required_args=required_args
    )

    print()
    print(
        f"Found {len(results)} matching "
        f"one-argument SMGloM candidate(s)."
    )
    print()

    for number, result in enumerate(
        results,
        start=1
    ):

        print("=" * 60)

        print(f"[{number}]")

        print(
            f"Semantic macro: \\{result['macro']}"
        )

        print(
            f"Meaning: {result['name']}"
        )

        print(
            f"Arguments: {result['args']}"
        )

        print(
            f"Module: {result['module']}"
        )

        print(
            f"File: {result['file'].name}"
        )

        print(
            f"Line: {result['line']}"
        )

        print()

        print("Declaration:")

        print(
            result["declaration"]
        )

        print()

    selected = choose_candidate(results)

    if selected is None:

        print()
        print("No semantic candidate selected.")

        return

    print()
    print("=" * 60)
    print("SELECTED SEMANTIC CANDIDATE")
    print("=" * 60)

    print()

    print(
        f"Macro: \\{selected['macro']}"
    )

    print(
        f"Meaning: {selected['name']}"
    )

    print(
        f"Module: {selected['module']}"
    )

    print(
        f"Arguments: {selected['args']}"
    )

    print()

    print("Example semantic macro:")

    print(
        f"\\{selected['macro']}{{...}}"
    )


if __name__ == "__main__":
    main()