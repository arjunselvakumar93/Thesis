# Final thesis evaluation tables

All results in these tables refer to the evaluated three-document corpus.

## A. Structural evaluation

| Measure | Result |
|---|---:|
| Parsed math regions | 141/141 (100%) |
| Correctly located labeled scopes | 58/58 (100%) |

| Document | Parsed regions | Correct labeled scopes |
|---|---:|---:|
| quadratic-paper | 70/70 (100%) | 20/20 (100%) |
| general-expressions | 63/63 (100%) | 30/30 (100%) |
| configured-integrals | 8/8 (100%) | 8/8 (100%) |

## B. Semantic candidate evaluation

| Measure | Result |
|---|---:|
| Supported candidate recall | 44/44 (100%) |
| Top-1 recall | 44/44 (100%) |
| Top-3 recall | 44/44 (100%) |
| Top-5 recall | 44/44 (100%) |
| Mean reciprocal rank | 1.0 |

## C. Annotation readiness

| Measure | Result |
|---|---:|
| Annotation-eligible regions | 107/141 (75.9%) |
| Supported eligible regions | 99/107 (92.5%) |
| Review-ready among supported regions | 99/99 (100%) |
| Automatically-ready among supported regions | 88/99 (88.9%) |
| Human selection required among supported regions | 11/99 (11.1%) |
| Unsupported among eligible regions | 8/107 (7.5%) |

The 88 automatically-ready regions are semantically determined without a human choice. All 99 supported regions are review-ready. The additional 11 regions require a human semantic choice, after which they can complete decision construction, validation, and safe application planning.

### Human-in-the-loop waterfall

~~~text
141 total math regions
  v
107 annotation-eligible
  v
99 supported annotation-eligible
  v
99 review-ready
  +-- 88 automatically-ready
  \-- 11 require human semantic selection

8 unsupported eligible
  +-- 7 missing set semantics/repository
  \-- 1 integral signature limitation
~~~

## D. Application safety

| Measure | Result |
|---|---:|
| Current decisions rendered | 59/59 (100%) |
| Same-span conflicts | 0 |
| Partial-overlap conflicts | 0 |
| Failures | 0 |
| Original sources unchanged | True |
| Idempotence regression | Passed |

## E. Per-document results

| Document | Regions | Eligible | Supported | Review-ready | Automatic | Human selection | Unsupported eligible |
|---|---:|---:|---:|---:|---:|---:|---:|
| quadratic-paper | 70 | 47 | 47 | 47 | 37 | 10 | 0 |
| general-expressions | 63 | 52 | 45 | 45 | 44 | 1 | 7 |
| configured-integrals | 8 | 8 | 7 | 7 | 7 | 0 | 1 |

## F. Remaining unsupported cases

| Limitation | Cases | Share of unsupported eligible cases |
|---|---:|---:|
| Missing set semantics/repository | 7 | 7/8 (87.5%) |
| Indefinite integral lacks bounds required by the configured signature | 1 | 1/8 (12.5%) |

These are semantic-resource or signature limitations, not parser failures.

## Reproducibility

~~~powershell
py -3.10 -m unittest discover -s tests -v
py -3.10 src\evaluate_corpus.py --corpus evaluation\corpus.json
py -3.10 src\project_status.py
py -3.10 src\smglom_dependencies.py --check
~~~
