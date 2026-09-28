# Professor demo cheat sheet

Run these commands from the repository root in Windows PowerShell.

1. `py -3.10 src\inspect_expression.py "\sum_{\omega_i\in A} P(\omega_i)"`  
   Demonstrates the complete LaTeX summation, membership bound, scripts, and function body.

2. `py -3.10 src\inspect_expression.py "∑_{ωᵢ∈A} P(ωᵢ)"`  
   Demonstrates structurally equivalent Unicode grammar with its original Unicode source preserved.

3. `py -3.10 src\review_workflow.py --paper tests\fixtures\professor_demo.tex --preview`  
   Search for `ωᵢ∈A` to demonstrate exact structural review without fabricated semantics.

4. `py -3.10 src\review_workflow.py --paper tests\fixtures\professor_demo.tex --preview`  
   Search for `+` to demonstrate the normal SMGloM-backed semantic candidate path.

5. `py -3.10 src\review_workflow.py --paper tests\fixtures\professor_demo.tex --preview`  
   Search for `*` to demonstrate complete multiplication structure without a production semantic mapping.

6. `py -3.10 src\review_workflow.py --paper tests\fixtures\professor_demo.tex --expression-config tests\fixtures\demo-star-config.json --preview`  
   Search for `*` to demonstrate explicitly synthetic, test-only `\fooTimes` metadata.

7. `py -3.10 -m unittest discover -s tests -v`  
   Demonstrates the complete regression suite.

8. `py -3.10 src\project_status.py --professor-demo`  
   Displays the current parser, review, semantic, application, corpus, and stress-suite status.
