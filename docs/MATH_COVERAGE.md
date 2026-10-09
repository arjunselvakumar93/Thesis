# Mathematical input coverage

The system accepts **textual** mathematics. OCR or recognition from images is a
separate preprocessing problem and is outside this parser.

Input can use known LaTeX grammar, direct Unicode mathematical characters,
generic symbol fallback, opaque unknown-command fallback, structural or opaque
environment fallback, configurable operator/command/binder metadata, and
semantic metadata extracted from SMGloM.

Coverage has three distinct levels:

1. **LEVEL 1 — SOURCE PRESERVED** — the exact source characters and offsets survive safely.
2. **LEVEL 2 — STRUCTURALLY UNDERSTOOD** — an AST, review scope, and structural arguments
   are available.
3. **LEVEL 3 — SEMANTICALLY ANNOTATABLE** — validated external metadata supplies an
   annotation candidate and its argument mapping.

Opaque preservation satisfies Level 1, but does not by itself establish full
structural understanding at Level 2.

Structural recognition never establishes mathematical meaning. Unicode `ω`,
for example, is a symbol atom without deciding what it denotes. Registered
Unicode operators describe grammar only unless separate semantic metadata is
present. Unicode and TeX presentation forms are not treated as semantically
equivalent merely because they look related.

Unicode letters are tokenized from Unicode character properties. Adjacent
letters such as `ωi` remain exactly those two source characters and are parsed
using the configured juxtaposition grammar; they are never rewritten as
`ω_i`. Unicode characters with an explicit `<sub>` or `<super>` decomposition,
such as `ᵢ`, `₁`, and `²`, use the existing scripted-expression AST while
retaining their original source span.

Known Unicode operators, relations, binders, and big operators are configured
declaratively. Unregistered Unicode mathematical symbols are retained as
`OpaqueMathToken` nodes when they can safely occupy an operand position.

The AST remains internal during review. An automatic source range is proposed
first; the reviewer may replace it with one of a small number of complete,
AST-derived and reparsed ranges. Semantic lookup and argument extraction run
again for the selected range, followed by candidate reranking. Review state
separates structural parsing, candidate availability, annotation readiness,
semantic uncertainty, and unsupported input. Unknown or ambiguous semantics
remain explicit.

TeX superscripts and subscripts are structural syntax rather than standalone
semantic operators. `\cdot` uses generic configured sequence structure, while
its semantic candidates are independently backed by installed SMGloM metadata.
Function signatures, biconditional grouping, and definite-integral differential
forms are configuration-dependent structural support. Greek capital sigma `Σ` remains
a letter, while mathematical summation `∑` uses big-operator metadata.
