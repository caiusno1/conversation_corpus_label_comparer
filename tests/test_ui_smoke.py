"""Headless smoke tests: build the window and drive a query end to end.

Run under the offscreen Qt platform (``QT_QPA_PLATFORM=offscreen``).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cclc.core.query import Term  # noqa: E402
from cclc.ui.config_view import CORPUS_ROLE, PATH_ROLE, ConfigView  # noqa: E402
from cclc.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import write_eaf  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_main_window_builds(app):
    window = MainWindow()
    window.controller.add_corpus("A")
    assert window.controller.project.corpus_names() == ["A"]


def test_config_view_reflects_corpora(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f = write_eaf(tmp_path / "f.eaf", tiers={"G": [("point", 0, 10)]})
    controller.add_corpus("A")
    controller.add_files("A", [f])

    view = ConfigView(controller)
    top = view.tree.topLevelItem(0)
    assert top.data(0, CORPUS_ROLE) == "A"
    assert top.child(0).data(0, PATH_ROLE) == str(f)


def test_query_view_runs(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f = write_eaf(
        tmp_path / "f.eaf",
        tiers={"A": [("point", 0, 10)], "B": [("nod", 100, 110)]},
    )
    controller.add_corpus("C")
    controller.add_files("C", [f])

    # The Query view is the fifth tab (Corpora, Analysis, Interval, Transitions, Query).
    tabs = window.centralWidget()
    query_view = tabs.widget(4)
    query_view.corpus_combo.setCurrentText("C")

    # Programmatically populate the builder: point AND nod.
    for term in (Term("A", "point"), Term("B", "nod")):
        query_view.builder_widget.add_term(term)

    query_view.distance.setValue(1000)
    query_view._run()
    assert len(query_view._instances) == 1
    assert "1 instances" in query_view.run_status.text()

    # Hand off to statistics.
    query_view._compute_statistics()
    assert "total: 1" in query_view.stats_summary.text()


def test_interval_view_counts_and_coverage(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f1 = write_eaf(
        tmp_path / "f1.eaf",
        tiers={"G": [("a", 0, 100), ("b", 200, 300), ("c", 900, 1000)]},
        cvs={"cv": ["a", "b", "c", "d"]},
        tier_cv={"G": "cv"},
    )
    f2 = write_eaf(
        tmp_path / "f2.eaf",
        tiers={"G": [("d", 0, 50)]},
        cvs={"cv": ["a", "b", "c", "d"]},
        tier_cv={"G": "cv"},
    )
    controller.add_corpus("C")
    controller.add_files("C", [f1, f2])

    tabs = window.centralWidget()
    interval_view = tabs.widget(2)  # Corpora, Analysis, Interval, Query
    assert interval_view.corpus_combo.currentText() == "C"
    assert interval_view.tier_combo.currentText() == "G"
    # slider range follows the corpus extent (max end time = 1000 ms)
    assert interval_view.hi_slider.maximum() == 1000
    assert interval_view.hi_spin.value() == 1000

    # narrow the window to [0, 400]: a and b inside, c outside; f2 keeps d
    interval_view.hi_spin.setValue(400)
    headers = [
        interval_view.table.horizontalHeaderItem(c).text()
        for c in range(interval_view.table.columnCount())
    ]
    assert headers == ["File", "a", "b", "c", "d", "Out-of-dict", "Coverage"]
    assert interval_view.table.rowCount() == 2
    row_f1 = 0 if interval_view.table.item(0, 0).text() == "f1.eaf" else 1
    counts_f1 = [interval_view.table.item(row_f1, c).text() for c in range(1, 5)]
    assert counts_f1 == ["1", "1", "0", "0"]
    assert "37.5%" in interval_view.summary.text()

    # bounds stay ordered: pushing From above To drags To along
    interval_view.lo_spin.setValue(600)
    assert interval_view.hi_spin.value() == 600


def test_transitions_view_matrix(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f1 = write_eaf(
        tmp_path / "f1.eaf",
        tiers={"G": [("a", 0, 10), ("b", 100, 110), ("a", 200, 210)]},
        cvs={"cv": ["a", "b"]},
        tier_cv={"G": "cv"},
    )
    f2 = write_eaf(
        tmp_path / "f2.eaf",
        tiers={"G": [("a", 0, 10)]},
        cvs={"cv": ["a", "b"]},
        tier_cv={"G": "cv"},
    )
    controller.add_corpus("C")
    controller.add_files("C", [f1, f2])

    tabs = window.centralWidget()
    view = tabs.widget(3)  # Corpora, Analysis, Interval, Transitions, Query
    assert view.corpus_combo.currentText() == "C"
    assert view._selected_tiers() == ["G"]  # first tier checked by default

    # whole corpus, forward & row-stochastic. Sequence f1: a,b,a (trailing a has
    # no successor); f2: a alone. Transitions a->b and b->a, each once.
    headers = [
        view.table.horizontalHeaderItem(c).text()
        for c in range(view.table.columnCount())
    ]
    assert headers == ["a", "b"]
    row_headers = [
        view.table.verticalHeaderItem(r).text() for r in range(view.table.rowCount())
    ]
    # n = transitions OUT of the label (the row denominator), not occurrences
    assert row_headers == ["a  (n=1)", "b  (n=1)"]
    assert view.table.item(0, 1).text() == "1.000"  # a -> b
    assert view.table.item(1, 0).text() == "1.000"  # b -> a
    assert view.table.item(0, 0).text() == "0.000"  # a -> a
    # row a is stochastic: 0.000 + 1.000 == 1
    assert float(view.table.item(0, 0).text()) + float(view.table.item(0, 1).text()) == 1.0

    # raw counts toggle shows the numerator
    view.raw_check.setChecked(True)
    assert view.table.item(0, 1).text() == "1"
    view.raw_check.setChecked(False)

    # single-file scope: f2 has one a, no transitions -> every row empty ("—")
    idx = view.scope_combo.findText("f2.eaf")
    view.scope_combo.setCurrentIndex(idx)
    assert view.table.item(0, 1).text() == "—"  # a has no successor in f2
    assert "0 transitions" in view.summary.text()


def test_transitions_view_tier_to_tier_and_compound_modes(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f = write_eaf(
        tmp_path / "f.eaf",
        tiers={
            "S": [("j1", 0, 10), ("j2", 1000, 1010)],
            "T": [("i1", 500, 510), ("i2", 1500, 1510)],
        },
        cvs={"cvs": ["j1", "j2"], "cvt": ["i1", "i2"]},
        tier_cv={"S": "cvs", "T": "cvt"},
    )
    controller.add_corpus("C")
    controller.add_files("C", [f])

    tabs = window.centralWidget()
    view = tabs.widget(3)

    # --- mode 2: tier -> tier (rows = source "from", columns = target "to") ---
    view.mode_combo.setCurrentIndex(1)
    view.source_tier_combo.setCurrentText("S")
    view.target_tier_combo.setCurrentText("T")
    cols = [
        view.table.horizontalHeaderItem(c).text()
        for c in range(view.table.columnCount())
    ]
    rows = [view.table.verticalHeaderItem(r).text() for r in range(view.table.rowCount())]
    assert cols == ["i1", "i2"]  # target dictionary = columns (to)
    assert rows == ["j1  (n=1)", "j2  (n=1)"]  # source dictionary = rows (from)
    assert view.table.item(0, 0).text() == "1.000"  # j1 -> next target i1
    assert view.table.item(1, 1).text() == "1.000"  # j2 -> next target i2
    assert view.table.item(1, 0).text() == "0.000"

    # --- mode 3: compound -> compound ---
    view.mode_combo.setCurrentIndex(2)
    assert "Run compounds" in view.status.text()  # hint until run
    view.builder_a.add_term(Term("S", "j1"))
    view.builder_b.add_term(Term("T", "i1"))
    view.distance.setValue(10000)
    view._run_compounds()
    rows = [view.table.verticalHeaderItem(r).text() for r in range(view.table.rowCount())]
    assert rows == ["A  (n=1)", "B  (n=0)"]  # B has no successor
    # sequence: A(j1@0) -> B(i1@500); row A, col B
    assert view.table.item(0, 1).text() == "1.000"
    assert "2 instances, 1 transitions" in view.summary.text()


def test_transitions_compound_mode_with_free_variable(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f = write_eaf(
        tmp_path / "f.eaf",
        tiers={"G": [("a", 0, 10), ("b", 1000, 1010), ("a", 2000, 2010)]},
    )
    controller.add_corpus("C")
    controller.add_files("C", [f])

    tabs = window.centralWidget()
    view = tabs.widget(3)
    view.mode_combo.setCurrentIndex(2)
    view.builder_a.add_term(Term("G", "", free=True))  # compound A = ALL G
    view.builder_b.add_term(Term("G", "a"))  # compound B = G="a"
    view.distance.setValue(1000)
    view._run_compounds()
    rows = [view.table.verticalHeaderItem(r).text() for r in range(view.table.rowCount())]
    # A expands by its free-variable bindings; B stays plain. n = transitions out:
    # A[a] occurs twice and is followed both times, A[b] once, B once (the trailing
    # B has no successor).
    assert rows == ["A[a]  (n=2)", "A[b]  (n=1)", "B  (n=1)"]


def test_query_view_text_input_with_brackets(app, tmp_path):
    window = MainWindow()
    controller = window.controller
    f = write_eaf(
        tmp_path / "f.eaf",
        tiers={
            "A": [("a", 0, 10)],
            "B": [("b", 100, 110)],
            "C": [("c", 5000, 5010)],
            "D": [("d", 5100, 5110)],
        },
    )
    controller.add_corpus("C")
    controller.add_files("C", [f])

    tabs = window.centralWidget()
    query_view = tabs.widget(4)
    query_view.corpus_combo.setCurrentText("C")

    # Type a bracketed disjunction and apply it to the tree.
    query_view.builder_widget.text_input.setText("(A = a AND B = b) OR (C = c AND D = d)")
    query_view.builder_widget._apply_text()
    assert query_view.builder_widget.parse_error.text() == ""
    assert query_view.builder_widget.root_group().op == "OR"

    query_view.distance.setValue(1000)
    query_view._run()
    # both well-separated compounds are found (OR no longer collapses to one)
    assert len(query_view._instances) == 2

    # a malformed expression reports an error and leaves the tree intact
    query_view.builder_widget.text_input.setText("(A = a AND")
    query_view.builder_widget._apply_text()
    assert "Cannot parse" in query_view.builder_widget.parse_error.text()
