"""Evaluate the independent Unicode/LaTeX structural stress fixture."""

import argparse
import json
from pathlib import Path

from notation_catalog import build_expression_config
from expression_parser import ExpressionParser, ParseError
from expression_parser import NotationRegistry


def generated_fixture():
    """Build broad cases from grammar families rather than parser branches."""
    atoms = list("xyzABC\u03b1\u03b2\u03b3\u03c9\u03b8\u03a9")
    operators = list("+\u2212\u00d7\u00f7\u222a\u2229\u2227\u2228")
    relations = ["=", "<", ">", "\u2264", "\u2265", "\u2260", "\u2248", "\u2261",
                 "\u2208", "\u2209", "\u2282", "\u2286", "\u2283", "\u2287",
                 "\u2192", "\u21d2"]
    expressions = [left + operator + atoms[(number + 3) % len(atoms)]
                   for number, left in enumerate(atoms) for operator in operators]
    expressions += [left + relation + atoms[(number + 1) % len(atoms)]
                    for number, left in enumerate(atoms) for relation in relations]
    expressions += [
        "\u03c9\u1d62\u2208A", "x\u00b2+y\u00b2", "x\u2081\u2264x\u2082", "P(\u03c9\u1d62)",
        "f(\u03b1+\u03b2)", "\u221ax", "\u221a(x\u00b2+y\u00b2)",
        "\u2211_{i=1}^{n} x_i", "\u2211_{\u03c9\u1d62\u2208A} P(\u03c9\u1d62)",
        "\u222b\u2080\u00b9 f(x) dx", "\u2200x P(x)", "\u2203x Q(x)", "\u03a3", "\u221e",
        "x\u2192\u221e", "\u27e8\u03b1+\u03b2\u27e9", r"\sum_{\omega_i\in A} P(\omega_i)",
        "\u2211_{\\omega_i\u2208A} P(\\omega_i)", r"\frac{α+β}{x²+y²}",
        "P(\u03c9i) \u03c9\u2208A", "\u2295"]
    return expressions


def evaluate(expressions, configuration):
    registry = NotationRegistry.from_config(configuration)
    parser = ExpressionParser(registry=registry)
    results = []
    for source in expressions:
        try:
            tree = parser.parse(source)
            nodes = tuple(tree.walk())
            opaque = any(node.kind in {"OpaqueMathToken", "OpaqueCommandApplication",
                                       "OpaqueEnvironmentBody"} for node in nodes)
            status = "OPAQUE_PRESERVED" if opaque else "FULLY_STRUCTURED"
            semantic = any(bool(getattr(node.metadata, "semantic_macro", "")) or
                           (isinstance(node.metadata, dict) and
                            bool(node.metadata.get("semantic_macro"))) for node in nodes)
            results.append({
                "expression": source,
                "status": status,
                "semantic_mapping_available": semantic,
                "review_scope_available": True,
                "annotation_ready": semantic,
                "exact_source_preserved": tree.text == source,
            })
        except ParseError as error:
            results.append({
                "expression": source,
                "status": "UNSUPPORTED",
                "semantic_mapping_available": False,
                "review_scope_available": False,
                "annotation_ready": False,
                "exact_source_preserved": True,
                "error": str(error),
            })
    return results


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--fixture", type=Path,
                     default=root / "tests/fixtures/unicode_stress.json")
    cli.add_argument("--output", type=Path,
                     default=root / "evaluation/unicode_stress_results.json")
    cli.add_argument("--generate-fixture", action="store_true")
    args = cli.parse_args(argv)
    if args.generate_fixture:
        args.fixture.write_text(json.dumps(generated_fixture(), ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    expressions = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
    raw = json.loads((root / "expression-config.example.json").read_text(encoding="utf-8"))
    results = evaluate(expressions, build_expression_config(raw, []))
    counts = {name: sum(item["status"] == name for item in results)
              for name in ("FULLY_STRUCTURED", "OPAQUE_PRESERVED", "PARTIAL", "UNSUPPORTED")}
    report = {"total": len(results), "counts": counts, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"total": len(results), **counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
