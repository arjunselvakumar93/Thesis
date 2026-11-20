"""Source-preserving expression grammar, tokenizer and Pratt parser."""

from dataclasses import dataclass
from copy import deepcopy
import re
import unicodedata
from dataclasses import dataclass, field, replace

# TeX-syntax equivalences independent of SMGloM semantic meanings.


# The first entry is the canonical spelling used for internal comparison.
# Entries contain TeX tokens only; semantic macro names do not belong here.
TEX_NOTATION_ALIAS_GROUPS = (
    (r"\ne", r"\neq"),
)


_CANONICAL_BY_NOTATION = {
    notation: group[0]
    for group in TEX_NOTATION_ALIAS_GROUPS
    for notation in group
}

_EQUIVALENTS_BY_CANONICAL = {
    group[0]: group
    for group in TEX_NOTATION_ALIAS_GROUPS
}


def _configured_groups(configured_aliases=None):
    """Return validated, semantics-free alias groups from project configuration."""
    if not configured_aliases:
        return ()
    if not isinstance(configured_aliases, dict):
        raise ValueError("notation_aliases must map TeX spellings to TeX spellings")
    groups = []
    for alias, target in configured_aliases.items():
        if not all(isinstance(item, str) and item and not any(c.isspace() for c in item)
                   for item in (alias, target)):
            raise ValueError("notation aliases require nonempty TeX tokens")
        groups.append((target, alias))
    return tuple(groups)


def _alias_maps(configured_aliases=None):
    groups = (*TEX_NOTATION_ALIAS_GROUPS, *_configured_groups(configured_aliases))
    graph = {}
    for group in groups:
        for item in group:
            graph.setdefault(item, set()).update(group)
    canonical, equivalents = {}, {}
    unseen = set(graph)
    while unseen:
        seed = unseen.pop()
        component, pending = {seed}, [seed]
        while pending:
            item = pending.pop()
            for neighbour in graph.get(item, ()):
                if neighbour not in component:
                    component.add(neighbour); unseen.discard(neighbour); pending.append(neighbour)
        preferred = next((group[0] for group in groups if group[0] in component), min(component))
        ordered = (preferred, *sorted(component - {preferred}))
        equivalents[preferred] = ordered
        canonical.update({item: preferred for item in component})
    return canonical, equivalents


def canonicalize_tex_notation(token, configured_aliases=None):
    """Return the canonical TeX spelling for a visible notation token."""
    canonical, _ = _alias_maps(configured_aliases)
    return canonical.get(token, token)


def get_equivalent_tex_notations(token, configured_aliases=None):
    """Return equivalent TeX spellings, canonical spelling first."""
    canonical_map, equivalents = _alias_maps(configured_aliases)
    canonical = canonical_map.get(token, token)
    return list(equivalents.get(canonical, (token,)))


# Validated notation data shared by tokenization, parsing and annotation.


STRUCTURAL_TOKENS = {"%", "#", "^", "_", "{", "}", "(", ")", "[", "]", ",", r"\left", r"\right"}


@dataclass(frozen=True)
class Operator:
    token: str
    precedence: int
    associativity: str
    fixity: str = "infix"
    arity: object = 2
    variants: tuple = ()
    semantic_macro: str = ""
    source: str = "user configuration"
    uncertain_chain: bool = False
    precedence_source: str = "configuration"
    associativity_source: str = "configuration"

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token or type(self.precedence) is not int:
            raise ValueError("Operators require a token and integer binding power")
        if self.associativity not in {"left", "right", "none", "sequence"}:
            raise ValueError("Associativity must be left, right, none, or sequence")
        if self.associativity == "sequence" and (self.arity != "variable" or self.fixity != "infix"):
            raise ValueError("Sequence grouping requires variable-arity infix metadata")
        if self.fixity not in {"prefix", "infix", "postfix"}:
            raise ValueError("Unsupported operator fixity")
        if self.arity not in ({2, "variable"} if self.fixity == "infix" else {1}):
            raise ValueError("Arity does not agree with fixity")
        if not self.precedence_source or not self.associativity_source:
            raise ValueError("Operator precedence and associativity sources must be explicit")
        object.__setattr__(self, "variants", tuple(self.variants))


    @property
    def precedence_is_explicit(self):
        return self.precedence_source not in {"", "default", "unknown"}


@dataclass(frozen=True)
class BoundConstruct:
    """Declarative grammar for operators that bind a body or variables."""
    token: str
    node_kind: str
    scripts: object = None
    leading_roles: tuple = ()
    body_role: str = "body"
    body_precedence: int = 0
    terminators: tuple = ()
    trailing_prefix: str = ""
    trailing_role: str = "variable"
    trailing_forms: tuple = ()
    semantic_macro: str = ""
    semantic_argument_roles: tuple = ()
    semantic_module: str = ""
    semantic_repository: str = ""
    name: str = ""
    keywords: tuple = ()
    description: str = ""
    source: str = "user configuration"

    def __post_init__(self):
        if not (re.fullmatch(r"\\(?:[A-Za-z]+|[^A-Za-z])", self.token)
                or (len(self.token) == 1 and not self.token.isspace())):
            raise ValueError("Bound constructs require a TeX control sequence or Unicode head")
        if self.node_kind not in {"BigOperatorApplication", "BinderApplication", "BoundExpression"}:
            raise ValueError("Unsupported bound construct node kind")
        scripts = dict(self.scripts or {})
        if any(marker not in {"_", "^"} or not isinstance(role, str)
               for marker, role in scripts.items()):
            raise ValueError("Bound construct scripts must map _/^ to role names")
        if type(self.body_precedence) is not int:
            raise ValueError("Bound construct body precedence must be an integer")
        object.__setattr__(self, "scripts", scripts)
        object.__setattr__(self, "leading_roles", tuple(self.leading_roles))
        object.__setattr__(self, "terminators", tuple(self.terminators))
        forms = tuple(dict(form) for form in self.trailing_forms)
        for form in forms:
            if not (form.get("separator") or form.get("prefix") or form.get("prefix_command")):
                raise ValueError("Trailing forms require a separator or prefix")
            if type(form.get("variables", 1)) is not int or form.get("variables", 1) < 1:
                raise ValueError("Trailing-form variable count must be positive")
        object.__setattr__(self, "trailing_forms", forms)
        object.__setattr__(self, "semantic_argument_roles", tuple(self.semantic_argument_roles))
        object.__setattr__(self, "keywords", tuple(self.keywords))


class NotationRegistry:
    def __init__(self, operators=(), commands=None, syntax=None, bound_constructs=None,
                 notation_aliases=None):
        raw_syntax = deepcopy(syntax or {})
        self.notation_aliases = deepcopy(notation_aliases or {})
        self.operators = {}
        configured_operators = list(operators)
        for operator_class in raw_syntax.get("literal_operator_classes", ()):
            characters = operator_class.get("characters", "")
            if (not isinstance(characters, str) or not characters
                    or any(character.isspace() or character == "\\" for character in characters)):
                raise ValueError("Literal operator classes require non-space literal characters")
            shared = {key: value for key, value in operator_class.items() if key != "characters"}
            configured_operators.extend(dict(shared, token=character) for character in characters)
        for item in configured_operators:
            op = item if isinstance(item, Operator) else Operator(**item)
            for variant in (op.token, *op.variants):
                if not isinstance(variant, str) or not variant or any(char.isspace() for char in variant):
                    raise ValueError("Operator spellings must be nonempty tokens")
                if variant in STRUCTURAL_TOKENS:
                    raise ValueError("TeX structural syntax cannot be registered as an operator")
                for spelling in get_equivalent_tex_notations(variant, self.notation_aliases):
                    key = spelling, op.fixity
                    previous = self.operators.get(key)
                    if previous is not None and previous != op:
                        raise ValueError("Conflicting operator metadata: " + spelling)
                    self.operators[key] = op
        self.spellings = sorted({key[0] for key in self.operators}, key=lambda x: (-len(x), x))
        self.commands = deepcopy(commands or {})
        for command, spec in self.commands.items():
            if not re.fullmatch(r"\\(?:[A-Za-z]+|[^A-Za-z])", command):
                raise ValueError("Commands require a TeX control sequence")
            if type(spec.get("arity")) is not int or spec["arity"] < 0:
                raise ValueError("Commands require a nonnegative arity")
            if spec.get("optional_arguments", 0) not in (0, 1):
                raise ValueError("At most one leading optional argument is supported")
            spec.setdefault("source", "user configuration")
            argument_types = spec.get("argument_types", [])
            if argument_types and (len(argument_types) != spec["arity"]
                                   or any(kind not in {"expression", "sequence", "opaque"} for kind in argument_types)):
                raise ValueError("argument_types must describe every command argument")
        self.bound_constructs = {}
        raw_constructs = bound_constructs or {}
        items = raw_constructs.items() if isinstance(raw_constructs, dict) else (
            (item.get("token"), item) for item in raw_constructs)
        for token, raw in items:
            spec = dict(raw)
            spec.setdefault("token", token)
            construct = BoundConstruct(**spec)
            if construct.token in self.bound_constructs:
                raise ValueError("Conflicting bound construct metadata: " + construct.token)
            self.bound_constructs[construct.token] = construct
        self.syntax = raw_syntax
        self.script_precedence = self.syntax.get("script_precedence", 100)
        self.application_precedence = self.syntax.get("application_precedence", 100)
        if type(self.script_precedence) is not int or type(self.application_precedence) is not int:
            raise ValueError("Syntax binding powers must be integers")
        self.functions = set(self.syntax.get("functions", ()))
        pairs = self.syntax.get("delimiter_pairs", {"(": ")", "[": "]", "{": "}"})
        if not isinstance(pairs, dict) or any(not isinstance(a, str) or not isinstance(b, str)
                                              or len(a) != 1 or len(b) != 1
                                              for a, b in pairs.items()):
            raise ValueError("delimiter_pairs must map single characters")
        self.delimiter_pairs = dict(pairs)
        self.open_delimiters = set(pairs)
        self.close_delimiters = set(pairs.values())
        self.symbolic_calls = bool(self.syntax.get("symbolic_calls", False))
        punctuation = self.syntax.get("trailing_punctuation", ())
        if (not isinstance(punctuation, (list, tuple))
                or any(not isinstance(item, str) or len(item) != 1 for item in punctuation)):
            raise ValueError("trailing_punctuation must contain single-character tokens")
        self.trailing_punctuation = set(punctuation)
        environments = self.syntax.get("alignment_environments", ())
        if not isinstance(environments, (list, tuple)) or any(not isinstance(x, str) for x in environments):
            raise ValueError("alignment_environments must be a list of names")
        self.alignment_environments = set(environments)
        ignored = self.syntax.get("ignored_commands", {})
        if isinstance(ignored, list):
            ignored = {command: 0 for command in ignored}
        if not isinstance(ignored, dict):
            raise ValueError("ignored_commands must map TeX commands to argument counts")
        self.ignored_commands = {}
        for command, arity in ignored.items():
            if (not re.fullmatch(r"\\(?:[A-Za-z]+|[^A-Za-z])", command)
                    or type(arity) is not int or arity < 0):
                raise ValueError("Ignored TeX commands require a control sequence and nonnegative arity")
            self.ignored_commands[command] = arity
        self.implicit = None
        if self.syntax.get("implicit_operator"):
            self.implicit = Operator(**self.syntax["implicit_operator"])
            if self.implicit.fixity != "infix":
                raise ValueError("Implicit operator must be infix")

    @classmethod
    def from_config(cls, config):
        return cls(config.get("operators", ()), config.get("commands"), config.get("syntax"),
                   config.get("bound_constructs"), config.get("notation_aliases"))

    def equivalent(self, visible, node_operator):
        visible_forms = set(get_equivalent_tex_notations(visible, self.notation_aliases))
        operator_forms = {node_operator.token, *node_operator.variants}
        operator_forms = {form for item in operator_forms
                          for form in get_equivalent_tex_notations(item, self.notation_aliases)}
        return bool(visible_forms & operator_forms)


# TeX lexical syntax only; mathematical meanings come from the registry.


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


def unicode_script(character):
    """Return (TeX-style role, plain value) for Unicode sub/superscripts."""
    decomposition = unicodedata.decomposition(character)
    if not decomposition:
        return None
    parts = decomposition.split()
    if not parts or parts[0] not in {"<sub>", "<super>"}:
        return None
    try:
        value = "".join(chr(int(code, 16)) for code in parts[1:])
    except ValueError:
        return None
    return ("_" if parts[0] == "<sub>" else "^", value)


def tokenize(source, registry):
    tokens = []
    position = 0
    while position < len(source):
        start = position
        character = source[position]
        if character.isspace():
            position += 1
            continue
        if character == "%":
            end = source.find("\n", position)
            position = len(source) if end == -1 else end
            continue
        spelling = next((item for item in registry.spellings
                         if source.startswith(item, position)
                         and not (item[-1].isalpha() and position + len(item) < len(source)
                                  and source[position + len(item)].isalpha())), None)
        if spelling:
            position += len(spelling)
            kind = "operator"
        elif character == "\\":
            found = re.match(r"\\(?:[A-Za-z]+|[^A-Za-z])", source[position:])
            position += len(found.group()) if found else 1
            kind = "command" if found else "unknown"
            if found and found.group() in {r"\left", r"\right"}:
                kind = "sized_open" if found.group() == r"\left" else "sized_close"
            elif found and found.group() in {r"\{", r"\}"}:
                kind = "open" if found.group() == r"\{" else "close"
        elif unicode_script(character):
            position += 1
            kind = "unicode_script"
        elif character.isdecimal():
            found = re.match(r"\d+(?:\.\d+)?", source[position:])
            position += len(found.group())
            kind = "number"
        elif character.isalpha() or unicodedata.category(character).startswith("L"):
            position += 1
            kind = "atom"
        else:
            position += 1
            kind = ("open" if character in registry.open_delimiters else
                    "close" if character in registry.close_delimiters else
                    {"^": "script", "_": "script", ",": "comma"}.get(character))
            if kind is None:
                category = unicodedata.category(character)
                kind = ("combining" if category.startswith("M") else
                        "math_symbol" if category in {"Sm", "So"} else "unknown")
        tokens.append(Token(kind, source[start:position], start, position))
    tokens.append(Token("end", "", len(source), len(source)))
    return tokens


# Notation-independent, source-preserving Pratt parser of complete expressions.


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


    def named_children(self):
        """Iterate structural children with stable, domain-specific roles."""
        roles = self.roles or tuple(
            f"argument_{index}" for index in range(1, len(self.children) + 1))
        return tuple(zip(roles, self.children))

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
        environment = self._environment_expression()
        if environment is not None:
            return environment
        sequence = self._independent_whitespace_sequence()
        if sequence is not None:
            return sequence
        self.tokens = tokenize(source, self.registry)
        self.index = 0
        self.stop_tokens = []
        try:
            self._skip_ignored_commands()
            children = self._comma_list(allow_trailing=True)
            result = children[0] if len(children) == 1 else self._node(
                "MathSequence", children[0].start, children[-1].end,
                "comma-separated expressions", children,
                roles=tuple("item" for _ in children),
                metadata={"semantic_mapping_available": False,
                          "source": "top-level comma separation"})
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

    def _independent_whitespace_sequence(self):
        """Preserve independently complete top-level fragments honestly.

        TeX whitespace normally has no mathematical meaning, so ordinary
        adjacency remains governed by configured juxtaposition.  When a
        top-level whitespace boundary separates complete expressions and a
        later fragment is itself a relation, however, joining the preceding
        fragment into that relation would invent a connection.  Represent
        those fragments as a neutral MathSequence container instead.
        """
        depth, boundaries, start = 0, [], 0
        position = 0
        while position < len(self.source):
            char = self.source[position]
            if char in "{([⟨":
                depth += 1
            elif char in "})]⟩" and depth:
                depth -= 1
            elif depth == 0 and char.isspace():
                end = position
                while position < len(self.source) and self.source[position].isspace():
                    position += 1
                boundaries.append((start, end))
                start = position
                continue
            position += 1
        boundaries.append((start, len(self.source)))
        spans = [(a, b) for a, b in boundaries if self.source[a:b]]
        if len(spans) < 2:
            return None

        def rebase(node, offset):
            return replace(node, start=node.start + offset, end=node.end + offset,
                           operator_start=(node.operator_start + offset
                                           if node.operator_start >= 0 else -1),
                           source=self.source,
                           children=tuple(rebase(child, offset) for child in node.children))
        children = []
        try:
            for begin, end in spans:
                child = ExpressionParser(registry=self.registry).parse(self.source[begin:end])
                children.append(rebase(child, begin))
        except ParseError:
            return None
        relational = any(
            child.kind in {"BinaryOperator", "NaryOperator"}
            and isinstance(child.metadata, Operator)
            and child.metadata.precedence <= 2
            for child in children[1:])
        if not relational:
            return None
        return self._node("MathSequence", children[0].start, children[-1].end,
                          "independent fragments", children,
                          roles=tuple("fragment" for _ in children),
                          metadata={"semantic_mapping_available": False,
                                    "source": "top-level whitespace separation"})

    def _environment_expression(self):
        """Parse one complete TeX environment as a source-preserving container."""
        match = re.fullmatch(
            r"\s*\\begin\{([^{}]+)\}([\s\S]*)\\end\{\1\}\s*", self.source)
        if match is None:
            return None
        name = match.group(1)
        body_start, body_end = match.start(2), match.end(2)

        def spans(start, end, separator):
            depth, item_start, position = 0, start, start
            while position < end:
                character = self.source[position]
                if character in "{([":
                    depth += 1
                elif character in "})]" and depth:
                    depth -= 1
                elif depth == 0 and self.source.startswith(separator, position):
                    yield item_start, position
                    position += len(separator)
                    item_start = position
                    continue
                position += 1
            yield item_start, end

        def trimmed(start, end):
            while start < end and self.source[start].isspace():
                start += 1
            while end > start and self.source[end - 1].isspace():
                end -= 1
            return start, end

        def rebase(node, offset):
            return replace(
                node, start=node.start + offset, end=node.end + offset,
                operator_start=(node.operator_start + offset
                                if node.operator_start >= 0 else -1),
                source=self.source,
                children=tuple(rebase(child, offset) for child in node.children))

        rows = []
        try:
            for row_start, row_end in spans(body_start, body_end, r"\\"):
                content_start, content_end = trimmed(row_start, row_end)
                if content_start == content_end:
                    continue
                cells = []
                for cell_start, cell_end in spans(content_start, content_end, "&"):
                    expression_start, expression_end = trimmed(cell_start, cell_end)
                    if expression_start == expression_end:
                        raise ParseError("Environment contains an empty cell")
                    child_source = self.source[expression_start:expression_end]
                    child = ExpressionParser(registry=self.registry).parse(child_source)
                    child = rebase(child, expression_start)
                    cells.append(self._node("Cell", cell_start, cell_end, "cell", (child,),
                                            roles=("expression",)))
                rows.append(self._node("Row", row_start, row_end, "row", cells,
                                       roles=tuple("cell" for _ in cells)))
        except ParseError as error:
            opaque = self._node(
                "OpaqueEnvironmentBody", body_start, body_end, "body",
                metadata={"opaque": True, "error": str(error),
                          "semantic_mapping_available": False})
            return self._node(
                "EnvironmentExpression", match.start(), match.end(), name, (opaque,),
                roles=("body",), metadata={"environment": name, "opaque": True,
                                           "semantic_mapping_available": False})
        return self._node(
            "EnvironmentExpression", match.start(), match.end(), name, rows,
            roles=tuple("row" for _ in rows),
            metadata={"environment": name, "opaque": False,
                      "semantic_mapping_available": False})

    def _locate_error(self, error):
        import re
        position = re.search(r"offset (\d+)", str(error))
        if error.position is None:
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
        # Layout commands can occur at the start of any recursively parsed
        # expression, including binder bodies and grouped subexpressions.
        self._skip_ignored_commands()
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
            if token.kind == "combining":
                mark = self._take()
                left = self._node("DecoratedExpression", left.start, mark.end, mark.text,
                                  (left,), roles=("base",), notation=mark.text,
                                  metadata={"source": "Unicode combining mark",
                                            "semantic_mapping_available": False})
                continue
            if token.kind in {"script", "unicode_script"} and self.registry.script_precedence >= minimum:
                self._take()
                marker = token.text
                if token.kind == "unicode_script":
                    marker, value = unicode_script(token.text)
                    argument = self._node("Atom", token.start, token.end, value,
                                          metadata={"raw_unicode_script": token.text})
                else:
                    argument = self._tex_argument()
                if left.kind == "ScriptedExpression" and marker in left.roles[1:]:
                    raise ParseError("Repeated script requires explicit grouping")
                if left.kind == "ScriptedExpression":
                    children, roles = (*left.children, argument), (*left.roles, marker)
                else:
                    children, roles = (left, argument), ("base", marker)
                left = self._node("ScriptedExpression", left.start, argument.end, "scripts", children,
                                  roles=roles, metadata={"precedence": self.registry.script_precedence})
                continue
            if (self._starts_call_group() and self.registry.application_precedence >= minimum
                    and self._callable(left)):
                group = (self._sized_group() if token.kind == "sized_open"
                         else self._group(allow_list=True))
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
            if (left.kind in {"BinaryOperator", "NaryOperator", "UncertainOperatorChain"}
                    and isinstance(left.metadata, Operator)
                    and left.metadata.precedence == op.precedence
                    and (left.metadata.associativity == "none" or op.associativity == "none")):
                if (left.metadata == op and op.associativity == "none"
                        and op.uncertain_chain):
                    right = self._expression(op.precedence + 1)
                    children = ((*left.children, right)
                                if left.kind == "UncertainOperatorChain" else (left, right))
                    left = self._node(
                        "UncertainOperatorChain", left.start, right.end, op.token, children,
                        operator_start=token.start, metadata=op, notation=token.text,
                        roles=tuple(f"alternative_operand_{index}"
                                    for index in range(1, len(children) + 1)))
                    continue
                raise ParseError("Non-associative operators require explicit grouping",
                                 position=token.start, token=token.text,
                                 category="non_associative_chain")
            right = self._expression(op.precedence + (op.associativity != "right"))
            if (right.kind in {"BinaryOperator", "NaryOperator"} and isinstance(right.metadata, Operator)
                    and right.metadata.precedence == op.precedence
                    and (right.metadata.associativity == "none" or op.associativity == "none")):
                raise ParseError("Non-associative operators require explicit grouping",
                                 position=(right.operator_start if right.operator_start >= 0 else token.start),
                                 token=right.notation or right.value,
                                 category="non_associative_chain")
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
        return ((node.kind == "Atom" and (self.registry.symbolic_calls
                                          or node.value in self.registry.functions))
                or (node.kind == "Command" and bool((node.metadata or {}).get("callable"))))

    def _starts_call_group(self):
        if self.current.text == "(":
            return True
        return (self.current.kind == "sized_open"
                and self.index + 1 < len(self.tokens)
                and self.tokens[self.index + 1].text == "(")

    def _starts_primary(self):
        return self.current.kind in {"atom", "number", "command", "sized_open", "math_symbol", "open"}

    def _sized_group(self):
        start = self._take().start
        opening = self._take()
        delimiters = {"(": ")", "[": "]", r"\{": r"\}"}
        closing = delimiters.get(opening.text)
        if closing is None:
            raise ParseError("Unsupported sizing delimiter at offset " + str(opening.start))
        children = self._comma_list()
        if self.current.kind != "sized_close":
            raise ParseError("Expected TeX closing size delimiter")
        self._take()
        if self.current.text != closing:
            raise ParseError("Mismatched TeX sizing delimiters")
        end = self._take().end
        return self._node("GroupedExpression", start, end, opening.text, children,
                          metadata={"sized": True})

    def _group(self, allow_list=False):
        opening = self._take()
        closing = ({r"\{": r"\}"}.get(opening.text)
                   or self.registry.delimiter_pairs.get(opening.text))
        if closing is None:
            raise ParseError(f"Unsupported delimiter at offset {opening.start}")
        children = (self._comma_list() if allow_list else
                    [self._expression(float("-inf"))])
        if self.current.text != closing:
            raise ParseError(f"Expected {closing} at offset {self.current.start}")
        end = self._take().end
        return self._node("GroupedExpression", opening.start, end, opening.text, children)

    def _comma_list(self, allow_trailing=False):
        """Share comma-list syntax without interpreting ellipses or relations."""
        children = [self._expression(float("-inf"))]
        while self.current.kind == "comma":
            # Preserve the existing configured trailing-comma convention.
            if allow_trailing and "," in self.registry.trailing_punctuation:
                saved_index = self.index
                while self.current.text in self.registry.trailing_punctuation:
                    self._take()
                    self._skip_ignored_commands()
                if self.current.kind == "end":
                    break
                self.index = saved_index
            self._take()
            children.append(self._expression(float("-inf")))
        return children

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
        construct = self.registry.bound_constructs.get(token.text)
        if construct is not None:
            return self._bound_construct(construct)
        if token.kind == "sized_open":
            return self._sized_group()
        if token.kind == "open":
            return self._group(allow_list=True)
        if token.kind in {"atom", "number"}:
            self._take()
            return self._node("Number" if token.kind == "number" else "Atom", token.start, token.end, token.text)
        if token.kind == "math_symbol":
            self._take()
            return self._node("OpaqueMathToken", token.start, token.end, token.text,
                              notation=token.text,
                              metadata={"source": "generic Unicode symbol fallback",
                                        "opaque": True,
                                        "semantic_mapping_available": False})
        if token.kind == "command":
            self._take()
            spec = self.registry.commands.get(token.text)
            if spec is None:
                if token.text in {r"\%", r"\#", r"\$", r"\&", r"\_", r"\{", r"\}"}:
                    return self._node("Command", token.start, token.end, token.text,
                                      notation=token.text)
                # An alphabetic control sequence is safe to preserve without
                # assigning meaning. Consecutive explicit groups are retained
                # as generically named children; their mathematical contents
                # are still parsed, but their command-specific roles are not
                # guessed. Configured commands and bound constructs have
                # already taken their higher-priority paths above.
                if not re.fullmatch(r"\\[A-Za-z]+", token.text):
                    raise ParseError(
                        f"Unknown command {token.text} at offset {token.start}; "
                        "configure its arguments")
                children, roles = [], []
                optional_number = argument_number = 0
                while self.current.text in {"[", "{"}:
                    if self.current.text == "[":
                        optional_number += 1
                        roles.append(f"optional{optional_number}")
                    else:
                        argument_number += 1
                        roles.append(f"arg{argument_number}")
                    children.append(self._group(allow_list=True))
                if children:
                    return self._node(
                        "OpaqueCommandApplication", token.start, children[-1].end,
                        token.text, children, roles=tuple(roles), notation=token.text,
                        metadata={"source": "structural unknown-command fallback",
                                  "opaque": True,
                                  "semantic_mapping_available": False,
                                  "argument_roles_known": False})
                return self._node(
                    "SymbolAtom", token.start, token.end, token.text,
                    notation=token.text,
                    metadata={"source": "structural unknown-command fallback",
                              "semantic_mapping_available": False,
                              "uncertain_arity": True})
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
                    kinds = spec.get("argument_types", ["expression"] * spec["arity"])
                    children.append(self._opaque_argument() if kinds[number] == "opaque"
                                    else self._tex_argument())
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
        while self.current.kind in {"script", "unicode_script"}:
            token = self.current
            marker = (unicode_script(token.text)[0]
                      if token.kind == "unicode_script" else token.text)
            if marker not in spec.scripts:
                break
            self._take()
            if marker in seen_scripts:
                raise ParseError("Repeated bound-operator script requires explicit grouping")
            seen_scripts.add(marker)
            if token.kind == "unicode_script":
                value = unicode_script(token.text)[1]
                children.append(self._node("Atom", token.start, token.end, value,
                                           metadata={"raw_unicode_script": token.text}))
            else:
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
