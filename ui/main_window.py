from PyQt5 import uic
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                             QScrollArea, QLabel, QPushButton, QHBoxLayout, 
                             QFileDialog, QProgressBar, QLineEdit, QCheckBox,
                             QSpinBox, QGroupBox, QTextEdit, QSplitter, QComboBox,
                             QListWidget, QListWidgetItem, QMessageBox)
from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect, QUrl)
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen, QBrush, QDragEnterEvent, QDropEvent

import re

from widgets.code_editor import TextDisplay
from widgets.search_table import SearchTable
from logic.search_manager import SearchManager
from logic.file_io import FileHandler
from index.file_indexer import FileIndexer
from logic.search_engine import ParallelSearchEngine, SearchEngineFactory
from dataform.search_result import SearchResult

import os
from functools import partial

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("log_ui.ui", self)

        self.search_table: SearchTable | None = None
        self.filter_engine: ParallelSearchEngine | None = None
        self.search_manager = SearchManager()
        self.file_handler = FileHandler()

        self.indexer = None   # 线程索引
        self.active_search_engines = []  # 跟踪活动的搜索引擎
        
        # 实时搜索相关
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._delayed_search)
        self.pending_search_params = None

        # 启用拖拽功能
        self.setAcceptDrops(True)

        self._bind_ui_actions()

    def closeEvent(self, event):
        """窗口关闭事件 - 确保所有线程正确停止"""
        # 停止搜索定时器
        if self.search_timer:
            self.search_timer.stop()
        
        # 停止索引线程
        if self.indexer and self.indexer.isRunning():
            self.indexer.quit()
            self.indexer.wait(3000)  # 等待最多3秒
        
        # 停止所有活动的搜索引擎
        for engine in self.active_search_engines[:]:  # 复制列表避免迭代时修改
            if engine and engine.isRunning():
                engine.stop_search()
                engine.quit()
                engine.wait(2000)  # 等待最多2秒
        
        # 停止所有编辑器中的搜索引擎
        for i in range(self.tabs.count()):
            editor = self.tabs.widget(i)
            if isinstance(editor, TextDisplay):
                if hasattr(editor, 'current_search_engine') and editor.current_search_engine:
                    if editor.current_search_engine.isRunning():
                        editor.current_search_engine.stop_search()
                        editor.current_search_engine.quit()
                        editor.current_search_engine.wait(2000)
                
                # 停止预加载线程
                if hasattr(editor, 'preload_thread') and editor.preload_thread:
                    if editor.preload_thread.isRunning():
                        editor.preload_thread.quit()
                        editor.preload_thread.wait(1000)
        
        # 清理资源
        self.active_search_engines.clear()
        
        # 接受关闭事件
        event.accept()
        super().closeEvent(event)

    def _bind_ui_actions(self):
        """绑定UI事件"""
        self.menu_open.triggered.connect(self._import_logs)
        self.menu_download.triggered.connect(self._download_results)
        self.apply.clicked.connect(self._apply_filters)
        self.reset_button.clicked.connect(self._reset_editor)
        self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
        self.norm_input.triggered.connect(self._input_regex_filter)
        
        # 绑定实时搜索
        self.only_match_check.stateChanged.connect(self._on_match_only_changed)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            # 检查是否包含支持的文件类型
            urls = event.mimeData().urls()
            for url in urls:
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if file_path.lower().endswith(('.log', '.txt')):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        """拖拽放下事件"""
        urls = event.mimeData().urls()
        for url in urls:
            if url.isLocalFile():
                file_path = url.toLocalFile()
                if file_path.lower().endswith(('.log', '.txt')):
                    self._load_file(file_path)
        event.acceptProposedAction()

    def _on_match_only_changed(self, state):
        """match_only选项变化时的处理"""
        if state == Qt.Checked:
            # 启用实时搜索模式
            self._trigger_realtime_search()
        else:
            # 禁用实时搜索，恢复完整显示
            self._restore_full_display()

    def _trigger_realtime_search(self):
        """触发实时搜索（带延迟防抖）"""
        editor = self._get_current_editor()
        if not editor:
            return

        # 获取当前搜索参数
        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]
        include_all, exclude_all = self._get_all_keys(include, exclude)

        if not include_all and not exclude_all:
            return

        # 保存搜索参数
        self.pending_search_params = {
            'editor': editor,
            'include_keywords': include_all,
            'exclude_keywords': exclude_all,
            'show_only': True,  # 实时搜索总是只显示匹配行
            'ignore_case': not self.Maxmi.isChecked(),
            'whole_pair': self.whole_pair_check.isChecked()
        }

        # 重启定时器（防抖）
        self.search_timer.stop()
        self.search_timer.start(300)  # 300ms延迟

    def _delayed_search(self):
        """延迟执行的搜索"""
        if self.pending_search_params:
            params = self.pending_search_params
            self._apply_search_to_editor_optimized(
                params['editor'],
                params['include_keywords'], 
                params['exclude_keywords'],
                params['show_only'],
                params['ignore_case'],
                params['whole_pair']
            )

    def _on_table_changed(self):
        """搜索表格变化时触发实时搜索"""
        if self.only_match_check.isChecked():
            self._trigger_realtime_search()

    def _restore_full_display(self):
        """恢复完整显示"""
        editor = self._get_current_editor()
        if editor:
            # 清除搜索结果和过滤
            editor.search_results_manager.clear_results()
            editor.filtered_line_numbers.clear()
            editor.set_display_mode("all")  # 切换回全部显示模式
            editor.update()

    def _reset_editor(self):
        """重置编辑器状态"""
        editor = self._get_current_editor()
        if editor:
            editor.search_results_manager.clear_results()
            editor.filtered_line_numbers.clear()
            editor.set_display_mode("all")
            editor.update()
        
        # 清除搜索输入
        self.in_word.clear()
        self.ex_word.clear()
        self.only_match_check.setChecked(False)
        self.Maxmi.setChecked(False)
        self.whole_pair_check.setChecked(False)
        self.all_page.setChecked(False)

        # 清除搜索表格
        if self.search_table:
            self.search_table.clear_table()

        self.status_label.setText("就绪")

    def _input_regex_filter(self):
        """输入正则过滤器"""
        editor = self._get_current_editor()
        if editor and self.search_table:
            self.search_table.add_regex_entry_from_user(self, editor)

    def _import_logs(self):
        """文件导入"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择日志文件", "", "Log Files (*.log *.txt);;All Files (*)"
        )

        if not files:
            return

        for filepath in files:
            self._load_file(filepath)

    def _load_file(self, filepath: str):
        """文件读取，获得大小和内容"""
        def get_size(filepath):
            file_size = os.path.getsize(filepath)
            size_mb = file_size / (1024 * 1024)
            return size_mb
        
        size_mb = get_size(filepath)
        self.status_label.setText(f"🔄 正在建立索引... 文件大小: {size_mb:.1f}MB")

        # 保存文件路径
        self._pending_file_path = filepath
        self.setIndexer(filepath, None)
        
    def setIndexer(self, file_path: str, text_widget: TextDisplay = None):
        """启动索引线程"""
        # 如果之前有索引线程在运行，先停止
        if self.indexer and self.indexer.isRunning():
            self.indexer.quit()
            self.indexer.wait(2000)
            
        self.indexer = FileIndexer(file_path)
        self.indexer.indexing_progress.connect(self.on_indexing_progress)
        self.indexer.indexing_finished.connect(self.on_indexing_finished)
        self.indexer.indexing_error.connect(self.on_indexing_error)
        self.indexer.start()
        
    def _get_all_keys(self, include, exclude):
        """获得考虑搜索记录逻辑后的所有过滤条件"""
        include_keys, exclude_keys = [], []
        if self.search_table:
            include_keys, exclude_keys = self.search_manager.get_keywords_from_table(self.search_table)

        include_all = list(set(include + include_keys))
        exclude_all = list(set(exclude + exclude_keys))

        return include_all, exclude_all

    def _apply_filters(self):
        """应用过滤"""
        editor = self._get_current_editor()
        if not editor:
            return

        # 参数准备
        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]
        show_only = self.only_match_check.isChecked()
        ignore_case = self.Maxmi.isChecked()
        whole_pair = self.whole_pair_check.isChecked()
        all_tabs = self.all_page.isChecked()
        include_all, exclude_all = self._get_all_keys(include, exclude)

        # 检查搜索前置条件
        if not self._check(editor, include_all, exclude_all):
            return

        self.status_label.setText("🔍 搜索中...")

        # 应用搜索到所有标签或当前标签
        if all_tabs:
            for i in range(self.tabs.count()):
                tab_editor = self.tabs.widget(i)
                if isinstance(tab_editor, TextDisplay):
                    self._apply_search_to_editor_optimized(
                        tab_editor, include_all, exclude_all, 
                        show_only, ignore_case, whole_pair
                    )
        else:
            self._apply_search_to_editor_optimized(
                editor, include_all, exclude_all, 
                show_only, ignore_case, whole_pair
            )

    def _apply_search_to_editor_optimized(self, editor: TextDisplay, include_keywords: list[str], 
                                        exclude_keywords: list[str], show_only: bool, 
                                        ignore_case: bool, whole_pair: bool):
        """
        优化的搜索应用 - 减少卡顿
        """
        
        # 如果已有搜索在进行，先停止
        if hasattr(editor, 'current_search_engine') and editor.current_search_engine:
            if editor.current_search_engine.isRunning():
                editor.current_search_engine.stop_search()
                # 从活动列表中移除
                if editor.current_search_engine in self.active_search_engines:
                    self.active_search_engines.remove(editor.current_search_engine)

        # 清除之前的搜索结果
        editor.search_results_manager.clear_results()
        
        # 如果是只显示匹配行模式，重置过滤状态
        if show_only:
            editor.filtered_line_numbers.clear()
            editor.set_display_mode("all")  # 先重置为全部显示
        
        # 创建优化的搜索引擎
        if show_only:
            # 实时搜索使用优化的引擎
            search_engine = SearchEngineFactory.create_realtime_engine(editor.file_path, editor.line_offsets)
        else:
            # 常规搜索使用标准引擎
            search_engine = SearchEngineFactory.create_standard_engine(editor.file_path, editor.line_offsets)
        
        # 配置搜索参数
        search_engine.setup_search(
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            case_sensitive=(not ignore_case),
            use_regex=False,
            whole_word_only=whole_pair,
            match_all_includes=True
        )
        
        # 连接信号
        search_engine.search_progress.connect(self.on_search_progress)
        search_engine.search_finished.connect(
            lambda total, elapsed: self.on_search_finished(total, elapsed, editor)
        )
        search_engine.search_error.connect(self.on_search_error)
        search_engine.search_result_found.connect(
            lambda result: self.on_search_result_found(result, editor, show_only)
        )
        
        # 保存搜索引擎引用
        editor.current_search_engine = search_engine
        self.active_search_engines.append(search_engine)  # 跟踪活动的搜索引擎
        
        # 启动搜索
        search_engine.start()

    def _display_results(self, results_count: int, pattern: str, desc: str, 
                        include_all: list[str], exclude_all: list[str]):
        """显示搜索结果"""
        if not self.search_table:
            self.search_table = SearchTable()
            layout = QVBoxLayout()
            self.search_info.setLayout(layout)
            layout.addWidget(self.search_table)
            
            # 连接表格变化事件到实时搜索
            self.search_table.checkbox_changed.connect(self._on_table_changed)
        
        # 格式化模式显示
        formatted_pattern = self.search_manager.format_pattern_display(include_all, exclude_all)
        
        self.search_table.table_add_row(results_count, include_all, exclude_all, desc)

    def _on_table_changed(self):
        """搜索表格变化时触发实时搜索"""
        if self.only_match_check.isChecked():
            self._trigger_realtime_search()

    def _get_current_editor(self) -> TextDisplay | None:
        """获得当前的tab"""
        editor = self.tabs.currentWidget()
        return editor if isinstance(editor, TextDisplay) else None

    def _download_results(self):
        """下载搜索结果"""
        editor = self._get_current_editor()
        if not editor:
            return

        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]

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

    def on_indexing_progress(self, lines, total_size):
        """索引进度更新"""
        self.status_label.setText(f"建立索引中... 已处理 {lines:,} 行")
        
    def on_indexing_finished(self, line_offsets):
        """索引建立完成"""
        text_widget = TextDisplay()
        filename = os.path.basename(self._pending_file_path)
        
        if text_widget.load_text(self.indexer.file_path, line_offsets):
            self.tabs.addTab(text_widget, filename)
            
            total_lines = len(line_offsets) - 1
            self.status_label.setText(f"文件加载完成 - {total_lines:,} 行")
        else:
            self.status_label.setText("文件加载失败")

    def on_indexing_error(self, error_msg):
        """索引错误处理"""
        self.status_label.setText(f"❌ 索引错误: {error_msg}")

    def _check(self, editor: TextDisplay, include_keywords: list[str], exclude_keywords: list[str]) -> bool:
        """检查搜索前置要求"""
        if not (include_keywords or exclude_keywords):
            QMessageBox.warning(self, "搜索警告", "请输入搜索内容！")
            return False
            
        if not editor.file_path:
            QMessageBox.warning(self, "搜索警告", "请先加载文件！")
            return False
        
        return True

    def on_search_result_found(self, result: SearchResult, editor: TextDisplay, show_only: bool = False):
        """处理找到的搜索结果"""
        # 将结果添加到对应编辑器的搜索结果管理器
        editor.search_results_manager.add_result(result)
        
        # 如果是只显示匹配行模式，更新过滤行列表
        if show_only:
            if not hasattr(editor, 'filtered_line_numbers'):
                editor.filtered_line_numbers = set()
            editor.filtered_line_numbers.add(result.line_number)
            
            # 更新显示模式
            editor.set_display_mode("filtered")
            
        # 如果是当前活动的编辑器，更新UI
        if editor == self._get_current_editor():
            editor.update()

    def on_search_finished(self, total_results: int, elapsed_time: float, editor: TextDisplay):
        """搜索完成处理"""
        self.status_label.setText(
            f"✅ 搜索完成！找到 {total_results} 个结果，耗时 {elapsed_time:.2f} 秒"
        )
        
        # 如果是只显示匹配行模式且有结果，切换到过滤模式
        if hasattr(editor, 'filtered_line_numbers') and editor.filtered_line_numbers:
            editor.set_display_mode("filtered")
        
        # 更新搜索结果显示
        self._update_search_results_display(editor, total_results)
        
        # 清理搜索引擎引用
        if hasattr(editor, 'current_search_engine'):
            # 从活动列表中移除
            if editor.current_search_engine in self.active_search_engines:
                self.active_search_engines.remove(editor.current_search_engine)
            delattr(editor, 'current_search_engine')

    def _update_search_results_display(self, editor: TextDisplay, total_results: int):
        """更新搜索结果显示"""
        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]
        include_all, exclude_all = self._get_all_keys(include, exclude)
        
        # 创建描述信息
        desc_parts = []
        if include_all:
            desc_parts.append(f"包含：{', '.join(include_all)}")
        if exclude_all:
            desc_parts.append(f"排除：{', '.join(exclude_all)}")
        
        description = "\n".join(desc_parts)
        
        # 更新搜索表格
        self._display_results(total_results, "搜索完成", description, include_all, exclude_all)

    def on_search_progress(self, progress: int, found_count: int):
        """搜索进度更新"""
        self.status_label.setText(f"🔍 搜索中... 进度 {progress}% - 已找到 {found_count} 个结果")

    def on_search_error(self, error_msg: str):
        """搜索错误处理"""
        self.status_label.setText(f"❌ 搜索错误: {error_msg}")
        QMessageBox.critical(self, "搜索错误", f"搜索过程中出现错误：\n{error_msg}")

    # 导航功能
    def navigate_to_previous_result(self):
        """导航到上一个搜索结果"""
        editor = self._get_current_editor()
        if editor:
            editor.search_results_manager.navigate_to_previous()
        
    def navigate_to_next_result(self):
        """导航到下一个搜索结果"""
        editor = self._get_current_editor()
        if editor:
            editor.search_results_manager.navigate_to_next()