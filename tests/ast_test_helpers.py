"""Small fixture helpers exercising the production AST parser and matcher."""
import json
from functools import lru_cache
from pathlib import Path

from expression_document import parse_papers, find_ast_occurrences, normalize_math_expression
from expression_parser import ExpressionParser, ParseError
from expression_parser import NotationRegistry
from notation_catalog import build_expression_config
from expression_annotation import match_classification


@lru_cache(maxsize=1)
def default_config():
    path = Path(__file__).resolve().parents[1] / 'expression-config.example.json'
    return build_expression_config(json.loads(path.read_text(encoding='utf-8-sig')))


def scan_rule(rule, path):
    config = rule.get('expression_config') or default_config()
    return find_ast_occurrences(parse_papers(path, config), rule, config)


def match_rule(source, rule):
    config = rule.get('expression_config') or default_config()
    registry = NotationRegistry.from_config(config)
    try:
        tree = ExpressionParser(registry=registry).parse(source)
    except ParseError:
        return []
    matches = match_classification(tree, rule, registry)
    for match in matches:
        match['matched_expression'] = normalize_math_expression(match['matched_expression'])
        match['arguments'] = [normalize_math_expression(arg) for arg in match['arguments']]
    return matches
