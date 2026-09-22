# Limitations

- TeX is programmable. The project parses a deliberately bounded mathematical subset, not full TeX expansion.
- Visible notation can represent several semantic concepts. Candidate ambiguity remains and ranking is not yet implemented.
- Semantic candidate precision is lower than structural parsing accuracy. All candidates remain subject to human review.
- Some SMGloM argument signatures contain binding or hidden arguments whose source mapping cannot be inferred safely.
- The real SMGloM calculus repository is absent locally. Integral structure works, while `\fooIntegral` is an explicitly synthetic configured fallback.
- Several repositories referenced by the installed `arithmetics` and `mv` sources are absent locally.
- Output compilation is not performed when no supported TeX compiler is available.
- The current quantitative real-paper evaluation covers one small paper corpus and should not be generalized to all mathematical TeX.
- The extractor handles declaration syntax rather than arbitrary macro expansion. Unsupported declarations are reported by repository health checks.
- Source application depends on stored source spans remaining current; stale decisions are rejected rather than guessed.
