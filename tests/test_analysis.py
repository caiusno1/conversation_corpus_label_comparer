from __future__ import annotations

import math

import pytest

from cclc.core.analysis import (
    MissingTierError,
    corpus_time_extent,
    count_label,
    coverage,
    dictionary_tiers,
    interval_label_counts,
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


# --- interval label counts ------------------------------------------------------


def test_interval_label_counts_contained(tmp_path):
    cv = {"cv": ["a", "b", "c", "d"]}
    project, corpus = _project_with(
        tmp_path,
        {
            # a@[0,100], b@[200,300], c@[900,1000]
            "f1.eaf": dict(
                tiers={"G": [("a", 0, 100), ("b", 200, 300), ("c", 900, 1000)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            ),
            # d@[0,50]
            "f2.eaf": dict(tiers={"G": [("d", 0, 50)]}, cvs=cv, tier_cv={"G": "cv"}),
        },
    )
    res = interval_label_counts(project, corpus, "G", 0, 400)
    f1 = tmp_path / "f1.eaf"
    f2 = tmp_path / "f2.eaf"
    assert res.per_file_label_counts[f1] == {"a": 1, "b": 1, "c": 0, "d": 0}
    assert res.per_file_label_counts[f2] == {"a": 0, "b": 0, "c": 0, "d": 1}
    # coverage restricted to the interval: f1 -> 2/4 = 50 %, f2 -> 1/4 = 25 %
    assert res.per_file_percent[f1] == pytest.approx(50.0)
    assert res.per_file_percent[f2] == pytest.approx(25.0)
    assert res.mean_coverage == pytest.approx(37.5)


def test_interval_containment_is_boundary_inclusive(tmp_path):
    cv = {"cv": ["b"]}
    project, corpus = _project_with(
        tmp_path,
        {"f.eaf": dict(tiers={"G": [("b", 200, 300)]}, cvs=cv, tier_cv={"G": "cv"})},
    )
    exact = interval_label_counts(project, corpus, "G", 200, 300)
    assert exact.per_file_label_counts[tmp_path / "f.eaf"]["b"] == 1
    too_small = interval_label_counts(project, corpus, "G", 201, 300)
    assert too_small.per_file_label_counts[tmp_path / "f.eaf"]["b"] == 0


def test_interval_overlapping_mode(tmp_path):
    cv = {"cv": ["c"]}
    project, corpus = _project_with(
        tmp_path,
        {"f.eaf": dict(tiers={"G": [("c", 900, 1000)]}, cvs=cv, tier_cv={"G": "cv"})},
    )
    f = tmp_path / "f.eaf"
    # interval inside the annotation: overlapping counts, contained does not
    contained = interval_label_counts(project, corpus, "G", 950, 975, mode="contained")
    assert contained.per_file_label_counts[f]["c"] == 0
    overlapping = interval_label_counts(project, corpus, "G", 950, 975, mode="overlapping")
    assert overlapping.per_file_label_counts[f]["c"] == 1
    # merely touching the edge is not an overlap
    touching = interval_label_counts(project, corpus, "G", 800, 900, mode="overlapping")
    assert touching.per_file_label_counts[f]["c"] == 0


def test_interval_out_of_dictionary_and_missing_tier(tmp_path):
    cv = {"cv": ["a"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("a", 0, 100), ("typo", 10, 90)]}, cvs=cv, tier_cv={"G": "cv"}
            ),
            "f2.eaf": dict(tiers={"Other": [("x", 0, 10)]}),
        },
    )
    with pytest.raises(MissingTierError):
        interval_label_counts(project, corpus, "G", 0, 100)
    project.get_corpus("C").remove(tmp_path / "f2.eaf")
    res = interval_label_counts(project, corpus, "G", 0, 100)
    assert res.out_of_dictionary[tmp_path / "f1.eaf"] == 1


def test_interval_case_insensitive(tmp_path):
    cv = {"cv": ["Point"]}
    project, corpus = _project_with(
        tmp_path,
        {"f.eaf": dict(tiers={"G": [("point", 0, 50)]}, cvs=cv, tier_cv={"G": "cv"})},
    )
    f = tmp_path / "f.eaf"
    sensitive = interval_label_counts(project, corpus, "G", 0, 100)
    assert sensitive.per_file_label_counts[f]["Point"] == 0
    insensitive = interval_label_counts(project, corpus, "G", 0, 100, case_sensitive=False)
    assert insensitive.per_file_label_counts[f]["Point"] == 1


def test_corpus_time_extent(tmp_path):
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(tiers={"A": [("x", 0, 1500)], "B": [("y", 100, 4200)]}),
            "f2.eaf": dict(tiers={"A": [("z", 0, 900)]}),
        },
    )
    assert corpus_time_extent(project, corpus) == 4200
