"""Notation-independent, source-preserving Pratt parser of complete expressions."""

from dataclasses import dataclass, field
from expression_registry import NotationRegistry, Operator
from expression_tokenizer import tokenize, Token


class ParseError(ValueError):
    def __init__(self, message, position=None, token="", category="unsupported_syntax"):
        super().__init__(message)
        self.position, self.token, self.category = position, token, category


@dataclass(frozen=True)
class Expression:
    kind: str
    start: int
    end: int
    value: str = ""
    children: tuple = ()
    operator_start: int = -1
    roles: tuple = ()
    source: str = field(default="", repr=False, compare=False)
    metadata: object = field(default=None, compare=False)
    notation: str = ""

    @property
    def text(self):
        return self.source[self.start:self.end]

    def walk(self):
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))


class ExpressionParser:
    def __init__(self, operators=(), commands=None, syntax=None, registry=None):
        self.registry = registry or NotationRegistry(operators, commands, syntax)

    def parse(self, source):
        self.source = source
        self.tokens = tokenize(source, self.registry)
        self.index = 0
        self.stop_tokens = []
        try:
            self._skip_ignored_commands()
            result = self._expression(float("-inf"))
            self._skip_ignored_commands()
            while self.current.text in self.registry.trailing_punctuation:
                self._take()
                self._skip_ignored_commands()
        except ParseError as error:
            self._locate_error(error)
            raise
        except RecursionError as error:
            raise ParseError("Expression nesting exceeds the parser recursion limit") from error
        if self.current.kind != "end":
            error = ParseError(f"Unsupported syntax at offset {self.current.start}: {self.current.text!r}")
            self._locate_error(error)
            raise error
        return result

    def _locate_error(self, error):
        import re
        position = re.search(r"offset (\d+)", str(error))
        error.position = int(position.group(1)) if position else self.current.start
        token = next((item for item in self.tokens if item.start == error.position), self.current)
        error.token = token.text
        if str(error).startswith("Unknown command"):
            error.category = "unknown_control_sequence"
        elif token.text in {"&", r"\\"}:
            error.category = "alignment_syntax"
        elif token.kind == "comma":
            error.category = "expression_list"
        elif "delimiter" in str(error) or token.kind in {"open", "close", "sized_open", "sized_close"}:
            error.category = "delimiter_or_group"
        elif "argument" in str(error):
            error.category = "command_arguments"
        elif token.kind == "end":
            error.category = "incomplete_expression"

    @property
    def current(self):
        return self.tokens[self.index]

    def _take(self):
        token = self.current
        if token.kind != "end":
            self.index += 1
        return token

    def _node(self, kind, start, end, value="", children=(), **kwargs):
        return Expression(kind, start, end, value, tuple(children), source=self.source, **kwargs)

    def _operator(self, fixity):
        return self.registry.operators.get((self.current.text, fixity))

    def _skip_ignored_commands(self):
        """Consume configured layout/metadata commands without AST semantics."""
        while (self.current.text not in self.stop_tokens
               and self.current.kind == "command"
               and self.current.text in self.registry.ignored_commands):
            arity = self.registry.ignored_commands[self._take().text]
            for _ in range(arity):
                if self.current.text not in {"{", r"\{"}:
                    if self.current.kind == "end":
                        raise ParseError("Ignored command is missing an argument")
                    self._take()
                    continue
                opening = self._take().text
                closing = "}" if opening == "{" else r"\}"
                depth = 1
                while depth and self.current.kind != "end":
                    token = self._take()
                    if token.text == opening:
                        depth += 1
                    elif token.text == closing:
                        depth -= 1
                if depth:
                    raise ParseError("Ignored command has an unclosed argument")

    def _expression(self, minimum):
        prefix = self._operator("prefix")
        if prefix:
            token = self._take()
            child = self._expression(prefix.precedence)
            left = self._node("UnaryOperator", token.start, child.end, prefix.token, (child,),
                              operator_start=token.start, metadata=prefix, notation=token.text)
        else:
            left = self._primary()
        while True:
            if self.current.text in self.stop_tokens:
                break
            self._skip_ignored_commands()
            token = self.current
            if token.text in self.stop_tokens:
                break
            if token.kind == "script" and self.registry.script_precedence >= minimum:
                self._take()
                argument = self._tex_argument()
                if left.kind == "ScriptedExpression" and token.text in left.roles[1:]:
                    raise ParseError("Repeated script requires explicit grouping")
                if left.kind == "ScriptedExpression":
                    children, roles = (*left.children, argument), (*left.roles, token.text)
                else:
                    children, roles = (left, argument), ("base", token.text)
                left = self._node("ScriptedExpression", left.start, argument.end, "scripts", children,
                                  roles=roles, metadata={"precedence": self.registry.script_precedence})
                continue
            if token.text == "(" and self.registry.application_precedence >= minimum and self._callable(left):
                group = self._group(allow_list=True)
                left = self._node("FunctionApplication", left.start, group.end, "application", (left, group),
                                  roles=("function", "arguments"),
                                  metadata={"precedence": self.registry.application_precedence})
                continue
            postfix = self._operator("postfix")
            if postfix and postfix.precedence >= minimum:
                self._take()
                left = self._node("UnaryOperator", left.start, token.end, postfix.token, (left,),
                                  operator_start=token.start, metadata=postfix, notation=token.text)
                continue
            op = self._operator("infix")
            implicit = False
            if op is None and self._starts_primary() and self.registry.implicit:
                op, implicit = self.registry.implicit, True
            if op is None or op.precedence < minimum:
                break
            if not implicit:
                self._take()
            if (left.kind in {"BinaryOperator", "NaryOperator"} and isinstance(left.metadata, Operator)
                    and left.metadata.precedence == op.precedence
                    and (left.metadata.associativity == "none" or op.associativity == "none")):
                raise ParseError("Non-associative operators require explicit grouping")
            right = self._expression(op.precedence + (op.associativity != "right"))
            if (right.kind in {"BinaryOperator", "NaryOperator"} and isinstance(right.metadata, Operator)
                    and right.metadata.precedence == op.precedence
                    and (right.metadata.associativity == "none" or op.associativity == "none")):
                raise ParseError("Non-associative operators require explicit grouping")
            children = (left, right)
            kind = "BinaryOperator"
            if op.associativity == "sequence":
                kind = "NaryOperator"
                if left.kind == kind and left.metadata == op:
                    children = (*left.children, right)
            left = self._node(kind, left.start, right.end, op.token, children,
                              operator_start=-1 if implicit else token.start, metadata=op,
                              notation="" if implicit else token.text)
        return left

    def _callable(self, node):
        return ((node.kind == "Atom" and node.value in self.registry.functions)
                or (node.kind == "Command" and bool((node.metadata or {}).get("callable"))))

    def _starts_primary(self):
        return self.current.kind in {"atom", "number", "command", "sized_open"} or self.current.text in {"(", "["}

    def _sized_group(self):
        start = self._take().start
        opening = self._take()
        delimiters = {"(": ")", "[": "]", r"\{": r"\}"}
        closing = delimiters.get(opening.text)
        if closing is None:
            raise ParseError("Unsupported sizing delimiter at offset " + str(opening.start))
        child = self._expression(float("-inf"))
        if self.current.kind != "sized_close":
            raise ParseError("Expected TeX closing size delimiter")
        self._take()
        if self.current.text != closing:
            raise ParseError("Mismatched TeX sizing delimiters")
        end = self._take().end
        return self._node("GroupedExpression", start, end, opening.text, (child,),
                          metadata={"sized": True})

    def _group(self, allow_list=False):
        opening = self._take()
        closing = {"(": ")", "[": "]", "{": "}", r"\{": r"\}"}[opening.text]
        children = [self._expression(float("-inf"))]
        while allow_list and self.current.kind == "comma":
            self._take()
            children.append(self._expression(float("-inf")))
        if self.current.text != closing:
            raise ParseError(f"Expected {closing} at offset {self.current.start}")
        end = self._take().end
        return self._node("GroupedExpression", opening.start, end, opening.text, children)

    def _tex_argument(self):
        if self.current.text == "{":
            return self._group(allow_list=True)
        # TeX takes one unbraced token, including just one digit of a number.
        token = self.current
        if token.kind == "number" and len(token.text) > 1:
            self.tokens[self.index:self.index + 1] = [
                Token("number", token.text[0], token.start, token.start + 1),
                Token("number", token.text[1:], token.start + 1, token.end)]
        return self._primary()

    def _primary(self):
        token = self.current
        if token.kind == "sized_open":
            return self._sized_group()
        if token.kind == "open":
            return self._group(allow_list=True)
        if token.kind in {"atom", "number"}:
            self._take()
            return self._node("Number" if token.kind == "number" else "Atom", token.start, token.end, token.text)
        if token.kind == "command":
            construct = self.registry.bound_constructs.get(token.text)
            if construct is not None:
                return self._bound_construct(construct)
            self._take()
            spec = self.registry.commands.get(token.text)
            if spec is None:
                if token.text in {r"\%", r"\#", r"\$", r"\&", r"\_", r"\{", r"\}"}:
                    return self._node("Command", token.start, token.end, token.text,
                                      notation=token.text)
                raise ParseError(f"Unknown command {token.text} at offset {token.start}; configure its arguments")
            children, roles = [], []
            if spec.get("optional_arguments") and self.current.text == "[":
                children.append(self._group())
                roles.append("optional")
            for number in range(spec["arity"]):
                if spec.get("semantic"):
                    kinds = spec.get("argument_types", ["opaque"] * spec["arity"])
                    if kinds[number] == "opaque":
                        children.append(self._opaque_argument())
                    else:
                        if self.current.text != "{":
                            raise ParseError("Semantic arguments require braces")
                        children.append(self._group(allow_list=kinds[number] == "sequence"))
                else:
                    children.append(self._tex_argument())
                names = spec.get("roles", ())
                roles.append(names[number] if number < len(names) else f"argument{number + 1}")
            end = children[-1].end if children else token.end
            return self._node("SemanticCommand" if spec.get("semantic") else "Command",
                              token.start, end, token.text, children, roles=tuple(roles),
                              metadata=spec, notation=token.text)
        raise ParseError(f"Expected operand at offset {token.start}")

    def _bound_construct(self, spec):
        """Parse a metadata-described head, scripts, body and bound variables."""
        head = self._take()
        children, roles = [], []
        seen_scripts = set()
        while self.current.kind == "script" and self.current.text in spec.scripts:
            marker = self._take().text
            if marker in seen_scripts:
                raise ParseError("Repeated bound-operator script requires explicit grouping")
            seen_scripts.add(marker)
            children.append(self._tex_argument())
            roles.append(spec.scripts[marker])
        for role in spec.leading_roles:
            children.append(self._tex_argument())
            roles.append(role)
        forms = spec.trailing_forms
        terminators = tuple(dict.fromkeys((*spec.terminators, *(
            form.get("separator") or form.get("prefix") or form.get("prefix_command")
            for form in forms))))
        if spec.body_role:
            self.stop_tokens.extend(terminators)
            try:
                body = self._expression(spec.body_precedence)
            finally:
                if terminators:
                    del self.stop_tokens[-len(terminators):]
            children.append(body)
            roles.append(spec.body_role)
        if forms:
            def starts_form(item):
                separator = item.get("separator")
                if separator and self.current.text != separator:
                    return False
                offset = 1 if separator else 0
                expected = item.get("prefix_command") or item.get("prefix")
                return (self.index + offset < len(self.tokens)
                        and self.tokens[self.index + offset].text == expected)
            form = next((item for item in forms if starts_form(item)), None)
            if form is None:
                raise ParseError("Bound expression is missing a configured trailing form")
            variables = form.get("variables", 1)
            for number in range(variables):
                if number or form.get("separator"):
                    separator = form.get("separator")
                    if separator and self.current.text != separator:
                        raise ParseError("Bound expression is missing its configured differential separator")
                    if separator:
                        self._take()
                prefix_command = form.get("prefix_command")
                if prefix_command:
                    if self.current.text != prefix_command:
                        raise ParseError("Bound expression is missing its configured differential command")
                    self._take()
                    argument = self._tex_argument()
                    expected = form.get("prefix_argument")
                    actual = argument.text[1:-1] if argument.kind == "GroupedExpression" else argument.text
                    if expected is not None and actual != expected:
                        raise ParseError("Bound expression differential command has an unexpected argument")
                elif form.get("prefix"):
                    if self.current.text != form["prefix"]:
                        raise ParseError("Bound expression is missing its configured variable prefix")
                    self._take()
                children.append(self._tex_argument())
                roles.append(form.get("role", spec.trailing_role) if variables == 1
                             else f"{form.get('role', spec.trailing_role)}{number + 1}")
        elif spec.terminators:
            if self.current.text not in spec.terminators:
                raise ParseError("Bound expression is missing its configured terminator")
            self._take()
            if spec.trailing_prefix:
                if self.current.text != spec.trailing_prefix:
                    raise ParseError("Bound expression is missing its configured variable prefix")
                self._take()
            children.append(self._tex_argument())
            roles.append(spec.trailing_role)
        end = children[-1].end if children else head.end
        return self._node(spec.node_kind, head.start, end, spec.token, children,
                          roles=tuple(roles), metadata=spec, notation=head.text,
                          operator_start=head.start)

    def _opaque_argument(self):
        if self.current.text != "{":
            raise ParseError("Semantic arguments require braces")
        start = self.current.start
        depth = 0
        while self.current.kind != "end":
            token = self._take()
            if token.text == "{":
                depth += 1
            elif token.text == "}":
                depth -= 1
                if depth == 0:
                    return self._node("OpaqueArgument", start, token.end)
        raise ParseError("Unclosed semantic argument")


def match_expression_scopes(text, classification, config):
    """Compatibility adapter; annotation matching itself accepts an AST."""
    from expression_annotation import match_ast
    registry = NotationRegistry.from_config(config)
    try:
        root = ExpressionParser(registry=registry).parse(text)
    except ParseError:
        return []
    return match_ast(root, classification, registry)
