import json
from pathlib import Path

from symbol_scanner import PAPERS_DIR, find_symbol_occurrences
from smglom_lookup import find_symbol_definitions, choose_candidate


PROJECT_DIR = Path(__file__).resolve().parents[1]

DECISIONS_DIR = PROJECT_DIR / "decisions"

DECISION_FILE = DECISIONS_DIR / "review_results.json"


def generate_semantic_suggestion(
    symbol,
    matched_expression,
    semantic_macro
):
    """
    Replace the visible LaTeX symbol with the
    selected semantic sTeX macro.

    Example:

        visible symbol:
        \sqrt

        selected semantic macro:
        asqrt

        expression:
        \sqrt{3}

    becomes:

        \asqrt{3}
    """

    replacement = "\\" + semantic_macro

    return matched_expression.replace(
        symbol,
        replacement,
        1
    )


def ask_for_decision():
    """
    Ask whether the current suggestion should
    be accepted, rejected, skipped, or whether
    the review should stop.
    """

    while True:

        choice = input(
            "[A] Accept  [R] Reject  "
            "[S] Skip  [Q] Quit: "
        ).strip().lower()

        if choice == "a":
            return "accepted"

        if choice == "r":
            return "rejected"

        if choice == "s":
            return "skipped"

        if choice == "q":
            return "quit"

        print("Please enter A, R, S, or Q.")


def save_decisions(decisions):
    """
    Save review decisions to JSON.
    """

    DECISIONS_DIR.mkdir(exist_ok=True)

    DECISION_FILE.write_text(
        json.dumps(
            decisions,
            indent=4,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )


def display_semantic_candidates(results):
    """
    Display semantic definitions found in SMGloM.
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
            f"- module: {result['module']}"
        )

    print()


def main():

    # -------------------------------------------------
    # STEP 1:
    # User chooses the visible LaTeX notation
    # -------------------------------------------------

    symbol = input(
        r"Enter LaTeX symbol, for example \sqrt: "
    ).strip()

    if not symbol:
        print("No symbol was entered.")
        return

    # -------------------------------------------------
    # STEP 2:
    # Search SMGloM automatically
    # -------------------------------------------------

    semantic_candidates = find_symbol_definitions(
        symbol,
        required_args=1
    )

    if not semantic_candidates:

        print()
        print(
            "No matching one-argument "
            "SMGloM definitions were found."
        )

        return

    display_semantic_candidates(
        semantic_candidates
    )

    # -------------------------------------------------
    # STEP 3:
    # User selects the semantic interpretation
    # -------------------------------------------------

    selected = choose_candidate(
        semantic_candidates
    )

    if selected is None:

        print()
        print(
            "No semantic candidate selected."
        )

        return

    semantic_macro = selected["macro"]
    semantic_module = selected["module"]
    semantic_meaning = selected["name"]

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

    # -------------------------------------------------
    # STEP 4:
    # Search the document collection
    # -------------------------------------------------

    matches = find_symbol_occurrences(
        symbol
    )

    if not matches:

        print()
        print(
            "No occurrences were found "
            "in the papers."
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

    # -------------------------------------------------
    # STEP 5:
    # Review candidates one by one
    # -------------------------------------------------

    for number, match in enumerate(
        matches,
        start=1
    ):

        suggestion = (
            generate_semantic_suggestion(
                symbol,
                match["matched_expression"],
                semantic_macro
            )
        )

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

        print(
            "Semantic sTeX suggestion:"
        )

        print(
            suggestion
        )

        print()

        decision = ask_for_decision()

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

                "file":
                    str(
                        match["file"].relative_to(
                            PAPERS_DIR
                        )
                    ),

                "line":
                    match["line"],

                "column":
                    match["column"],

                "full_text":
                    match["text"],

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

    # -------------------------------------------------
    # STEP 6:
    # Save results
    # -------------------------------------------------

    save_decisions(
        decisions
    )

    print()

    print(
        f"Saved {len(decisions)} "
        "decision(s) to:"
    )

    print(
        DECISION_FILE
    )


if __name__ == "__main__":
    main()