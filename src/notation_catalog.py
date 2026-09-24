"""A searchable catalogue of every extracted SMGloM and user notation record."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from collections import Counter

from notation_classifier import classify_catalog_notation
from notation_adapters import select_grammar_adapter, apply_adapter_override
from smglom_notation_extractor import scan_available_smglom, discover_repositories
from tex_notation_aliases import get_equivalent_tex_notations, canonicalize_tex_notation


def build_catalog(records, configuration=None):
    config = configuration or {}
    aliases = config.get("notation_aliases")
    definitions = [record for record in records if record.get("declaration_type") == "symdef"]
    entries = []
    for record in records:
        definition = record if record.get("declaration_type") == "symdef" else None
        if definition is None:
            target = record.get("target", "").rsplit("?", 1)[-1]
            candidates = [item for item in definitions
                          if item.get("repository") == record.get("repository")
                          and item.get("module") == record.get("module")
                          and target in {item.get("macro"), item.get("name")}]
            if len(candidates) == 1:
                definition = candidates[0]
        definition = definition or {}
        classified_record = dict(record, args=definition.get("args"))
        rule = select_grammar_adapter(classified_record, classify_catalog_notation(classified_record))
        overrides = {item.get("semantic_macro"): item
                     for item in (configuration or {}).get("grammar_adapters", [])
                     if item.get("semantic_macro")}
        semantic_macro = definition.get("macro")
        if semantic_macro in overrides:
            rule = apply_adapter_override(rule, overrides[semantic_macro])
        if (rule.get("pattern_type") == "constant" and rule.get("grammar_supported")
                and semantic_macro and not rule.get("grammar_adapter")):
            rule = dict(rule, grammar_adapter="SemanticConstantAdapter",
                        annotation_ready=True,
                        grammar_interpretation={"node_kind": "SemanticConstant",
                                                "parts": [{"kind": "fixed",
                                                           "text": record.get("notation") or rule.get("visible", "")} ]})
        if rule.get("grammar_supported") and not rule.get("annotation_ready",
                rule.get("pattern_type") in {"binary_infix", "sequence", "unary", "command"}):
            rule.setdefault("annotation_blocker", "no_ast_matcher")
        visible = rule.get("operator", rule.get("separator", rule.get("command", rule.get("visible"))))
        options = record.get("options", {})
        entries.append({"repository": record.get("repository"), "module": record.get("module"),
                        "macro": definition.get("macro"), "name": definition.get("name"),
                        "visible": visible, "variants": get_equivalent_tex_notations(visible, aliases) if visible else [],
                        "notation": record.get("notation"), "args": definition.get("args"),
                        "placeholders": record.get("placeholders", []), "precedence": options.get("prec"),
                        "associativity": options.get("associativity", options.get("assoc")),
                        "fixity": rule.get("fixity"), "classification": rule,
                        "file": str(record.get("file", "")), "line": record.get("line"),
                        "record": record, "source": "SMGloM", "resolved": bool(definition)})
    for operator in config.get("operators", []):
        arity, fixity = operator.get("arity", 2), operator.get("fixity", "infix")
        kind = "unary" if fixity != "infix" else "sequence" if arity == "variable" else "binary_infix"
        rule = {"pattern_type": kind, "separator" if kind == "sequence" else "operator": operator["token"],
                "fixity": fixity, "arity": arity, "argument_count": 2 if fixity == "infix" else 1,
                "grammar_supported": True}
        entries.append({"repository": None, "module": None, "macro": operator.get("semantic_macro"),
                        "name": operator.get("name", operator.get("semantic_macro")), "visible": operator["token"],
                        "variants": list(dict.fromkeys([*get_equivalent_tex_notations(operator["token"], aliases), *operator.get("variants", [])])),
                        "notation": operator.get("pattern", operator["token"]), "args": "a" if arity == "variable" else str(arity),
                        "placeholders": [], "precedence": operator.get("precedence"),
                        "associativity": operator.get("associativity"), "fixity": fixity, "classification": rule,
                        "file": "", "line": None, "record": None, "source": "user configuration", "resolved": True})
    for token, command in config.get("commands", {}).items():
        kind = "semantic_application" if command.get("semantic") else "command"
        entries.append({"repository": None, "module": None, "macro": command.get("semantic_macro"),
                        "name": command.get("name"), "visible": token, "variants": [token],
                        "notation": command.get("pattern", token), "args": str(command["arity"]),
                        "placeholders": [], "precedence": None, "associativity": None, "fixity": "command",
                        "classification": {"pattern_type": kind, "command": token, "argument_count": command["arity"],
                                           "arity": command["arity"], "grammar_supported": True},
                        "file": "", "line": None, "record": None, "source": "user configuration", "resolved": True})
    for token, construct in config.get("bound_constructs", {}).items():
        roles = construct.get("semantic_argument_roles", [])
        ready = bool(construct.get("semantic_macro") and roles)
        entries.append({"repository": construct.get("semantic_repository"),
                        "module": construct.get("semantic_module", construct.get("module")),
                        "macro": construct.get("semantic_macro"), "name": construct.get("name"),
                        "keywords": construct.get("keywords", []),
                        "description": construct.get("description"),
                        "visible": token, "variants": [token], "notation": construct.get("pattern", token),
                        "args": str(len(roles)) if roles else construct.get("args"),
                        "placeholders": [], "precedence": None,
                        "associativity": None, "fixity": "bound",
                        "classification": {"pattern_type": "bound_construct", "token": token,
                                           "node_kind": construct.get("node_kind"),
                                           "argument_roles": roles, "argument_count": len(roles),
                                           "components": {"scripts": construct.get("scripts", {}),
                                                          "leading_roles": construct.get("leading_roles", []),
                                                          "body_role": construct.get("body_role"),
                                                          "trailing_role": construct.get("trailing_role")},
                                           "registry_spec": dict(construct),
                                           "grammar_supported": True, "annotation_ready": ready,
                                           **({} if ready else {
                                               "annotation_blocker": "missing_argument_mapping"})},
                        "file": "", "line": None, "record": None,
                        "source": "user configuration", "resolved": True})
    # Rich structures are represented without claiming an executable adapter.
    for notation in config.get("notations", []):
        entries.append({"repository": None, "module": notation.get("module"),
                        "macro": notation.get("semantic_macro"), "name": notation.get("name"),
                        "visible": notation.get("visible"), "variants": notation.get("variants", []),
                        "notation": notation.get("pattern"), "args": notation.get("args"),
                        "placeholders": notation.get("placeholders", []), "precedence": notation.get("precedence"),
                        "associativity": notation.get("associativity"), "fixity": notation.get("fixity"),
                        "classification": {"pattern_type": notation.get("pattern_type", "template"),
                                           "components": notation.get("components", {}), "grammar_supported": False},
                        "file": "", "line": None, "record": None, "source": "user configuration", "resolved": True})
    return entries


def search_catalog(entries, query):
    query = query.strip()
    if query in {"", "?"}:
        return list(entries)
    folded = query.casefold()
    canonical = canonicalize_tex_notation(folded)
    notation_like = bool(re.fullmatch(r"(?:\\[A-Za-z]+|\\[^A-Za-z]|[^\w\s])", query))
    scored = []
    for index, entry in enumerate(entries):
        visible = str(entry.get("visible") or "").casefold()
        spellings = [str(value).casefold() for value in entry.get("variants", []) if value]
        macro = str(entry.get("macro") or "").casefold().lstrip("\\")
        name = str(entry.get("name") or "").casefold()
        module = str(entry.get("module") or "").casefold()
        repository = str(entry.get("repository") or "").casefold()
        keywords = [str(value).casefold() for value in entry.get("keywords", [])]
        description = str(entry.get("description") or "").casefold()
        if visible == folded:
            rank, match_type = 0, "exact visible notation"
        elif any(value == folded or canonicalize_tex_notation(value) == canonical
                 for value in spellings):
            rank, match_type = 1, "notation alias"
        elif macro and macro == folded.lstrip("\\"):
            rank, match_type = 2, "semantic macro"
        elif name and name == folded:
            rank, match_type = 3, "exact meaning/name"
        elif name and all(word in name for word in folded.split()):
            rank, match_type = 4, "meaning/name search"
        elif folded in keywords or any(folded in value for value in keywords):
            rank, match_type = 4, "configured keyword"
        elif folded in {module, repository}:
            rank, match_type = 5, "module/metadata search"
        elif not notation_like and all(word in " ".join(
                [macro, name, module, repository, visible, description, *keywords,
                 str(entry.get("notation") or "").casefold()]) for word in folded.split()):
            rank, match_type = 6, "broad text search"
        else:
            continue
        matched = dict(entry)
        matched["match_type"] = match_type
        scored.append((rank, index, matched))
    best = min((rank for rank, _, _ in scored), default=None)
    return [entry for rank, _, entry in scored if rank == best]


def annotation_candidates(entries):
    candidates = []
    for entry in entries:
        rule = entry["classification"]
        ready = rule.get("annotation_ready", rule["pattern_type"] in {
            "binary_infix", "sequence", "unary", "command"})
        if not entry.get("macro") or not rule.get("grammar_supported") or not ready:
            continue
        candidate = {key: entry.get(key) for key in (
            "macro", "name", "args", "module", "repository", "record", "match_type", "source",
            "visible", "variants")}
        candidate["semantic_source"] = entry.get("source")
        candidate["classification"] = dict(rule)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def catalog_statistics(entries):
    classified = sum(entry["classification"]["pattern_type"] not in {"template", "unknown"} for entry in entries)
    supported = sum(bool(entry["classification"].get("grammar_supported")) for entry in entries)
    reasons = {}
    for entry in entries:
        rule = entry["classification"]
        if rule.get("grammar_supported"):
            continue
        reason = rule.get("unsupported_reason", rule.get("pattern_type", "unknown"))
        reasons[reason] = reasons.get(reason, 0) + 1
    adapters = {}
    for entry in entries:
        adapter = entry["classification"].get("grammar_adapter")
        if adapter:
            adapters[adapter] = adapters.get(adapter, 0) + 1
    return {"entries": len(entries), "classified": classified, "grammar_supported": supported,
            "grammar_unsupported": len(entries) - supported,
            "annotation_ready_entries": len(annotation_candidates(entries)),
            "unsupported_by_reason": dict(sorted(reasons.items())),
            "supported_by_adapter": dict(sorted(adapters.items()))}


def display_catalog(entries):
    print("Semantic macro | Visible notation | Module | Source | Classification | Adapter | Annotation | Status | Match type")
    for entry in entries:
        rule = entry["classification"]
        macro = "\\" + entry["macro"] if entry.get("macro") else "(unresolved)"
        readiness = rule.get("annotation_ready", rule.get("pattern_type") in {
            "binary_infix", "sequence", "unary", "command"})
        status = "supported" if rule.get("grammar_supported") else rule.get("unsupported_reason", "needs adapter")
        print(f"{macro} | {entry.get('visible') or entry.get('notation')} | {entry.get('module')} | "
              f"{entry.get('source')} | "
              f"{rule['pattern_type']} | {rule.get('grammar_adapter', '')} | "
              f"{'ready' if readiness else 'not ready'} | {status} | "
              f"{entry.get('match_type', '')}")


def adapter_examples(entries, limit=3):
    """Representative records for thesis/debug reports, grouped by adapter."""
    examples = {}
    for entry in entries:
        adapter = entry["classification"].get("grammar_adapter")
        if not adapter or len(examples.setdefault(adapter, [])) >= limit:
            continue
        examples[adapter].append({
            "semantic_macro": entry.get("macro"),
            "visible": entry.get("visible"),
            "module": entry.get("module"),
            "notation": entry.get("notation"),
            "adapter": adapter,
            "interpretation": entry["classification"].get("grammar_interpretation"),
        })
    return examples


def adapter_summary(entries):
    """Concise readiness totals and blockers for grammar-supported entries."""
    rows = {}
    blockers = Counter()
    by_pattern = Counter()
    for entry in entries:
        rule = entry["classification"]
        if not rule.get("grammar_supported"):
            continue
        ready = rule.get("annotation_ready", rule.get("pattern_type") in {
            "binary_infix", "sequence", "unary", "command"})
        adapter = rule.get("grammar_adapter", "CoreGrammar")
        row = rows.setdefault(adapter, {"supported": 0, "ready": 0, "blocked": 0})
        row["supported"] += 1
        row["ready" if ready else "blocked"] += 1
        if not ready:
            blockers[rule.get("annotation_blocker", "unspecified")] += 1
            by_pattern[rule.get("pattern_type", "unknown")] += 1
    return {"adapters": dict(sorted(rows.items())),
            "blockers": dict(sorted(blockers.items())),
            "blocked_by_pattern_type": dict(sorted(by_pattern.items()))}


def display_adapter_summary(entries):
    summary = adapter_summary(entries)
    print(f"{'Adapter':30} {'Supported':>9} {'Ready':>7} {'Blocked':>8}")
    print("-" * 58)
    for adapter, counts in summary["adapters"].items():
        print(f"{adapter:30} {counts['supported']:>9} {counts['ready']:>7} {counts['blocked']:>8}")
    print("\nBlockers:")
    for reason, count in summary["blockers"].items():
        print(f"  {reason}: {count}")
    print("\nBlocked pattern types:")
    for pattern, count in summary["blocked_by_pattern_type"].items():
        print(f"  {pattern}: {count}")


def registry_from_catalog(entries, policy=None, base=None):
    """Compile only unambiguous metadata; report missing data instead of guessing.

    Base user configuration wins. Associativity/precedence direction must come
    from the record or explicit policy. Ambiguous binding specifications are not
    selected by filesystem order.
    """
    policy, config = policy or {}, deepcopy(base or {})
    operators, commands = config.setdefault("operators", []), config.setdefault("commands", {})
    bound_constructs = config.setdefault("bound_constructs", {})
    occupied = {(item["token"], item.get("fixity", "infix")) for item in operators}
    pending, diagnostics = {}, []
    for entry in entries:
        rule, token = entry["classification"], entry.get("visible")
        if not rule.get("grammar_supported") or not token:
            diagnostics.append({"macro": entry.get("macro"), "reason": "unsupported notation structure"})
            continue
        kind = rule["pattern_type"]
        if kind in {"binder", "big_operator", "bound_construct"} and rule.get("registry_spec"):
            if token not in bound_constructs:
                bound_constructs[token] = deepcopy(rule["registry_spec"])
            continue
        if kind in {"command", "constant"}:
            if not token.startswith("\\"):
                continue
            key = (token, "command")
            if token in commands:
                continue
            spec = {"arity": rule.get("arity", 0), "source": "SMGloM catalogue"}
        elif kind in {"binary_infix", "sequence", "unary"}:
            fixity = rule.get("fixity", "infix")
            key = (token, fixity)
            if key in occupied:
                continue
            raw = str(entry.get("precedence") or "").split(";", 1)[0].strip()
            direction = policy.get("precedence_direction")
            association = entry.get("associativity") or policy.get("associativity")
            if not re.fullmatch(r"-?\d+", raw) or direction not in {"lower", "higher"} or not association:
                diagnostics.append({"macro": entry.get("macro"), "visible": token,
                                    "reason": "missing precedence/direction or associativity"})
                continue
            spec = {"token": token, "fixity": fixity, "arity": rule["arity"],
                    "precedence": int(raw) * (-1 if direction == "lower" else 1),
                    "associativity": association, "source": "SMGloM catalogue"}
        else:
            continue
        pending.setdefault(key, []).append(spec)
    for (token, fixity), specs in pending.items():
        if any(spec != specs[0] for spec in specs[1:]):
            diagnostics.append({"visible": token, "reason": "conflicting registry metadata; configure explicitly"})
        elif fixity == "command":
            commands[token] = specs[0]
        else:
            operators.append(specs[0])
    return config, diagnostics


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--list", action="store_true")
    cli.add_argument("--search", default="")
    cli.add_argument("--reference", type=Path)
    cli.add_argument("--config", type=Path)
    cli.add_argument("--json", action="store_true")
    cli.add_argument("--adapter-report", action="store_true")
    cli.add_argument("--adapter-summary", action="store_true")
    cli.add_argument("--adapter")
    readiness = cli.add_mutually_exclusive_group()
    readiness.add_argument("--ready", action="store_true")
    readiness.add_argument("--not-ready", action="store_true")
    args = cli.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else None
    entries = build_catalog(scan_available_smglom(args.reference, quiet=True), config)
    matches = search_catalog(entries, args.search)
    if args.adapter:
        matches = [entry for entry in matches
                   if entry["classification"].get("grammar_adapter") == args.adapter]
    if args.ready or args.not_ready:
        def is_ready(entry):
            rule = entry["classification"]
            return rule.get("annotation_ready", rule.get("pattern_type") in {
                "binary_infix", "sequence", "unary", "command"})
        matches = [entry for entry in matches if is_ready(entry) == args.ready]
    if args.json:
        print(json.dumps({"repositories": list(discover_repositories(args.reference)),
                          "statistics": catalog_statistics(entries), "matches": matches,
                          **({"adapter_examples": adapter_examples(entries)} if args.adapter_report else {}),
                          **({"adapter_summary": adapter_summary(entries)} if args.adapter_summary else {})},
                         default=str, indent=2))
    else:
        if args.adapter_summary:
            display_adapter_summary(entries)
        else:
            display_catalog(matches)
            print(f"\nCatalogue: {catalog_statistics(entries)}; search results: {len(matches)}")
        if args.adapter_report:
            print("\nAdapter examples:")
            print(json.dumps(adapter_examples(entries), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
