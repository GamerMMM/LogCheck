from PyQt5.QtWidgets import (
    QMainWindow, QFileDialog, QVBoxLayout
)
from PyQt5 import uic
from PyQt5.QtWidgets import QInputDialog
import re

from widgets.code_editor import CodeEditor
from widgets.search_table import SearchTable
from logic.filter_engine import FilterEngine
from logic.search_manager import SearchManager
from logic.file_io import FileHandler

import os

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("log_ui.ui", self)

        self.search_table: SearchTable | None = None
        self.filter_engine = FilterEngine()
        self.search_manager = SearchManager()
        self.file_handler = FileHandler()

        self._bind_ui_actions()

    def _bind_ui_actions(self):
        self.menu_open.triggered.connect(self._import_logs)
        self.menu_download.triggered.connect(self._download_results)
        self.apply.clicked.connect(self._apply_filters)
        self.reset_button.clicked.connect(self._reset_editor)
        self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
        self.norm_input.triggered.connect(self._input_regex_filter)

    def _reset_editor(self):
        editor = self._get_current_editor()
        if editor:
            editor.reset_text()
        
        self.in_word.clear()
        self.ex_word.clear()
        self.only_match_check.setChecked(False)
        self.Maxmi.setChecked(False)
        self.whole_pair_check.setChecked(False)
        self.all_page.setChecked(False)

        if self.search_table:
            self.search_table.clear_table()

    def _input_regex_filter(self):
        pattern, ok = QInputDialog.getText(self, "正则输入", "请输入正则表达式：")
        if not ok or not pattern.strip():
            return

        editor = self._get_current_editor()
        if not editor:
            return

        try:
            regex = re.compile(pattern)
        except re.error as e:
            print(f"正则表达式错误: {e}")
            return

        matches = []
        for line in editor.toPlainText().splitlines():
            if regex.search(line):
                matches.append(line.strip())

        if not matches:
            print("无匹配结果")
            return

        hint_count = len(matches)
        desc = f"包含：{pattern}\n排除："
        self._display_results(hint_count, pattern, desc)

    def _import_logs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择日志文件", "", "Log Files (*.log *.txt);;All Files (*)"
        )
        for filepath in files:
            self._add_log_tab(filepath)

    def _add_log_tab(self, filepath: str):
        content = self.file_handler.load_file(filepath)
        if content is None:
            return
        editor = CodeEditor()
        editor.setPlainText(content)
        editor.load_text(content)
        filename = os.path.basename(filepath)
        self.tabs.addTab(editor, filename)

    def _apply_filters(self):
        editor = self._get_current_editor()
        if not editor:
            return

        include = self.in_word.toPlainText().splitlines()
        exclude = self.ex_word.toPlainText().splitlines()
        show_only = self.only_match_check.isChecked()
        ignore_case = self.Maxmi.isChecked()
        whole_pair = self.whole_pair_check.isChecked()
        all_tabs = self.all_page.isChecked()

        include_keys, exclude_keys = [], []
        if self.search_table:
            include_keys, exclude_keys = self.search_manager.get_keywords_from_table(self.search_table)

        include_all = list(set(include + include_keys))
        exclude_all = list(set(exclude + exclude_keys))

        if all_tabs:
            for i in range(self.tabs.count()):
                editor = self.tabs.widget(i)
                if isinstance(editor, CodeEditor):
                    self.filter_engine.apply(editor, include_all, exclude_all, show_only, ignore_case, whole_pair)
        else:
            self.filter_engine.apply(editor, include_all, exclude_all, show_only, ignore_case, whole_pair)

        hints, pattern, desc = editor.get_search_res(   # 包括“包含”
            self.filter_engine.get_regex(include_all),
            self.filter_engine.get_regex(exclude_all)
        )
        print(f"hints: {hints}, pattern: {pattern}, desc: {desc}")
        self._display_results(hints, pattern, desc,include_all,exclude_all)

    def _display_results(self, hints, pattern, desc,include_all,exclude_all):
        if not self.search_table:
            self.search_table = SearchTable()
            layout = QVBoxLayout()
            self.search_info.setLayout(layout)
            layout.addWidget(self.search_table)
        self.search_table.table_add_row(hints,include_all,exclude_all, desc)

    def _reset_editor(self):
        editor = self._get_current_editor()
        if editor:
            editor.reset_text()

    def _get_current_editor(self) -> CodeEditor | None:
        editor = self.tabs.currentWidget()
        return editor if isinstance(editor, CodeEditor) else None

    def _download_results(self):
        editor = self._get_current_editor()
        if not editor:
            return

        include = self.in_word.toPlainText().splitlines()
        exclude = self.ex_word.toPlainText().splitlines()

        include_keys, exclude_keys = [], []
        if self.search_table:
            include_keys, exclude_keys = self.search_manager.get_keywords_from_table(self.search_table)

        include_all = list(set(include + include_keys))
        exclude_all = list(set(exclude + exclude_keys))

        show_only = self.only_match_check.isChecked()
        ignore_case = self.Maxmi.isChecked()
        whole_pair = self.whole_pair_check.isChecked()

        self.file_handler.save_filtered_result(
            editor, include_all, exclude_all,
            show_only, ignore_case, whole_pair,
            self.tabs.tabText(self.tabs.currentIndex())
        )
