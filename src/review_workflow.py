import json
import sys
from pathlib import Path

from symbol_scanner import (
    PAPERS_DIR,
    find_symbol_occurrences,
)

from smglom_lookup import (
    find_symbol_definitions,
    choose_candidate,
)
from smglom_notation_extractor import scan_available_smglom
from notation_classifier import classify_notation
from notation_scanner import find_notation_occurrences
from tex_notation_aliases import canonicalize_tex_notation


PROJECT_DIR = Path(__file__).resolve().parents[1]

DECISIONS_DIR = PROJECT_DIR / "decisions"

DECISION_FILE = (
    DECISIONS_DIR
    / "review_results.json"
)


def generate_semantic_suggestion(
    symbol,
    matched_expression,
    semantic_macro
):
    """
    Replace the visible LaTeX command with
    the selected semantic sTeX macro.

    Example 1:

        \\sqrt{3}
        ->
        \\asqrt{3}

    Example 2:

        \\frac{a}{b}
        ->
        \\adivide{a}{b}
    """

    replacement = "\\" + semantic_macro

    return matched_expression.replace(
        symbol,
        replacement,
        1
    )


def find_generic_candidates(symbol, records=None):
    """Find semantic records whose classified visible notation equals symbol."""
    if records is None:
        records = scan_available_smglom()

    definitions = {
        (record.get("repository"), record.get("module"), record["macro"]): record
        for record in records
        if record.get("declaration_type") == "symdef"
    }
    candidates = []
    for record in records:
        classification = classify_notation(record)
        pattern_type = classification.get("pattern_type")
        visible = classification.get({
            "command": "command", "binary_infix": "operator", "sequence": "separator"
        }.get(pattern_type, ""))
        if canonicalize_tex_notation(visible) != canonicalize_tex_notation(symbol):
            continue

        if record.get("declaration_type") == "symdef":
            definition = record
            macro = record["macro"]
        else:
            macro = record.get("target", "").rsplit("?", 1)[-1]
            definition = definitions.get(
                (record.get("repository"), record.get("module"), macro)
            )
            if definition is None:
                continue

        candidates.append({
            "macro": macro,
            "name": definition.get("name"),
            "args": definition.get("args"),
            "module": definition.get("module"),
            "repository": record.get("repository"),
            "record": record,
            "classification": classification,
        })
    return candidates


def generate_classified_suggestion(match, candidate):
    """Build a semantic macro call from a classified notation match."""
    pattern_type = candidate["classification"].get("pattern_type")
    arguments = match.get("arguments", [])
    if match.get("pattern_type") != pattern_type or not arguments:
        return None

    macro = "\\" + candidate["macro"]
    if pattern_type == "sequence":
        if candidate.get("args") != "a" or len(arguments) < 2:
            return None
        return macro + "{" + ",".join(arguments) + "}"

    if pattern_type in {"command", "binary_infix"}:
        expected = candidate["classification"].get("argument_count")
        if expected != len(arguments):
            return None
        return macro + "".join("{" + argument + "}" for argument in arguments)

    return None


def run_generic_review(symbol, candidates, papers_dir=PAPERS_DIR):
    """Review classified matches and persist the human's decisions."""
    display_semantic_candidates(candidates)
    selected = choose_candidate(candidates)
    if selected is None:
        print("No semantic candidate selected.")
        return 0

    matches = find_notation_occurrences(selected["classification"], papers_dir)
    if not matches:
        print("No matching occurrences were found in the papers.")
        return 0

    print(f"Found {len(matches)} occurrence(s) in the paper collection.")
    decisions = []
    for number, match in enumerate(matches, start=1):
        suggestion = generate_classified_suggestion(match, selected)
        if suggestion is None:
            continue
        print("=" * 60)
        print(f"Candidate {number} of {len(matches)}")
        print(f"File: {match['file'].name}")
        print(f"Line: {match['line']}  Column: {match['column']}")
        print(f"Full text: {match['text']}")
        print(f"Matched expression: {match['matched_expression']}")
        print(f"Matched visible notation: {match['matched_visible_notation']}")
        print(f"Extracted arguments: {match['arguments']}")
        print(f"Semantic sTeX suggestion: {suggestion}")
        decision = ask_for_decision()
        if decision == "quit":
            break
        decisions.append({
            "visible_symbol": match["matched_visible_notation"],
            "semantic_macro": selected["macro"],
            "semantic_meaning": selected["name"],
            "semantic_module": selected["module"],
            "semantic_repository": selected["repository"],
            "pattern_type": match["pattern_type"],
            "argument_count": len(match["arguments"]),
            "arguments": match["arguments"],
            "file": str(match["file"].relative_to(papers_dir)),
            "line": match["line"],
            "column": match["column"],
            "full_text": match["text"],
            "matched_expression": match["matched_expression"],
            "semantic_suggestion": suggestion,
            "decision": decision,
        })

    total = save_decisions(decisions)
    print(f"Saved {len(decisions)} decision(s). Total stored: {total}")
    print(f"Decision file: {DECISION_FILE}")
    if any(item["decision"] == "accepted" for item in decisions):
        print("Run 'py src/apply_annotations.py' to create annotated copies.")
    return len(decisions)


def ask_for_decision():
    """
    Ask the user what to do with the suggestion.
    """

    while True:

        choice = input(
            "[A] Accept  "
            "[R] Reject  "
            "[S] Skip  "
            "[Q] Quit: "
        ).strip().lower()

        if choice == "a":
            return "accepted"

        if choice == "r":
            return "rejected"

        if choice == "s":
            return "skipped"

        if choice == "q":
            return "quit"

        print(
            "Please enter A, R, S, or Q."
        )


def load_existing_decisions():
    """
    Load previously saved review decisions.

    If no decision file exists yet,
    return an empty list.
    """

    if not DECISION_FILE.exists():
        return []

    try:

        data = json.loads(
            DECISION_FILE.read_text(
                encoding="utf-8"
            )
        )

    except json.JSONDecodeError:

        print(
            "Warning: Existing decision file "
            "could not be read."
        )

        return []

    if not isinstance(
        data,
        list
    ):

        print(
            "Warning: Existing decision file "
            "does not contain a list."
        )

        return []

    return data


def decision_key(
    decision
):
    """
    Create an identifier for one formula occurrence.

    The file, line, column and visible symbol
    together identify the reviewed occurrence.

    If the same occurrence is reviewed again,
    the newest decision replaces the older one.
    """

    return (
        decision.get(
            "file"
        ),
        decision.get(
            "line"
        ),
        decision.get(
            "column"
        ),
        decision.get(
            "visible_symbol"
        ),
    )


def save_decisions(
    decisions
):
    """
    Preserve previous review decisions
    and add decisions from the current session.

    Previous sessions are not overwritten.

    If the same occurrence is reviewed again,
    the newest decision replaces the old one.
    """

    DECISIONS_DIR.mkdir(
        exist_ok=True
    )

    existing_decisions = (
        load_existing_decisions()
    )

    merged_decisions = {
        decision_key(
            decision
        ): decision
        for decision
        in existing_decisions
    }

    for decision in decisions:

        merged_decisions[
            decision_key(
                decision
            )
        ] = decision

    final_decisions = list(
        merged_decisions.values()
    )

    DECISION_FILE.write_text(
        json.dumps(
            final_decisions,
            indent=4,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    return len(
        final_decisions
    )


def display_semantic_candidates(
    results
):
    """
    Display semantic candidates found
    automatically in SMGloM.
    """

    print()

    print(
        f"Found {len(results)} possible "
        "semantic candidate(s)."
    )

    print()

    for number, result in enumerate(
        results,
        start=1
    ):

        print(
            f"[{number}] "
            f"\\{result['macro']} "
            f"- {result['name']} "
            f"- arguments: {result['args']} "
            f"- module: {result['module']}"
        )

    print()


def main():

    # ---------------------------------------------
    # STEP 1
    # User selects visible LaTeX notation
    # ---------------------------------------------

    symbol = input(
        r"Enter visible notation (for example +, -, <, >, =, /, \ne, \times, \cdot, or \frac): "
    ).strip()

    if not symbol:

        print(
            "No symbol was entered."
        )

        return

    if "--legacy" not in sys.argv[1:]:
        generic_candidates = find_generic_candidates(symbol)
        if generic_candidates:
            run_generic_review(symbol, generic_candidates)
            return
        if not symbol.startswith("\\"):
            print("No supported SMGloM notation rule was found for this symbol.")
            return

    # ---------------------------------------------
    # STEP 2
    # Search SMGloM
    # ---------------------------------------------

    semantic_candidates = (
        find_symbol_definitions(
            symbol
        )
    )

    if not semantic_candidates:

        print()

        print(
            "No matching SMGloM "
            "definitions were found."
        )

        return

    display_semantic_candidates(
        semantic_candidates
    )

    # ---------------------------------------------
    # STEP 3
    # User chooses semantic meaning
    # ---------------------------------------------

    selected = choose_candidate(
        semantic_candidates
    )

    if selected is None:

        print()

        print(
            "No semantic candidate selected."
        )

        return

    semantic_macro = selected[
        "macro"
    ]

    semantic_module = selected[
        "module"
    ]

    semantic_meaning = selected[
        "name"
    ]

    argument_value = selected[
        "args"
    ]

    # Current prototype supports
    # numeric argument counts such as 1 or 2.
    try:

        argument_count = int(
            argument_value
        )

    except (
        TypeError,
        ValueError
    ):

        print()

        print(
            "This semantic definition does not "
            "use a simple numeric argument count."
        )

        print(
            "It is not supported by the "
            "current prototype yet."
        )

        return

    print()

    print(
        "Selected semantic interpretation:"
    )

    print(
        f"Macro: \\{semantic_macro}"
    )

    print(
        f"Meaning: {semantic_meaning}"
    )

    print(
        f"Module: {semantic_module}"
    )

    print(
        f"Arguments: {argument_count}"
    )

    # ---------------------------------------------
    # STEP 4
    # Search all documents
    # ---------------------------------------------

    matches = find_symbol_occurrences(
        symbol,
        argument_count
    )

    if not matches:

        print()

        print(
            "No matching occurrences "
            "were found in the papers."
        )

        return

    print()

    print(
        f"Found {len(matches)} occurrence(s) "
        "in the paper collection."
    )

    print()

    print(
        "Review each semantic suggestion."
    )

    print()

    decisions = []

    # ---------------------------------------------
    # STEP 5
    # Review each occurrence
    # ---------------------------------------------

    for number, match in enumerate(
        matches,
        start=1
    ):

        suggestion = (
            generate_semantic_suggestion(
                symbol,
                match[
                    "matched_expression"
                ],
                semantic_macro
            )
        )

        print(
            "=" * 60
        )

        print(
            f"Candidate {number} "
            f"of {len(matches)}"
        )

        print()

        print(
            f"File: "
            f"{match['file'].name}"
        )

        print(
            f"Line: "
            f"{match['line']}"
        )

        print(
            f"Column: "
            f"{match['column']}"
        )

        print()

        print(
            "Full text:"
        )

        print(
            match[
                "text"
            ]
        )

        print()

        print(
            "Matched expression:"
        )

        print(
            match[
                "matched_expression"
            ]
        )

        print()

        print(
            "Extracted arguments:"
        )

        for (
            argument_number,
            argument
        ) in enumerate(
            match[
                "arguments"
            ],
            start=1
        ):

            print(
                f"Argument "
                f"{argument_number}: "
                f"{argument}"
            )

        print()

        print(
            "Semantic sTeX suggestion:"
        )

        print(
            suggestion
        )

        print()

        decision = (
            ask_for_decision()
        )

        if decision == "quit":

            print()

            print(
                "Review stopped by user."
            )

            break

        decisions.append(
            {
                "visible_symbol":
                    symbol,

                "semantic_macro":
                    semantic_macro,

                "semantic_meaning":
                    semantic_meaning,

                "semantic_module":
                    semantic_module,

                "argument_count":
                    argument_count,

                "arguments":
                    match[
                        "arguments"
                    ],

                "file":
                    str(
                        match[
                            "file"
                        ].relative_to(
                            PAPERS_DIR
                        )
                    ),

                "line":
                    match[
                        "line"
                    ],

                "column":
                    match[
                        "column"
                    ],

                "full_text":
                    match[
                        "text"
                    ],

                "matched_expression":
                    match[
                        "matched_expression"
                    ],

                "semantic_suggestion":
                    suggestion,

                "decision":
                    decision
            }
        )

        print()

    # ---------------------------------------------
    # STEP 6
    # Save review results
    # ---------------------------------------------

    total_saved = save_decisions(
        decisions
    )

    print()

    print(
        f"Saved {len(decisions)} "
        "decision(s) from this session."
    )

    print()

    print(
        f"Total stored decisions: "
        f"{total_saved}"
    )

    print()

    print(
        "Decision file:"
    )

    print(
        DECISION_FILE
    )


if __name__ == "__main__":
    main()
