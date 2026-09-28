"""TeX lexical syntax only; mathematical meanings come from the registry."""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


def unicode_script(character):
    """Return (TeX-style role, plain value) for Unicode sub/superscripts."""
    decomposition = unicodedata.decomposition(character)
    if not decomposition:
        return None
    parts = decomposition.split()
    if not parts or parts[0] not in {"<sub>", "<super>"}:
        return None
    try:
        value = "".join(chr(int(code, 16)) for code in parts[1:])
    except ValueError:
        return None
    return ("_" if parts[0] == "<sub>" else "^", value)


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
        elif unicode_script(character):
            position += 1
            kind = "unicode_script"
        elif character.isdecimal():
            found = re.match(r"\d+(?:\.\d+)?", source[position:])
            position += len(found.group())
            kind = "number"
        elif character.isalpha() or unicodedata.category(character).startswith("L"):
            position += 1
            kind = "atom"
        else:
            position += 1
            kind = ("open" if character in registry.open_delimiters else
                    "close" if character in registry.close_delimiters else
                    {"^": "script", "_": "script", ",": "comma"}.get(character))
            if kind is None:
                category = unicodedata.category(character)
                kind = ("combining" if category.startswith("M") else
                        "math_symbol" if category in {"Sm", "So"} else "unknown")
        tokens.append(Token(kind, source[start:position], start, position))
    tokens.append(Token("end", "", len(source), len(source)))
    return tokens
