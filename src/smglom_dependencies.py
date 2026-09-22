"""Report local and missing repositories referenced by SMGloM modules."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
from pathlib import Path

from smglom_notation_extractor import (
    PROJECT_DIR,
    discover_repositories,
    remove_latex_comments,
    scan_repository,
)
from notation_catalog import build_catalog, catalog_statistics


MODULE_REFERENCE = re.compile(
    r"\\(?P<command>importmodule|usemodule|requiremodule)\s*"
    r"\[\s*smglom/(?P<repository>[^\]/\s]+)\s*\]\s*"
    r"\{\s*(?:mod\?)?(?P<module>[^}\s]+)\s*\}"
)


def scan_repository_references(reference_dir=None):
    """Return repository-qualified SMGloM module references with locations."""
    root = Path(reference_dir) if reference_dir is not None else PROJECT_DIR / "reference"
    references = []
    if not root.is_dir():
        return references
    for tex_file in sorted(root.rglob("*.tex")):
        text = remove_latex_comments(tex_file.read_text(encoding="utf-8", errors="replace"))
        for match in MODULE_REFERENCE.finditer(text):
            references.append({
                "repository": match.group("repository"),
                "module": match.group("module"),
                "command": match.group("command"),
                "file": tex_file,
                "line": text.count("\n", 0, match.start()) + 1,
            })
    return references


def dependency_report(reference_dir=None):
    """Describe installed, referenced, and missing repositories generically."""
    root = Path(reference_dir) if reference_dir is not None else PROJECT_DIR / "reference"
    available = discover_repositories(root)
    references = scan_repository_references(root)
    referenced = sorted({item["repository"] for item in references})
    missing = {}
    for repository in referenced:
        if repository in available:
            continue
        missing[repository] = {
            "expected_path": root / repository / "source",
            "references": [item for item in references if item["repository"] == repository],
        }
    return {
        "reference_dir": root,
        "available_repositories": sorted(available),
        "referenced_repositories": referenced,
        "missing_repositories": missing,
        "references": references,
    }


def repository_health(reference_dir=None):
    """Measure extraction and catalogue readiness for every installed repository."""
    root = Path(reference_dir) if reference_dir is not None else PROJECT_DIR / "reference"
    results = []
    for repository, source in discover_repositories(root).items():
        files = sorted(source.rglob("*.tex"))
        records = scan_repository(repository, source)
        catalogue = build_catalog(records)
        expected = Counter()
        parse_error_details = []
        for path in files:
            text = remove_latex_comments(path.read_text(encoding="utf-8", errors="replace"))
            file_expected = Counter({
                "symdef": len(re.findall(r"\\symdef\b", text)),
                "notation": len(re.findall(r"\\notation\b", text)),
            })
            expected.update(file_expected)
            file_extracted = Counter(record["declaration_type"] for record in records
                                     if record["file"] == path)
            for declaration_type in ("symdef", "notation"):
                missing = file_expected[declaration_type] - file_extracted[declaration_type]
                if missing > 0:
                    parse_error_details.append({
                        "file": str(path.relative_to(root)).replace("\\", "/"),
                        "declaration_type": declaration_type,
                        "unextracted": missing,
                    })
        extracted = Counter(record["declaration_type"] for record in records)
        parse_errors = ((expected["symdef"] - extracted["symdef"])
                        + (expected["notation"] - extracted["notation"]))
        statistics = catalog_statistics(catalogue)
        results.append({
            "repository": repository,
            "path": str(Path(repository) / "source").replace("\\", "/"),
            "source_directory_exists": source.is_dir(),
            "tex_files": len(files),
            "symdefs": extracted["symdef"],
            "notations": extracted["notation"],
            "parse_errors": max(parse_errors, 0),
            "parse_error_details": parse_error_details,
            "total_catalogue_records": statistics["entries"],
            "classified_records": statistics["classified"],
            "grammar_supported_records": statistics["grammar_supported"],
            "annotation_ready_records": statistics["annotation_ready_entries"],
            "status": "ok" if source.is_dir() and parse_errors == 0 else "review",
        })
    return results


def semantic_family_summary(entries):
    """Group records by their declared repository and module, without macro rules."""
    families = Counter()
    for entry in entries:
        repository = entry.get("repository") or "user"
        module = entry.get("module") or "unscoped"
        families[f"{repository}/{module}"] += 1
    return dict(sorted(families.items()))


def repository_snapshot(reference_dir=None, timestamp=None):
    """Create a portable semantic-coverage snapshot for the installed repositories."""
    root = Path(reference_dir) if reference_dir is not None else PROJECT_DIR / "reference"
    health = repository_health(root)
    records = []
    for repository, source in discover_repositories(root).items():
        records.extend(scan_repository(repository, source))
    catalogue = build_catalog(records)
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "repositories_installed": [item["repository"] for item in health],
        "records_by_repository": {
            item["repository"]: item["total_catalogue_records"] for item in health
        },
        "classification_counts": dict(sorted(Counter(
            entry["classification"].get("pattern_type", "unknown") for entry in catalogue
        ).items())),
        "grammar_supported_counts": {
            item["repository"]: item["grammar_supported_records"] for item in health
        },
        "annotation_ready_counts": {
            item["repository"]: item["annotation_ready_records"] for item in health
        },
        "catalogue": catalog_statistics(catalogue),
        "semantic_families": semantic_family_summary(catalogue),
        "repository_health": health,
    }


def compare_snapshots(before, after):
    """Compare additive coverage measurements without modifying either snapshot."""
    scalar_keys = ("entries", "classified", "grammar_supported", "annotation_ready_entries")
    return {
        "repositories_added": sorted(set(after["repositories_installed"])
                                     - set(before["repositories_installed"])),
        "repositories_removed": sorted(set(before["repositories_installed"])
                                       - set(after["repositories_installed"])),
        "catalogue_change": {
            key: after["catalogue"].get(key, 0) - before["catalogue"].get(key, 0)
            for key in scalar_keys
        },
        "before_timestamp": before.get("timestamp"),
        "after_timestamp": after.get("timestamp"),
    }


def display_report(report):
    print("Available repositories: " + (", ".join(report["available_repositories"]) or "none"))
    print("Referenced repositories: " + (", ".join(report["referenced_repositories"]) or "none"))
    print("Missing repositories: " + (", ".join(report["missing_repositories"]) or "none"))
    for repository, details in report["missing_repositories"].items():
        print(f"\nReferenced repository: {repository}")
        print(f"Local path expected: {details['expected_path']}")
        print("Status: missing")
        print("Referenced by:")
        for reference in details["references"]:
            print(f"  {reference['file']}:{reference['line']} -> {reference['module']}")


def display_health(rows):
    headings = ("Repository", "TeX", "Symdefs", "Notations", "Records",
                "Classified", "Grammar", "Ready", "Errors", "Status")
    print(" | ".join(headings))
    for row in rows:
        print(" | ".join(map(str, (row["repository"], row["tex_files"], row["symdefs"],
            row["notations"], row["total_catalogue_records"], row["classified_records"],
            row["grammar_supported_records"], row["annotation_ready_records"],
            row["parse_errors"], row["status"]))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="Report extraction and catalogue health for installed repositories")
    parser.add_argument("--snapshot", type=Path,
                        help="Write a new repository coverage snapshot without replacing others")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args(argv)
    if args.compare:
        before, after = (json.loads(path.read_text(encoding="utf-8")) for path in args.compare)
        print(json.dumps(compare_snapshots(before, after), indent=2))
        return 0
    if args.snapshot:
        snapshot = repository_snapshot(args.reference)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        print(f"Repository snapshot written: {args.snapshot}")
        return 0
    if args.check:
        rows = repository_health(args.reference)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            display_health(rows)
        return 0
    report = dependency_report(args.reference)
    if args.json:
        print(json.dumps(report, default=str, indent=2))
    else:
        display_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
