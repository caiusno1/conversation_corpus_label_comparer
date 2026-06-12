from __future__ import annotations

from cclc.core.corpus import CorpusProject
from cclc.core.query import (
    Group,
    Query,
    Term,
    evaluate,
    instance_statistics,
)
from tests.conftest import write_eaf


def _single_file(tmp_path, tiers, name="f.eaf"):
    project = CorpusProject()
    corpus = project.add_corpus("C")
    corpus.add(write_eaf(tmp_path / name, tiers=tiers))
    return project, corpus


def _q(root, distance=1000, ref="begin", mode="anchor"):
    return Query(root=root, max_distance_ms=distance, reference_point=ref, counting_mode=mode)


# --- basic AND / counting -----------------------------------------------------


def test_and_one_per_anchor(tmp_path):
    # two points, one nod near each point
    project, corpus = _single_file(
        tmp_path,
        {
            "A": [("point", 0, 10), ("point", 5000, 5010)],
            "B": [("nod", 100, 110), ("nod", 5100, 5110)],
        },
    )
    q = _q(Group("AND", [Term("A", "point"), Term("B", "nod")]))
    instances = evaluate(project, corpus, q)
    assert len(instances) == 2


def test_and_requires_partner_within_distance(tmp_path):
    project, corpus = _single_file(
        tmp_path,
        {"A": [("point", 0, 10)], "B": [("nod", 5000, 5010)]},  # 5000 ms away
    )
    q = _q(Group("AND", [Term("A", "point"), Term("B", "nod")]), distance=1000)
    assert evaluate(project, corpus, q) == []
    q2 = _q(Group("AND", [Term("A", "point"), Term("B", "nod")]), distance=6000)
    assert len(evaluate(project, corpus, q2)) == 1


# --- NOT semantics (near-any-member rule), from PLAN.md section 7.1 -----------


def test_a_and_not_b(tmp_path):
    # B 10 ms from A -> rejected
    project, corpus = _single_file(
        tmp_path, {"A": [("a", 0, 5)], "B": [("b", 10, 15)]}, name="near.eaf"
    )
    q = _q(Group("AND", [Term("A", "a"), Term("B", "b", negated=True)]))
    assert evaluate(project, corpus, q) == []

    # B 1005 ms from A -> kept
    project2, corpus2 = _single_file(
        tmp_path, {"A": [("a", 0, 5)], "B": [("b", 1005, 1010)]}, name="far.eaf"
    )
    q2 = _q(Group("AND", [Term("A", "a"), Term("B", "b", negated=True)]))
    assert len(evaluate(project2, corpus2, q2)) == 1


def test_a_and_b_and_not_c_near_any_member(tmp_path):
    expr = Group("AND", [Term("A", "a"), Term("B", "b"), Term("C", "c", negated=True)])

    # C 500 ms from A and 490 ms from B -> near a member -> rejected
    p1, c1 = _single_file(
        tmp_path,
        {"A": [("a", 0, 5)], "B": [("b", 10, 15)], "C": [("c", 500, 505)]},
        name="ex1.eaf",
    )
    assert evaluate(p1, c1, _q(expr)) == []

    # C 100 ms from member B (1100 ms from A) -> near a member -> rejected
    p2, c2 = _single_file(
        tmp_path,
        {"A": [("a", 0, 5)], "B": [("b", 1000, 1005)], "C": [("c", 1100, 1105)]},
        name="ex2.eaf",
    )
    assert evaluate(p2, c2, _q(expr)) == []

    # C 600 ms from member A (1100 ms from B) -> near a member -> rejected
    p3, c3 = _single_file(
        tmp_path,
        {"A": [("a", 500, 505)], "B": [("b", 0, 5)], "C": [("c", 1100, 1105)]},
        name="ex3.eaf",
    )
    assert evaluate(p3, c3, _q(expr)) == []

    # C at least 1000 ms away from BOTH members -> instance kept
    p4, c4 = _single_file(
        tmp_path,
        {"A": [("a", 0, 5)], "B": [("b", 900, 905)], "C": [("c", 2200, 2205)]},
        name="ex4.eaf",
    )
    assert len(evaluate(p4, c4, _q(expr))) == 1


def test_positive_tuple_forms_a_chain(tmp_path):
    # A -900ms- B -900ms- C: every consecutive gap is within D=1000, so the
    # tuple is selected even though a and c are 1800 ms apart (chain rule).
    project, corpus = _single_file(
        tmp_path,
        {"T1": [("a", 0, 5)], "T2": [("b", 900, 905)], "T3": [("c", 1800, 1805)]},
    )
    expr = Group("AND", [Term("T1", "a"), Term("T2", "b"), Term("T3", "c")])
    assert len(evaluate(project, corpus, _q(expr, distance=1000))) == 1


def test_chain_with_too_large_gap_rejects(tmp_path):
    # gap b -> c is 1100 ms > D: the chain is broken, no instance
    project, corpus = _single_file(
        tmp_path,
        {"T1": [("a", 0, 5)], "T2": [("b", 900, 905)], "T3": [("c", 2000, 2005)]},
    )
    expr = Group("AND", [Term("T1", "a"), Term("T2", "b"), Term("T3", "c")])
    assert evaluate(project, corpus, _q(expr, distance=1000)) == []
    assert len(evaluate(project, corpus, _q(expr, distance=1100))) == 1


def test_two_member_chain_equals_direct_distance(tmp_path):
    # with only two positive terms the chain rule is the plain distance check
    project, corpus = _single_file(
        tmp_path, {"T1": [("a", 0, 5)], "T2": [("b", 1800, 1805)]}
    )
    expr = Group("AND", [Term("T1", "a"), Term("T2", "b")])
    assert evaluate(project, corpus, _q(expr, distance=1000)) == []
    assert len(evaluate(project, corpus, _q(expr, distance=1800))) == 1


# --- distance boundary --------------------------------------------------------


def test_distance_boundary_inclusive(tmp_path):
    # 5 ms vs 1000 ms example from the plan
    project, corpus = _single_file(
        tmp_path, {"A": [("a", 5, 6)], "B": [("b", 1000, 1001)]}
    )
    expr = Group("AND", [Term("A", "a"), Term("B", "b")])
    assert evaluate(project, corpus, _q(expr, distance=500)) == []
    assert len(evaluate(project, corpus, _q(expr, distance=995))) == 1  # |1000-5|=995


def test_reference_point_mid(tmp_path):
    # begin distance large, midpoint distance small
    project, corpus = _single_file(
        tmp_path, {"A": [("a", 0, 2000)], "B": [("b", 900, 1100)]}
    )
    expr = Group("AND", [Term("A", "a"), Term("B", "b")])
    # begin: |0-900| = 900
    assert len(evaluate(project, corpus, _q(expr, distance=950, ref="begin"))) == 1
    # mid: |1000-1000| = 0
    assert len(evaluate(project, corpus, _q(expr, distance=10, ref="mid"))) == 1


# --- OR -----------------------------------------------------------------------


def test_or_at_root(tmp_path):
    project, corpus = _single_file(
        tmp_path,
        {"A": [("a", 0, 5)], "B": [("b", 5000, 5005)]},  # far apart, independent
    )
    q = _q(Group("OR", [Term("A", "a"), Term("B", "b")]))
    assert len(evaluate(project, corpus, q)) == 2


def test_nested_or_within_and(tmp_path):
    # point AND (nod OR shake)
    project, corpus = _single_file(
        tmp_path,
        {
            "A": [("point", 0, 5)],
            "Head": [("shake", 100, 105)],
        },
    )
    q = _q(
        Group(
            "AND",
            [Term("A", "point"), Group("OR", [Term("Head", "nod"), Term("Head", "shake")])],
        )
    )
    assert len(evaluate(project, corpus, q)) == 1


# --- combinations mode --------------------------------------------------------


def test_combinations_mode(tmp_path):
    project, corpus = _single_file(
        tmp_path,
        {
            "A": [("point", 0, 5), ("point", 50, 55)],
            "B": [("nod", 10, 15), ("nod", 60, 65)],
        },
    )
    expr = Group("AND", [Term("A", "point"), Term("B", "nod")])
    anchor = evaluate(project, corpus, _q(expr, distance=1000, mode="anchor"))
    combos = evaluate(project, corpus, _q(expr, distance=1000, mode="combinations"))
    assert len(anchor) == 2  # one per point
    assert len(combos) == 4  # 2 points x 2 nods, all within range


# --- interval relations -------------------------------------------------------


def test_interval_relation_overlaps(tmp_path):
    project, corpus = _single_file(
        tmp_path,
        {"A": [("a", 0, 100)], "B": [("b", 50, 150)]},  # overlap
    )
    expr = Group("AND", [Term("A", "a"), Term("B", "b")], relation="overlaps")
    assert len(evaluate(project, corpus, _q(expr))) == 1

    project2, corpus2 = _single_file(
        tmp_path,
        {"A": [("a", 0, 100)], "B": [("b", 200, 300)]},  # no overlap
        name="no_overlap.eaf",
    )
    expr2 = Group("AND", [Term("A", "a"), Term("B", "b")], relation="overlaps")
    assert evaluate(project2, corpus2, _q(expr2)) == []


# --- validation ---------------------------------------------------------------


def test_only_negated_term_is_invalid(tmp_path):
    project, corpus = _single_file(tmp_path, {"A": [("a", 0, 5)]})
    q = _q(Group("AND", [Term("A", "a", negated=True)]))
    try:
        evaluate(project, corpus, q)
    except ValueError as exc:
        assert "non-negated" in str(exc)
    else:
        raise AssertionError("expected ValueError for an only-negated query")


# --- statistics ---------------------------------------------------------------


def test_instance_statistics(tmp_path):
    project = CorpusProject()
    corpus = project.add_corpus("C")
    f1 = write_eaf(
        tmp_path / "f1.eaf",
        tiers={"A": [("point", 0, 5), ("point", 50, 55)], "B": [("nod", 10, 15), ("nod", 60, 65)]},
    )
    f2 = write_eaf(tmp_path / "f2.eaf", tiers={"A": [("point", 0, 5)], "B": [("nod", 9000, 9005)]})
    corpus.add(f1)
    corpus.add(f2)
    expr = Group("AND", [Term("A", "point"), Term("B", "nod")])
    instances = evaluate(project, corpus, _q(expr, distance=1000))
    stats = instance_statistics(corpus, instances)
    # f1 -> 2 instances, f2 -> 0 (nod too far)
    assert stats.per_file[f1] == 2
    assert stats.per_file[f2] == 0
    assert stats.total == 2
    assert stats.mean == 1.0


def test_statistics_combination_breakdown(tmp_path):
    project = CorpusProject()
    corpus = project.add_corpus("C")
    f1 = write_eaf(
        tmp_path / "f.eaf",
        tiers={"A": [("point", 0, 5)], "Head": [("nod", 10, 15)]},
    )
    corpus.add(f1)
    expr = Group("AND", [Term("A", "point"), Term("Head", "nod")])
    instances = evaluate(project, corpus, _q(expr))
    stats = instance_statistics(corpus, instances, breakdown=True)
    assert stats.combinations == {("A=point", "Head=nod"): 1}


# --- ALL operator (free variables) ----------------------------------------------


def test_all_term_binds_any_label_in_range(tmp_path):
    # A=point AND ALL C: x@100 is in range, z@5000 is not
    project, corpus = _single_file(
        tmp_path,
        {"A": [("point", 0, 10)], "C": [("x", 100, 110), ("z", 5000, 5010)]},
    )
    expr = Group("AND", [Term("A", "point"), Term("C", "", free=True)])
    instances = evaluate(project, corpus, _q(expr, distance=1000))
    assert len(instances) == 1
    assert instances[0].matched["ALL C"].value == "x"
    # the binding is visible in the label combination
    assert instances[0].label_combination() == ("A=point", "C=x")


def test_all_term_is_required(tmp_path):
    # no annotation on C within range -> no compound
    project, corpus = _single_file(
        tmp_path, {"A": [("point", 0, 10)], "C": [("x", 5000, 5010)]}
    )
    expr = Group("AND", [Term("A", "point"), Term("C", "", free=True)])
    assert evaluate(project, corpus, _q(expr, distance=1000)) == []


def test_all_term_combinations_enumerate_bindings(tmp_path):
    project, corpus = _single_file(
        tmp_path,
        {"A": [("point", 0, 10)], "C": [("x", 100, 110), ("y", 800, 810)]},
    )
    expr = Group("AND", [Term("A", "point"), Term("C", "", free=True)])
    anchor = evaluate(project, corpus, _q(expr, distance=1000, mode="anchor"))
    assert len(anchor) == 1  # nearest binding only
    combos = evaluate(project, corpus, _q(expr, distance=1000, mode="combinations"))
    assert {i.matched["ALL C"].value for i in combos} == {"x", "y"}


def test_not_all_rejects_any_nearby_annotation(tmp_path):
    # NOT ALL C: any annotation on C near the compound rejects
    project, corpus = _single_file(
        tmp_path,
        {"A": [("point", 0, 10)], "C": [("whatever", 200, 210)]},
        name="near.eaf",
    )
    expr = Group("AND", [Term("A", "point"), Term("C", "", negated=True, free=True)])
    assert evaluate(project, corpus, _q(expr, distance=1000)) == []

    project2, corpus2 = _single_file(
        tmp_path,
        {"A": [("point", 0, 10)], "C": [("whatever", 5000, 5010)]},
        name="far.eaf",
    )
    expr2 = Group("AND", [Term("A", "point"), Term("C", "", negated=True, free=True)])
    assert len(evaluate(project2, corpus2, _q(expr2, distance=1000))) == 1


def test_all_term_as_anchor(tmp_path):
    # a query of just "ALL G" yields one instance per annotation on G
    project, corpus = _single_file(
        tmp_path, {"G": [("a", 0, 10), ("b", 100, 110), ("a", 200, 210)]}
    )
    expr = Group("AND", [Term("G", "", free=True)])
    instances = evaluate(project, corpus, _q(expr, distance=1000))
    assert [i.matched["ALL G"].value for i in instances] == ["a", "b", "a"]
