"""TeX lexical syntax only; mathematical meanings come from the registry."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


def tokenize(source, registry):
    tokens = []
    position = 0
    while position < len(source):
        start = position
        character = source[position]
        if character.isspace():
            position += 1
            continue
        if character == "%":
            end = source.find("\n", position)
            position = len(source) if end == -1 else end
            continue
        spelling = next((item for item in registry.spellings
                         if source.startswith(item, position)
                         and not (item[-1].isalpha() and position + len(item) < len(source)
                                  and source[position + len(item)].isalpha())), None)
        if spelling:
            position += len(spelling)
            kind = "operator"
        elif character == "\\":
            found = re.match(r"\\(?:[A-Za-z]+|[^A-Za-z])", source[position:])
            position += len(found.group()) if found else 1
            kind = "command" if found else "unknown"
            if found and found.group() in {r"\left", r"\right"}:
                kind = "sized_open" if found.group() == r"\left" else "sized_close"
            elif found and found.group() in {r"\{", r"\}"}:
                kind = "open" if found.group() == r"\{" else "close"
        elif character.isdecimal():
            found = re.match(r"\d+(?:\.\d+)?", source[position:])
            position += len(found.group())
            kind = "number"
        elif character.isalpha():
            position += 1
            kind = "atom"
        else:
            position += 1
            kind = {"(": "open", "[": "open", "{": "open",
                    ")": "close", "]": "close", "}": "close",
                    "^": "script", "_": "script", ",": "comma"}.get(character, "unknown")
        tokens.append(Token(kind, source[start:position], start, position))
    tokens.append(Token("end", "", len(source), len(source)))
    return tokens
