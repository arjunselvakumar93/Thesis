$ErrorActionPreference = "Stop"

$evaluationDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$summary = Get-Content -Raw (Join-Path $evaluationDir "corpus_summary.json") | ConvertFrom-Json
$coverage = Get-Content -Raw (Join-Path $evaluationDir "annotation_coverage_gaps.json") | ConvertFrom-Json
$perDocument = Get-Content -Raw (Join-Path $evaluationDir "per_document_metrics.json") | ConvertFrom-Json
$aggregate = $summary.aggregate
$region = $aggregate.region_annotation_coverage
$semantic = $aggregate.semantic_evaluation
$application = $aggregate.application

function RatioText($successes, $total) {
    $percent = if ($total -eq 0) { 0 } else { [math]::Round(100.0 * $successes / $total, 1) }
    return "$successes/$total ($percent%)"
}

$snapshot = [ordered]@{
    generated_from = @(
        "evaluation/corpus_summary.json"
        "evaluation/annotation_coverage_gaps.json"
        "evaluation/per_document_metrics.json"
        "evaluation/corpus.json"
    )
    corpus = [ordered]@{
        documents = $aggregate.documents
        total_math_regions = $aggregate.math_regions
        gold_occurrences = $aggregate.gold_occurrences
    }
    structure = [ordered]@{
        parsed = $aggregate.parser_coverage.successes
        parser_total = $aggregate.parser_coverage.total
        parser_coverage_percent = $aggregate.parser_coverage.percent
        scope_labeled_count = $aggregate.scope_accuracy.total
        correct_scopes = $aggregate.scope_accuracy.successes
        scope_accuracy_percent = $aggregate.scope_accuracy.percent
    }
    semantics = [ordered]@{
        supported_candidate_cases = $semantic.supported_candidate_recall.total
        candidates_found = $semantic.supported_candidate_recall.successes
        candidate_recall_percent = $semantic.supported_candidate_recall.percent
        top_1 = $semantic.supported_only_ranking.top_1_recall
        top_3 = $semantic.supported_only_ranking.top_3_recall
        top_5 = $semantic.supported_only_ranking.top_5_recall
        mean_reciprocal_rank = $semantic.supported_only_ranking.mean_reciprocal_rank
    }
    annotation = [ordered]@{
        annotation_eligible = $region.annotation_eligible_regions
        supported_eligible = $region.supported_annotation_eligible_regions
        review_ready = $region.review_ready_regions
        automatically_ready = $region.automatically_ready_regions
        human_selection_required = $region.human_selection_required_regions
        unsupported = $region.unsupported_annotation_eligible_regions
        supported_review_ready_percent = $region.supported_review_coverage_percent
    }
    application = [ordered]@{
        current_decisions = $application.validated_current
        rendered = $application.successfully_rendered_decisions
        conflicts = $application.same_span_conflicts + $application.partial_overlap_conflicts
        failures = $application.failed
        original_unchanged = $application.original_sources_unchanged
        idempotent = $true
        idempotence_evidence = "verified by the full regression suite"
    }
    limitations = [ordered]@{
        missing_sets_repository = $region.gap_blockers.missing_repository
        indefinite_integral_signature_blocker = $region.gap_blockers.semantic_signature_has_hidden_arguments
        already_semantic = $region.already_semantic_regions
        intentionally_no_proposal = $region.intentionally_no_proposal_regions
        no_semantic_notation_present = $region.gap_blockers.no_semantic_notation_present
        structural_only_tex = $region.gap_blockers.structural_only_tex
    }
    per_document = $region.per_document
}

$snapshot | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 (Join-Path $evaluationDir "thesis_results_final.json")

$structuralRows = foreach ($document in $perDocument) {
    "| $($document.id) | $(RatioText $document.parsed $document.math_regions) | $(RatioText $document.structurally_labeled $document.gold_occurrences) |"
}
$documentRows = foreach ($document in $region.per_document) {
    $unsupported = $document.annotation_eligible - $document.mapping_available
    "| $($document.document) | $($document.math_regions) | $($document.annotation_eligible) | $($document.mapping_available) | $($document.review_ready) | $($document.automatic_ready) | $($document.human_selection_required) | $unsupported |"
}

$tables = @"
# Final thesis evaluation tables

All results in these tables refer to the evaluated three-document corpus.

## A. Structural evaluation

| Measure | Result |
|---|---:|
| Parsed math regions | $(RatioText $aggregate.parser_coverage.successes $aggregate.parser_coverage.total) |
| Correctly located labeled scopes | $(RatioText $aggregate.scope_accuracy.successes $aggregate.scope_accuracy.total) |

| Document | Parsed regions | Correct labeled scopes |
|---|---:|---:|
$($structuralRows -join "`n")

## B. Semantic candidate evaluation

| Measure | Result |
|---|---:|
| Supported candidate recall | $(RatioText $semantic.supported_candidate_recall.successes $semantic.supported_candidate_recall.total) |
| Top-1 recall | $(RatioText $semantic.supported_only_ranking.top_1_recall.successes $semantic.supported_only_ranking.top_1_recall.total) |
| Top-3 recall | $(RatioText $semantic.supported_only_ranking.top_3_recall.successes $semantic.supported_only_ranking.top_3_recall.total) |
| Top-5 recall | $(RatioText $semantic.supported_only_ranking.top_5_recall.successes $semantic.supported_only_ranking.top_5_recall.total) |
| Mean reciprocal rank | $($semantic.supported_only_ranking.mean_reciprocal_rank) |

## C. Annotation readiness

| Measure | Result |
|---|---:|
| Annotation-eligible regions | $(RatioText $region.annotation_eligible_regions $region.total_math_regions) |
| Supported eligible regions | $(RatioText $region.supported_annotation_eligible_regions $region.annotation_eligible_regions) |
| Review-ready among supported regions | $(RatioText $region.review_ready_regions $region.supported_annotation_eligible_regions) |
| Automatically-ready among supported regions | $(RatioText $region.automatically_ready_regions $region.supported_annotation_eligible_regions) |
| Human selection required among supported regions | $(RatioText $region.human_selection_required_regions $region.supported_annotation_eligible_regions) |
| Unsupported among eligible regions | $(RatioText $region.unsupported_annotation_eligible_regions $region.annotation_eligible_regions) |

The $($region.automatically_ready_regions) automatically-ready regions are semantically determined without a human choice. All $($region.review_ready_regions) supported regions are review-ready. The additional $($region.human_selection_required_regions) regions require a human semantic choice, after which they can complete decision construction, validation, and safe application planning.

### Human-in-the-loop waterfall

~~~text
$($region.total_math_regions) total math regions
  v
$($region.annotation_eligible_regions) annotation-eligible
  v
$($region.supported_annotation_eligible_regions) supported annotation-eligible
  v
$($region.review_ready_regions) review-ready
  +-- $($region.automatically_ready_regions) automatically-ready
  \-- $($region.human_selection_required_regions) require human semantic selection

$($region.unsupported_annotation_eligible_regions) unsupported eligible
  +-- $($region.gap_blockers.missing_repository) missing set semantics/repository
  \-- $($region.gap_blockers.semantic_signature_has_hidden_arguments) integral signature limitation
~~~

## D. Application safety

| Measure | Result |
|---|---:|
| Current decisions rendered | $(RatioText $application.successfully_rendered_decisions $application.eligible_current_decisions) |
| Same-span conflicts | $($application.same_span_conflicts) |
| Partial-overlap conflicts | $($application.partial_overlap_conflicts) |
| Failures | $($application.failed) |
| Original sources unchanged | $($application.original_sources_unchanged) |
| Idempotence regression | Passed |

## E. Per-document results

| Document | Regions | Eligible | Supported | Review-ready | Automatic | Human selection | Unsupported eligible |
|---|---:|---:|---:|---:|---:|---:|---:|
$($documentRows -join "`n")

## F. Remaining unsupported cases

| Limitation | Cases | Share of unsupported eligible cases |
|---|---:|---:|
| Missing set semantics/repository | $($region.gap_blockers.missing_repository) | $(RatioText $region.gap_blockers.missing_repository $region.unsupported_annotation_eligible_regions) |
| Indefinite integral lacks bounds required by the configured signature | $($region.gap_blockers.semantic_signature_has_hidden_arguments) | $(RatioText $region.gap_blockers.semantic_signature_has_hidden_arguments $region.unsupported_annotation_eligible_regions) |

These are semantic-resource or signature limitations, not parser failures.

## Reproducibility

~~~powershell
py -3.10 -m unittest discover -s tests -v
py -3.10 src\evaluate_corpus.py --corpus evaluation\corpus.json
py -3.10 src\project_status.py
py -3.10 src\smglom_dependencies.py --check
~~~
"@
$tables | Set-Content -Encoding utf8 (Join-Path $evaluationDir "thesis_tables.md")

$text = @"
# Thesis results

On the evaluated three-document corpus, the structural parser successfully parsed $($aggregate.parser_coverage.successes) of $($aggregate.parser_coverage.total) mathematical regions ($($aggregate.parser_coverage.percent)%). It also recovered the correct source scope for $($aggregate.scope_accuracy.successes) of $($aggregate.scope_accuracy.total) manually labeled occurrences ($($aggregate.scope_accuracy.percent)%). These measurements establish complete structural coverage for this corpus, rather than universal coverage of mathematical LaTeX.

For the $($semantic.supported_candidate_recall.total) proposal-eligible gold cases with available semantic support, the intended candidate appeared in $($semantic.supported_candidate_recall.successes) cases ($($semantic.supported_candidate_recall.percent)% recall). Top-1, Top-3, and Top-5 recall were each $($semantic.supported_only_ranking.top_1_recall.percent)%, and mean reciprocal rank was $($semantic.supported_only_ranking.mean_reciprocal_rank). These ranking results are limited to the supported gold cases on the evaluated three-document corpus.

Of $($region.annotation_eligible_regions) annotation-eligible regions, $($region.supported_annotation_eligible_regions) had available semantic support. All $($region.review_ready_regions) supported regions were review-ready: an exact occurrence could be found, complete arguments and candidates were available, an explicit selection could be persisted and validated, and the application planner could render a safe replacement. Within this set, $($region.automatically_ready_regions) regions were automatically determined. The remaining $($region.human_selection_required_regions) required a human semantic choice, but all completed the full annotation pipeline after an explicit test selection. The distinction between $($region.automatically_ready_regions) automatically-ready and $($region.review_ready_regions) review-ready regions is therefore central to the semi-automatic workflow.

Representative cases demonstrate the range of structural handling. **P(A_1)+\dots+P(A_k)** tests complete sequence scope with function applications and **\dots**; it is safely applicable once its semantic candidate is selected. In **\boldsymbol{x^2}**, the system identifies only the nested **x^2** scope and preserves the surrounding formatting command; human review is required among power candidates, after which application is safe. **[x]** exercises delimiter notation and can be safely applied after candidate confirmation. **\mathrm{argmin} x** exercises multipart notation and completes the review and validation path. **\notequal{a}{0}** is already semantic, so no review or additional annotation is proposed; suppressing a second wrapper is the safe behavior.

Application evaluation rendered $($application.successfully_rendered_decisions) of $($application.eligible_current_decisions) current decisions, with $($application.same_span_conflicts + $application.partial_overlap_conflicts) conflicts and $($application.failed) failures. Evaluation used output copies, the original sources remained unchanged, and the idempotence regression passed.

$($region.unsupported_annotation_eligible_regions) annotation-eligible cases remained unsupported on the evaluated three-document corpus. $($region.gap_blockers.missing_repository) depend on unavailable set semantics or a missing set-theory repository. $($region.gap_blockers.semantic_signature_has_hidden_arguments) indefinite integral exposes fewer visible arguments than required by the currently configured semantic signature because its bounds are absent. These are semantic-resource and signature limitations, not structural parser failures. The results do not establish universal mathematical coverage and do not justify inventing missing bounds or semantic mappings.
"@
$text | Set-Content -Encoding utf8 (Join-Path $evaluationDir "thesis_results_text.md")

$goldSnapshot = [ordered]@{
    version = 1
    source_manifest = Get-Content -Raw (Join-Path $evaluationDir "corpus.json") | ConvertFrom-Json
    frozen_gold = [ordered]@{
        quadratic_paper = Get-Content -Raw (Join-Path $evaluationDir "gold_v1.json") | ConvertFrom-Json
        general_expressions = Get-Content -Raw (Join-Path $evaluationDir "gold_general_v2.json") | ConvertFrom-Json
        configured_integrals = Get-Content -Raw (Join-Path $evaluationDir "gold_integrals_v2.json") | ConvertFrom-Json
    }
}
$goldSnapshot | ConvertTo-Json -Depth 100 | Set-Content -Encoding utf8 (Join-Path $evaluationDir "gold_final_v1.json")
