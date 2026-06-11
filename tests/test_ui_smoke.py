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
from cclc.ui.query_view import NODE_ROLE  # noqa: E402
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

    # The Query view is the fourth tab (Corpora, Analysis, Interval, Query).
    tabs = window.centralWidget()
    query_view = tabs.widget(3)
    query_view.corpus_combo.setCurrentText("C")

    # Programmatically populate the builder: point AND nod.
    root = query_view.builder.topLevelItem(0)
    for term in (Term("A", "point"), Term("B", "nod")):
        from PySide6.QtWidgets import QTreeWidgetItem

        item = QTreeWidgetItem()
        item.setData(0, NODE_ROLE, {"kind": "term", "term": term})
        root.addChild(item)
        query_view._refresh_builder_labels(item)

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
