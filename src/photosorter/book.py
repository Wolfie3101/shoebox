"""Photo book picks: spread N photos over years and months, one best photo per time cluster."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

CLUSTER_GAP_S = 2 * 3600  # a new cluster starts after a 2 h gap


@dataclass
class Candidate:
    idx: int
    date: datetime
    blur: float
    brightness: float

    @property
    def score(self) -> float:
        sharp = min(1.0, math.log1p(max(self.blur, 0)) / math.log1p(1000))
        exposure = 1 - min(1.0, abs(self.brightness - 128) / 128)
        return 0.7 * sharp + 0.3 * exposure


def allocate(total: int, weights: list[float], caps: list[int]) -> list[int]:
    """Split `total` by weight (largest remainder), no slot above its cap; leftovers flow to others."""
    n = len(weights)
    alloc = [0] * n
    total = min(total, sum(caps))
    while total > 0:
        open_ = [i for i in range(n) if alloc[i] < caps[i]]
        if not open_:
            break
        wsum = sum(weights[i] for i in open_) or len(open_)
        shares = {i: total * (weights[i] or 1) / wsum for i in open_}
        floor = {i: min(int(shares[i]), caps[i] - alloc[i]) for i in open_}
        for i in open_:
            alloc[i] += floor[i]
        total -= sum(floor.values())
        if total == 0:
            break
        # hand out the remainder by largest fractional share
        for i in sorted(open_, key=lambda i: shares[i] - int(shares[i]), reverse=True):
            if total and alloc[i] < caps[i]:
                alloc[i] += 1
                total -= 1
    return alloc


def clusters(cands: list[Candidate]) -> list[list[Candidate]]:
    out: list[list[Candidate]] = []
    for c in sorted(cands, key=lambda c: c.date):
        if out and (c.date - out[-1][-1].date).total_seconds() <= CLUSTER_GAP_S:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def pick_book(cands: list[Candidate], n: int) -> list[int]:
    """Indices (Candidate.idx) of the picks.

    Years share N in proportion to their number of time clusters (events, so a burst-heavy year
    doesn't swallow the book). Inside a year the same split runs over months with every month
    getting at least one pick when the quota allows; if there are more months than picks, the
    months are spread evenly through the year.
    """
    if n <= 0 or not cands:
        return []
    by_year: dict[int, list[Candidate]] = {}
    for c in cands:
        by_year.setdefault(c.date.year, []).append(c)
    years = sorted(by_year)
    year_clusters = {y: clusters(by_year[y]) for y in years}
    year_alloc = allocate(n, [len(year_clusters[y]) for y in years], [len(year_clusters[y]) for y in years])
    picks: list[int] = []
    for y, quota in zip(years, year_alloc):
        if quota <= 0:
            continue
        months: dict[int, list[list[Candidate]]] = {}
        for cl in year_clusters[y]:
            months.setdefault(cl[0].date.month, []).append(cl)
        ms = sorted(months)
        if quota < len(ms):  # spread evenly
            ms = [ms[round(k * (len(ms) - 1) / max(quota - 1, 1))] for k in range(quota)] if quota > 1 else [ms[len(ms) // 2]]
            ms = sorted(set(ms))
        caps = [len(months[m]) for m in ms]
        base = [1 if quota >= len(ms) else 0 for _ in ms]
        if sum(base) == 0:
            base = [1] * len(ms)
        rest = allocate(max(quota - sum(base), 0), [len(months[m]) for m in ms], [c - b for c, b in zip(caps, base)])
        for m, b, r in zip(ms, base, rest):
            best = sorted(((max(cl, key=lambda c: c.score)) for cl in months[m]), key=lambda c: -c.score)
            picks.extend(c.idx for c in best[:b + r])
    return picks
