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


def _configured_groups(configured_aliases=None):
    """Return validated, semantics-free alias groups from project configuration."""
    if not configured_aliases:
        return ()
    if not isinstance(configured_aliases, dict):
        raise ValueError("notation_aliases must map TeX spellings to TeX spellings")
    groups = []
    for alias, target in configured_aliases.items():
        if not all(isinstance(item, str) and item and not any(c.isspace() for c in item)
                   for item in (alias, target)):
            raise ValueError("notation aliases require nonempty TeX tokens")
        groups.append((target, alias))
    return tuple(groups)


def _alias_maps(configured_aliases=None):
    groups = (*TEX_NOTATION_ALIAS_GROUPS, *_configured_groups(configured_aliases))
    graph = {}
    for group in groups:
        for item in group:
            graph.setdefault(item, set()).update(group)
    canonical, equivalents = {}, {}
    unseen = set(graph)
    while unseen:
        seed = unseen.pop()
        component, pending = {seed}, [seed]
        while pending:
            item = pending.pop()
            for neighbour in graph.get(item, ()):
                if neighbour not in component:
                    component.add(neighbour); unseen.discard(neighbour); pending.append(neighbour)
        preferred = next((group[0] for group in groups if group[0] in component), min(component))
        ordered = (preferred, *sorted(component - {preferred}))
        equivalents[preferred] = ordered
        canonical.update({item: preferred for item in component})
    return canonical, equivalents


def canonicalize_tex_notation(token, configured_aliases=None):
    """Return the canonical TeX spelling for a visible notation token."""
    canonical, _ = _alias_maps(configured_aliases)
    return canonical.get(token, token)


def get_equivalent_tex_notations(token, configured_aliases=None):
    """Return equivalent TeX spellings, canonical spelling first."""
    canonical_map, equivalents = _alias_maps(configured_aliases)
    canonical = canonical_map.get(token, token)
    return list(equivalents.get(canonical, (token,)))
