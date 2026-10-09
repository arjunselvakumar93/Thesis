import hashlib
import json
import sys
from pathlib import Path

from smglom_notation_extractor import scan_available_smglom


PROJECT_DIR = Path(__file__).resolve().parents[1]
PAPERS_DIR = PROJECT_DIR / "papers"

DECISIONS_DIR = PROJECT_DIR / "decisions"

DECISION_FILE = (
    DECISIONS_DIR
    / "review_results.json"
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
    from expression_parser import NotationRegistry
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
    from notation_catalog import configured_annotation_candidates
    for candidate in configured_annotation_candidates(symbol, configuration):
        if not any(item.get("macro") == candidate.get("macro") for item in candidates):
            candidates.append(candidate)
    for candidate in candidates:
        rule = candidate.get("classification", {})
        visible = next((rule.get(key) for key in
                        ("operator", "separator", "command", "token") if rule.get(key)), symbol)
        review_label = configuration.get("review_labels", {}).get(visible)
        argument_labels = configuration.get("review_argument_labels", {}).get(visible)
        if review_label or argument_labels:
            additions = {}
            if review_label:
                additions["structural_name"] = review_label
            if argument_labels:
                additions["review_argument_labels"] = dict(argument_labels)
            candidate["classification"] = dict(candidate["classification"], **additions)
    return candidates


def structural_review_classification(symbol, configuration):
    """Return display-only structural metadata for explicitly reviewable syntax.

    The opt-in ``review_labels`` entry is deliberately separate from semantic
    mappings.  It lets the UI explain a parsed scope when its real semantic
    catalogue is unavailable, without producing an annotation candidate.
    """
    from expression_parser import NotationRegistry
    if symbol not in configuration.get("review_labels", {}):
        return None
    registry = NotationRegistry.from_config(configuration)
    operator = registry.operators.get((symbol, "infix"))
    if operator is None:
        return None
    pattern = "sequence" if operator.arity == "variable" else "binary_infix"
    rule = {
        "pattern_type": pattern,
        "separator" if pattern == "sequence" else "operator": operator.token,
        "argument_count": 2,
        "structural_name": configuration["review_labels"][symbol],
    }
    labels = configuration.get("review_argument_labels", {}).get(symbol)
    if labels:
        rule["review_argument_labels"] = dict(labels)
    status = configuration.get("review_unavailable_status", {}).get(symbol)
    if status:
        rule["semantic_unavailable_status"] = status
    return rule


def run_structural_preview(symbol, papers_dir=PAPERS_DIR, expression_config=None,
                           parsed_regions=None, show_ast=False, scope_order="innermost",
                           semantic_status=None):
    """Render parsed scopes with no semantic candidate and never write decisions."""
    if expression_config is None:
        expression_config = json.loads(
            (PROJECT_DIR / "expression-config.example.json").read_text(encoding="utf-8-sig"))
    from notation_catalog import build_expression_config
    from expression_document import find_structural_occurrences, parse_papers
    expression_config = build_expression_config(expression_config)
    regions = parsed_regions if parsed_regions is not None else parse_papers(
        papers_dir, expression_config)
    matches = find_structural_occurrences(
        regions, symbol, expression_config, semantic_status)
    from review_scope import build_review_scopes, render_review_scope
    scopes = build_review_scopes(matches, (), scope_order, include_structural=True)
    if not scopes:
        return 0
    print(f"Found {len(scopes)} structural review scope(s) in the paper collection.")
    for number, scope in enumerate(scopes, 1):
        print(render_review_scope(scope, number, len(scopes), show_ast))
    print("\nPreview mode: no decisions were written.")
    return len(scopes)

def generate_classified_suggestion(match, candidate):
    """Build a semantic macro call from a classified notation match."""
    pattern_type = candidate["classification"].get("pattern_type")
    arguments = match.get("arguments", [])
    if match.get("pattern_type") != pattern_type:
        return None

    if candidate["classification"].get("grammar_adapter"):
        from notation_catalog import adapter_suggestion
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


def build_review_decision(match, selected, classification, papers_dir, decision="accepted"):
    """Build the persisted record after an explicit human candidate choice."""
    suggestion = generate_classified_suggestion(match, selected)
    if suggestion is None:
        return None
    macro = selected.get("macro", selected.get("semantic_macro"))
    base = Path(papers_dir)
    match_file = Path(match["file"])
    relative = match_file.name if base.is_file() else str(match_file.relative_to(base))
    record = {
        "visible_symbol": (match.get("matched_visible_notation")
                           if match.get("matched_visible_notation") is not None
                           else classification.get("separator", "")),
        "semantic_macro": macro,
        "semantic_meaning": selected.get("name", macro),
        "semantic_module": selected.get("module"),
        "semantic_repository": selected.get("repository"),
        "pattern_type": match["pattern_type"],
        "argument_count": len(match["arguments"]),
        "arguments": match["arguments"],
        "file": relative,
        "line": match["line"], "column": match["column"],
        "full_text": match["text"],
        "matched_expression": match["matched_expression"],
        "semantic_suggestion": suggestion,
        "decision": decision,
        "argument_roles": list(classification.get("argument_roles", [])),
        "configuration_source": selected.get(
            "semantic_source", selected.get("source", "SMGloM" if selected.get("record") else
                                             "user configuration")),
        "candidate_classification": dict(selected["classification"]),
        "adapter_family": selected["classification"].get("grammar_adapter"),
    }
    if "expression_config" in classification:
        record["expression_config"] = classification["expression_config"]
    record["start"] = match["source_start"]
    record["end"] = match["source_end"]
    record["review_target_selection"] = match.get(
        "review_target_selection", "automatic")
    source_snapshot = match["source_expression"]
    record["source_hash"] = hashlib.sha256(source_snapshot.encode("utf-8")).hexdigest()
    identity = "\0".join((record["file"], str(record["start"]),
                            str(record["end"]), macro, source_snapshot))
    record["decision_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return record


def run_generic_review(symbol, candidates, papers_dir=PAPERS_DIR, expression_config=None,
                       parsed_regions=None, preview=False, show_ast=False,
                       scope_order="innermost", range_selection=None):
    """Review classified matches and persist the human's decisions."""
    if range_selection is None:
        # Preserve the programmatic API while enabling correction in the
        # actual interactive CLI. Preview selection remains explicitly opt-in.
        range_selection = (__name__ == "__main__"
                           and (not preview or "--select-range" in sys.argv[1:]))
    # Parse complete regions before offering any semantic choice.
    if expression_config is None:
        expression_config = json.loads(
            (PROJECT_DIR / "expression-config.example.json").read_text(encoding="utf-8-sig"))
    from notation_catalog import build_expression_config
    from expression_document import parse_papers
    expression_config = build_expression_config(expression_config)
    if parsed_regions is None:
        parsed_regions = parse_papers(papers_dir, expression_config)
    for region in parsed_regions:
        if region.error:
            print(f"Unparsed math region in {region.file}, offset {region.start}: {region.error}")
    from expression_annotation import document_context, rank_candidates
    paths = [Path(papers_dir)] if Path(papers_dir).is_file() else sorted(Path(papers_dir).rglob("*.tex"))
    source = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths)
    records = scan_available_smglom(quiet=True)
    from notation_catalog import build_catalog
    context = document_context(source, build_catalog(records))
    history = []
    occurrence = {"visible_symbol": symbol}
    if Path(papers_dir).is_file():
        occurrence["file"] = Path(papers_dir).name
        from apply_annotations import materialize_valid_decisions, validate_decisions
        accepted = [item for item in load_existing_decisions() if item.get("decision") == "accepted"]
        history = materialize_valid_decisions(validate_decisions(accepted, records, papers_dir))
    candidates = rank_candidates(candidates, occurrence, context, history)
    if not candidates:
        print("No semantic candidates were found.")
        return 0
    # Ranked candidates in one review group share the structural notation.
    # Semantic selection happens only after the exact source scope is shown.
    selected = candidates[0]

    classification = dict(selected["classification"], expression_config=expression_config)
    from expression_document import find_ast_occurrences
    matches = find_ast_occurrences(
        parsed_regions, classification, expression_config, selected)
    if not matches:
        print("No matching occurrences were found in the papers.")
        return 0

    for match in matches:
        match.setdefault("classification", dict(classification))
        match.setdefault("argument_roles", list(classification.get("argument_roles", ())))
    from review_scope import build_review_scopes, render_review_scope
    scopes = build_review_scopes(matches, candidates, scope_order)
    if not scopes:
        print("No review-ready occurrences were found in the papers.")
        return 0

    print(f"Found {len(scopes)} review scope(s) in the paper collection.")
    decisions = []
    for number, scope in enumerate(scopes, start=1):
        print(render_review_scope(scope, number, len(scopes), show_ast))
        if range_selection:
            scope, range_action = choose_review_range(
                scope, expression_config, parsed_regions, records,
                ranking_context=context, accepted_history=history)
            if range_action == "quit":
                break
            if range_action == "skip":
                continue
            if range_action == "changed":
                print("\nSelected scope:")
                print(scope.highlighted)
                print(render_review_scope(scope, show_ast=show_ast))
        match = scope.match
        if not scope.semantic_candidates:
            continue
        selected = scope.semantic_candidates[0] if preview else choose_candidate(
            list(scope.semantic_candidates))
        if selected is None:
            print("Review stopped without selecting a semantic meaning for this scope.")
            break
        suggestion = generate_classified_suggestion(match, selected)
        if suggestion is None:
            continue
        print(f"\nSelected semantic macro: \\{selected['macro']}")
        print(f"Proposed annotation: {suggestion}")
        if preview:
            continue
        decision = ask_for_decision()
        if decision == "quit":
            break
        decisions.append(build_review_decision(
            match, selected, classification, papers_dir, decision))

    if preview:
        print("\nPreview mode: no decisions were written.")
        return len(scopes)
    total = save_decisions(decisions)
    print(f"Saved {len(decisions)} decision(s). Total stored: {total}")
    print(f"Decision file: {DECISION_FILE}")
    if any(item["decision"] == "accepted" for item in decisions):
        print("Run 'py src/apply_annotations.py' to create annotated copies.")
    return len(decisions)


def recompute_review_scope(scope, alternative, configuration, parsed_regions, records=None,
                           ranking_context=None, accepted_history=()):
    """Rebuild structure and semantics for the selected exact source range."""
    from review_scope import build_review_scope, match_for_alternative
    from expression_document import find_ast_occurrences
    from expression_annotation import match_candidate
    from expression_parser import NotationRegistry
    structural_match = match_for_alternative(scope, alternative)
    node = alternative['node']
    query = node.notation or node.value or alternative['node'].text
    # Re-evaluate both candidates already loaded for this review session and
    # candidates discoverable for the newly selected head. Exact structural
    # matching below decides which remain valid for the corrected range.
    candidates, seen = [], set()
    for candidate in (*scope.semantic_candidates,
                      *find_expression_candidates(query, configuration, records)):
        identity = (candidate.get("macro", candidate.get("semantic_macro")),
                    candidate.get("module"), candidate.get("repository"),
                    repr(candidate.get("classification", {})))
        if identity not in seen:
            seen.add(identity)
            candidates.append(candidate)
    valid, semantic_match = [], None
    for candidate in candidates:
        matches = find_ast_occurrences(
            parsed_regions, candidate["classification"], configuration, candidate)
        selected = next((item for item in matches
                         if item.get("source_start") == alternative['start']
                         and item.get("source_end") == alternative['end']), None)
        if selected is not None:
            selected.setdefault("classification", dict(candidate["classification"]))
            valid.append(candidate)
            semantic_match = semantic_match or selected
            continue
        local = match_candidate(
            alternative['node'], candidate, NotationRegistry.from_config(configuration))
        exact = next((item for item in local
                      if item.get("start") == 0
                      and item.get("end") == len(alternative['node'].text)), None)
        if exact is not None:
            valid.append(candidate)
            if semantic_match is None:
                structural_match.update(
                    classification=dict(candidate["classification"]),
                    pattern_type=exact["pattern_type"],
                    arguments=list(exact.get("arguments", ())),
                    argument_roles=list(exact.get("argument_roles", ())),
                    matched_visible_notation=exact.get("matched_visible_notation", query),
                    structural_node=False)
                semantic_match = structural_match
    if semantic_match is not None:
        semantic_match["review_target_selection"] = "user_corrected"
        from expression_annotation import rank_candidates
        ranked = rank_candidates(
            valid, {
                "matched_visible_notation": semantic_match.get("matched_visible_notation", query),
                "pattern_type": semantic_match.get("pattern_type"),
                "file": semantic_match.get("file"),
            }, ranking_context, accepted_history)
        return build_review_scope(semantic_match, ranked)
    return build_review_scope(structural_match, ())


def choose_review_range(scope, configuration, parsed_regions, records=None, input_fn=None,
                        ranking_context=None, accepted_history=()):
    """Accept or replace the suggested scope without exposing the raw AST."""
    from review_scope import scope_alternatives
    input_fn = input if input_fn is None else input_fn
    while True:
        choice = input_fn(
            "[A] Accept suggested range  [R] Change range  [S] Skip  [Q] Quit: "
        ).strip().lower()
        if choice == "a":
            return scope, "accepted"
        if choice == "s":
            return scope, "skip"
        if choice == "q":
            return scope, "quit"
        if choice != "r":
            print("Please enter A, R, S, or Q.")
            continue
        alternatives = scope_alternatives(scope, configuration)
        if not alternatives:
            print("No alternative structurally complete ranges are available.")
            continue
        print("\nAlternative ranges:")
        for index, alternative in enumerate(alternatives, 1):
            print(f"[{index}] {alternative['node'].text}")
        selected = input_fn("Select a range number (or A to keep suggested): ").strip()
        if selected.lower() == "a":
            return scope, "accepted"
        if not selected.isdigit() or not 1 <= int(selected) <= len(alternatives):
            print("Please enter a listed range number or A.")
            continue
        alternative = alternatives[int(selected) - 1]
        return recompute_review_scope(
            scope, alternative, configuration, parsed_regions, records,
            ranking_context, accepted_history), "changed"


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
    """Read the current decision list; refuse to overwrite malformed data."""
    if not DECISION_FILE.exists():
        return []
    data = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("The decision file must contain a list")
    return data


def decision_key(decision):
    if decision.get("start") is None:
        return (decision.get("decision_id"),)
    return (decision.get("file"), decision.get("start"), decision.get("end"),
            decision.get("visible_symbol"))


def save_decisions(decisions):
    """Replace rereviewed occurrences, retaining all other decisions."""
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    merged = {decision_key(item): item for item in load_existing_decisions()}
    merged.update({decision_key(item): item for item in decisions})
    temporary = DECISION_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(list(merged.values()), indent=2, ensure_ascii=False),
                         encoding="utf-8")
    temporary.replace(DECISION_FILE)
    return len(merged)


def main():
    # Windows terminals commonly default to a legacy code page. Preserve
    # direct Unicode mathematical input and output without source rewriting.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    papers_target = PAPERS_DIR
    preview = "--preview" in sys.argv
    show_ast = "--show-ast" in sys.argv
    scope_order = "innermost"
    if "--scope-order" in sys.argv:
        index = sys.argv.index("--scope-order")
        if index + 1 >= len(sys.argv) or sys.argv[index + 1] not in {"innermost", "outermost"}:
            raise ValueError("--scope-order requires innermost or outermost")
        scope_order = sys.argv[index + 1]
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
        from notation_catalog import build_expression_config
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

    from expression_parser import STRUCTURAL_TOKENS
    if symbol in STRUCTURAL_TOKENS:
        descriptions = {"^": "superscript", "_": "subscript", "%": "comment",
                        "#": "macro parameter", "{": "grouping", "}": "grouping"}
        print(f"This is TeX {descriptions.get(symbol, 'structural')} syntax handled by the expression parser, "
              "not a standalone semantic notation.")
        return

    generic_candidates = (find_expression_candidates(symbol, configuration)
                          if parsed_regions is not None else find_generic_candidates(symbol))
    if generic_candidates:
        review_options = {}
        if preview:
            review_options["preview"] = True
        if show_ast:
            review_options["show_ast"] = True
        if scope_order != "innermost":
            review_options["scope_order"] = scope_order
        if configuration is None:
            if paper_option:
                run_generic_review(symbol, generic_candidates, papers_dir=papers_target,
                                   **review_options)
            else:
                run_generic_review(symbol, generic_candidates, **review_options)
        else:
            if paper_option:
                run_generic_review(symbol, generic_candidates, papers_dir=papers_target,
                                   expression_config=configuration, parsed_regions=parsed_regions,
                                   **review_options)
            else:
                run_generic_review(symbol, generic_candidates, expression_config=configuration,
                                   parsed_regions=parsed_regions, **review_options)
        return
    from notation_catalog import build_catalog, search_catalog, display_catalog
    matches = search_catalog(build_catalog(scan_available_smglom(), configuration), symbol)
    status = None
    if matches:
        blockers = {item.get("classification", {}).get("annotation_blocker")
                    for item in matches}
        status = ("Real semantic candidates exist, but required argument mapping is incomplete."
                  if "missing_argument_mapping" in blockers else
                  "Semantic catalogue records exist, but they are not annotation-ready.")
    structural_config = configuration
    structural_regions = parsed_regions
    if structural_config is None:
        structural_config = json.loads(
            (PROJECT_DIR / "expression-config.example.json").read_text(encoding="utf-8-sig"))
    if run_structural_preview(
            symbol, papers_target, structural_config, structural_regions,
            show_ast, scope_order, status):
        return
    if matches:
        display_catalog(matches)
        print("These records need annotation metadata or a grammar adapter before review.")
    else:
        print("No matching notation was found in the loaded catalogue or configuration.")
    return



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


if __name__ == "__main__":
    main()
