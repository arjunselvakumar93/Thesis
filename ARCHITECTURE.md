# Architecture

The current pipeline is:

```text
LaTeX source
  -> math-region extraction
  -> tokenizer
  -> notation registry
  -> generic expression parser
  -> source-preserving AST
  -> SMGloM and user semantic catalogue
  -> grammar adapters
  -> semantic candidates
  -> human review
  -> stored decisions
  -> decision validation
  -> source-span application to an output copy
  -> evaluation
```

## Main modules

- `expression_document.py`: extracts active TeX math regions and alignment rows while preserving source offsets.
- `expression_tokenizer.py`: converts math source into source-positioned tokens.
- `expression_registry.py`: validates metadata-driven operators, commands, aliases, and bound constructs.
- `expression_parser.py`: creates the generic expression AST from registry metadata.
- `smglom_notation_extractor.py`: discovers local repositories and extracts SMGloM notation declarations.
- `notation_classifier.py`: classifies extracted notation shapes conservatively.
- `notation_adapters.py`: maps reusable presentation-template shapes to grammar descriptions.
- `notation_catalog.py`: combines extracted SMGloM records and explicit user configuration.
- `expression_annotation.py`: matches catalogue classifications against an existing AST.
- `review_workflow.py`: presents semantic candidates for human decisions.
- `decision_validator.py`: separates current, stale, and unverifiable stored decisions.
- `apply_annotations.py`: composes validated source-span replacements into copied documents.
- `evaluate_workflow.py`: measures structural, semantic, review, and application outcomes.
- `smglom_dependencies.py`: reports repository dependencies, health, statistics, and snapshots.
- `project_status.py`: assembles the current project status from those existing layers.

## Configuration

`expression-config.example.json` demonstrates the general expression grammar. `integral-config.example.json` adds synthetic `\fooIntegral` metadata to demonstrate runtime extension. It is not a real SMGloM calculus declaration.

## Evolution

Earlier token and notation matchers remain for compatibility and focused workflows. Current whole-expression evaluation uses math-region extraction, the registry-driven parser, AST matching, validated decisions, and source-span application.

## Structural fallback hierarchy

The parser keeps structural recognition separate from semantic claims:

```text
known configured construct
  -> precise structural AST

unknown bare alphabetic control word
  -> SymbolAtom

unknown command followed by explicit groups
  -> OpaqueCommandApplication with generic group roles

complete TeX environment
  -> EnvironmentExpression containing source-positioned rows and cells
  -> OpaqueEnvironmentBody if a cell cannot be parsed safely

known operator or configured literal-operator class
  -> precedence-aware operator AST

unknown semantics
  -> structural representation only; no semantic candidate or annotation claim
```

Environment parsing treats top-level `&` and `\\` as cell and row separators. It does not assign matrix or domain-specific meaning to an environment name. Expressions inside recoverable cells use the normal parser and can expose their own review scopes independently.

## Limitations

- Full arbitrary TeX programming and macro expansion are not supported.
- Environment parsing preserves structural row/cell containers; it does not reproduce every package-specific environment behavior.
- Semantic availability depends on installed SMGloM repositories or explicit configuration.
- Opaque preservation prevents loss of source structure but is not semantic understanding.

## Coverage levels

**LEVEL 1 — SOURCE PRESERVED**

The input survives with exact source spans. Opaque preservation at this level
does not imply full structural understanding.

**LEVEL 2 — STRUCTURALLY UNDERSTOOD**

The system knows expression structure, scope, nesting, and arguments. This does
not imply that it knows the mathematical meaning.

**LEVEL 3 — SEMANTICALLY ANNOTATABLE**

A validated semantic candidate and argument mapping are available from SMGloM
or explicitly supplied configuration.

## Quick demo

```powershell
py src/inspect_expression.py "a+b*c"
py src/inspect_expression.py "a+bc"
py src/inspect_expression.py "x_i^2"
py src/inspect_expression.py "\sum_{i=1}^{n} a_i"
py src/inspect_expression.py "\int_0^1 (a+b*x)\,dx"
py src/notation_catalog.py --search "\times"
py src/evaluate_workflow.py --json
py src/project_status.py
```
