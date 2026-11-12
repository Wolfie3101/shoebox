import json

import pytest

from photosorter.sidecar import SidecarIndex, norm_dir, pair_key, split_media_name


def doc(title, ts):
    return json.dumps({"title": title, "photoTakenTime": {"timestamp": str(ts)}})


D = "Takeout/Google Photos/Photos from 2019/"


def idx(*entries):
    i = SidecarIndex()
    for name, title, ts in entries:
        i.add(D + name, doc(title, ts))
    return i


@pytest.mark.parametrize("json_name", [
    "IMG_0001.jpg.json",
    "IMG_0001.jpg.supplemental-metadata.json",
    "IMG_0001.jpg.supplemental-metad.json",     # truncated by Takeout
    "IMG_0001.jpg.supplemental-.json",
])
def test_name_variants_match(json_name):
    i = idx((json_name, "IMG_0001.jpg", 1000))
    assert i.match(D + "IMG_0001.jpg").taken.timestamp() == 1000


def test_truncated_long_name():
    long = "a_really_long_original_photo_name_from_a_phone_2019.jpg"
    i = idx(("a_really_long_original_photo_name_from_a.json", long, 5))  # cut at ~46 chars
    assert i.match(D + long).taken.timestamp() == 5


def test_edited_copy_uses_original_json():
    i = idx(("IMG_0002.jpg.supplemental-metadata.json", "IMG_0002.jpg", 7))
    assert i.match(D + "IMG_0002-edited.jpg").taken.timestamp() == 7


def test_duplicate_counter_moves_behind_extension():
    i = idx(("IMG_0003.jpg.json", "IMG_0003.jpg", 1),
            ("IMG_0003.jpg(1).json", "IMG_0003.jpg", 2))
    assert i.match(D + "IMG_0003.jpg").taken.timestamp() == 1
    assert i.match(D + "IMG_0003(1).jpg").taken.timestamp() == 2


def test_counter_with_supplemental_and_edited():
    i = idx(("IMG_0004.jpg.supplemental-metadata(1).json", "IMG_0004.jpg", 9))
    assert i.match(D + "IMG_0004(1)-edited.jpg").taken.timestamp() == 9


def test_sidecar_in_other_archive_same_folder_is_found():
    i = SidecarIndex()
    i.add("Takeout/Google Photos/Photos from 2019/IMG_5.jpg.json", doc("IMG_5.jpg", 3))  # zip 2
    assert i.match("Google Photos/Photos from 2019/IMG_5.jpg").taken.timestamp() == 3     # photo in zip 1 (unpacked folder form)


def test_other_folder_does_not_match():
    i = idx(("IMG_6.jpg.json", "IMG_6.jpg", 3))
    assert i.match("Takeout/Google Photos/Other album/IMG_6.jpg") is None


def test_no_match_returns_none():
    assert idx(("IMG_7.jpg.json", "IMG_7.jpg", 1)).match(D + "IMG_8.jpg") is None


def test_ambiguous_short_prefix_is_not_guessed():
    i = idx(("IMG_.json", "x", 1))  # 4 chars: too short to be a safe prefix
    assert i.match(D + "IMG_0009.jpg") is None


def test_json_without_photo_taken_time_ignored():
    i = SidecarIndex()
    i.add(D + "metadata.json", json.dumps({"title": "Album", "description": ""}))
    i.add(D + "broken.json", "{not json")
    assert i.count == 0


def test_helpers():
    assert split_media_name("IMG(2)-edited.jpg") == ("IMG.jpg", "(2)", True)
    assert split_media_name("IMG.jpg") == ("IMG.jpg", None, False)
    assert norm_dir("Takeout/Google Photos/x/a.jpg") == "Google Photos/x"
    k1, e1 = pair_key(D + "IMG_1.jpg")
    k2, e2 = pair_key(D + "IMG_1-edited.jpg")
    assert k1 == k2 and (e1, e2) == (False, True)
