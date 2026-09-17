import json
import shutil
from collections import defaultdict
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]

PAPERS_DIR = PROJECT_DIR / "papers"

DECISIONS_FILE = (
    PROJECT_DIR
    / "decisions"
    / "review_results.json"
)

OUTPUT_DIR = (
    PROJECT_DIR
    / "output"
    / "annotated_papers"
)


def build_module_declaration(module_name, repository="arithmetics"):
    """
    Create the required external sTeX module declaration.
    """

    return (
        "\\usemodule[smglom/"
        + repository
        + "]{mod?"
        + module_name
        + "}"
    )


def load_accepted_decisions():
    """
    Load only accepted annotation suggestions.
    """

    if not DECISIONS_FILE.exists():
        print("Decision file does not exist.")
        return []

    data = json.loads(
        DECISIONS_FILE.read_text(
            encoding="utf-8"
        )
    )

    accepted = []

    for decision in data:

        if (
            decision.get("decision") == "accepted"
            and decision.get("semantic_suggestion")
        ):
            accepted.append(decision)

    return accepted


def prepare_output_folder():
    """
    Create a fresh copy of the original papers.
    """

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)

    shutil.copytree(
        PAPERS_DIR,
        OUTPUT_DIR
    )


def insert_required_modules(text, module_names):
    """
    Insert all required semantic module declarations.

    Existing declarations are not inserted twice.
    """

    declarations = []

    module_specs = {
        item if isinstance(item, tuple) else ("arithmetics", item)
        for item in module_names
    }

    for repository, module_name in sorted(module_specs):

        declaration = build_module_declaration(
            module_name,
            repository
        )

        if declaration not in text:
            declarations.append(declaration)

    if not declarations:
        return text

    documentclass_end = text.find("\n")

    module_text = (
        "\n".join(declarations)
        + "\n"
    )

    if documentclass_end == -1:

        return (
            module_text
            + text
        )

    return (
        text[:documentclass_end + 1]
        + module_text
        + text[documentclass_end + 1:]
    )


def apply_annotations(decisions):
    """
    Apply accepted annotations to copied documents.
    """

    decisions_by_file = defaultdict(list)

    for decision in decisions:

        decisions_by_file[
            decision["file"]
        ].append(decision)

    number_of_replacements = 0

    for relative_file, file_decisions in decisions_by_file.items():

        output_file = (
            OUTPUT_DIR
            / Path(relative_file)
        )

        if not output_file.exists():

            print(
                f"File not found: {output_file}"
            )

            continue

        lines = output_file.read_text(
            encoding="utf-8",
            errors="replace"
        ).splitlines(
            keepends=True
        )

        decisions_by_line = defaultdict(list)

        for decision in file_decisions:

            decisions_by_line[
                int(decision["line"])
            ].append(decision)

        for line_number, line_decisions in decisions_by_line.items():

            index = line_number - 1

            if (
                index < 0
                or index >= len(lines)
            ):
                continue

            line = lines[index]

            # Replace from right to left so column
            # positions earlier in the line do not move.
            line_decisions = sorted(
                line_decisions,
                key=lambda item: int(
                    item["column"]
                ),
                reverse=True
            )

            for decision in line_decisions:

                start = (
                    int(decision["column"])
                    - 1
                )

                original = (
                    decision[
                        "matched_expression"
                    ]
                )

                replacement = (
                    decision[
                        "semantic_suggestion"
                    ]
                )

                actual_text = line[
                    start:
                    start + len(original)
                ]

                if actual_text != original:

                    print()
                    print(
                        "WARNING: Could not apply annotation."
                    )

                    print(
                        f"File: {relative_file}"
                    )

                    print(
                        f"Line: {line_number}"
                    )

                    print(
                        f"Expected: {original}"
                    )

                    print(
                        f"Found: {actual_text}"
                    )

                    print()

                    continue

                line = (
                    line[:start]
                    + replacement
                    + line[
                        start + len(original):
                    ]
                )

                number_of_replacements += 1

            lines[index] = line

        text = "".join(lines)

        required_modules = []

        for decision in file_decisions:

            module_name = decision.get(
                "semantic_module"
            )

            if module_name:
                required_modules.append(
                    (
                        decision.get(
                            "semantic_repository",
                            "arithmetics"
                        ),
                        module_name
                    )
                )

        text = insert_required_modules(
            text,
            required_modules
        )

        output_file.write_text(
            text,
            encoding="utf-8"
        )

    return number_of_replacements


def main():

    accepted = load_accepted_decisions()

    if not accepted:

        print(
            "No accepted annotations were found."
        )

        return

    print(
        f"Found {len(accepted)} "
        "accepted annotation(s)."
    )

    print()
    print(
        "Creating annotated copies..."
    )

    prepare_output_folder()

    replacements = apply_annotations(
        accepted
    )

    print()
    print(
        f"Applied {replacements} "
        "annotation(s)."
    )

    modules = sorted(
        {
            (
                decision.get(
                    "semantic_repository",
                    "arithmetics"
                ),
                decision["semantic_module"]
            )
            for decision in accepted
            if decision.get(
                "semantic_module"
            )
        }
    )

    print()
    print(
        "Required semantic module(s):"
    )

    for repository, module in modules:

        print(
            build_module_declaration(
                module,
                repository
            )
        )

    print()
    print(
        "Annotated documents are available at:"
    )

    print(
        OUTPUT_DIR
    )

    print()
    print(
        "Original documents were not modified."
    )


if __name__ == "__main__":
    main()
