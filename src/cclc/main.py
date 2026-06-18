"""Application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from cclc.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ELAN Corpus Label Comparer")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
