# Professor demo summary

## A. What the system does

The system provides broad, extensible structural support for textual
mathematical notation using generic LaTeX and Unicode parsing, source-span
preservation, configurable grammar metadata, and safe opaque fallbacks.

## B. Professor example

Both `\sum_{\omega_i\in A} P(\omega_i)` and `∑_{ωᵢ∈A} P(ωᵢ)` are parsed as
source-preserving big-operator applications. Their lower expressions contain a
complete membership relation and their bodies contain a complete function
application.

## C. Unicode support

Unicode letters use Unicode character properties. Common Unicode scripts use
Unicode decomposition metadata. Configured Unicode operators, relations,
delimiters, binders, and big operators reuse the same AST concepts as LaTeX
input while retaining distinct raw source strings.

## D. Unknown notation behavior

Unknown bare LaTeX symbol commands, commands with explicit arguments, and
Unicode mathematical symbols use `SymbolAtom`, `OpaqueCommandApplication`, or
`OpaqueMathToken` fallbacks. These preserve source safely without inventing
meaning.

## E. Semantic annotation behavior

Semantic annotation remains metadata-driven and is only proposed when the
available SMGloM or explicitly loaded configuration evidence supports it.
Structural-only review scopes are displayed as not annotation-ready.

## F. Main evaluation results

On the evaluated three-document corpus, structural parsing covers 141/141 math
regions and gold scope accuracy is 58/58. Supported candidate recall and
Top-1/Top-3/Top-5 ranking are 44/44, MRR is 1.0, and application succeeds for
59/59 evaluated decisions. The independent Unicode stress suite contains 309
expressions: 306 are fully structured, 3 are opaque-preserved, and 0 are
unsupported.

## G. Remaining limitations

The parser implements a bounded textual mathematics grammar rather than full
TeX macro expansion. Semantic availability depends on installed repositories
and validated mappings. Opaque preservation is not full structural or semantic
understanding. OCR is outside the system. Corpus results should not be
generalized to universal mathematical coverage.
