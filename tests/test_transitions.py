from __future__ import annotations

import pytest

from cclc.core.analysis import MissingTierError
from cclc.core.corpus import CorpusProject
from cclc.core.query import Group, Query, Term
from cclc.core.transitions import (
    TransitionResult,
    compound_transition_matrix,
    tier_to_tier_matrix,
    transition_matrix,
)
from tests.conftest import write_eaf


def _project_with(tmp_path, files):
    project = CorpusProject()
    corpus = project.add_corpus("C")
    for name, kwargs in files.items():
        corpus.add(write_eaf(tmp_path / name, **kwargs))
    return project, corpus


def _assert_rows_sum_to_one(res: TransitionResult) -> None:
    """Every non-empty row of a stochastic matrix sums to 1."""
    for i in res.row_labels:
        ratios = [res.ratio(i, j) for j in res.col_labels]
        if res.row_totals.get(i, 0) == 0:
            assert all(r is None for r in ratios)  # empty row -> all undefined
        else:
            assert sum(r for r in ratios if r is not None) == pytest.approx(1.0)


# --- mode 1: merged sequence ----------------------------------------------------


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
    assert res.row_labels == ["a", "b", "c"]
    assert res.col_labels == ["a", "b", "c"]
    # transitions a->b, b->a, a->c, c->a. The trailing a has no successor.
    assert res.count("a", "b") == 1
    assert res.count("a", "c") == 1
    assert res.count("b", "a") == 1
    assert res.count("c", "a") == 1
    # row denominators = transitions OUT of each label (the trailing a excluded)
    assert res.row_totals == {"a": 2, "b": 1, "c": 1}
    # forward, row-stochastic: a is followed equally by b and c
    assert res.ratio("a", "b") == pytest.approx(0.5)
    assert res.ratio("a", "c") == pytest.approx(0.5)
    assert res.ratio("b", "a") == pytest.approx(1.0)
    assert res.ratio("a", "a") == pytest.approx(0.0)
    _assert_rows_sum_to_one(res)
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
    # f1 contributes a->b, f2 contributes b->a; the file boundary between f1's
    # last (b) and f2's first (b) must NOT count.
    assert res.count("a", "b") == 1
    assert res.count("b", "a") == 1
    assert res.count("b", "b") == 0
    assert res.row_totals == {"a": 1, "b": 1}
    assert res.ratio("a", "b") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)


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
    assert res.count("a", "b") == 1
    assert res.row_totals == {"a": 1, "b": 0}
    assert res.ratio("a", "b") == pytest.approx(1.0)
    assert res.ratio("b", "a") is None  # b only ends the sequence -> empty row
    _assert_rows_sum_to_one(res)

    res2 = transition_matrix(project, corpus, ["G"], scope_file=tmp_path / "f2.eaf")
    assert res2.transition_total == 0  # single annotation -> no transition
    assert res2.ratio("a", "b") is None


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
    assert res.row_labels == ["a", "c", "x"]  # union in selection order
    assert res.count("a", "x") == 1  # a -> x (cross-tier)
    assert res.count("x", "c") == 1  # x -> c (cross-tier)
    assert res.count("a", "c") == 0  # a and c are NOT adjacent (x in between)
    assert res.ratio("a", "x") == pytest.approx(1.0)
    assert res.ratio("x", "c") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)


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
    assert res.count("a", "b") == 1
    assert res.ratio("a", "b") == pytest.approx(1.0)
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
    # lowercase "point" is out-of-dictionary -> only one element, no transition
    assert sensitive.annotations_considered == 1
    assert sensitive.transition_total == 0
    insensitive = transition_matrix(project, corpus, ["G"], case_sensitive=False)
    assert insensitive.annotations_considered == 2
    assert insensitive.count("Point", "Point") == 1
    assert insensitive.ratio("Point", "Point") == pytest.approx(1.0)


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


# --- mode 2: tier to tier -------------------------------------------------------


def test_tier_to_tier_next_target(tmp_path):
    # source S: j1@0, j2@1000; target T: i1@500, i2@1500
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "S": [("j1", 0, 10), ("j2", 1000, 1010)],
                    "T": [("i1", 500, 510), ("i2", 1500, 1510)],
                },
                cvs={"cvs": ["j1", "j2"], "cvt": ["i1", "i2"]},
                tier_cv={"S": "cvs", "T": "cvt"},
            )
        },
    )
    res = tier_to_tier_matrix(project, corpus, "S", "T")
    assert res.row_labels == ["j1", "j2"]  # source dictionary = rows (from)
    assert res.col_labels == ["i1", "i2"]  # target dictionary = columns (to)
    assert res.count("j1", "i1") == 1  # next target after j1@0 is i1@500
    assert res.count("j2", "i2") == 1  # next target after j2@1000 is i2@1500
    assert res.count("j1", "i2") == 0
    assert res.row_totals == {"j1": 1, "j2": 1}
    assert res.ratio("j1", "i1") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)


def test_tier_to_tier_shared_target_and_simultaneous_start(tmp_path):
    # two sources map onto the SAME next target; each source row still sums to 1
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "S": [("j1", 0, 10), ("j2", 100, 110), ("j1", 500, 510)],
                    "T": [("i", 300, 310)],
                },
                cvs={"cvs": ["j1", "j2"], "cvt": ["i"]},
                tier_cv={"S": "cvs", "T": "cvt"},
            )
        },
    )
    res = tier_to_tier_matrix(project, corpus, "S", "T")
    # j1@0 -> i, j2@100 -> i; j1@500 has no later target -> no transition
    assert res.count("j1", "i") == 1
    assert res.count("j2", "i") == 1
    assert res.row_totals == {"j1": 1, "j2": 1}
    assert res.ratio("j1", "i") == pytest.approx(1.0)
    assert res.ratio("j2", "i") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)
    assert res.transition_total == 2

    # a target starting at the same time as a source is NOT "after"
    project2, corpus2 = _project_with(
        tmp_path,
        {
            "g.eaf": dict(
                tiers={"S": [("j1", 500, 510)], "T": [("i", 500, 510)]},
                cvs={"cvs": ["j1"], "cvt": ["i"]},
                tier_cv={"S": "cvs", "T": "cvt"},
            )
        },
    )
    res2 = tier_to_tier_matrix(project2, corpus2, "S", "T")
    assert res2.transition_total == 0


def test_tier_to_tier_scope_and_missing_tier(tmp_path):
    cvs = {"cvs": ["j"], "cvt": ["i"]}
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(
                tiers={"S": [("j", 0, 10)], "T": [("i", 100, 110)]},
                cvs=cvs,
                tier_cv={"S": "cvs", "T": "cvt"},
            ),
            "f2.eaf": dict(
                tiers={"S": [("j", 0, 10)], "T": []},
                cvs=cvs,
                tier_cv={"S": "cvs", "T": "cvt"},
            ),
        },
    )
    full = tier_to_tier_matrix(project, corpus, "S", "T")
    assert full.count("j", "i") == 1
    only_f2 = tier_to_tier_matrix(project, corpus, "S", "T", scope_file=tmp_path / "f2.eaf")
    assert only_f2.transition_total == 0

    project2, corpus2 = _project_with(
        tmp_path, {"h1.eaf": dict(tiers={"S": [("j", 0, 10)]})}
    )
    with pytest.raises(MissingTierError):
        tier_to_tier_matrix(project2, corpus2, "S", "T")


# --- mode 3: compound to compound -----------------------------------------------


def _term_query(tier, label, distance=10000):
    return Query(Group("AND", [Term(tier, label)]), max_distance_ms=distance)


def test_compound_transitions_simple(tmp_path):
    # A-instances at 0 and 2000 (tier T1), B-instance at 1000 (tier T2):
    # sequence A, B, A -> A->B, B->A; the trailing A has no successor
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "T1": [("a", 0, 10), ("a", 2000, 2010)],
                    "T2": [("b", 1000, 1010)],
                }
            )
        },
    )
    queries = {"A": _term_query("T1", "a"), "B": _term_query("T2", "b")}
    res = compound_transition_matrix(project, corpus, queries)
    assert res.row_labels == ["A", "B"]
    assert res.count("A", "B") == 1
    assert res.count("B", "A") == 1
    assert res.row_totals == {"A": 1, "B": 1}
    assert res.ratio("A", "B") == pytest.approx(1.0)
    assert res.ratio("B", "A") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)


def test_compound_transitions_with_and_compound(tmp_path):
    # compound A = (a on T1) AND (x on T2) within 500 ms: only the pair at 0/100
    # forms an instance; the lone a@5000 does not. B = b on T3 at 2000.
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "T1": [("a", 0, 10), ("a", 5000, 5010)],
                    "T2": [("x", 100, 110)],
                    "T3": [("b", 2000, 2010)],
                }
            )
        },
    )
    compound_a = Query(
        Group("AND", [Term("T1", "a"), Term("T2", "x")]), max_distance_ms=500
    )
    queries = {"A": compound_a, "B": _term_query("T3", "b")}
    res = compound_transition_matrix(project, corpus, queries)
    assert res.count("A", "B") == 1  # the A-instance (start 0) is followed by B@2000
    assert res.row_totals == {"A": 1, "B": 0}
    assert res.ratio("A", "B") == pytest.approx(1.0)
    assert res.ratio("B", "A") is None  # B never has a successor
    _assert_rows_sum_to_one(res)


def test_compound_transitions_scope_and_validation(tmp_path):
    project, corpus = _project_with(
        tmp_path,
        {
            "f1.eaf": dict(tiers={"T1": [("a", 0, 10)], "T2": [("b", 100, 110)]}),
            "f2.eaf": dict(tiers={"T1": [("a", 0, 10)], "T2": []}),
        },
    )
    queries = {"A": _term_query("T1", "a"), "B": _term_query("T2", "b")}
    full = compound_transition_matrix(project, corpus, queries)
    assert full.count("A", "B") == 1  # only inside f1; no cross-file transition
    only_f2 = compound_transition_matrix(
        project, corpus, queries, scope_file=tmp_path / "f2.eaf"
    )
    assert only_f2.transition_total == 0
    with pytest.raises(ValueError):
        compound_transition_matrix(project, corpus, {})


def test_compound_transitions_with_free_variable_expansion(tmp_path):
    # compound A = ALL G: every annotation on G becomes an element A[label]
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={"G": [("a", 0, 10), ("b", 1000, 1010), ("a", 2000, 2010)]}
            )
        },
    )
    queries = {"A": Query(Group("AND", [Term("G", "", free=True)]), max_distance_ms=1000)}
    res = compound_transition_matrix(project, corpus, queries)
    assert res.row_labels == ["A[a]", "A[b]"]
    # sequence A[a], A[b], A[a]: A[a]->A[b], A[b]->A[a]; trailing A[a] excluded
    assert res.count("A[a]", "A[b]") == 1
    assert res.count("A[b]", "A[a]") == 1
    assert res.row_totals == {"A[a]": 1, "A[b]": 1}
    assert res.ratio("A[a]", "A[b]") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)


def test_compound_transitions_free_variable_with_fixed_terms(tmp_path):
    # A = (T1=p AND ALL T2) -> elements A[x] / A[y]; B = T3=b stays plain
    project, corpus = _project_with(
        tmp_path,
        {
            "f.eaf": dict(
                tiers={
                    "T1": [("p", 0, 10), ("p", 3000, 3010)],
                    "T2": [("x", 100, 110), ("y", 3100, 3110)],
                    "T3": [("b", 1500, 1510)],
                }
            )
        },
    )
    compound_a = Query(
        Group("AND", [Term("T1", "p"), Term("T2", "", free=True)]), max_distance_ms=500
    )
    queries = {"A": compound_a, "B": Query(Group("AND", [Term("T3", "b")]), 500)}
    res = compound_transition_matrix(project, corpus, queries)
    # sequence: A[x]@0, B@1500, A[y]@3000
    assert res.row_labels == ["A[x]", "A[y]", "B"]
    assert res.count("A[x]", "B") == 1
    assert res.count("B", "A[y]") == 1
    assert res.ratio("A[x]", "B") == pytest.approx(1.0)
    assert res.ratio("B", "A[y]") == pytest.approx(1.0)
    _assert_rows_sum_to_one(res)
