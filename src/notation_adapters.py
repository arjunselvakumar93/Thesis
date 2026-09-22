"""Generic grammar adapters for classified SMGloM presentation templates."""

from copy import deepcopy
import re


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
