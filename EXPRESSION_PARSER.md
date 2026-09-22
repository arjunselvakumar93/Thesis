# Generic mathematical expression parser: implementation report

This extends the existing repository. It parses complete expressions within an
explicit LaTeX/sTeX grammar; it is not a TeX interpreter. The original matcher and
review path remain available without a registry configuration.

## Verification

- Baseline for this continuation: **133 tests**, with 127 passing, one failure,
  and five errors caused by incomplete catalogue-policy wiring and stale
  classifier expectations.
- Final: **135 passing tests**, using `py -m unittest discover -s tests -v`.
- The synthetic `.tex` corpus has **63 active math regions**: 61 parse and the
  two deliberately unsupported big-operator/integral regions retain explicit
  diagnostics. Ordinary text and commented math are excluded.
- The real paper has **70 math regions**: 69 parse, for **98.6% structural
  coverage**. The only remaining failure is a multi-row alignment region.
- Tests run through the installed Python outside the sandbox because the
  sandboxed launcher is denied access; the workspace virtual environment points
  to an unavailable interpreter.

## Files

Created in this continuation:

- `src/expression_registry.py`: validated operator, command and syntax metadata.
- `src/expression_tokenizer.py`: source-preserving TeX-aware lexical tokens.
- `src/expression_annotation.py`: AST-only scope matching (no parsing).
- `src/expression_document.py`: full-region parsing, diagnostics and document offsets.
- `src/inspect_expression.py`: readable AST inspection CLI.
- `tests/test_general_expression_parser.py`: 32 additional tests.
- `tests/fixtures/general_math_expressions.tex`: synthetic expression corpus.
- `expression-config.smglom.example.json`: real-SMGloM import example.
- `EXPRESSION_DEMOS.md`: output from the inspection CLI for the whole corpus.

Modified existing files, including files introduced in the preceding milestone:

- `src/expression_parser.py`: extends the Pratt parser with lexical tokens,
  scripts, applications, n-ary sequences, sized groups and structured semantic arguments.
- `src/expression_metadata.py`: independent whole-registry loading and user mappings.
- `src/notation_matcher.py`, `src/notation_scanner.py`: route configured requests
  through complete-region AST parsing and subtree matching.
- `src/review_workflow.py`: parse before annotation selection, reuse trees, resolve
  registry aliases/custom mappings, persist the grammar and exact source spans.
- `src/decision_validator.py`: reconstruct registry choices and check source spans.
- `src/apply_annotations.py`: exact-source application for AST decisions, including
  multiline scopes; reject stale and overlapping source scopes.
- `expression-config.example.json`: standalone demonstration grammar.
- `EXPRESSION_PARSER.md`: this updated report.

The SMGloM extractor, legacy classifier, relation reader, TeX operand reader,
existing TeX alias registry and original test files are retained.

## Layers and workflow

1. Extract math regions from the original LaTeX source.
2. Build the complete notation registry from JSON and explicit SMGloM imports.
3. Tokenize and parse all math regions into ASTs, recording unsupported regions.
4. Let the user select an annotation notation/semantic candidate.
5. Match existing AST nodes against that selection, without reparsing.
6. Review candidate scopes, store decisions, validate, then apply to copies.

`parse_math_regions(source, registry)` is independent of annotation selection.
Each result has document offsets, original source, a tree or an error diagnostic.
AST offsets are relative to their math region. `match_ast(tree, rule, registry)`
can select addition and multiplication from the **same** `a+b*c` tree.

The configured CLI parses papers before displaying the visible-notation prompt:

```powershell
py src/review_workflow.py --expression-config expression-config.example.json
```

The former `selected` configuration is retained as a compatibility adapter, but
it does not provide the parse-before-selection workflow. New configurations
should define the whole grammar without a `selected` entry.

## Token model

Tokens preserve exact text and half-open start/end offsets. Categories include
letters, numbers, complete control sequences, registered operators, group opens
and closes, script markers, separators, sizing commands, unknown syntax and EOF.
Prefix/infix/postfix roles are resolved from registry metadata in parser context;
function application is an AST construct rather than a guessed lexical token.

Control words use maximal matching, so an alias such as `\neq` cannot match the
beginning of `\neqUnknown`. Registered visible operators use longest spelling
first. Math letters are individual TeX atoms; configured implicit multiplication
can turn `Bx` into a product. Numbers are grouped for ordinary expression parsing;
unbraced script/command arguments consume one TeX token.

Whitespace and comments are skipped lexically, but remain in the original source
and node spans. Raw `%` begins a comment. Escaped `\%` and `\#` remain visible
control symbols. Raw `#` is unsupported macro-parameter syntax, not an operator.
Groups, `^`, `_`, and sizing commands are TeX syntax and cannot be registered or
looked up as standalone mathematical operators.

## AST model

`Expression` stores kind, value/canonical notation, children, roles, original
source, start/end offsets, operator position, metadata and original operator
spelling. `node.text` returns the exact source slice.

Current generic node kinds are Atom, Number, Command, SemanticCommand,
OpaqueArgument, GroupedExpression, UnaryOperator, BinaryOperator, NaryOperator,
ScriptedExpression and FunctionApplication. A UnaryOperator's metadata records
prefix versus postfix fixity. There are no PlusNode/MinusNode/UnionNode classes.

Script children have roles `base`, `_` and/or `^`, preserving written order.
Command roles can be named, such as numerator/denominator/radicand. This same
child-role representation can later support binder bounds, body and variable;
no integral-specific node or core parser branch was introduced. Big operators
and binders are deliberately reported as unsupported until their bound/body
roles can be represented without guessing.

## Precedence and associativity

The Pratt parser uses integer binding powers: higher binds more tightly.
Parenthesized/braced/bracketed groups preserve explicit scope. Metadata supports
prefix, infix and postfix fixity, unary/binary/variable arity, and left, right,
non-associative or explicit sequence grouping.

`left` and `right` produce nested trees. `none` rejects an ungrouped chain at the
same precedence. `sequence` requires variable infix arity and produces an ordered
NaryOperator. It does not cross explicit groups or merge different operators.
Variable arity alone never authorizes flattening: the ordinary subtraction
example remains nested. Sequence grouping is a user-selected syntactic model,
not a proof of algebraic associativity.

Functions must be declared through `syntax.functions` or a command's `callable`
metadata. Implicit products also require explicit policy. Thus `f(x)` can be an
application while `(x-R)(x-S)` is a product; this is configuration, not a guess
about the meaning of adjacent parentheses.

## SMGloM integration

The current extractor already preserves options and signatures. Local inspection
found these declarations in `reference/arithmetics/source/mod/arithmetics.en.tex`:

| Declaration | Recorded metadata |
| --- | --- |
| addition | `args=a, prec=500`, separator through `\argsep` |
| subtraction | `args=a, prec=450`, separator through `\argsep` |
| multiplication | `prec=400`; explicit `\cdot` and `\times` notation variants |
| division | `args=2, prec=400`; fraction variant uses `prec=nobrackets` |
| exponentiation | `prec=200;300x\infprec` |

`reference/mv/source/mod/equal.en.tex` supplies equality at 700 (`args=a`) and
not-equal at 800 (`args=ai`). No associativity direction is inferred from `args`
or `\argsep`; they describe argument/sequence presentation structure.

The registry loader resolves explicit repository/module/macro/visible-notation
selectors, requiring a unique definition and notation. It imports visible tokens,
command templates, argument signatures, semantic command registration and numeric
outer precedence. Metadata records its SMGloM/user origin.

Set `smglom_precedence_direction` to `lower` or `higher`. The former negates the
SMGloM outer number into a binding power. This policy is explicit because source
precedences describe presentation; arbitrary per-argument constraints do not
translate directly into a complete parsing grammar. Compound argument constraints
are preserved in the extracted record but not applied by the parser.
`nobrackets` does not invent an infix binding power; braced command templates can
be imported without one. Missing information requires an explicit override.

```powershell
py src/inspect_expression.py 'a+b*c' --config expression-config.smglom.example.json
```

The SMGloM example explicitly maps `*` as a user variant of the selected `\cdot`
notation. It retains the different recorded addition/subtraction precedences:
`a+b-c` becomes `a+(b-c)` under that example. The standalone conventional example
instead sets equal addition/subtraction binding powers and produces `(a+b)-c`.
Neither result is silently declared equivalent to the other.

Local arithmetic and mv repositories are present; the configured local sets
repository is absent. Set/logic demonstrations therefore use user metadata.

## User-defined notation

No Python change is needed to introduce an ordinary operator:

```json
{
  "operators": [
    {"token": "\\fooop", "precedence": 20, "associativity": "left",
     "fixity": "infix", "arity": 2, "semantic_macro": "customOperation"}
  ]
}
```

This parses `a\fooop b`. An explicit semantic mapping also permits the review
workflow to suggest `\customOperation{a}{b}` without a SMGloM record. Such a local
macro must be defined by the user's document/environment; no module import is
invented. Real SMGloM selections retain their module/repository information.

Optional `variants` is a list of explicit equivalent operator spellings. The
existing global TeX syntax aliases are reused, including `\ne`/`\neq`. The example
configuration, not parser code, supplies `\wedge`/`\land`. Styled applications
`\mathcal{P}(X)` and `\mathfrak{P}(X)` retain distinct function heads and are not
made semantically equivalent. Full semantic template matching for these styles
remains future work.

A required regression test changes only metadata for `@` from 100 to 300 while
`$` stays at 200: `a@b$c` changes from `a@(b$c)` to `(a@b)$c`. Synthetic `$` is
used in the raw-expression API; document extraction reserves `$` for math delimiters.

## Partially semantic expressions

Semantic command argument structure comes from metadata. `argument_types` allows
`expression`, `sequence`, or `opaque`; opaque is the conservative default.
SMGloM `a` arguments can be registered as sequences, ordinary `i` arguments as
expressions, and unimplemented binder signatures remain opaque.

`\aplus{a,b*c}` is one SemanticCommand with one sequence argument containing the
`a` and multiplication subtrees. It can participate as the left operand of
`\aplus{a,b*c}-\frac{x}{y}`. Nested semantic applications work through the same
command mechanism. Existing opaque semantic configurations continue to work.

## Required demonstrations

These compact trees use the standalone example grammar. `Group` is explicit;
`Semantic`, `Command`, `Function`, and `Script` name generic AST kinds.

| Input | Structure |
| --- | --- |
| `a+b*c` | `+(a, *(b,c))` |
| `(a+b)*c` | `*(Group(+(a,b)), c)` |
| `a*b+c` | `+(*(a,b), c)` |
| `a+b-c` | `-(+(a,b), c)` |
| `a-b-c` | `-(-(a,b), c)` |
| `a+(b-c)` | `+(a, Group(-(b,c)))` |
| `\aplus{a,b}*c` | `*(Semantic(aplus; sequence[a,b]), c)` |
| `x_i^2` | `Script(base=x, subscript=i, superscript=2)` |
| `\frac{a+b*c}{d-e}` | `Command(frac; Group(+(a,*(b,c))), Group(-(d,e)))` |
| `A\wedge(B\vee C)` | `wedge(A, Group(vee(B,C)))` |
| `A\cup(B\cap C)` | `cup(A, Group(cap(B,C)))` |
| `\mathcal{P}(X)` | `Function(Command(mathcal; P), X)` |
| `\mathfrak{P}(X)` | `Function(Command(mathfrak; P), X)` |

Long example:

```text
NaryOperator(=)
  Command(\frac)
    Group
      BinaryOperator(+)
        Script(base=x, subscript=i, superscript=2)
        BinaryOperator(*)
          Atom(a)
          Atom(b)
    Group
      Command(\sqrt)
        Group
          BinaryOperator(-)
            Atom(c)
            Atom(d)
  Script(base=y, superscript=Group(+(n,1)))
```

This is the structure of `\frac{x_i^2+a*b}{\sqrt{c-d}} = y^{n+1}`.
The fixture additionally contains `\sum` and `\int` examples whose unsupported
status is asserted by tests instead of being hidden.

```powershell
py src/inspect_expression.py 'a+b*c'
py src/inspect_expression.py --file tests/fixtures/general_math_expressions.tex
```

## Candidates and safe application

Candidate scopes are actual matching subtrees. In `a-b-c`, left-nested metadata
exposes both `a-b` and `a-b-c`; the outer operands are `a-b` and `c`. It does not
manufacture a `b-c` subtree. In `a+b*c`, the addition operands are `a` and `b*c`,
while multiplication can independently target `b*c`.

New AST decisions store the complete grammar, original source offsets and raw
source expression. Validation reconstructs that grammar and compares the source.
Application checks original text, rejects stale spans, skips all mutually
overlapping accepted scopes and replaces disjoint spans from right to left.
Multiline/comment-containing scopes use exact source spans; comments are masked
when constructing semantic arguments. Files are still applied to copied output.
Legacy decisions retain their existing application path.

## Supported boundary and remaining limitations

- No arbitrary macro expansion, TeX execution, environments inside expressions,
  alignment-row ASTs, general binders/integrals, or arbitrary mixfix templates.
- Real-paper coverage is 69/70 regions. The remaining `align` region uses `&`
  and `\\`; it needs a source-preserving row/container AST rather than token
  deletion.
- Unknown control words yield a diagnostic for the whole region. Register a
  zero-argument command for a known symbol, or specify its argument structure.
  They are not silently treated as function/product operands.
- Supported sized groups are parentheses, brackets and escaped braces. Absolute
  value bars, invisible delimiters and other sizing delimiters need grammar work.
- Function names/callable commands and implicit-product policy require metadata.
  Function argument lists are nonempty. This is not semantic type inference.
- Command templates support fixed arguments and one leading optional bracketed
  argument. Annotation matching skips optional arguments it cannot preserve.
- Symbolic/compound SMGloM argument-precedence expressions, complex templates and
  general style-template annotation are not interpreted automatically.
- Semantic argument parsing is only as strong as configured signatures. Binder
  arguments and unknown signatures are kept opaque or require configuration.
- Long flat expressions are tested at 1,200 terms. Excessive recursive nesting
  returns ParseError; the document API records it as an unsupported region.
- Unsupported regions are reported independently and do not prevent parsing other
  regions. There is no fallback that fabricates a nearest-operand AST match.
- No mathematical-symbol-specific branches remain in the new parser/tokenizer.
  Branches for braces, scripts, escaped TeX characters and `\left`/`\right` are
  lexical/structural TeX rules. Mathematical operators live in registry data.
  Earlier compatibility readers retain their existing historical implementation.
