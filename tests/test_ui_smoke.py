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

    # The Query view is the third tab.
    tabs = window.centralWidget()
    query_view = tabs.widget(2)
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
