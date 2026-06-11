from __future__ import annotations

import math

import pytest

from cclc.core.analysis import (
    MissingTierError,
    count_label,
    coverage,
    dictionary_tiers,
    label_matches,
    union_dictionary,
)
from cclc.core.corpus import CorpusProject
from tests.conftest import write_eaf


def _project_with(tmp_path, files):
    """files: dict[name -> build_eaf kwargs]; returns (project, corpus)."""
    project = CorpusProject()
    corpus = project.add_corpus("C")
    for name, kwargs in files.items():
        path = write_eaf(tmp_path / name, **kwargs)
        corpus.add(path)
    return project, corpus


def test_label_matches_case():
    assert label_matches("Point", "Point")
    assert not label_matches("Point", "point")
    assert label_matches("Point", "point", case_sensitive=False)


def test_count_label_basic_stats(tmp_path):
    cv = {"cv": ["point", "wave"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("point", 0, 1), ("point", 5, 6), ("wave", 9, 10)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            ),
            "f2.eaf": dict(
                tiers={"G": [("point", 0, 1)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            ),
        },
    )
    res = count_label(project, corpus, "G", "point")
    assert sorted(res.per_file.values()) == [1, 2]
    assert res.total == 3
    assert res.mean == 1.5
    assert res.stdev == pytest.approx(math.sqrt(0.5))


def test_count_label_single_file_stdev_none(tmp_path):
    project, corpus = _project_with(
        tmp_path, {"f1.eaf": dict(tiers={"G": [("point", 0, 1)]})}
    )
    res = count_label(project, corpus, "G", "point")
    assert res.stdev is None


def test_count_out_of_dictionary(tmp_path):
    cv = {"cv": ["point", "wave"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("point", 0, 1), ("typo", 5, 6), ("garbage", 7, 8)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            )
        },
    )
    res = count_label(project, corpus, "G", "point")
    assert res.total == 1
    assert sum(res.out_of_dictionary.values()) == 2  # typo + garbage


def test_missing_tier_raises(tmp_path):
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(tiers={"G": [("point", 0, 1)]}),
            "f2.eaf": dict(tiers={"Other": [("x", 0, 1)]}),
        },
    )
    with pytest.raises(MissingTierError) as exc:
        count_label(project, corpus, "G", "point")
    assert "f2.eaf" in str(exc.value)


def test_present_tier_without_label_counts_zero(tmp_path):
    cv = {"cv": ["point", "wave"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(tiers={"G": [("point", 0, 1)]}, cvs=cv, tier_cv={"G": "cv"}),
            "f2.eaf": dict(tiers={"G": [("wave", 0, 1)]}, cvs=cv, tier_cv={"G": "cv"}),
        },
    )
    res = count_label(project, corpus, "G", "point")
    assert res.total == 1  # f2 has the tier but no "point" -> 0
    assert res.mean == 0.5


def test_coverage(tmp_path):
    cv = {"cv": ["a", "b", "c", "d", "e"]}  # dictionary size 5
    project, corpus = _project_with(
        tmp_path,
        {
            # f1: a, b, c, d present -> 4/5 = 80%
            "f1.eaf": dict(
                tiers={"G": [("a", 0, 1), ("b", 2, 3), ("c", 4, 5), ("d", 6, 7)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            ),
            # f2: all five present (a twice) -> 100%
            "f2.eaf": dict(
                tiers={
                    "G": [
                        ("a", 0, 1),
                        ("a", 1, 2),
                        ("b", 2, 3),
                        ("c", 4, 5),
                        ("d", 6, 7),
                        ("e", 8, 9),
                    ]
                },
                cvs=cv,
                tier_cv={"G": "cv"},
            ),
        },
    )
    res = coverage(project, corpus, "G")
    assert res.dictionary_size == 5
    percents = sorted(res.per_file_percent.values())
    assert percents == pytest.approx([80.0, 100.0])
    assert res.mean_coverage == pytest.approx(90.0)


def test_union_dictionary_merges_versions(tmp_path):
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("a", 0, 1)]}, cvs={"cv": ["a", "b"]}, tier_cv={"G": "cv"}
            ),
            "f2.eaf": dict(
                tiers={"G": [("c", 0, 1)]}, cvs={"cv": ["b", "c"]}, tier_cv={"G": "cv"}
            ),
        },
    )
    assert union_dictionary(project, corpus, "G") == ["a", "b", "c"]
    assert dictionary_tiers(project, corpus) == ["G"]


def test_case_insensitive_counting(tmp_path):
    cv = {"cv": ["Point"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("Point", 0, 1), ("point", 2, 3)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            )
        },
    )
    sensitive = count_label(project, corpus, "G", "Point", case_sensitive=True)
    assert sensitive.total == 1
    insensitive = count_label(project, corpus, "G", "Point", case_sensitive=False)
    assert insensitive.total == 2
