"""Scan paper lines using classified SMGloM notation rules."""

from pathlib import Path

from notation_classifier import classify_notation
from notation_matcher import match_notation
from symbol_scanner import PAPERS_DIR


def uncommented_prefix(line):
    """Keep escaped percent signs and stop at the first TeX comment."""
    for index, character in enumerate(line):
        if character != "%":
            continue
        previous = index - 1
        while previous >= 0 and line[previous] == "\\":
            previous -= 1
        if (index - 1 - previous) % 2 == 0:
            return line[:index]
    return line


def find_notation_occurrences(rule_or_record, papers_dir=PAPERS_DIR):
    """Search all paper TeX files with a classification or extracted record.

    Unsupported classifications produce no suggestions. Matching remains
    line based so the reported column is usable by the annotation applier.
    """
    classification = (
        rule_or_record
        if "pattern_type" in rule_or_record
        else classify_notation(rule_or_record)
    )
    if classification.get("pattern_type") not in {
        "command", "binary_infix", "sequence"
    }:
        return []

    occurrences = []
    for tex_file in sorted(Path(papers_dir).rglob("*.tex")):
        for line_number, original_line in enumerate(
            tex_file.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            line = uncommented_prefix(original_line)
            for match in match_notation(line, classification):
                occurrences.append({
                    "file": tex_file,
                    "line": line_number,
                    "column": match["start"] + 1,
                    "text": line.rstrip(),
                    "matched_expression": match["matched_expression"],
                    "arguments": match["arguments"],
                    "pattern_type": match["pattern_type"],
                })
    return occurrences
