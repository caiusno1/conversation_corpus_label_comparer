from __future__ import annotations

import pytest

from cclc.core.analysis import MissingTierError
from cclc.core.corpus import CorpusProject
from cclc.core.query import Group, Query, Term
from cclc.core.transitions import (
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
    row_a = sum(res.ratio("a", j) for j in res.col_labels)
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
    assert res.row_labels == ["a", "c", "x"]  # union in selection order
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
    assert res.col_labels == ["j1", "j2"]  # source dictionary = columns (j)
    assert res.row_labels == ["i1", "i2"]  # target dictionary = rows (i)
    assert res.count("i1", "j1") == 1  # next target after j1@0 is i1@500
    assert res.count("i2", "j2") == 1  # next target after j2@1000 is i2@1500
    assert res.count("i2", "j1") == 0
    assert res.label_totals == {"i1": 1, "i2": 1}
    assert res.ratio("i1", "j1") == pytest.approx(1.0)


def test_tier_to_tier_shared_target_and_simultaneous_start(tmp_path):
    # two sources both map onto the SAME next target -> the i-row sums above 1;
    # a target starting at the same time as a source is NOT "next" (strictly later)
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
    assert res.count("i", "j1") == 1
    assert res.count("i", "j2") == 1
    assert res.label_totals == {"i": 1}
    # both ratios are 1/1: rows may sum to more than 1 in this mode
    assert res.ratio("i", "j1") == pytest.approx(1.0)
    assert res.ratio("i", "j2") == pytest.approx(1.0)
    # source j1@500 has no later target -> no transition
    assert res.transition_total == 2

    # simultaneous start is not "after"
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
    assert full.count("i", "j") == 1
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
    # sequence A, B, A -> (B after A)=1, (A after B)=1; totals A=2, B=1
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
    assert res.label_totals == {"A": 2, "B": 1}
    assert res.count("B", "A") == 1
    assert res.count("A", "B") == 1
    assert res.ratio("B", "A") == pytest.approx(1.0)
    assert res.ratio("A", "B") == pytest.approx(0.5)


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
    assert res.label_totals == {"A": 1, "B": 1}
    assert res.count("B", "A") == 1  # B@2000 follows the A-instance starting at 0
    assert res.ratio("B", "A") == pytest.approx(1.0)


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
    assert full.count("B", "A") == 1  # only inside f1; no cross-file transition
    only_f2 = compound_transition_matrix(
        project, corpus, queries, scope_file=tmp_path / "f2.eaf"
    )
    assert only_f2.label_totals == {"A": 1, "B": 0}
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
    assert res.label_totals == {"A[a]": 2, "A[b]": 1}
    assert res.count("A[b]", "A[a]") == 1
    assert res.count("A[a]", "A[b]") == 1
    assert res.ratio("A[a]", "A[b]") == pytest.approx(0.5)


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
    assert res.count("B", "A[x]") == 1
    assert res.count("A[y]", "B") == 1
    assert res.ratio("A[y]", "B") == pytest.approx(1.0)
