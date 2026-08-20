import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]

SMGLOM_DIR = (
    PROJECT_DIR
    / "reference"
    / "arithmetics"
    / "source"
)


def get_module_name(line):
    """
    Read an sTeX module name.
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
    Extract information from a symdef declaration.
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


def get_notation_target(line):
    """
    Read the semantic symbol used by a notation declaration.

    Example:

        \\notation{adivide}[...]{\\frac{#1}{#2}}

    returns:

        adivide
    """

    match = re.search(
        r"\\notation\{([^}]+)\}",
        line
    )

    if not match:
        return None

    target = match.group(1)

    # Handle qualified names such as:
    # arithmetics?exponentiation
    if "?" in target:
        target = target.split("?")[-1]

    return target


def build_symdef_index():
    """
    Build an index of semantic symbols found in SMGloM.

    The index lets us connect a notation declaration
    with the corresponding symdef.
    """

    index = {}

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

            information = get_symdef_information(line)

            if information is None:
                continue

            key = (
                current_module,
                information["macro"]
            )

            index[key] = {
                "macro": information["macro"],
                "args": information["args"],
                "name": information["name"],
                "module": current_module,
                "file": tex_file,
                "line": line_number,
                "declaration": line.strip()
            }

    return index


def find_symbol_definitions(
    symbol,
    required_args=None
):
    """
    Find semantic candidates for a visible LaTeX symbol.

    This function supports both:

    1. visible notation directly inside symdef

       example:
       \\symdef{asqrt}[args=1,...]{\\sqrt{#1}}

    2. separate notation declarations

       example:
       \\notation{adivide}[...]{\\frac{#1}{#2}}
    """

    results = []

    symdef_index = build_symdef_index()

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

            # --------------------------------------
            # Case 1: symbol appears in a symdef
            # --------------------------------------

            if "\\symdef" in line and symbol in line:

                information = get_symdef_information(
                    line
                )

                if information is None:
                    continue

                if required_args is not None:

                    if (
                        information["args"]
                        != str(required_args)
                    ):
                        continue

                results.append(
                    {
                        "file": tex_file,
                        "line": line_number,
                        "module": current_module,
                        "macro": information["macro"],
                        "args": information["args"],
                        "name": information["name"],
                        "source_type": "symdef",
                        "declaration": line.strip()
                    }
                )

            # --------------------------------------
            # Case 2: symbol appears in notation
            # --------------------------------------

            elif "\\notation" in line and symbol in line:

                target_macro = get_notation_target(
                    line
                )

                if target_macro is None:
                    continue

                key = (
                    current_module,
                    target_macro
                )

                semantic_information = (
                    symdef_index.get(key)
                )

                if semantic_information is None:
                    continue

                if required_args is not None:

                    if (
                        semantic_information["args"]
                        != str(required_args)
                    ):
                        continue

                results.append(
                    {
                        "file": tex_file,
                        "line": line_number,
                        "module": current_module,
                        "macro": target_macro,
                        "args": semantic_information["args"],
                        "name": semantic_information["name"],
                        "source_type": "notation",
                        "declaration": line.strip()
                    }
                )

    return results


def choose_candidate(results):
    """
    Let the user choose one semantic candidate.
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

        if (
            number < 1
            or number > len(results)
        ):
            print(
                f"Choose a number between "
                f"1 and {len(results)}."
            )
            continue

        return results[number - 1]


def main():

    symbol = input(
        r"Enter visible LaTeX symbol, for example \sqrt or \frac: "
    ).strip()

    if not symbol:
        print("No symbol entered.")
        return

    results = find_symbol_definitions(
        symbol
    )

    print()
    print(
        f"Found {len(results)} "
        "SMGloM candidate(s)."
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
            f"Definition type: "
            f"{result['source_type']}"
        )

        print()

        print("Declaration:")

        print(
            result["declaration"]
        )

        print()

    selected = choose_candidate(
        results
    )

    if selected is None:

        print()
        print(
            "No semantic candidate selected."
        )

        return

    print()
    print("=" * 60)
    print(
        "SELECTED SEMANTIC CANDIDATE"
    )
    print("=" * 60)

    print()

    print(
        f"Macro: \\{selected['macro']}"
    )

    print(
        f"Meaning: {selected['name']}"
    )

    print(
        f"Arguments: {selected['args']}"
    )

    print(
        f"Module: {selected['module']}"
    )

    print(
        f"Definition type: "
        f"{selected['source_type']}"
    )


if __name__ == "__main__":
    main()