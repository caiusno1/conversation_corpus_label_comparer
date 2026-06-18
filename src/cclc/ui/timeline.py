"""A small custom-painted timeline excerpt for one query instance."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


@dataclass
class Box:
    tier: str
    label: str
    start: int
    end: int
    highlight: bool


class TimelineWidget(QWidget):
    """Paints one row per visible tier with annotation boxes for an instance."""

    ROW_H = 34
    LABEL_W = 120
    PAD_MS = 2000

    def __init__(self) -> None:
        super().__init__()
        self._tiers: list[str] = []
        self._boxes: list[Box] = []
        self._win = (0, 1)
        self.setMinimumHeight(80)

    def set_data(self, tiers: list[str], boxes: list[Box], window: tuple[int, int]) -> None:
        self._tiers = tiers
        self._boxes = boxes
        lo, hi = window
        if hi <= lo:
            hi = lo + 1
        self._win = (lo, hi)
        self.setMinimumHeight(max(80, self.ROW_H * len(tiers) + 20))
        self.update()

    def clear(self) -> None:
        self._tiers = []
        self._boxes = []
        self.update()

    def _x(self, t: int) -> float:
        lo, hi = self._win
        usable = max(1, self.width() - self.LABEL_W - 10)
        return self.LABEL_W + (t - lo) / (hi - lo) * usable

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if not self._tiers:
            painter.setPen(QColor("#888"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Run a query and select an instance")
            return

        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        row_of = {tier: i for i, tier in enumerate(self._tiers)}
        for tier, i in row_of.items():
            y = i * self.ROW_H + 4
            painter.setPen(QColor("#333"))
            painter.drawText(QRectF(2, y, self.LABEL_W - 6, self.ROW_H - 8),
                             Qt.AlignVCenter | Qt.AlignLeft, tier)
            painter.setPen(QPen(QColor("#eee")))
            painter.drawLine(self.LABEL_W, int(y + self.ROW_H / 2),
                             self.width(), int(y + self.ROW_H / 2))

        for box in self._boxes:
            if box.tier not in row_of:
                continue
            y = row_of[box.tier] * self.ROW_H + 8
            x0 = self._x(box.start)
            x1 = max(x0 + 3, self._x(box.end))
            rect = QRectF(x0, y, x1 - x0, self.ROW_H - 16)
            if box.highlight:
                painter.setBrush(QBrush(QColor("#ffd24d")))
                painter.setPen(QPen(QColor("#c89000")))
            else:
                painter.setBrush(QBrush(QColor("#cfe3ff")))
                painter.setPen(QPen(QColor("#7aa8e0")))
            painter.drawRect(rect)
            painter.setPen(QColor("#222"))
            painter.drawText(rect, Qt.AlignCenter, box.label)
