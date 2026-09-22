"""Adapt a user-selected SMGloM notation to explicit parser configuration."""

from copy import deepcopy
import re

from expression_parser import ExpressionParser
from expression_registry import NotationRegistry
from notation_classifier import classify_notation, classify_catalog_notation


def prepare_expression_config(candidate, configuration):
    """Merge the selected notation with context operators supplied by the user.

    SMGloM precedence is presentation metadata. Its numeric outer precedence
    can be used only when the caller explicitly supplies its ordering direction.
    Compound argument precedences are retained by extraction, not interpreted.
    Associativity is never inferred from an associative argument signature.
    """
    config = deepcopy(configuration)
    catalogue_policy = config.pop("catalogue", None)
    selected = config.pop("selected", {})
    rule = candidate["classification"]
    if rule["pattern_type"] in {"sequence", "binary_infix"}:
        token = rule.get("operator", rule.get("separator"))
        if not token:
            raise ValueError("Implicit operators need an explicit grammar extension")
        precedence = selected.get("precedence")
        if precedence is None:
            raw = candidate["record"].get("options", {}).get("prec")
            outer = str(raw).split(";", 1)[0].strip()
            direction = config.pop("smglom_precedence_direction", None)
            if not re.fullmatch(r"-?\d+", outer) or direction not in {"lower", "higher"}:
                raise ValueError("Supply selected.precedence or numeric SMGloM precedence with an explicit direction")
            precedence = int(outer) * (-1 if direction == "lower" else 1)
        if "associativity" not in selected:
            raise ValueError("Supply selected.associativity; SMGloM args does not establish grouping")
        operator = dict(token=token, precedence=precedence,
                        associativity=selected["associativity"], fixity="infix",
                        arity="variable" if rule["pattern_type"] == "sequence" else 2)
        operators = config.setdefault("operators", [])
        operators[:] = [item for item in operators
                        if (item["token"], item.get("fixity", "infix")) != (token, "infix")]
        operators.append(operator)
    commands = config.setdefault("commands", {})
    args = candidate.get("args")
    # sTeX signatures describe TeX arguments, including sequence/binding args.
    if isinstance(args, int) or (isinstance(args, str) and args.isdigit()):
        arity = int(args)
    elif isinstance(args, str) and re.fullmatch(r"[aib]+", args):
        arity = len(args)
    else:
        raise ValueError("Supply a supported semantic argument signature")
    commands["\\" + candidate["macro"]] = {"arity": arity, "semantic": True}
    ExpressionParser(config.get("operators", ()), commands)
    return config


def _arity(signature):
    if isinstance(signature, int) or str(signature).isdigit():
        return int(signature)
    if isinstance(signature, str) and re.fullmatch(r"[aib]+", signature):
        return len(signature)
    raise ValueError("Unknown SMGloM argument signature; configure the command explicitly")


def build_expression_config(configuration, records=None):
    """Build the complete grammar independently of an annotation selection.

    Explicit SMGloM selectors avoid choosing an arbitrary meaning among modules.
    Numeric outer precedence is interpreted only with a declared direction.
    """
    config = deepcopy(configuration)
    imports = config.pop("smglom", [])
    semantic_imports = config.pop("semantic_commands", [])
    catalogue_policy = config.pop("catalogue", None)
    if "selected" in config:
        raise ValueError("Whole-region registries cannot depend on a selected annotation")
    if (imports or semantic_imports or catalogue_policy is not None) and records is None:
        from smglom_notation_extractor import scan_available_smglom
        records = scan_available_smglom()
    records = records or []
    operators = config.setdefault("operators", [])
    commands = config.setdefault("commands", {})

    def definition_for(selector):
        matches = [record for record in records
                   if record.get("declaration_type") == "symdef"
                   and record.get("macro") == selector["macro"]
                   and all(record.get(key) == selector[key]
                           for key in ("module", "repository") if key in selector)]
        if len(matches) != 1:
            raise ValueError(f"SMGloM selector must resolve uniquely: {selector}")
        return matches[0]

    def register_semantic(definition):
        name = "\\" + definition["macro"]
        signature = definition.get("args")
        argument_types = (["sequence" if arg == "a" else "expression" for arg in signature]
                          if isinstance(signature, str) and re.fullmatch(r"[ai]+", signature)
                          else ["opaque"] * _arity(signature))
        commands.setdefault(name, {"arity": _arity(definition.get("args")), "semantic": True,
                                   "argument_types": argument_types,
                                   "source": "SMGloM", "semantic_macro": definition["macro"]})

    for selector in semantic_imports:
        register_semantic(definition_for(selector))
    for selector in imports:
        definition = definition_for(selector)
        register_semantic(definition)
        options = []
        for record in records:
            if (record.get("repository"), record.get("module")) != (
                    definition.get("repository"), definition.get("module")):
                continue
            if record is not definition and not (
                    record.get("declaration_type") == "notation"
                    and record.get("target", "").rsplit("?", 1)[-1]
                    in {definition["macro"], definition.get("name")}):
                continue
            rule = classify_catalog_notation(dict(record, args=definition.get("args")))
            visible = rule.get({"sequence": "separator", "binary_infix": "operator", "unary": "operator",
                                "command": "command"}.get(rule["pattern_type"], ""))
            if selector.get("visible") is not None and visible != selector["visible"]:
                continue
            if selector.get("notation") is not None and record.get("notation") != selector["notation"]:
                continue
            options.append((record, rule, visible))
        if len(options) != 1:
            raise ValueError("Choose a unique SMGloM notation using visible or notation: " + str(selector))
        record, rule, visible = options[0]
        if rule["pattern_type"] == "command":
            # Classifier intentionally has broad legacy command support. Import
            # only exact argument templates; styled function patterns need data.
            expected = visible + "".join("{#" + str(i) + "}"
                                         for i in range(1, rule["argument_count"] + 1))
            if re.sub(r"\s+", "", record["notation"]) != expected:
                raise ValueError("Nontrivial command template requires explicit configuration")
            commands.setdefault(visible, {"arity": rule["argument_count"], "source": "SMGloM",
                                           "semantic_macro": definition["macro"]})
            continue
        if rule["pattern_type"] not in {"sequence", "binary_infix", "unary"} or not visible:
            raise ValueError("This SMGloM template needs an explicit grammar adapter")
        raw = record.get("options", {}).get("prec")
        precedence = selector.get("precedence")
        if precedence is None:
            outer = str(raw).split(";", 1)[0].strip()
            direction = config.get("smglom_precedence_direction")
            if not re.fullmatch(r"-?\d+", outer) or direction not in {"lower", "higher"}:
                raise ValueError("Supply precedence or a numeric SMGloM prec and explicit direction")
            precedence = int(outer) * (-1 if direction == "lower" else 1)
        if "associativity" not in selector:
            raise ValueError("Associativity must be configured; args/argsep do not define grouping")
        operators.append({"token": visible, "precedence": precedence,
                          "associativity": selector["associativity"],
                          "arity": rule.get("arity", 2), "fixity": rule.get("fixity", "infix"),
                          "variants": selector.get("variants", []),
                          "semantic_macro": definition["macro"],
                          "source": "SMGloM + user configuration"})
    if catalogue_policy is not None:
        from notation_catalog import build_catalog, registry_from_catalog
        config, diagnostics = registry_from_catalog(build_catalog(records), catalogue_policy, config)
        config["catalogue_diagnostics"] = diagnostics
        operators, commands = config["operators"], config["commands"]
    for operator in operators:
        macro = operator.get("semantic_macro")
        if macro:
            arity = 1 if operator.get("arity") == "variable" else operator.get("arity", 2)
            types = ["sequence"] if operator.get("arity") == "variable" else ["expression"] * arity
            commands.setdefault("\\" + macro, {"arity": arity, "semantic": True,
                                                 "argument_types": types,
                                                 "source": operator.get("source", "user configuration")})
    NotationRegistry.from_config(config)
    return config


def configured_annotation_candidates(symbol, configuration):
    """Expose explicit semantic mappings without baking notation names into UI."""
    registry = NotationRegistry.from_config(configuration)
    candidates = []
    for fixity in ("infix", "prefix", "postfix"):
        op = registry.operators.get((symbol, fixity))
        if op is None or not op.semantic_macro:
            continue
        pattern = "sequence" if op.arity == "variable" else "binary_infix" if fixity == "infix" else "unary"
        classification = {"pattern_type": pattern,
                          "separator" if pattern == "sequence" else "operator": op.token,
                          "argument_count": 2 if fixity == "infix" else 1,
                          "fixity": fixity}
        candidates.append({"macro": op.semantic_macro, "name": op.semantic_macro,
                           "args": "a" if op.arity == "variable" else str(op.arity),
                           "module": None, "repository": None,
                           "classification": classification, "record": None})
    query = symbol.lstrip("\\").lower()
    for spec in registry.bound_constructs.values():
        searchable = (spec.token.lstrip("\\").lower(), spec.semantic_macro.lower())
        if not spec.semantic_macro or not any(query in value for value in searchable):
            continue
        roles = spec.semantic_argument_roles
        available = set(spec.scripts.values()) | set(spec.leading_roles)
        if spec.body_role:
            available.add(spec.body_role)
        available.add(spec.trailing_role)
        ready = bool(roles) and set(roles) <= available
        classification = {"pattern_type": "bound_construct", "token": spec.token,
                          "node_kind": spec.node_kind, "argument_roles": list(roles),
                          "argument_count": len(roles), "grammar_supported": True,
                          "annotation_ready": ready,
                          **({} if ready else {"annotation_blocker": "missing_argument_mapping"})}
        candidates.append({"macro": spec.semantic_macro, "name": spec.name or spec.semantic_macro,
                           "args": str(len(roles)), "module": spec.semantic_module or None,
                           "repository": spec.semantic_repository or None,
                           "classification": classification, "record": None,
                           "semantic_source": spec.source, "source": spec.source})
    return candidates
