from PyQt5.QtWidgets import (
    QTableWidget, QAbstractItemView, QTableWidgetItem, QCheckBox,
    QWidget, QHBoxLayout, QHeaderView, QInputDialog
)
from PyQt5.QtCore import Qt
import re

class SearchTable(QTableWidget):
    def __init__(self):
        super().__init__()
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["✔", "Pattern", "Description", "Hits"])

        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)

    def table_add_row(self, hit_count: int, include_keywords: list[str], exclude_keywords: list[str], description: str = ""):
        row = self.rowCount()
        self.insertRow(row)

        pattern_str = self._format_expression(include_keywords, exclude_keywords)
        desc_str = self._format_description(include_keywords, exclude_keywords)
        # if description:
        #     desc_str += f"\n{description}"

        self._add_checkbox(row)
        self._add_item(row, 1, pattern_str)
        self._add_item(row, 2, desc_str)
        self._add_item(row, 3, str(hit_count), editable=False)
        self.resizeRowToContents(row)

    def _format_expression(self, include_keywords: list[str], exclude_keywords: list[str]) -> str:
        include_expr = " | ".join(f"'{kw}'" for kw in include_keywords) if include_keywords else ""
        exclude_expr = " &! (" + " | ".join(f"'{kw}'" for kw in exclude_keywords) + ")" if exclude_keywords else ""
        return f"({include_expr}) {exclude_expr}".strip()

    def _format_description(self, include_keywords: list[str], exclude_keywords: list[str]) -> str:
        include_desc = ", ".join(include_keywords)
        exclude_desc = ", ".join(exclude_keywords)
        return f"包含：{include_desc}\n排除：{exclude_desc}"

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

    def clear_table(self):
        self.setRowCount(0)

    def add_regex_entry_from_user(self, parent, editor):
        pattern, ok = QInputDialog.getText(parent, "正则输入", "请输入正则表达式：")
        if not ok or not pattern.strip():
            return

        try:
            regex = re.compile(pattern)
        except re.error as e:
            print(f"正则表达式错误: {e}")
            return

        matches = [line for line in editor.toPlainText().splitlines() if regex.search(line)]
        if not matches:
            print("无匹配结果")
            return

        self.table_add_row(
            hit_count=len(matches),
            include_keywords=[pattern],
            exclude_keywords=[],
            description="（来自正则输入）"
        )
