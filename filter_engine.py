import re

from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                             QScrollArea, QLabel, QPushButton, QHBoxLayout, 
                             QFileDialog, QProgressBar, QLineEdit, QCheckBox,
                             QSpinBox, QGroupBox, QTextEdit, QSplitter, QComboBox,
                             QListWidget, QListWidgetItem, QMessageBox)
from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect)
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen, QBrush

from widgets.code_editor import TextDisplay
from logic.search_engine import ParallelSearchEngine

class FilterEngine:
    def __init__(self):
        pass

    def check(self, editor: TextDisplay, include_keywords: list[str], exclude_keywords: list[str]):
        """检查搜索前置要求（是否上传文件，是否输入搜索内容）"""
        def connect_sigs():
            # 连接信号
            self.search_engine.search_progress.connect(self.on_search_progress)
            self.search_engine.search_result_found.connect(self.on_search_result_found)
            self.search_engine.search_finished.connect(self.on_search_finished)
            self.search_engine.search_error.connect(self.on_search_error)

        if not (include_keywords or exclude_keywords):
            QMessageBox.warning(self, "搜索警告", "请输入搜索内容！")
            return False
            
        if not editor.file_path:
            QMessageBox.warning(self, "搜索警告", "请先加载文件！")
            return False
        
        return True

    def apply(self, editor: TextDisplay, include_keywords: list[str], exclude_keywords: list[str],
              show_only: bool, ignore_alpha: bool, whole_pair: bool):
        """开始搜索"""
        
        # 创建搜索引擎
        self.search_engine = ParallelSearchEngine(
            editor.file_path, 
            editor.line_offsets
        )

        def connect_sigs():
            # 连接信号
            self.search_engine.search_progress.connect(self.on_search_progress)
            self.search_engine.search_result_found.connect(self.on_search_result_found)
            self.search_engine.search_finished.connect(self.on_search_finished)
            self.search_engine.search_error.connect(self.on_search_error)
        
        # 配置搜索参数
        self.search_engine.setup_search(
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            case_sensitive=(not ignore_alpha),
            use_regex=False,
            whole_word_only=whole_pair,
            match_all_includes=True
        )
        
        connect_sigs()  # 连接信号      
        
        # 启动搜索
        self.search_engine.start()

    def get_pattern(self, include_keywords, exclude_keywords,
                              show_only_matches=False,
                              ignore_alpha=True,
                              whole_pair=False):
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.ignore_alpha = ignore_alpha
        self.whole_pair = whole_pair

        self.clear_highlights()

        flags = 0 if ignore_alpha else re.IGNORECASE
        self.results = [line for line in self.original_lines if self.is_line_valid(line, flags)]

        display_lines = self.results if show_only_matches else self.original_lines
        self.setPlainText("\n".join(display_lines))


    def get_regex(self, keywords: list[str]) -> str:
        if not keywords:
            return ""
        pattern = "|".join(re.escape(kw) for kw in keywords)
        return pattern
