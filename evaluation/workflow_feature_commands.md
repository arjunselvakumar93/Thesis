# Workflow feature commands

Run these commands from the repository root in Windows PowerShell.

1. `py -3.10 src\inspect_expression.py "\sum_{\omega_i\in A} P(\omega_i)"`  
   Demonstrates the complete LaTeX summation, membership bound, scripts, and function body.

2. `py -3.10 src\inspect_expression.py "âˆ‘_{Ï‰áµ¢âˆˆA} P(Ï‰áµ¢)"`  
   Demonstrates structurally equivalent Unicode grammar with its original Unicode source preserved.

3. `py -3.10 src\review_workflow.py --paper tests\fixtures\expression_review_cases.tex --preview`  
   Search for `Ï‰áµ¢âˆˆA` to demonstrate exact structural review without fabricated semantics.

4. `py -3.10 src\review_workflow.py --paper tests\fixtures\expression_review_cases.tex --preview`  
   Search for `+` to demonstrate the normal SMGloM-backed semantic candidate path.

5. `py -3.10 src\review_workflow.py --paper tests\fixtures\expression_review_cases.tex --preview`  
   Search for `*` to demonstrate complete multiplication structure without a production semantic mapping.

6. `py -3.10 src\review_workflow.py --paper tests\fixtures\expression_review_cases.tex --expression-config tests\fixtures\configs/test_raw_star_semantics.json --preview`  
   Search for `*` to demonstrate explicitly synthetic, test-only `\fooTimes` metadata.

7. `py -3.10 -m unittest discover -s tests -v`  
   Demonstrates the complete regression suite.

8. `py -3.10 src\project_status.py --feature-summary`  
   Displays the current parser, review, semantic, application, corpus, and stress-suite status.
