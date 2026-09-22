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
