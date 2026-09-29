"""Окно истории диктовок."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import inserter

MODE_TITLES = {"clean": "чистовик", "raw": "как есть", "translate": "перевод"}


class HistoryDialog(QDialog):
    def __init__(self, history, parent=None) -> None:
        super().__init__(parent)
        self.history = history
        self.setWindowTitle("Шёпот — история")
        self.resize(640, 460)

        layout = QVBoxLayout(self)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_current)
        layout.addWidget(self.list, 3)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 2)

        buttons = QHBoxLayout()
        copy_button = QPushButton("Скопировать")
        copy_button.clicked.connect(self._copy)
        clear_button = QPushButton("Очистить историю")
        clear_button.clicked.connect(self._clear)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(copy_button)
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        self.preview.clear()
        for entry in self.history.items():
            when = entry.get("at", "").replace("T", " ")
            mode = MODE_TITLES.get(entry.get("mode", ""), entry.get("mode", ""))
            preview = " ".join(entry.get("text", "").split())[:70]
            item = QListWidgetItem(f"{when}  ·  {mode}  ·  {preview}")
            item.setData(256, entry.get("text", ""))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _show_current(self, row: int) -> None:
        item = self.list.item(row)
        self.preview.setPlainText(item.data(256) if item else "")

    def _copy(self) -> None:
        text = self.preview.toPlainText()
        if text:
            inserter.set_clipboard_text(text)

    def _clear(self) -> None:
        self.history.clear()
        self._reload()
