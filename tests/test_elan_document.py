from __future__ import annotations

import pytest

from cclc.core.elan_document import Annotation, ElanDocument, ElanParseError
from tests.conftest import write_eaf


def test_parses_tiers_and_annotations(tmp_path):
    path = write_eaf(
        tmp_path / "a.eaf",
        tiers={
            "Gesture": [("point", 0, 100), ("wave", 200, 300)],
            "Speech": [("hello", 0, 500)],
        },
    )
    doc = ElanDocument.parse(path)
    assert set(doc.tier_names()) == {"Gesture", "Speech"}
    gesture = doc.tiers["Gesture"]
    assert [a.value for a in gesture.annotations] == ["point", "wave"]
    assert gesture.annotations[0] == Annotation("point", 0, 100, None)


def test_dictionary_tiers_and_vocabulary(tmp_path):
    path = write_eaf(
        tmp_path / "a.eaf",
        tiers={"Gesture": [("point", 0, 100)], "Free": [("x", 0, 50)]},
        cvs={"cv1": ["point", "wave", "nod"]},
        tier_cv={"Gesture": "cv1"},
    )
    doc = ElanDocument.parse(path)
    assert doc.dictionary_tier_names() == ["Gesture"]
    assert doc.tiers["Gesture"].follows_dictionary
    assert not doc.tiers["Free"].follows_dictionary
    assert doc.dictionary_for_tier("Gesture") == ["point", "wave", "nod"]
    assert doc.dictionary_for_tier("Free") == []


def test_parses_multilingual_cv(tmp_path):
    path = write_eaf(
        tmp_path / "ml.eaf",
        tiers={"Gesture": [("point", 0, 100)]},
        cvs={"cv1": ["point", "wave"]},
        tier_cv={"Gesture": "cv1"},
        ml_cv=True,
    )
    doc = ElanDocument.parse(path)
    assert doc.dictionary_for_tier("Gesture") == ["point", "wave"]


def test_reference_point_modes():
    ann = Annotation("x", 100, 300, None)
    assert ann.reference_point("begin") == 100
    assert ann.reference_point("end") == 300
    assert ann.reference_point("mid") == 200
    assert Annotation("x", None, None, None).reference_point("begin") is None


def test_invalid_xml_raises(tmp_path):
    bad = tmp_path / "bad.eaf"
    bad.write_text("<not><closed>", encoding="utf-8")
    with pytest.raises(ElanParseError):
        ElanDocument.parse(bad)


def test_non_elan_root_raises(tmp_path):
    other = tmp_path / "other.eaf"
    other.write_text('<?xml version="1.0"?><SOMETHING/>', encoding="utf-8")
    with pytest.raises(ElanParseError):
        ElanDocument.parse(other)
