"""Classify SMGloM notation, build the catalogue and compile explicit grammar metadata."""

import re
from pathlib import Path
from smglom_notation_extractor import scan_available_smglom, read_group, skip_whitespace
from copy import deepcopy
from expression_parser import NotationRegistry
import argparse
import json
from collections import Counter
from smglom_notation_extractor import discover_repositories
from expression_parser import get_equivalent_tex_notations, canonicalize_tex_notation



def read_simple_tex_argument(
    text,
    position
):
    """
    Read one simple TeX argument.

    Supports:

        {+}
        +
        \alpha
    """

    position = skip_whitespace(
        text,
        position
    )

    if position >= len(text):
        return None, position

    # Braced argument
    if text[position] == "{":

        return read_group(
            text,
            position
        )

    # Control sequence
    if text[position] == "\\":

        start = position
        position += 1

        if position >= len(text):
            return "\\", position

        if text[position].isalpha():

            while (
                position < len(text)
                and text[position].isalpha()
            ):
                position += 1

        else:
            position += 1

        return (
            text[start:position],
            position
        )

    # Single character
    return (
        text[position],
        position + 1
    )


def extract_visible_operator(fragment):
    """Read the operator marked by SMGloM's presentation wrappers."""
    # Prefer the main component; other declarations use the general component.
    for command in (r"\maincomp", r"\comp"):
        position = fragment.find(command)
        if position == -1:
            continue
        value, _ = read_simple_tex_argument(fragment, position + len(command))
        if value is not None:
            return value
    return fragment.strip()


def parse_argsep(notation):
    """
    Parse an argsep sequence.

    Example:

        \argsep{#1}{\mathbin{\maincomp+}}

    returns information about the separator.
    """

    position = notation.find(
        r"\argsep"
    )

    if position == -1:
        return None

    current = (
        position
        + len(r"\argsep")
    )

    current = skip_whitespace(
        notation,
        current
    )

    first_argument, current = read_group(
        notation,
        current
    )

    if first_argument is None:
        return None

    current = skip_whitespace(
        notation,
        current
    )

    separator_argument, current = read_group(
        notation,
        current
    )

    if separator_argument is None:
        return None

    return {
        "argument": first_argument,
        "separator_fragment":
            separator_argument,
        "separator":
            extract_visible_operator(
                separator_argument
            )
    }


def add_operator_metadata(record, classification, fragment):
    """Retain generic binding information carried by an SMGloM operator.

    ``\mathrel`` is presentation metadata, rather than a property of one
    particular visible character.  The matcher can therefore give all
    relations complete expression operands without maintaining a list of
    equality/ordering symbols.
    """

    if r"\mathrel" in fragment:
        classification["operator_role"] = "relation"

    precedence = record.get("options", {}).get("prec")
    if precedence is not None:
        try:
            precedence = int(precedence)
        except (TypeError, ValueError):
            pass
        classification["precedence"] = precedence

    return classification


def classify_notation(record):
    """
    Classify an extracted SMGloM notation.

    Current categories:

        sequence
        command
        binary_infix
        constant
        template
    """

    notation = record.get(
        "notation"
    )

    placeholders = record.get(
        "placeholders",
        []
    )

    if not notation:

        return {
            "pattern_type": "unknown"
        }

    # -------------------------------------------------
    # Case 1:
    # Variable-length sequence
    #
    # Example:
    #
    # \argsep{#1}{\mathbin{\maincomp+}}
    # -------------------------------------------------

    if r"\argsep" in notation:

        information = parse_argsep(
            notation
        )

        if information is not None:

            return add_operator_metadata(record, {
                "pattern_type": "sequence",
                "separator":
                    information["separator"],
                "separator_fragment":
                    information[
                        "separator_fragment"
                    ],
                "variable_arguments": True
            }, information["separator_fragment"])

    # -------------------------------------------------
    # Case 2:
    # No arguments
    #
    # Example:
    #
    # \mathbb{N}^+
    # -------------------------------------------------

    if not placeholders:

        return {
            "pattern_type": "constant"
        }

    # -------------------------------------------------
    # Case 3:
    # Binary infix
    #
    # Example:
    #
    # #1\mathrel{\maincomp{>}}#2
    # -------------------------------------------------

    stripped = notation.strip()

    if (
        placeholders == [1, 2]
        and stripped.startswith("#1")
        and stripped.endswith("#2")
    ):

        middle = stripped[
            len("#1"):
            len(stripped) - len("#2")
        ]

        return add_operator_metadata(record, {
            "pattern_type": "binary_infix",
            "operator_fragment": middle,
            "operator":
                extract_visible_operator(
                    middle
                ),
            "argument_count": 2
        }, middle)

    # -------------------------------------------------
    # Case 4:
    # Command notation
    #
    # Example:
    #
    # \frac{#1}{#2}
    # \sqrt{#1}
    # -------------------------------------------------

    if stripped.startswith("\\"):

        current = 1

        while (
            current < len(stripped)
            and stripped[current].isalpha()
        ):
            current += 1

        command = stripped[:current]

        if (
            command
            and command not in (
                r"\argsep",
                r"\mathrel",
                r"\mathbin"
            )
        ):

            return {
                "pattern_type": "command",
                "command": command,
                "argument_count":
                    len(placeholders)
            }

    # -------------------------------------------------
    # Everything more complicated stays a template
    # for now.
    # -------------------------------------------------

    return {
        "pattern_type": "template"
    }




def classify_catalog_notation(record):
    """Conservative structural classification for catalogue/registry imports.

    The historical classifier remains available to compatibility callers.
    Recognizing a template category does not claim it is executable grammar.
    """
    body = (record.get("notation") or "").strip()
    placeholders = record.get("placeholders", [])
    signature = str(record.get("args") or record.get("options", {}).get("args", ""))
    components = {f"argument{index}": f"#{index}" for index in placeholders}
    if "b" in signature:
        operator = extract_visible_operator(body)
        if not re.fullmatch(r"\\(?:[A-Za-z]+|[^A-Za-z])", operator or ""):
            operator = None
        return {"pattern_type": "binder", "components": components, "operator": operator,
                "grammar_supported": False,
                "unsupported_reason": "binder_metadata_incomplete"}
    old = classify_notation(record)
    # Strip only presentation wrappers around a single visible token.
    def visible(fragment):
        fragment = fragment.strip()
        wrapper = re.fullmatch(r"\\(?:mathbin|mathrel|mathop|maincomp|comp)\s*\{(.*)\}", fragment, re.S)
        if wrapper:
            return visible(wrapper.group(1))
        wrapper = re.fullmatch(r"\\(?:maincomp|comp)\s*((?:\\[A-Za-z]+)|[^\w\s{}#\\])", fragment)
        if wrapper:
            return wrapper.group(1)
        return fragment if re.fullmatch(r"(?:\\[A-Za-z]+|\\[^A-Za-z]|[^\w\s{}#\\])", fragment) else None
    if old["pattern_type"] == "sequence":
        current = body.find(r"\argsep") + len(r"\argsep")
        first, current = read_group(body, skip_whitespace(body, current))
        second, current = read_group(body, skip_whitespace(body, current))
        token = visible(second or "")
        if body.startswith(r"\argsep") and first == "#1" and not body[current:].strip():
            if token:
                return dict(old, grammar_supported=True, fixity="infix", arity="variable")
            if not (second or "").strip():
                return {"pattern_type": "sequence", "separator": "", "implicit": True,
                        "variable_arguments": True, "grammar_supported": True,
                        "annotation_ready": True, "fixity": "infix", "arity": "variable"}
    if old["pattern_type"] == "binary_infix":
        token = visible(body[2:-2])
        if token:
            return dict(old, operator=token, grammar_supported=True, fixity="infix", arity=2)
    # Exact command argument templates; a leading command alone is insufficient.
    command = re.match(r"\\[A-Za-z]+", body)
    if command and placeholders:
        expected = command.group() + "".join("{#" + str(index) + "}" for index in placeholders)
        if re.sub(r"\s+", "", body) == expected and placeholders == list(range(1, len(placeholders) + 1)):
            return {"pattern_type": "command", "command": command.group(), "argument_count": len(placeholders),
                    "arity": len(placeholders), "fixity": "command", "grammar_supported": True}
    if placeholders == [1]:
        for fixity, pattern in (("prefix", r"(.+?)(?:\{#1\}|#1)"),
                                ("postfix", r"(?:\{#1\}|#1)(.+)")):
            found = re.fullmatch(pattern, body, re.S)
            token = visible(found.group(1)) if found else None
            if token:
                return add_operator_metadata(record, {"pattern_type": "unary", "operator": token,
                    "fixity": fixity, "arity": 1, "argument_count": 1, "grammar_supported": True}, body)
    if not placeholders and body:
        token = visible(body)
        return {"pattern_type": "constant", "visible": token or body,
                "fixity": "constant", "arity": 0, "grammar_supported": bool(token and token.startswith("\\")),
                **({} if token and token.startswith("\\") else {"unsupported_reason": "decorated_constant"})}
    if re.search(r"\([^)]*#\d", body):
        kind = "function_application"
    elif re.search(r"[_^].*#\d", body):
        kind = "scripted_operator"
    elif body.startswith(r"\left"):
        kind = "delimiter_application"
    else:
        kind = "template" if body else "unknown"
    if kind == "function_application":
        reason = "mixfix"
    elif kind == "scripted_operator":
        reason = "big_operator" if r"\mathop" in body else "scripted_template"
    elif kind == "delimiter_application":
        reason = "delimiter_notation"
    elif len(re.findall(r"\\[A-Za-z]+", body)) > 1:
        reason = "multi_part_command"
    elif body.startswith("\\"):
        reason = "unknown_wrapper"
    else:
        reason = "unsupported_template"
    return {"pattern_type": kind, "components": components, "grammar_supported": False,
            "unsupported_reason": reason}


# Generic grammar adapters for classified SMGloM presentation templates.


PLACEHOLDER = re.compile(r"#(\d+)")


def template_parts(body):
    """Return an alternating fixed/argument pattern with no semantic guesses."""
    parts, position = [], 0
    for match in PLACEHOLDER.finditer(body or ""):
        if match.start() > position:
            parts.append({"kind": "fixed", "text": body[position:match.start()]})
        parts.append({"kind": "argument", "slot": int(match.group(1))})
        position = match.end()
    if position < len(body or ""):
        parts.append({"kind": "fixed", "text": body[position:]})
    return parts


def _complete_pattern(body, placeholders):
    slots = [part["slot"] for part in template_parts(body) if part["kind"] == "argument"]
    # Slot order and repetition are part of the template itself (for example,
    # reverse implication or a repeated sequence base), not parser semantics.
    return bool(slots) and set(slots) == set(placeholders)


def _strip_presentation(text):
    output, index = [], 0
    while index < len(text):
        command = next((value for value in (
            r"\maincomp", r"\comp", r"\mathop", r"\mathbin", r"\mathrel")
                        if text.startswith(value, index)), None)
        if command is None:
            output.append(text[index])
            index += 1
            continue
        index += len(command)
        if index < len(text) and text[index] == "{":
            depth, end = 1, index + 1
            while end < len(text) and depth:
                depth += (text[end] == "{") - (text[end] == "}")
                end += 1
            if depth == 0:
                output.append(_strip_presentation(text[index + 1:end - 1]))
                index = end
    return "".join(output)


def _fixed_regex(text):
    """Compile presentation-only wrappers into a conservative source pattern."""
    text = _strip_presentation(text)
    text = re.sub(r"\\[;,!]", " ", text)
    pieces = re.split(r"\s+", text.strip())
    return r"\s*".join(re.escape(piece) for piece in pieces if piece)


def annotation_blocker(record, adapter, parts):
    macro = record.get("macro") or str(record.get("target") or "").rsplit("?", 1)[-1]
    if not macro:
        return "missing_semantic_macro"
    body = record.get("notation") or ""
    if r"\argsep" in body and adapter != "SequencePatternAdapter":
        return "no_deterministic_source_pattern"
    if r"\begin" in body or r"\end" in body:
        return "unsupported_replacement_scope"
    for left, right in zip(parts, parts[1:]):
        if left["kind"] == right["kind"] == "argument":
            return "ambiguous_argument_scope"
    if adapter == "MultiPartAdapter" and not any(
            part["kind"] == "fixed" and _fixed_regex(part["text"]) for part in parts):
        return "missing_visible_pattern"
    return ""


def _balanced(value):
    stack = []
    pairs = {"}": "{", ")": "(", "]": "["}
    escaped = False
    for character in value:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character in "{([":
            stack.append(character)
        elif character in pairs:
            if not stack or stack.pop() != pairs[character]:
                return False
    return not stack


def match_adapter_expression(source, classification):
    """Match one complete math expression from an adapter's slot pattern."""
    interpretation = classification.get("grammar_interpretation", {})
    parts = interpretation.get("parts", [])
    adapter = classification.get("grammar_adapter")
    if not classification.get("annotation_ready") or not adapter:
        return None
    if adapter == "SequencePatternAdapter":
        separator = _fixed_regex(classification.get("separator_pattern", ""))
        if not separator:
            return None
        arguments = [item.strip() for item in re.split(separator, source.strip())]
        if len(arguments) < 2 or any(not item or not _balanced(item) for item in arguments):
            return None
        matched = re.fullmatch(r"\s*.*?\s*", source, re.S)
    elif adapter in {"DecoratedSymbolAdapter", "SemanticConstantAdapter"}:
        pattern = _fixed_regex(parts[0]["text"] if parts else "")
        matched = re.fullmatch(r"\s*" + pattern + r"\s*", source, re.S)
        arguments = []
    else:
        chunks, seen = [r"\s*"], set()
        for index, part in enumerate(parts):
            if part["kind"] == "fixed":
                fixed = part["text"]
                previous_is_argument = index > 0 and parts[index - 1]["kind"] == "argument"
                next_is_argument = index + 1 < len(parts) and parts[index + 1]["kind"] == "argument"
                prefix = r"(?:\{)?" if next_is_argument and fixed.endswith("{") else ""
                suffix = r"(?:\})?" if previous_is_argument and fixed.startswith("}") else ""
                if prefix:
                    fixed = fixed[:-1]
                if suffix:
                    fixed = fixed[1:]
                chunks.extend((suffix, _fixed_regex(fixed), prefix))
            elif part["slot"] in seen:
                chunks.append(rf"(?P=slot{part['slot']})")
            else:
                chunks.append(rf"(?P<slot{part['slot']}>.+?)")
                seen.add(part["slot"])
        chunks.append(r"\s*")
        matched = re.fullmatch("".join(chunks), source, re.S)
        if not matched:
            return None
        ordered = sorted(seen)
        arguments = [matched.group(f"slot{slot}").strip() for slot in ordered]
        if any(not argument or not _balanced(argument) for argument in arguments):
            return None
    if not matched:
        return None
    start, end = matched.span()
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return {"pattern_type": classification.get("pattern_type"),
            "matched_expression": source[start:end], "arguments": arguments,
            "start": start, "end": end, "matched_visible_notation": source[start:end]}


def adapter_suggestion(semantic_macro, arguments, variable_arguments=False):
    """Construct a semantic application solely from its macro and slot order."""
    if variable_arguments:
        return "\\" + semantic_macro + "{" + ",".join(arguments) + "}"
    return "\\" + semantic_macro + "".join("{" + argument + "}" for argument in arguments)


def select_grammar_adapter(record, classification):
    """Select by reusable notation shape; never by semantic macro or symbol."""
    body = (record.get("notation") or "").strip()
    placeholders = record.get("placeholders", [])
    reason = classification.get("unsupported_reason")
    adapter = None

    if classification.get("pattern_type") == "binder" and body and placeholders:
        adapter = "BinderAdapter"
    elif reason == "multi_part_command" and body.startswith(r"\argsep"):
        from smglom_notation_extractor import read_group, skip_whitespace
        position = skip_whitespace(body, len(r"\argsep"))
        argument, position = read_group(body, position)
        separator, position = read_group(body, skip_whitespace(body, position))
        if argument == "#1" and separator and not body[position:].strip():
            adapter = "SequencePatternAdapter"
    elif reason == "decorated_constant" and not placeholders and body:
        adapter = "DecoratedSymbolAdapter"
    elif reason == "scripted_template" and _complete_pattern(body, placeholders):
        adapter = "ScriptedTemplateAdapter"
    elif reason == "delimiter_notation" and _complete_pattern(body, placeholders):
        adapter = "DelimiterAdapter"
    elif reason == "mixfix" and _complete_pattern(body, placeholders):
        adapter = "MixfixAdapter"
    elif reason == "multi_part_command" and _complete_pattern(body, placeholders):
        adapter = "MultiPartAdapter"
    elif reason == "big_operator" and _complete_pattern(body, placeholders):
        adapter = "ScriptedTemplateAdapter"
    elif reason == "unknown_wrapper" and _complete_pattern(body, placeholders):
        # Optional/grouped command arguments are ordinary fixed/slot patterns.
        # Empty argsep templates deliberately remain unsupported: they carry no
        # visible separator and therefore cannot drive a matcher.
        if not (body.startswith(r"\argsep") and re.search(r"\{\s*\}\s*$", body)):
            adapter = "MultiPartAdapter"
    elif reason == "unsupported_template" and _complete_pattern(body, placeholders):
        if re.fullmatch(r"\[#\d+\]", body):
            adapter = "DelimiterAdapter"
        elif any(part["kind"] == "fixed" and part["text"] for part in template_parts(body)):
            adapter = "MixfixAdapter"

    if adapter is None:
        return classification
    node_kind = {
        "DecoratedSymbolAdapter": "DecoratedSymbol",
        "ScriptedTemplateAdapter": "ScriptedExpression",
        "DelimiterAdapter": "DelimitedApplication",
        "MixfixAdapter": "MixfixApplication",
        "MultiPartAdapter": "MultiPartApplication",
        "SequencePatternAdapter": "NaryOperator",
        "BinderAdapter": "BinderApplication",
    }[adapter]
    parts = template_parts(body)
    blocker = annotation_blocker(record, adapter, parts)
    signature = str(record.get("args") or record.get("options", {}).get("args", ""))
    if adapter == "BinderAdapter" and set(placeholders) != set(range(1, len(signature) + 1)):
        blocker = "missing_argument_mapping"
    result = dict(classification)
    result.update(grammar_adapter=adapter, grammar_supported=True,
                  annotation_ready=not blocker,
                  grammar_interpretation={"node_kind": node_kind,
                                          "parts": parts})
    if adapter == "SequencePatternAdapter":
        result.update(pattern_type="sequence", variable_arguments=True,
                      separator_pattern=separator, arity="variable")
    if adapter == "BinderAdapter":
        roles = {f"argument{slot}": slot for slot in placeholders}
        underset = re.search(r"\\underset\{#(\d+)(?:\\in\s*#(\d+))?\}", body)
        if underset:
            roles["bound_variable" if underset.group(2) else "constraint"] = int(underset.group(1))
            if underset.group(2):
                roles["domain"] = int(underset.group(2))
        roles["body"] = placeholders[-1]
        result["binder_spec"] = {"roles": roles}
    if blocker:
        result["annotation_blocker"] = blocker
    result.pop("unsupported_reason", None)
    return result


def apply_adapter_override(classification, metadata):
    """Fill incomplete grammar using explicit user metadata, without code edits."""
    result = dict(classification)
    kind = metadata.get("grammar_kind")
    roles = metadata.get("roles", {})
    adapter = metadata.get("grammar_adapter") or {
        "binder": "BinderAdapter",
        "multi_part": "MultiPartAdapter",
        "mixfix": "MixfixAdapter",
        "delimiter": "DelimiterAdapter",
        "scripted": "ScriptedTemplateAdapter",
        "decorated_constant": "DecoratedSymbolAdapter",
    }.get(kind)
    if not adapter:
        raise ValueError("Adapter metadata requires grammar_kind or grammar_adapter")
    if kind == "binder" and not ({"bound_variable", "body"} <= set(roles)):
        result.update(grammar_adapter=adapter, grammar_supported=False,
                      annotation_ready=False,
                      unsupported_reason="binder metadata requires bound_variable and body roles")
        return result
    result.update(pattern_type=kind or result.get("pattern_type"), grammar_adapter=adapter,
                  grammar_supported=True, annotation_ready=bool(metadata.get("annotation_ready", True)),
                  adapter_metadata=dict(metadata),
                  grammar_interpretation={"node_kind": metadata.get(
                      "node_kind", "BinderApplication" if kind == "binder" else "PatternApplication"),
                      "roles": dict(roles)})
    # Explicit metadata can bridge catalogue templates to an existing generic
    # registry construct.  The parser remains independent of semantic symbols.
    if metadata.get("visible"):
        result["visible"] = metadata["visible"]
        if (kind or result.get("pattern_type")) == "bound_construct":
            result["token"] = metadata["visible"]
    if metadata.get("node_kind"):
        result["node_kind"] = metadata["node_kind"]
    if metadata.get("registry_spec"):
        result["registry_spec"] = deepcopy(metadata["registry_spec"])
    if metadata.get("argument_roles"):
        result["argument_roles"] = list(metadata["argument_roles"])
    result.pop("unsupported_reason", None)
    return result


# Adapt a user-selected SMGloM notation to explicit parser configuration.


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
                          "source": "SMGloM + user configuration",
                          "precedence_source": ("explicit configuration" if selector.get("precedence") is not None
                                                else "SMGloM presentation precedence interpreted using configured direction"),
                          "associativity_source": "selected structural configuration"})
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


# A searchable catalogue of every extracted SMGloM and user notation record.


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
                        "file": "", "line": None, "record": None,
                        "source": operator.get("source", "user configuration"), "resolved": True})
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
                        "source": construct.get("source", "user configuration"), "resolved": True})
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
