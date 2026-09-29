"""Repeatable stratified sampling from a synthetic panel."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence

from .models import Persona


def stratified_sample(
    panel: Sequence[Persona], size: int, *, seed: int = 42,
    proportions: dict[str, float] | None = None,
) -> list[Persona]:
    """Sample without replacement by `segment`.

    `proportions` are requested scenario shares. Without them, observed panel
    shares are retained. Largest remainders allocate exact integer counts.
    """
    if size < 1 or size > len(panel):
        raise ValueError("sample size must be between 1 and panel size")
    groups: dict[str, list[Persona]] = defaultdict(list)
    seen: set[str] = set()
    for persona in panel:
        if persona.id in seen:
            raise ValueError("panel persona IDs must be unique")
        seen.add(persona.id)
        groups[persona.segment].append(persona)
    if proportions is None:
        shares = {segment: len(members) / len(panel) for segment, members in groups.items()}
    else:
        if set(proportions) != set(groups) or any(value < 0 for value in proportions.values()):
            raise ValueError("proportions must give nonnegative shares for every segment")
        total = sum(proportions.values())
        if total <= 0:
            raise ValueError("proportions must sum to a positive value")
        shares = {segment: value / total for segment, value in proportions.items()}
    raw = {segment: size * share for segment, share in shares.items()}
    counts = {segment: min(int(raw[segment]), len(groups[segment])) for segment in groups}
    remaining = size - sum(counts.values())
    while remaining:
        eligible = [segment for segment in groups if counts[segment] < len(groups[segment])]
        if not eligible:
            raise ValueError("requested sample cannot be allocated")
        segment = max(eligible, key=lambda s: (raw[s] - counts[s], s))
        counts[segment] += 1
        remaining -= 1
    rng = random.Random(seed)
    sample = [persona for segment in sorted(groups) for persona in rng.sample(groups[segment], counts[segment])]
    rng.shuffle(sample)
    return sample
