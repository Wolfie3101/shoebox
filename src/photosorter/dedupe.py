"""Near-duplicate and burst grouping. Pure functions over hashes and times."""
from __future__ import annotations

from datetime import datetime

import numpy as np

BK_MIN = 20_000  # above this many photos a BK-tree replaces the blocked brute force


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)

    def groups(self, min_size: int = 2) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.p)):
            out.setdefault(self.find(i), []).append(i)
        return [g for g in out.values() if len(g) >= min_size]


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _popcount(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    return np.unpackbits(x.view(np.uint8).reshape(*x.shape, 8), axis=-1).sum(axis=-1)


class BKTree:
    """Metric tree on Hamming distance: finds everything within `radius` without comparing all pairs."""

    def __init__(self) -> None:
        self.root: list | None = None  # [value, [indices], {dist: node}]

    def add(self, value: int, idx: int) -> None:
        if self.root is None:
            self.root = [value, [idx], {}]
            return
        node = self.root
        while True:
            d = hamming(value, node[0])
            if d == 0:
                node[1].append(idx)
                return
            nxt = node[2].get(d)
            if nxt is None:
                node[2][d] = [value, [idx], {}]
                return
            node = nxt

    def query(self, value: int, radius: int) -> list[int]:
        out: list[int] = []
        stack = [self.root] if self.root else []
        while stack:
            node = stack.pop()
            d = hamming(value, node[0])
            if d <= radius:
                out.extend(node[1])
            for k, child in node[2].items():
                if d - radius <= k <= d + radius:
                    stack.append(child)
        return out


def near_duplicate_groups(phashes: list[int], dhashes: list[int], threshold: int,
                          bk_min: int = BK_MIN) -> list[list[int]]:
    """Groups of indices whose phash AND dhash are both within `threshold` bits (union-find, so chains merge)."""
    n = len(phashes)
    uf = UnionFind(n)
    if n > bk_min:
        tree = BKTree()
        for i, p in enumerate(phashes):
            for j in tree.query(p, threshold):
                if hamming(dhashes[i], dhashes[j]) <= threshold:
                    uf.union(i, j)
            tree.add(p, i)
    else:
        p = np.array(phashes, dtype=np.uint64)
        d = np.array(dhashes, dtype=np.uint64)
        step = 512
        for s in range(0, n, step):
            dist_p = _popcount(p[s:s + step, None] ^ p[None, :])
            ii, jj = np.nonzero(dist_p <= threshold)
            for i, j in zip(ii + s, jj):
                if j > i and hamming(dhashes[i], dhashes[j]) <= threshold:
                    uf.union(int(i), int(j))
    return uf.groups()


def burst_groups(times: list[datetime | None], phashes: list[int], window: float = 3.0,
                 max_dist: int = 12) -> list[list[int]]:
    """Photos within `window` seconds of each other (chained) that also look alike.

    `times[i]` is None for photos without a trustworthy time of day: they never join a burst.
    """
    order = sorted((i for i, t in enumerate(times) if t is not None), key=lambda i: times[i])
    uf = UnionFind(len(times))
    for a, i in enumerate(order):
        for j in order[a + 1:]:
            if (times[j] - times[i]).total_seconds() > window:
                break
            if hamming(phashes[i], phashes[j]) <= max_dist:
                uf.union(i, j)
    return uf.groups()
