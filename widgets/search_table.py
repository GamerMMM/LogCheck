from PyQt5.QtWidgets import (
    QTableWidget, QAbstractItemView, QTableWidgetItem, QCheckBox,
    QWidget, QHBoxLayout, QHeaderView, QInputDialog
)
from PyQt5.QtCore import Qt, pyqtSignal
import re

class SearchTable(QTableWidget):
    """
    优化的搜索表格 - 支持复选框状态变化信号
    """
    
    # 新增信号：当复选框状态变化时发出
    checkbox_changed = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["✔", "Pattern", "Description", "Hits"])

        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)

    def table_add_row(self, hit_count: int, include_keywords: list[str], exclude_keywords: list[str], description: str = ""):
        """
        添加搜索结果行
        
        Args:
            hit_count: 匹配数量
            include_keywords: 包含关键词列表
            exclude_keywords: 排除关键词列表
            description: 额外描述
        """
        row = self.rowCount()
        self.insertRow(row)

        pattern_str = self._format_expression(include_keywords, exclude_keywords)
        desc_str = self._format_description(include_keywords, exclude_keywords)

        self._add_checkbox(row)
        self._add_item(row, 1, pattern_str)
        self._add_item(row, 2, desc_str)
        self._add_item(row, 3, str(hit_count), editable=False)
        self.resizeRowToContents(row)

    def _format_expression(self, include_keywords: list[str], exclude_keywords: list[str]) -> str:
        """
        格式化搜索表达式显示
        
        只显示被勾选的条件
        """
        parts = []
        
        if include_keywords:
            include_expr = " | ".join(f"'{kw}'" for kw in include_keywords)
            parts.append(f"({include_expr})")
        
        if exclude_keywords:
            exclude_expr = " | ".join(f"'{kw}'" for kw in exclude_keywords)
            parts.append(f"&! ({exclude_expr})")
        
        return " ".join(parts) if parts else "无条件"

    def _format_description(self, include_keywords: list[str], exclude_keywords: list[str]) -> str:
        """格式化描述信息"""
        include_desc = ", ".join(include_keywords) if include_keywords else "无"
        exclude_desc = ", ".join(exclude_keywords) if exclude_keywords else "无"
        return f"包含：{include_desc}\n排除：{exclude_desc}"

    def _add_checkbox(self, row: int):
        """添加复选框到指定行"""
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
        
        # 连接复选框状态变化信号
        checkbox.stateChanged.connect(lambda state, r=row: self._on_checkbox_changed(r, state))

    def _add_item(self, row: int, col: int, text: str, editable=True):
        """添加表格项"""
        item = QTableWidgetItem(text)
        if not editable:
            item.setFlags(Qt.ItemIsEnabled)
        self.setItem(row, col, item)

    def _on_checkbox_changed(self, row: int, state: int):
        """
        复选框状态变化处理
        
        Args:
            row: 行号
            state: 复选框状态
        """
        checked = state == Qt.Checked
        print(f"行 {row} 的选择状态变为: {'选中' if checked else '未选中'}")
        
        # 发出信号通知状态变化
        self.checkbox_changed.emit()
        
        # 更新表达式显示
        self._update_pattern_display()

    def _update_pattern_display(self):
        """
        更新所有行的表达式显示，只显示被勾选的条件
        """
        for row in range(self.rowCount()):
            checkbox_widget = self.cellWidget(row, 0)
            if checkbox_widget and hasattr(checkbox_widget, 'checkbox'):
                is_checked = checkbox_widget.checkbox.isChecked()
                
                # 获取原始描述信息
                desc_item = self.item(row, 2)
                if desc_item:
                    desc_text = desc_item.text()
                    
                    # 解析包含和排除关键词
                    include_keywords = []
                    exclude_keywords = []
                    
                    include_match = re.search(r"包含：(.*?)\n", desc_text)
                    exclude_match = re.search(r"排除：(.*)", desc_text)
                    
                    if include_match:
                        include_text = include_match.group(1).strip()
                        if include_text and include_text != "无":
                            include_keywords = [kw.strip() for kw in include_text.split(',')]
                    
                    if exclude_match:
                        exclude_text = exclude_match.group(1).strip()
                        if exclude_text and exclude_text != "无":
                            exclude_keywords = [kw.strip() for kw in exclude_text.split(',')]
                    
                    # 根据勾选状态更新表达式
                    if is_checked:
                        pattern_str = self._format_expression(include_keywords, exclude_keywords)
                    else:
                        pattern_str = "[未选中]"
                    
                    # 更新表达式列
                    pattern_item = self.item(row, 1)
                    if pattern_item:
                        pattern_item.setText(pattern_str)

    def get_checked_rows_data(self) -> list[dict]:
        """
        获取所有被勾选行的数据
        
        Returns:
            包含勾选行数据的列表
        """
        checked_rows = []
        
        for row in range(self.rowCount()):
            checkbox_widget = self.cellWidget(row, 0)
            if checkbox_widget and hasattr(checkbox_widget, 'checkbox') and checkbox_widget.checkbox.isChecked():
                # 获取行数据
                pattern_item = self.item(row, 1)
                desc_item = self.item(row, 2)
                hits_item = self.item(row, 3)
                
                row_data = {
                    'pattern': pattern_item.text() if pattern_item else "",
                    'description': desc_item.text() if desc_item else "",
                    'hits': hits_item.text() if hits_item else "0"
                }
                
                # 解析关键词
                if desc_item:
                    desc_text = desc_item.text()
                    include_keywords = []
                    exclude_keywords = []
                    
                    include_match = re.search(r"包含：(.*?)\n", desc_text)
                    exclude_match = re.search(r"排除：(.*)", desc_text)
                    
                    if include_match:
                        include_text = include_match.group(1).strip()
                        if include_text and include_text != "无":
                            include_keywords = [kw.strip() for kw in include_text.split(',')]
                    
                    if exclude_match:
                        exclude_text = exclude_match.group(1).strip()
                        if exclude_text and exclude_text != "无":
                            exclude_keywords = [kw.strip() for kw in exclude_text.split(',')]
                    
                    row_data['include_keywords'] = include_keywords
                    row_data['exclude_keywords'] = exclude_keywords
                
                checked_rows.append(row_data)
        
        return checked_rows

    def clear_table(self):
        """清空表格"""
        self.setRowCount(0)

    def add_regex_entry_from_user(self, parent, editor):
        """
        从用户输入添加正则表达式条目
        
        Args:
            parent: 父窗口
            editor: 文本编辑器实例
        """
        pattern, ok = QInputDialog.getText(parent, "正则输入", "请输入正则表达式：")
        if not ok or not pattern.strip():
            return

        try:
            regex = re.compile(pattern)
        except re.error as e:
            print(f"正则表达式错误: {e}")
            return

        # 这里需要根据实际的编辑器接口来获取文本内容
        # 由于TextDisplay没有toPlainText方法，需要另想办法
        # 暂时跳过实际匹配，直接添加条目
        
        self.table_add_row(
            hit_count=0,  # 暂时设为0，实际应该进行搜索
            include_keywords=[pattern],
            exclude_keywords=[],
            description="（来自正则输入）"
        )

    def set_all_checked(self, checked: bool):
        """
        设置所有行的复选框状态
        
        Args:
            checked: True为全选，False为全不选
        """
        for row in range(self.rowCount()):
            checkbox_widget = self.cellWidget(row, 0)
            if checkbox_widget and hasattr(checkbox_widget, 'checkbox'):
                checkbox_widget.checkbox.setChecked(checked)

    def get_checked_count(self) -> int:
        """获取被勾选的行数"""
        count = 0
        for row in range(self.rowCount()):
            checkbox_widget = self.cellWidget(row, 0)
            if checkbox_widget and hasattr(checkbox_widget, 'checkbox') and checkbox_widget.checkbox.isChecked():
                count += 1
        return count