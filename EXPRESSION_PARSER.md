# Expression parser

The authoritative parser is `src/expression_parser.py`. It contains grammar metadata, tokenization, and one generic `Expression` model.

Read `ARCHITECTURE.md` for the eight-file reading order and complete workflow. Use `py -3.10 src\inspect_expression.py "a+b*c"` to inspect source ranges and structure without making a semantic annotation.

The parser handles configured precedence, grouping, scripts, functions, bound constructs, environments, Unicode, and unknown-command preservation. Semantic candidates are matched separately in `src/expression_annotation.py` using the SMGloM catalogue. See `LIMITATIONS.md` for the supported subset and remaining limitations.
