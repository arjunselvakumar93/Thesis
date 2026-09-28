"""Report stored review decisions that no longer match the current scanner."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from notation_scanner import find_notation_occurrences  # compatibility patch point
from review_workflow import find_generic_candidates, find_expression_candidates
from smglom_notation_extractor import scan_available_smglom
from symbol_scanner import PAPERS_DIR


PROJECT_DIR = Path(__file__).resolve().parents[1]
DECISION_FILE = PROJECT_DIR / "decisions" / "review_results.json"


def _normalise_file(path):
    return str(path).replace("\\", "/")


def decision_occurrence_key(decision):
    return (
        _normalise_file(decision.get("file", "")),
        int(decision.get("line", 0)),
        int(decision.get("column", 0)),
        decision.get("matched_expression", ""),
    )


def match_occurrence_key(match, papers_dir):
    base = Path(papers_dir)
    relative = match["file"].name if base.is_file() else match["file"].relative_to(base)
    return (
        _normalise_file(relative),
        int(match["line"]),
        int(match["column"]),
        match["matched_expression"],
    )


def _select_candidate(decision, candidates):
    matching = [
        candidate for candidate in candidates
        if candidate.get("macro") == decision.get("semantic_macro")
    ]
    repository = decision.get("semantic_repository")
    module = decision.get("semantic_module")
    if repository:
        matching = [item for item in matching if item.get("repository") == repository]
    if module:
        matching = [item for item in matching if item.get("module") == module]
    if len(matching) == 1:
        return matching[0]
    stored = decision.get("candidate_classification")
    if stored and matching:
        keys = ("pattern_type", "grammar_adapter", "implicit", "separator", "operator",
                "command", "node_kind", "argument_roles")
        matching = [item for item in matching if all(
            item.get("classification", {}).get(key) == stored.get(key) for key in keys)]
        # Multiple catalogue records with the same semantic identity and the
        # same structural interpretation are presentation provenance variants,
        # not distinct reviewer choices.
        if matching:
            signatures = {json.dumps({key: item["classification"].get(key) for key in keys},
                                     sort_keys=True) for item in matching}
            if len(signatures) == 1:
                return matching[0]
    return None


def validate_decisions(decisions, records=None, papers_dir=PAPERS_DIR):
    """Partition decisions without changing the stored decision file."""
    if records is None:
        records = scan_available_smglom()
    candidates_by_symbol = {}
    occurrences_by_candidate = {}
    valid = []
    valid_matches = []
    stale = []
    unverifiable = []
    default_config = json.loads(
        (PROJECT_DIR / "expression-config.example.json").read_text(encoding="utf-8-sig"))

    for index, decision in enumerate(decisions):
        symbol = decision.get("visible_symbol")
        macro = decision.get("semantic_macro")
        if symbol is None or not macro:
            unverifiable.append((index, decision, "missing notation metadata"))
            continue

        direct_classification = decision.get("candidate_classification")
        search_key = (symbol, json.dumps(decision.get("expression_config"), sort_keys=True),
                      bool(direct_classification))
        if search_key not in candidates_by_symbol:
            if direct_classification:
                from notation_catalog import annotation_candidates, build_catalog
                candidates_by_symbol[search_key] = annotation_candidates(build_catalog(
                    records, decision.get("expression_config", default_config)))
            else:
                candidates_by_symbol[search_key] = (
                    find_expression_candidates(symbol, decision["expression_config"], records)
                    if "expression_config" in decision else find_generic_candidates(symbol, records))
        candidate = _select_candidate(decision, candidates_by_symbol[search_key])
        if candidate is None:
            unverifiable.append((index, decision, "semantic candidate not found uniquely"))
            continue

        candidate_key = (
            candidate.get("repository"), candidate.get("module"),
            candidate.get("macro"), symbol,
            json.dumps(decision.get("expression_config"), sort_keys=True),
        )
        classification = dict(candidate["classification"])
        if "expression_config" in decision:
            classification["expression_config"] = decision["expression_config"]
        if candidate_key not in occurrences_by_candidate:
            if "expression_config" in decision or "source_start" in decision:
                from expression_document import parse_papers, find_ast_occurrences
                from expression_metadata import build_expression_config
                expression_config = build_expression_config(
                    decision.get("expression_config", default_config), records)
                matches = find_ast_occurrences(
                    parse_papers(papers_dir, expression_config), classification,
                    expression_config, candidate)
            else:
                matches = find_notation_occurrences(classification, papers_dir)
            occurrences_by_candidate[candidate_key] = {
                match_occurrence_key(match, Path(papers_dir)): match
                for match in matches
            }

        item = (index, decision)
        matched = occurrences_by_candidate[candidate_key].get(decision_occurrence_key(decision))
        source_matches = matched is not None and all(
            decision[field] == matched.get(field)
            for field in ("source_start", "source_end", "source_expression") if field in decision)
        saved_hash = decision.get("source_hash")
        if saved_hash and matched is not None:
            current_source = matched.get("source_expression", matched.get("matched_expression", ""))
            source_matches = source_matches and saved_hash == hashlib.sha256(
                current_source.encode("utf-8")).hexdigest()
        if source_matches:
            valid.append(item)
            valid_matches.append((index, decision, matched))
        else:
            stale.append(item)

    return {"valid": valid, "valid_matches": valid_matches,
            "stale": stale, "unverifiable": unverifiable}


def materialize_valid_decisions(validation):
    """Return copies tied to the exact immutable spans recovered by validation."""
    result = []
    for _, decision, match in validation.get("valid_matches", []):
        enriched = dict(decision)
        for field in ("source_start", "source_end", "source_expression"):
            if field in match:
                enriched[field] = match[field]
        result.append(enriched)
    return result


def remove_stale_decisions(decisions, stale_indexes, decision_file, confirmation):
    """Remove reported entries only after exact confirmation and make a backup."""
    if confirmation != "REMOVE":
        return False
    decision_file = Path(decision_file)
    backup = decision_file.with_suffix(decision_file.suffix + ".bak")
    shutil.copy2(decision_file, backup)
    stale_indexes = set(stale_indexes)
    retained = [item for index, item in enumerate(decisions) if index not in stale_indexes]
    temporary = decision_file.with_suffix(decision_file.suffix + ".tmp")
    temporary.write_text(
        json.dumps(retained, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(decision_file)
    return True


def _describe(decision):
    return (
        f"{decision.get('decision', 'unknown'):8} "
        f"{decision.get('semantic_macro', '?'):12} "
        f"{decision.get('file', '?')}:{decision.get('line', '?')}:"
        f"{decision.get('column', '?')}  {decision.get('matched_expression', '')!r}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Dry-run validation of stored notation review decisions."
    )
    parser.add_argument(
        "--symbol", help="Only report one visible notation; use --symbol=- for minus."
    )
    parser.add_argument(
        "--remove-stale", action="store_true",
        help="Offer to remove reported stale entries after explicit confirmation.",
    )
    arguments = parser.parse_args()

    if not DECISION_FILE.exists():
        print(f"Decision file does not exist: {DECISION_FILE}")
        return
    decisions = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    selected = [
        (index, decision) for index, decision in enumerate(decisions)
        if arguments.symbol is None or decision.get("visible_symbol") == arguments.symbol
    ]
    selected_decisions = [decision for _, decision in selected]
    report = validate_decisions(selected_decisions)

    print(f"Checked: {len(selected_decisions)}")
    print(f"Current: {len(report['valid'])}")
    print(f"Stale: {len(report['stale'])}")
    print(f"Unverifiable: {len(report['unverifiable'])}")
    if report["stale"]:
        print("\nStale decisions:")
        for _, decision in report["stale"]:
            print(
                _describe(decision)
                + "  reason=current scanner no longer returns this occurrence"
            )

    if not arguments.remove_stale or not report["stale"]:
        print("\nDry run only. No decisions were changed.")
        return

    original_indexes = [selected[index][0] for index, _ in report["stale"]]
    confirmation = input(
        f"Type REMOVE to delete {len(original_indexes)} stale decision(s) "
        "after creating a .bak file: "
    ).strip()
    if remove_stale_decisions(decisions, original_indexes, DECISION_FILE, confirmation):
        print(f"Removed {len(original_indexes)} stale decision(s).")
        print(f"Backup: {DECISION_FILE.with_suffix(DECISION_FILE.suffix + '.bak')}")
    else:
        print("No decisions were changed.")


if __name__ == "__main__":
    main()
