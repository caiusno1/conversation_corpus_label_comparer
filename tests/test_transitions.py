from __future__ import annotations

import pytest

from cclc.core.analysis import MissingTierError
from cclc.core.corpus import CorpusProject
from cclc.core.transitions import transition_matrix
from tests.conftest import write_eaf


def _project_with(tmp_path, files):
    project = CorpusProject()
    corpus = project.add_corpus("C")
    for name, kwargs in files.items():
        corpus.add(write_eaf(tmp_path / name, **kwargs))
    return project, corpus


def test_single_tier_single_file(tmp_path):
    # sequence: a, b, a, c, a
    cv = {"cv": ["a", "b", "c"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "G": [
                        ("a", 0, 10),
                        ("b", 100, 110),
                        ("a", 200, 210),
                        ("c", 300, 310),
                        ("a", 400, 410),
                    ]
                },
                cvs=cv,
                tier_cv={"G": "cv"},
            )
        },
    )
    res = transition_matrix(project, corpus, ["G"])
    assert res.labels == ["a", "b", "c"]
    assert res.label_totals == {"a": 3, "b": 1, "c": 1}
    # transitions (i after j): b after a, a after b, c after a, a after c
    assert res.count("b", "a") == 1
    assert res.count("a", "b") == 1
    assert res.count("c", "a") == 1
    assert res.count("a", "c") == 1
    assert res.count("a", "a") == 0
    # ratios: divided by all instances of i
    assert res.ratio("a", "b") == pytest.approx(1 / 3)
    assert res.ratio("b", "a") == pytest.approx(1.0)
    # first annotation has no predecessor: row sums can stay below 1
    row_a = sum(res.ratio("a", j) for j in res.labels)
    assert row_a == pytest.approx(2 / 3)
    assert res.annotations_considered == 5
    assert res.transition_total == 4


def test_corpus_aggregates_without_cross_file_transitions(tmp_path):
    cv = {"cv": ["a", "b"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("a", 0, 10), ("b", 100, 110)]}, cvs=cv, tier_cv={"G": "cv"}
            ),
            "f2.eaf": dict(
                tiers={"G": [("b", 0, 10), ("a", 100, 110)]}, cvs=cv, tier_cv={"G": "cv"}
            ),
        },
    )
    res = transition_matrix(project, corpus, ["G"])
    assert res.label_totals == {"a": 2, "b": 2}
    # f1 contributes b-after-a, f2 contributes a-after-b; the file boundary
    # between f1's last (b) and f2's first (b) must NOT count.
    assert res.count("b", "a") == 1
    assert res.count("a", "b") == 1
    assert res.count("b", "b") == 0
    assert res.ratio("b", "a") == pytest.approx(0.5)


def test_single_file_scope(tmp_path):
    cv = {"cv": ["a", "b"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"G": [("a", 0, 10), ("b", 100, 110)]}, cvs=cv, tier_cv={"G": "cv"}
            ),
            "f2.eaf": dict(tiers={"G": [("a", 0, 10)]}, cvs=cv, tier_cv={"G": "cv"}),
        },
    )
    res = transition_matrix(project, corpus, ["G"], scope_file=tmp_path / "f1.eaf")
    assert res.label_totals == {"a": 1, "b": 1}
    assert res.count("b", "a") == 1
    res2 = transition_matrix(project, corpus, ["G"], scope_file=tmp_path / "f2.eaf")
    assert res2.label_totals == {"a": 1, "b": 0}
    assert res2.transition_total == 0
    assert res2.ratio("b", "a") is None  # b never occurs in f2


def test_cross_tier_union_and_merge(tmp_path):
    # tier G (dict a, c) and tier H (dict x): merged sequence a, x, c
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "G": [("a", 0, 10), ("c", 2000, 2010)],
                    "H": [("x", 1000, 1010)],
                },
                cvs={"cvg": ["a", "c"], "cvh": ["x"]},
                tier_cv={"G": "cvg", "H": "cvh"},
            )
        },
    )
    res = transition_matrix(project, corpus, ["G", "H"])
    assert res.labels == ["a", "c", "x"]  # union in selection order
    assert res.count("x", "a") == 1  # x right after a (cross-tier)
    assert res.count("c", "x") == 1  # c right after x (cross-tier)
    assert res.count("c", "a") == 0  # a and c are NOT adjacent (x in between)
    assert res.ratio("x", "a") == pytest.approx(1.0)


def test_out_of_dictionary_is_skipped_transparently(tmp_path):
    cv = {"cv": ["a", "b"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={"G": [("a", 0, 10), ("typo", 100, 110), ("b", 200, 210)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            )
        },
    )
    res = transition_matrix(project, corpus, ["G"])
    # the typo is invisible: a and b become adjacent
    assert res.count("b", "a") == 1
    assert res.annotations_considered == 2


def test_case_insensitive_folding(tmp_path):
    cv = {"cv": ["Point"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={"G": [("Point", 0, 10), ("point", 100, 110)]},
                cvs=cv,
                tier_cv={"G": "cv"},
            )
        },
    )
    sensitive = transition_matrix(project, corpus, ["G"])
    assert sensitive.label_totals == {"Point": 1}  # lowercase variant is OOD
    insensitive = transition_matrix(project, corpus, ["G"], case_sensitive=False)
    assert insensitive.label_totals == {"Point": 2}
    assert insensitive.count("Point", "Point") == 1


def test_missing_tier_and_empty_selection(tmp_path):
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(tiers={"G": [("a", 0, 10)]}),
            "f2.eaf": dict(tiers={"Other": [("x", 0, 10)]}),
        },
    )
    with pytest.raises(MissingTierError):
        transition_matrix(project, corpus, ["G"])
    with pytest.raises(ValueError):
        transition_matrix(project, corpus, [])
