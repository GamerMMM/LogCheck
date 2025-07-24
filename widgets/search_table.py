from PyQt5.QtWidgets import (
    QTableWidget, QAbstractItemView, QTableWidgetItem, QCheckBox,
    QWidget, QHBoxLayout, QHeaderView
)
from PyQt5.QtCore import Qt

class SearchTable(QTableWidget):
    def __init__(self):
        super().__init__()
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["✔", "Pattern", "Description", "Hits"])

        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)

    def table_add_row(self, hit_count: int, include_keywords: list[str], exclude_keywords: list[str], description: str = ""):
    # def table_add_row(self, hit_count: int, pattern_str: list[str], description: str = ""):
        row = self.rowCount()
        self.insertRow(row)

        # 构建逻辑表达式格式
        print(f"include_keywords: {include_keywords}, exclude_keywords: {exclude_keywords}")
        print(f"description: {description}")
        pattern_str = self._format_expression(include_keywords, exclude_keywords)

        self._add_checkbox(row)
        self._add_item(row, 1, pattern_str)
        self._add_item(row, 2, description)
        self._add_item(row, 3, str(hit_count), editable=False)
        self.resizeRowToContents(row)

    def _format_expression(self, include_keywords: list[str], exclude_keywords: list[str]) -> str:
        include_expr = " | ".join(f"'{kw}'" for kw in include_keywords) if include_keywords else ""
        exclude_expr = " &! (" + " | ".join(f"'{kw}'" for kw in exclude_keywords) + ")" if exclude_keywords else ""
        return f"({include_expr}) {exclude_expr}".strip()

    def _add_checkbox(self, row: int):
        widget = QWidget()
        checkbox = QCheckBox()
        checkbox.setCheckState(Qt.Checked)
        layout = QHBoxLayout(widget)
        layout.addWidget(checkbox)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)
        widget.checkbox = checkbox
        self.setCellWidget(row, 0, widget)
        checkbox.stateChanged.connect(lambda state: self._on_checkbox_changed(row, state))

    def _add_item(self, row: int, col: int, text: str, editable=True):
        item = QTableWidgetItem(text)
        if not editable:
            item.setFlags(Qt.ItemIsEnabled)
        self.setItem(row, col, item)

    def _on_checkbox_changed(self, row: int, state: int):
        checked = state == Qt.Checked
        print(f"行 {row} 的选择状态变为: {'选中' if checked else '未选中'}")
