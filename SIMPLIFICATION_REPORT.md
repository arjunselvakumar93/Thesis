# Final controlled simplification report

The final pass preserves the working parser, semantic matching, human review, range correction, decision storage, and safe annotation application. The full suite passes 261 tests with zero failures or errors. No commit or push was performed.

## Measured size

All figures are physical Python lines, including ordinary blank lines and comments. Downloaded references, the virtual environment, generated output, and local backup archives are excluded from project implementation counts.

| Point | Source LOC | Source modules | Tests |
|---|---:|---:|---:|
| Before all simplification | 11,274 | 33 | 306 |
| Earlier reported milestone | 7,912 | 17 | 261 |
| Verified start of this final pass | 7,748 | 17 | 261 |
| Final | **7,628** | **17** | **261 passing** |

The actual starting tree already contained changes made after the 7,912-line milestone. Its measured baseline is shown separately instead of attributing those changes to this final pass.

- Source reduction since the original tree: **3,646 lines, 32.34%**.
- Source reduction in this final pass: **120 lines**.
- Source reduction from the earlier 7,912-line milestone: **284 lines**.
- Source modules removed across simplification: **16**. No source module was deleted in this final pass.
- Eight runtime core files: **5,116 lines**.
- Other source modules: **2,512 lines**, primarily evaluation and reproducibility.
- Entire project Python: **12,513 lines across 48 files**: source 7,628, tests/helpers 4,804, migration tool 81.
- Original source and test Python: 16,897 lines; final total is 4,384 lines smaller, including the retained migration tool.

The 6,500–7,000-line source target was not forced. Further reductions would involve working grammar, real SMGloM pattern handling, evaluation calculations, or safety logic. This pass stops at the smallest size established by confirmed dead-code and duplication removal.

## Stabilization

The interrupted work had removed an early behavioral parser suite, producing a 249-test run. This pass restored that file from the verified pre-decision backup and removed its copied duplicate test from the scanner suite. The stabilized baseline returned to 261 passing tests before further source cleanup.

Each source-change group passed focused tests and the full suite. The final full command was:

```powershell
py -3.10 -m unittest discover -s tests -v
```

The original working-tree changes were preserved. Backup ZIPs remain local and ignored by Git.

## Deleted during this pass

Temporary Python programs, all removed:

- `simplify_worktree.py`
- `simplify_models.py`
- `simplify_ranges.py`
- `simplify_legacy.py`
- `simplify_modules.py`
- `simplify_decisions.py`
- `finish_legacy_removal.py`
- `finish_simplification.py`
- `freeze_evaluation_decisions.py`
- `manual_simplification_checks.py`
- `run_simplification_checks.py`

Obsolete temporary logs, also removed:

- `stage1-tests.log`
- `stage1-fix.log`
- `evaluation/simplification_decisions-fixed.log`
- `evaluation/simplification_decisions.log`
- `evaluation/simplification_final-core.log`
- `evaluation/simplification_legacy-fixed.log`
- `evaluation/simplification_legacy-focused.log`
- `evaluation/simplification_legacy.log`
- `evaluation/simplification_merged.log`

No runtime module, active configuration, mathematical adapter, or behavioral test was deleted during this final pass. `tools/migrate_decisions.py` remains as the small, optional historical conversion tool; normal runtime does not import it.

## Source simplifications

Confirmed dead definitions removed after repository-wide reference searches:

| Definition | Previous owner | Reason |
|---|---|---|
| `match_expression_scopes` | `expression_parser.py` | Unused compatibility entry point; runtime already matches ASTs directly. |
| `_node_arguments` | `evaluate_workflow.py` | Unused live-node conversion; evaluation consumes serialized-node arguments. |
| `get_record_name` | `notation_catalog.py` | Unused historical macro/target accessor. |

Single-use wrappers inlined:

- `extract_argument_after_command`: its two calls were the same presentation-wrapper operation. `extract_visible_operator` now performs one direct loop, preserving the preference for `\maincomp` over `\comp`.
- `_node_at`: the only caller now performs its exact AST-range lookup directly.

Other confirmed duplication removed:

- Unused imports of `ParseError`, `sys`, `ExpressionParser`, `classify_notation`, and `canonicalize_tex_notation` in their former consumers.
- A repeated SMGloM scanner import after consolidation.
- Unused catalogue directory constants.
- Repeated application path constants and the second name for the same decision file.
- A self-import inside application `main`.
- The `raw_source` alias repeated alongside the same `source_expression` in document matches and corrected ranges.

Source hashes, source-slice checks, and semantic validation remain separate where they protect different stages. Live AST and serialized evaluation records are also retained where their consumers differ. The multi-caller validated-decision copy helper remains because it clearly supplies independent decision dictionaries.

No ranking weights, ranking order, parser grammar, argument signatures, or semantic mappings were changed in this final pass. Candidate dictionaries retain fields consumed by review, ranking, validation, or evaluation.

## What earlier simplification already removed or merged

The final pass did not repeat these changes:

- Removed the old symbol scanner, operand reader, relation reader, text matcher, notation scanner, lookup CLI, and `--legacy` review path.
- Removed `core_models.py`, `SourceSpan`, status enums, typed provenance/candidate wrappers, `ReviewTarget`, `ScopeAlternative`, and the range relationship enum.
- Consolidated registry, tokenizer, and aliases into `expression_parser.py`.
- Consolidated classification, template handling, and expression metadata into `notation_catalog.py`.
- Consolidated ranking and eligibility into `expression_annotation.py`.
- Consolidated decision validation into `apply_annotations.py`.
- Moved math extraction and comment/location utilities into `expression_document.py`.

The runtime has one authoritative parser and one AST-based semantic matching path. Generic presentation templates are matched only at parsed scopes; they do not create a parallel paper scanner.

## Mathematical and review verification

All 14 inspection checks passed through the actual inspection CLI:

| Expression | Result |
|---|---|
| `a+b*c` | Pass |
| `p(x+y) * p(x-y)` | Pass |
| `x^{n+1}` | Pass |
| `x_i` | Pass |
| `a\cdot b\cdot c` | Pass |
| `P(A)` | Pass |
| `P(A_1)+\dots+P(A_k)` | Pass |
| `f: A \to B` | Pass |
| `\int_a^b f(x)\text{d}x` | Pass |
| `\omega_i\in\Omega` | Pass |
| `P(ωᵢ)` | Pass |
| `A∪B` | Pass |
| `∑_{ωᵢ∈A} P(ωᵢ)` | Pass |
| `\foo{x}+y` | Pass, preserving the unknown command structurally |

An initial test harness passed Unicode through PowerShell's ASCII pipe and changed those characters to question marks. Repeating the commands with correctly constructed Unicode arguments passed. No production parser change was made for that transport issue.

Actual review CLI previews on `tests/fixtures/review_scope_cases.tex` passed for `+`, `-`, and `*`. Searching `+` highlights `p([x+y]) * p(x - y)`, displays addition arguments `x` and `y`, and offers the real SMGloM arithmetic candidates. Raw `*` is available structurally without a fabricated production semantic mapping.

The manual range check offered `p(x+y)` and `p(x+y) * p(x - y)` as complete alternatives. Selecting the outer expression produced:

```text
[p(x+y) * p(x - y)]
```

The obsolete addition candidates were removed after recomputation, and the corrected scope became `STRUCTURAL_ONLY`. This check verified original source slicing and confirmed that the decision file's hash did not change. Behavioral tests additionally verify corrected ranges that have compatible semantic candidates and can be applied.

Exact command output is recorded in `evaluation/simplification_manual_checks.json`.

## Recalculated corpus results

On the evaluated three-document corpus:

| Metric | Result |
|---|---:|
| Structural parsing | 141/141 |
| Gold scope accuracy | 58/58 |
| Supported candidate recall | 44/44 |
| Top-1, Top-3, Top-5 | 44/44 each |
| MRR | 1.0 |
| Annotation-eligible regions | 107 |
| Supported annotation-eligible regions | 99 |
| Review-ready regions | 99 |
| Automatically ready | 88 |
| Human semantic selection required | 11 |
| Unsupported eligible regions | 8 |

Recalculation wrote only to `evaluation/simplification_verification/`. Gold labels, frozen papers, frozen review evidence, and historical result files were not rewritten in this pass. Final evidence hashes are recorded in `evaluation/simplification_final_summary.json`.

Every existing adapter family remains evidence-backed by local SMGloM records. The catalogue includes core grammar (88 records), multi-part templates (74), decorated symbols (36), scripted templates (34), binders (33), mixfix (8), semantic constants (5), delimiters (4), and sequence patterns (2). No adapter was removed to meet a line-count target.

## Application qualification

A fresh replay against the frozen paper validates **55 saved decisions**. All **55/55** are applied in an isolated temporary output copy, with zero same-range conflicts, zero partial overlaps, zero failures, and unchanged original source. Output sanity checks pass. Nested replacement and idempotence behavior remain covered by regression tests.

The archived evaluation mentions **59** decisions. That number is not claimed as a newly reproduced application result. The current frozen decision snapshot excludes 91 historical entries as stale or unresolved. Its unresolved entries include three multiline expression/location mismatches and three records for a paper absent from the frozen-paper directory. Reconciliation of the archived 59-count is left visible for review; this controlled pass does not rewrite frozen evidence or weaken validation to force that number.

The separate application record is `evaluation/simplification_application_check.json`. The corpus evaluator's printed application number comes from the existing archived application report, rather than executing application itself.

## Remaining source modules, largest first

| File | Physical LOC | Why it remains |
|---|---:|---|
| `notation_catalog.py` | 1,224 | Real declaration classification, evidence-backed template shapes, configured grammar compilation, and catalogue lookup. These are distinct necessary algorithms within the semantic-data owner. |
| `evaluate_corpus.py` | 1,130 | Three-document aggregation, semantic denominators, confidence intervals, readiness proofs, and detailed error analysis. This is research reporting, outside the runtime core. |
| `expression_parser.py` | 962 | Grammar validation, tokenization, precedence, scripts, groups, functions, bound constructs, environments, and safe unknown handling in one parser. |
| `review_workflow.py` | 649 | CLI options, catalogue search, review interaction, range rematching, and decision persistence. It is the complete human-facing algorithm entry point. |
| `smglom_notation_extractor.py` | 604 | Balanced TeX declaration reading, signatures/options, module context, and repository scans. It parses SMGloM declarations rather than mathematical expressions. |
| `evaluate_workflow.py` | 520 | Per-document AST/candidate records, gold scope/ranking metrics, decision validation, and optional application/sanity reporting. Shared by the corpus evaluator. |
| `apply_annotations.py` | 512 | Semantic/source validation, hash verification, nested replacement planning, overlap checks, and output copying. Safety is retained. |
| `review_scope.py` | 413 | One review record, source highlighting, complete range alternatives, and rendering. |
| `expression_document.py` | 394 | Math extraction, comments, original locations, alignment rows, and document-level AST occurrences. |
| `expression_annotation.py` | 358 | Eligibility, deduplication, ranking evidence, and AST matching. |
| `smglom_dependencies.py` | 233 | Repository dependency checks and reproducible snapshots. |
| `project_status.py` | 166 | Status command backed by evaluation and repository checks. |
| `evaluate_generalization.py` | 157 | Tested structural generalization evaluation. |
| `parser_diagnostics.py` | 138 | Parser coverage and unsupported-input explanations. |
| `evaluate_unicode_stress.py` | 95 | Tested Unicode/stress evaluation. |
| `inspect_expression.py` | 66 | Essential parser inspection command. |
| `validate_decisions.py` | 7 | Documented validation CLI entry point. |

All nine non-core modules have an evaluation/reproducibility or CLI/debug purpose; their individual classifications are in `ARCHITECTURE.md`. None was confirmed dead or redundant as a whole.

## Code-reading result

Yes: the complete runtime algorithm can be followed through the eight core files, in the reading order in `ARCHITECTURE.md`. The guide gives each file's purpose, functions, size, and the workflow's inputs/outputs. Understanding the evaluation methodology additionally requires its reporting modules.

The main workflow in plain English is: find possible meanings, read the paper's math, parse it, find complete matching scopes, show the proposal, let the reviewer correct the range, save the chosen meaning, and apply verified choices to copied source.

No further automatic refactoring follows this report. No commit or push was made.
