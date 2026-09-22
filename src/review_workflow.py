import hashlib
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
    """Search all loaded notation data by spelling, macro, meaning or module."""
    from notation_catalog import build_catalog, search_catalog, annotation_candidates
    if records is None:
        records = scan_available_smglom()
    return annotation_candidates(search_catalog(build_catalog(records), symbol))


def find_expression_candidates(symbol, configuration, records=None):
    """Search catalogue and user definitions, including configured variants."""
    from notation_catalog import build_catalog, search_catalog, annotation_candidates
    from expression_registry import NotationRegistry
    if records is None:
        records = scan_available_smglom()
    registry = NotationRegistry.from_config(configuration)
    spellings = {symbol}
    for fixity in ("infix", "prefix", "postfix"):
        op = registry.operators.get((symbol, fixity))
        if op:
            spellings.add(op.token)
    catalogue = build_catalog(records, configuration)
    candidates = []
    for spelling in sorted(spellings):
        for candidate in annotation_candidates(search_catalog(catalogue, spelling)):
            # Imported operators in a compiled config must not duplicate the
            # actual SMGloM candidate and lose its module provenance.
            if candidate.get("record") is None and any(item["macro"] == candidate["macro"] for item in candidates):
                continue
            if candidate not in candidates:
                candidates.append(candidate)
    from expression_metadata import configured_annotation_candidates
    for candidate in configured_annotation_candidates(symbol, configuration):
        if not any((item.get("macro"), item.get("module"), item.get("repository")) ==
                   (candidate.get("macro"), candidate.get("module"), candidate.get("repository"))
                   for item in candidates):
            candidates.append(candidate)
    return candidates

def generate_classified_suggestion(match, candidate):
    """Build a semantic macro call from a classified notation match."""
    pattern_type = candidate["classification"].get("pattern_type")
    arguments = match.get("arguments", [])
    if match.get("pattern_type") != pattern_type:
        return None

    if candidate["classification"].get("grammar_adapter"):
        from notation_adapters import adapter_suggestion
        return adapter_suggestion(candidate["macro"], arguments,
                                  pattern_type == "sequence" or candidate.get("args") == "a")

    if not arguments:
        return None

    macro = "\\" + candidate["macro"]
    if pattern_type == "sequence":
        if candidate.get("args") != "a" or len(arguments) < 2:
            return None
        return macro + "{" + ",".join(arguments) + "}"

    if pattern_type in {"command", "binary_infix", "unary", "bound_construct"}:
        expected = candidate["classification"].get("argument_count")
        if expected != len(arguments):
            return None
        return macro + "".join("{" + argument + "}" for argument in arguments)

    return None


def run_generic_review(symbol, candidates, papers_dir=PAPERS_DIR, expression_config=None,
                       parsed_regions=None):
    """Review classified matches and persist the human's decisions."""
    # Whole-region grammar is established before the semantic choice. The old
    # selected-notation configuration remains a compatibility path only.
    if expression_config is not None and "selected" not in expression_config:
        from expression_metadata import build_expression_config
        from expression_document import parse_papers
        expression_config = build_expression_config(expression_config)
        if parsed_regions is None:
            parsed_regions = parse_papers(papers_dir, expression_config)
        for region in parsed_regions:
            if region.error:
                print(f"Unparsed math region in {region.file}, offset {region.start}: {region.error}")
    display_semantic_candidates(candidates)
    selected = choose_candidate(candidates)
    if selected is None:
        print("No semantic candidate selected.")
        return 0

    classification = dict(selected["classification"])
    if expression_config is not None:
        if "selected" in expression_config:
            from expression_metadata import prepare_expression_config
            expression_config = prepare_expression_config(selected, expression_config)
        classification["expression_config"] = expression_config
    if parsed_regions is None:
        matches = find_notation_occurrences(classification, papers_dir)
    else:
        from expression_document import find_ast_occurrences
        matches = find_ast_occurrences(parsed_regions, classification, expression_config)
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
            "file": (match["file"].name if Path(papers_dir).is_file()
                     else str(match["file"].relative_to(papers_dir))),
            "line": match["line"],
            "column": match["column"],
            "full_text": match["text"],
            "matched_expression": match["matched_expression"],
            "semantic_suggestion": suggestion,
            "decision": decision,
        })
        if "expression_config" in classification:
            decisions[-1]["expression_config"] = classification["expression_config"]
        for field in ("source_start", "source_end", "source_expression"):
            if field in match:
                decisions[-1][field] = match[field]
        decisions[-1]["argument_roles"] = list(classification.get("argument_roles", []))
        decisions[-1]["configuration_source"] = selected.get(
            "semantic_source", selected.get("source", "SMGloM" if selected.get("record") else "user configuration"))
        source_snapshot = decisions[-1].get("source_expression", match["matched_expression"])
        decisions[-1]["source_hash"] = hashlib.sha256(
            source_snapshot.encode("utf-8")).hexdigest()
        identity = "\0".join((
            decisions[-1]["file"], str(decisions[-1].get("source_start", "")),
            str(decisions[-1].get("source_end", "")), selected["macro"], source_snapshot))
        decisions[-1]["decision_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()

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
        decision.get("matched_expression") if "expression_config" in decision else None,
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

    papers_target = PAPERS_DIR
    paper_option = "--paper" in sys.argv
    if paper_option:
        index = sys.argv.index("--paper")
        if index + 1 >= len(sys.argv):
            raise ValueError("--paper requires a TeX file or directory")
        papers_target = Path(sys.argv[index + 1]).resolve()
        if not papers_target.exists():
            raise ValueError(f"Paper target does not exist: {papers_target}")

    if "--list-notations" in sys.argv or "--search" in sys.argv:
        from notation_catalog import main as catalog_main
        options = ["--list"]
        for flag, target in (("--search", "--search"), ("--expression-config", "--config")):
            if flag in sys.argv:
                index = sys.argv.index(flag)
                if index + 1 >= len(sys.argv):
                    raise ValueError(flag + " requires a value")
                options.extend([target, sys.argv[index + 1]])
        return catalog_main(options)

    configuration = None
    parsed_regions = None
    if "--expression-config" in sys.argv or "--report-unsupported" in sys.argv:
        path = PROJECT_DIR / "expression-config.example.json"
        if "--expression-config" in sys.argv:
            index = sys.argv.index("--expression-config")
            if index + 1 >= len(sys.argv):
                raise ValueError("--expression-config requires a JSON file path")
            path = Path(sys.argv[index + 1])
        configuration = json.loads(path.read_text(encoding="utf-8-sig"))
        if "selected" not in configuration:
            from expression_metadata import build_expression_config
            from expression_document import parse_papers
            configuration = build_expression_config(configuration)
            parsed_regions = parse_papers(papers_target, configuration)
            from parser_diagnostics import coverage_report, display_report
            from notation_catalog import build_catalog
            catalogue = build_catalog(scan_available_smglom(quiet=True), configuration)
            display_report(coverage_report(parsed_regions, catalogue),
                           "--report-unsupported" in sys.argv)
            if "--report-unsupported" in sys.argv:
                return

    # ---------------------------------------------
    # STEP 1
    # User selects visible LaTeX notation
    # ---------------------------------------------

    while True:
        symbol = input("Enter a mathematical notation, semantic macro, or search term "
                       "(type ? to browse available notations): ").strip()
        if symbol != "?":
            break
        from notation_catalog import build_catalog, display_catalog
        display_catalog(build_catalog(scan_available_smglom(), configuration))

    if not symbol:

        print(
            "No symbol was entered."
        )

        return

    from expression_registry import STRUCTURAL_TOKENS
    if symbol in STRUCTURAL_TOKENS:
        descriptions = {"^": "superscript", "_": "subscript", "%": "comment",
                        "#": "macro parameter", "{": "grouping", "}": "grouping"}
        print(f"This is TeX {descriptions.get(symbol, 'structural')} syntax handled by the expression parser, "
              "not a standalone semantic notation.")
        return

    if "--legacy" not in sys.argv[1:]:
        generic_candidates = (find_expression_candidates(symbol, configuration)
                              if parsed_regions is not None else find_generic_candidates(symbol))
        if generic_candidates:
            if configuration is None:
                if paper_option:
                    run_generic_review(symbol, generic_candidates, papers_dir=papers_target)
                else:
                    run_generic_review(symbol, generic_candidates)
            else:
                if paper_option:
                    run_generic_review(symbol, generic_candidates, papers_dir=papers_target,
                                       expression_config=configuration, parsed_regions=parsed_regions)
                else:
                    run_generic_review(symbol, generic_candidates, expression_config=configuration,
                                       parsed_regions=parsed_regions)
            return
        from notation_catalog import build_catalog, search_catalog, display_catalog
        matches = search_catalog(build_catalog(scan_available_smglom(), configuration), symbol)
        if matches:
            display_catalog(matches)
            print("These records need annotation metadata or a grammar adapter before review.")
        else:
            print("No matching notation was found in the loaded catalogue or configuration.")
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
