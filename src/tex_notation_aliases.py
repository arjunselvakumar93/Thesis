"""TeX-syntax equivalences independent of SMGloM semantic meanings."""


# The first entry is the canonical spelling used for internal comparison.
# Entries contain TeX tokens only; semantic macro names do not belong here.
TEX_NOTATION_ALIAS_GROUPS = (
    (r"\ne", r"\neq"),
)


_CANONICAL_BY_NOTATION = {
    notation: group[0]
    for group in TEX_NOTATION_ALIAS_GROUPS
    for notation in group
}

_EQUIVALENTS_BY_CANONICAL = {
    group[0]: group
    for group in TEX_NOTATION_ALIAS_GROUPS
}


def canonicalize_tex_notation(token):
    """Return the canonical TeX spelling for a visible notation token."""
    return _CANONICAL_BY_NOTATION.get(token, token)


def get_equivalent_tex_notations(token):
    """Return equivalent TeX spellings, canonical spelling first."""
    canonical = canonicalize_tex_notation(token)
    return list(_EQUIVALENTS_BY_CANONICAL.get(canonical, (token,)))
