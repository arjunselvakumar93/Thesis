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
- Unicode and LaTeX input can be source-preserved or structurally understood
  without being semantically annotatable. No semantic equivalence is inferred
  from visual similarity alone.
- Unregistered Unicode mathematical symbols are preserved opaquely when safe;
  opaque preservation does not provide operator fixity, precedence, or meaning.
- OCR and image-to-mathematics conversion are separate preprocessing tasks and
  are not part of this textual parser.
- Automatic precedence follows available registry metadata. Operators marked
  non-associative or uncertain expose complete alternative review ranges rather
  than claiming an unstated grouping.
- Range correction is limited to complete, reparsable source boundaries derived
  from the surrounding expression; arbitrary partial TeX ranges are rejected.
