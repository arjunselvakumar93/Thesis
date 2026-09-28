"""Validated notation data shared by tokenization, parsing and annotation."""

from dataclasses import dataclass
from copy import deepcopy
import re
from tex_notation_aliases import get_equivalent_tex_notations

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
        object.__setattr__(self, "variants", tuple(self.variants))


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
