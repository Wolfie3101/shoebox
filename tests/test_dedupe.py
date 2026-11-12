import random
from datetime import datetime, timedelta

from photosorter.dedupe import BKTree, burst_groups, hamming, near_duplicate_groups


def flip(x, bits):
    for b in bits:
        x ^= 1 << b
    return x


def norm(groups):
    return sorted(sorted(g) for g in groups)


def test_near_duplicates_group_within_threshold_only():
    p = [0x0F0F0F0F0F0F0F0F, flip(0x0F0F0F0F0F0F0F0F, range(6)), flip(0x0F0F0F0F0F0F0F0F, range(10, 17)), 0xFFFF000000000000]
    d = list(p)
    assert norm(near_duplicate_groups(p, d, 6)) == [[0, 1]]   # 6 bits apart: in; 7 bits: out; unrelated: out


def test_both_hashes_must_agree():
    base = 0xAAAAAAAAAAAAAAAA
    p = [base, base]
    d = [0x1, flip(0x1, range(20))]     # phash identical, dhash far apart: not the same picture
    assert near_duplicate_groups(p, d, 6) == []


def test_chain_merges_into_one_group_via_union_find():
    a = 0x0123456789ABCDEF
    b = flip(a, range(5))
    c = flip(b, range(5, 10))           # c is 10 bits from a, 5 from b
    groups = near_duplicate_groups([a, b, c], [a, b, c], 6)
    assert norm(groups) == [[0, 1, 2]]


def test_bk_tree_gives_the_same_groups_as_brute_force():
    rnd = random.Random(3)
    base = [rnd.getrandbits(64) for _ in range(60)]
    p = base + [flip(x, rnd.sample(range(64), rnd.randint(0, 8))) for x in base[:40]]
    d = p[:]
    brute = near_duplicate_groups(p, d, 6, bk_min=10**9)
    tree = near_duplicate_groups(p, d, 6, bk_min=0)
    assert norm(brute) == norm(tree) and brute


def test_bk_tree_query_radius():
    t = BKTree()
    vals = [0, 1, 3, 0xFF, 0xFFFF]
    for i, v in enumerate(vals):
        t.add(v, i)
    assert sorted(t.query(0, 1)) == [0, 1]
    assert sorted(t.query(0, 2)) == [0, 1, 2]
    assert sorted(t.query(0, 8)) == [0, 1, 2, 3]
    assert hamming(0xFF, 0) == 8


T0 = datetime(2020, 5, 5, 12, 0, 0)


def test_burst_groups_by_time_and_similarity():
    t = [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2), T0 + timedelta(seconds=10)]
    h = [100, 101, 102, 100]                         # the last one is similar but 8 s later
    assert norm(burst_groups(t, h, 3, 12)) == [[0, 1, 2]]


def test_burst_needs_visual_similarity():
    t = [T0, T0 + timedelta(seconds=1)]
    assert burst_groups(t, [0, (1 << 40) - 1], 3, 12) == []


def test_burst_window_is_inclusive_and_chained():
    t = [T0, T0 + timedelta(seconds=3), T0 + timedelta(seconds=6)]   # 3 s steps chain together
    assert norm(burst_groups(t, [1, 1, 1], 3, 12)) == [[0, 1, 2]]
    assert burst_groups([T0, T0 + timedelta(seconds=3.5)], [1, 1], 3, 12) == []


def test_photos_without_a_real_time_never_join_a_burst():
    assert burst_groups([None, None, T0], [1, 1, 1], 3, 12) == []


def test_burst_groups_handle_unsorted_input():
    t = [T0 + timedelta(seconds=2), T0, T0 + timedelta(seconds=1)]
    assert norm(burst_groups(t, [1, 1, 1], 3, 12)) == [[0, 1, 2]]


def test_bk_path_also_requires_dhash_agreement():
    base = 0xAAAAAAAAAAAAAAAA
    p = [base, base]
    d = [0x1, flip(0x1, range(20))]
    assert near_duplicate_groups(p, d, 6, bk_min=0) == []
    assert near_duplicate_groups(p, [0x1, 0x1], 6, bk_min=0) == [[0, 1]]
