"""Validate FBX slots after UE merges identical source material identities."""
from collections import Counter


def material_bindings(source_slots, imported_slots):
    """Return actual slot identities; tolerate only exact-ID deduplication/reordering.

    Stable WT IDs cover shader, parameters and every source texture slot. Distinct
    identities must all remain present, and imported multiplicity cannot grow.
    """
    expected = Counter(source_slots)
    actual = Counter(imported_slots)
    if not expected or set(actual) != set(expected):
        raise ValueError(f'material identities differ: actual={list(imported_slots)}, source={list(source_slots)}')
    if any(count > expected[name] for name, count in actual.items()):
        raise ValueError('imported material identity multiplicity exceeds the source')
    return list(imported_slots)
