from PyQt5 import uic
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                             QScrollArea, QLabel, QPushButton, QHBoxLayout, 
                             QFileDialog, QProgressBar, QLineEdit, QCheckBox,
                             QSpinBox, QGroupBox, QTextEdit, QSplitter, QComboBox,
                             QListWidget, QListWidgetItem, QMessageBox)
from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect, QUrl)
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen, QBrush, QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QDialog, QInputDialog

import re

from widgets.code_editor import TextDisplay
from widgets.search_table import SearchTable
from logic.search_manager import SearchManager
from logic.file_io import FileHandler
from index.file_indexer import FileIndexer
# 更新导入 - 使用新的高性能搜索引擎
from logic.search_engine import HighPerformanceSearchEngine, RealTimeSearchEngine, SearchEngineFactory, OptimizedPatternMatcher
from dataform.search_result import SearchResult

import os
from functools import partial

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("log_ui.ui", self)

        self.search_table: SearchTable | None = None
        self.filter_engine: HighPerformanceSearchEngine | None = None  # 更新类型注解
        self.search_manager = SearchManager()
        self.file_handler = FileHandler()

        self.indexer = None   # 线程索引
        self.active_search_engines = []  # 跟踪活动的搜索引擎
        
        # 实时搜索相关 - 优化参数
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._delayed_search)
        self.pending_search_params = None
        
        # 搜索性能统计
        self.search_stats = {}

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
        
        # 停止所有活动的搜索引擎 - 改进停止逻辑
        for engine in self.active_search_engines[:]:  # 复制列表避免迭代时修改
            if engine and engine.isRunning():
                try:
                    engine.stop_search()  # 使用新的停止方法
                    if not engine.wait(3000):  # 等待最多3秒
                        print(f"搜索引擎未能正常停止: {type(engine).__name__}")
                except Exception as e:
                    print(f"停止搜索引擎时出错: {e}")
        
        # 停止所有编辑器中的搜索引擎
        for i in range(self.tabs.count()):
            editor = self.tabs.widget(i)
            if isinstance(editor, TextDisplay):
                if hasattr(editor, 'current_search_engine') and editor.current_search_engine:
                    if editor.current_search_engine.isRunning():
                        try:
                            editor.current_search_engine.stop_search()
                            editor.current_search_engine.wait(2000)
                        except Exception as e:
                            print(f"停止编辑器搜索引擎时出错: {e}")
                
                # 停止预加载线程
                if hasattr(editor, 'preload_thread') and editor.preload_thread:
                    if editor.preload_thread.isRunning():
                        editor.preload_thread.quit()
                        editor.preload_thread.wait(1000)
        
        # 清理资源
        self.active_search_engines.clear()
        self.search_stats.clear()
        
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
        
        # 字体缩放快捷键 (如果UI中有这些菜单项的话)
        # self.zoom_in_action.triggered.connect(self._zoom_in_current_editor)
        # self.zoom_out_action.triggered.connect(self._zoom_out_current_editor)
        # self.reset_zoom_action.triggered.connect(self._reset_zoom_current_editor)

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
        """触发实时搜索（带延迟防抖）- 改进搜索连续性"""
        editor = self._get_current_editor()
        if not editor:
            return

        # 获取当前搜索参数
        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]
        include_all, exclude_all = self._get_all_keys(include, exclude)

        # 如果没有搜索条件，清除过滤显示
        if not include_all and not exclude_all:
            self._restore_full_display()
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

        # 重启定时器（防抖），缩短延迟提高响应性
        self.search_timer.stop()
        self.search_timer.start(100)  # 进一步减少到100ms延迟

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
            editor.set_filter_mode(False)
            editor.update()

    def _reset_editor(self):
        """重置编辑器状态"""
        editor = self._get_current_editor()
        if editor:
            editor.search_results_manager.clear_results()
            editor.set_filter_mode(False)
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
        
        # 清除性能统计
        self.search_stats.clear()

    def _input_regex_filter(self):
        """输入正则过滤器 - 简化版本，直接连接到搜索"""
        editor = self._get_current_editor()
        if not editor:
            QMessageBox.warning(self, "输入警告", "请先加载文件！")
            return
        
        # 使用简化的输入对话框
        regex_pattern, ok = QInputDialog.getMultiLineText(
            self, 
            "输入正则表达式", 
            "请输入正则表达式模式：\n\n示例：\n• \\d{4}-\\d{2}-\\d{2} (日期格式)\n• ERROR|WARN|FATAL (日志级别)\n• \\w+@\\w+\\.\\w+ (邮箱格式)",
            ""
        )
        
        if not ok or not regex_pattern.strip():
            return
        
        # 询问搜索类型
        search_types = ["包含匹配", "排除匹配"]
        search_type, ok = QInputDialog.getItem(
            self, "选择搜索类型", "请选择搜索类型：", search_types, 0, False
        )
        
        if not ok:
            return
        
        # 验证正则表达式
        try:
            re.compile(regex_pattern)
        except re.error as e:
            QMessageBox.critical(self, "正则表达式错误", f"正则表达式语法错误：\n{e}")
            return
        
        # 将正则表达式添加到相应的输入框
        if search_type == "包含匹配":
            current_text = self.in_word.toPlainText()
            if current_text.strip():
                new_text = current_text + '\n' + regex_pattern
            else:
                new_text = regex_pattern
            self.in_word.setPlainText(new_text)
        else:  # 排除匹配
            current_text = self.ex_word.toPlainText()
            if current_text.strip():
                new_text = current_text + '\n' + regex_pattern
            else:
                new_text = regex_pattern
            self.ex_word.setPlainText(new_text)
        
        # 询问是否立即搜索
        reply = QMessageBox.question(
            self, "立即搜索", 
            "是否立即使用正则表达式进行搜索？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            # 直接调用现有的应用过滤方法，但启用正则表达式
            self._apply_regex_search()


    def _apply_regex_search(self):
        """应用正则表达式搜索 - 与GUI相同的工作流程"""
        editor = self._get_current_editor()
        if not editor:
            return

        # 获取搜索参数 - 与 _apply_filters 相同的逻辑
        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]
        show_only = self.only_match_check.isChecked()
        ignore_case = not self.Maxmi.isChecked()  # 注意：这里是取反
        whole_pair = self.whole_pair_check.isChecked()
        all_tabs = self.all_page.isChecked()
        
        # 获取所有关键词（包括表格中的）
        include_all, exclude_all = self._get_all_keys(include, exclude)

        # 检查搜索前置条件
        if not self._check(editor, include_all, exclude_all):
            return

        # 设置状态
        self.status_label.setText("🔍 正则表达式搜索中...")

        # 应用搜索到所有标签或当前标签 - 启用正则表达式模式
        if all_tabs:
            for i in range(self.tabs.count()):
                tab_editor = self.tabs.widget(i)
                if isinstance(tab_editor, TextDisplay):
                    self._apply_regex_search_to_editor(
                        tab_editor, include_all, exclude_all, 
                        show_only, ignore_case, whole_pair
                    )
        else:
            self._apply_regex_search_to_editor(
                editor, include_all, exclude_all, 
                show_only, ignore_case, whole_pair
            )

    def _apply_regex_search_to_editor(self, editor: TextDisplay, include_keywords: list[str], 
                                    exclude_keywords: list[str], show_only: bool, 
                                    ignore_case: bool, whole_pair: bool):
        """
        应用正则表达式搜索到编辑器 - 基于现有的搜索引擎架构
        """
        
        # 停止现有搜索
        if hasattr(editor, 'current_search_engine') and editor.current_search_engine:
            if editor.current_search_engine.isRunning():
                editor.current_search_engine.stop_search()
                if not editor.current_search_engine.wait(1000):
                    print(f"警告: 正则搜索引擎未能及时停止")
                if editor.current_search_engine in self.active_search_engines:
                    self.active_search_engines.remove(editor.current_search_engine)

        # 清除之前的搜索结果
        editor.search_results_manager.clear_results()
        
        if show_only:
            editor.set_filter_mode(False)
        
        # 创建搜索引擎 - 选择合适的引擎类型
        total_lines = len(editor.line_offsets) - 1
        
        if show_only:
            search_engine = SearchEngineFactory.create_realtime_engine(editor.file_path, editor.line_offsets)
            if total_lines > 500000:
                search_engine.setup_realtime_search(max_results=100, sampling_ratio=0.05)
            elif total_lines > 100000:
                search_engine.setup_realtime_search(max_results=200, sampling_ratio=0.1)
            else:
                search_engine.setup_realtime_search(max_results=500, sampling_ratio=0.2)
        else:
            search_engine = SearchEngineFactory.auto_select_engine(
                editor.file_path, editor.line_offsets, "auto"
            )
        
        # 🔥 配置正则表达式搜索参数 - 关键改动：启用正则表达式
        max_results = 50000 if not show_only else (100 if total_lines > 500000 else 500)
        
        search_engine.setup_search(
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            case_sensitive=(not ignore_case),
            use_regex=True,  # 🔥 启用正则表达式模式
            whole_word_only=whole_pair,
            match_all_includes=True,
            max_results=max_results
        )
        
        # 连接信号 - 与普通搜索相同
        search_engine.search_progress.connect(self.on_search_progress)
        search_engine.search_finished.connect(
            lambda total, elapsed: self.on_regex_search_finished(total, elapsed, editor, show_only)
        )
        search_engine.search_error.connect(self.on_search_error)
        search_engine.search_result_found.connect(
            lambda result: self.on_search_result_found(result, editor, show_only)
        )
        
        if hasattr(search_engine, 'search_stats'):
            search_engine.search_stats.connect(
                lambda stats: self.on_search_stats_updated(stats, editor)
            )
        
        # 保存搜索引擎引用
        editor.current_search_engine = search_engine
        self.active_search_engines.append(search_engine)
        
        # 启动搜索
        search_engine.start()
        
        # 记录搜索统计
        import time
        self.search_stats[id(search_engine)] = {
            'start_time': time.time(),
            'editor': editor,
            'total_lines': total_lines,
            'search_type': 'regex_realtime' if show_only else 'regex_full'
        }

    def on_regex_search_finished(self, total_results: int, elapsed_time: float, 
                            editor: TextDisplay, show_only: bool = False):
        """正则表达式搜索完成处理"""
        
        # 获取性能统计
        engine_id = None
        if hasattr(editor, 'current_search_engine'):
            engine_id = id(editor.current_search_engine)
        
        performance_info = ""
        if engine_id in self.search_stats:
            stats = self.search_stats[engine_id]
            total_lines = stats['total_lines']
            search_type = stats['search_type']
            
            if elapsed_time > 0:
                throughput = total_lines / elapsed_time
                performance_info = f" | {throughput:.0f} 行/秒"
            
            del self.search_stats[engine_id]
        
        # 更新状态显示
        if self.all_page.isChecked():
            total_all_tabs = 0
            for i in range(self.tabs.count()):
                tab_editor = self.tabs.widget(i)
                if isinstance(tab_editor, TextDisplay):
                    with QMutexLocker(tab_editor.search_results_manager.results_mutex):
                        total_all_tabs += len(tab_editor.search_results_manager.results)
                    
                    if show_only and len(tab_editor.search_results_manager.results) > 0:
                        matching_lines = []
                        with QMutexLocker(tab_editor.search_results_manager.results_mutex):
                            for result in tab_editor.search_results_manager.results:
                                if result.line_number not in matching_lines:
                                    matching_lines.append(result.line_number)
                        
                        if matching_lines:
                            tab_editor.set_filter_mode(True, matching_lines)
            
            self.status_label.setText(
                f"✅ 正则表达式全标签搜索完成！总共找到 {total_all_tabs} 个结果，"
                f"耗时 {elapsed_time:.2f} 秒{performance_info}"
            )
        else:
            status_msg = f"✅ 正则表达式搜索完成！找到 {total_results} 个结果，耗时 {elapsed_time:.2f} 秒{performance_info}"
            self.status_label.setText(status_msg)
        
        # 应用过滤模式
        if show_only and total_results > 0 and not self.all_page.isChecked():
            matching_lines = []
            with QMutexLocker(editor.search_results_manager.results_mutex):
                for result in editor.search_results_manager.results:
                    if result.line_number not in matching_lines:
                        matching_lines.append(result.line_number)
            
            if matching_lines:
                editor.set_filter_mode(True, matching_lines)
        
        # 更新搜索结果显示
        self._update_regex_search_results_display(editor, total_results)
        
        # 清理搜索引擎引用
        if hasattr(editor, 'current_search_engine'):
            if editor.current_search_engine in self.active_search_engines:
                self.active_search_engines.remove(editor.current_search_engine)
            editor.current_search_engine = None

    def _update_regex_search_results_display(self, editor: TextDisplay, total_results: int):
        """更新正则表达式搜索结果显示"""
        include = [line.strip() for line in self.in_word.toPlainText().splitlines() if line.strip()]
        exclude = [line.strip() for line in self.ex_word.toPlainText().splitlines() if line.strip()]
        include_all, exclude_all = self._get_all_keys(include, exclude)
        
        # 创建描述信息，标明是正则表达式搜索
        desc_parts = []
        if include_all:
            desc_parts.append(f"正则包含：{', '.join(include_all)}")
        if exclude_all:
            desc_parts.append(f"正则排除：{', '.join(exclude_all)}")
        
        description = "\n".join(desc_parts)
        
        # 更新搜索表格
        self._display_results(total_results, "正则搜索完成", description, include_all, exclude_all)


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
        优化的搜索应用 - 使用新的高性能搜索引擎
        """
        
        # 如果已有搜索在进行，先彻底停止
        if hasattr(editor, 'current_search_engine') and editor.current_search_engine:
            if editor.current_search_engine.isRunning():
                editor.current_search_engine.stop_search()
                # 强制等待停止，避免重复搜索
                if not editor.current_search_engine.wait(1000):
                    print(f"警告: 搜索引擎未能及时停止")
                # 从活动列表中移除
                if editor.current_search_engine in self.active_search_engines:
                    self.active_search_engines.remove(editor.current_search_engine)

        # 清除之前的搜索结果
        editor.search_results_manager.clear_results()
        
        # 如果是只显示匹配行模式，重置过滤状态
        if show_only:
            editor.set_filter_mode(False)  # 先重置为全部显示
        
        # 🎯 使用新的搜索引擎工厂创建最佳引擎
        total_lines = len(editor.line_offsets) - 1
        
        if show_only:
            # 实时搜索模式 - 使用实时引擎
            search_engine = SearchEngineFactory.create_realtime_engine(editor.file_path, editor.line_offsets)
            # 根据文件大小调整实时搜索参数
            if total_lines > 500000:  # 大文件
                search_engine.setup_realtime_search(max_results=100, sampling_ratio=0.05)
            elif total_lines > 100000:  # 中等文件
                search_engine.setup_realtime_search(max_results=200, sampling_ratio=0.1)
            else:  # 小文件
                search_engine.setup_realtime_search(max_results=500, sampling_ratio=0.2)
        else:
            # 完整搜索模式 - 自动选择最佳引擎
            search_engine = SearchEngineFactory.auto_select_engine(
                editor.file_path, editor.line_offsets, "auto"
            )
        
        # 🔧 配置搜索参数 - 使用新的setup_search方法
        max_results = 50000 if not show_only else (100 if total_lines > 500000 else 500)
        
        search_engine.setup_search(
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            case_sensitive=(not ignore_case),
            use_regex=False,
            whole_word_only=whole_pair,
            match_all_includes=True,  # 确保必须匹配所有包含词
            max_results=max_results
        )
        
        # 🔗 连接信号 - 包括新的统计信号
        search_engine.search_progress.connect(self.on_search_progress)
        search_engine.search_finished.connect(
            lambda total, elapsed: self.on_search_finished(total, elapsed, editor, show_only)
        )
        search_engine.search_error.connect(self.on_search_error)
        search_engine.search_result_found.connect(
            lambda result: self.on_search_result_found(result, editor, show_only)
        )
        
        # 连接新的统计信号（如果存在）
        if hasattr(search_engine, 'search_stats'):
            search_engine.search_stats.connect(
                lambda stats: self.on_search_stats_updated(stats, editor)
            )
        
        # 保存搜索引擎引用
        editor.current_search_engine = search_engine
        self.active_search_engines.append(search_engine)  # 跟踪活动的搜索引擎
        
        # 🚀 启动搜索
        search_engine.start()
        
        # 记录搜索开始时间用于性能统计
        import time
        self.search_stats[id(search_engine)] = {
            'start_time': time.time(),
            'editor': editor,
            'total_lines': total_lines,
            'search_type': 'realtime' if show_only else 'full'
        }

    def on_search_stats_updated(self, stats, editor: TextDisplay):
        """处理搜索统计信息更新 - 新增方法"""
        if hasattr(stats, 'throughput') and stats.throughput > 0:
            # 更新状态栏显示吞吐量信息
            throughput_info = f"处理速度: {stats.throughput:.0f} 行/秒"
            current_status = self.status_label.text()
            if "处理速度" not in current_status:
                self.status_label.setText(f"{current_status} | {throughput_info}")

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
            # 连接字体大小变化信号
            text_widget.font_size_changed.connect(self._on_font_size_changed)
            
            self.tabs.addTab(text_widget, filename)
            
            total_lines = len(line_offsets) - 1
            # 显示更详细的文件信息
            file_size = os.path.getsize(self._pending_file_path) / (1024*1024)
            self.status_label.setText(
                f"✅ 文件加载完成 - {total_lines:,} 行 | {file_size:.1f}MB"
            )
        else:
            self.status_label.setText("❌ 文件加载失败")

    def _on_font_size_changed(self, font_size: int):
        """处理字体大小变化"""
        # 可以在这里更新状态栏显示当前字体大小
        # 或者在工具提示中显示
        pass

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
        
        # 如果是当前活动的编辑器，更新UI
        if editor == self._get_current_editor():
            editor.update()

    def on_search_finished(self, total_results: int, elapsed_time: float, 
                          editor: TextDisplay, show_only: bool = False):
        """搜索完成处理 - 增强性能统计"""
        
        # 获取搜索引擎ID并清理统计信息
        engine_id = None
        if hasattr(editor, 'current_search_engine'):
            engine_id = id(editor.current_search_engine)
        
        # 计算性能指标
        performance_info = ""
        if engine_id in self.search_stats:
            stats = self.search_stats[engine_id]
            total_lines = stats['total_lines']
            search_type = stats['search_type']
            
            if elapsed_time > 0:
                throughput = total_lines / elapsed_time
                performance_info = f" | {throughput:.0f} 行/秒"
                
                # 根据性能给出提示
                if throughput < 50000 and total_lines > 100000:
                    performance_info += " (建议使用实时搜索模式)"
            
            # 清理统计信息
            del self.search_stats[engine_id]
        
        # 如果搜索了所有标签页，汇总结果
        if self.all_page.isChecked():
            total_all_tabs = 0
            for i in range(self.tabs.count()):
                tab_editor = self.tabs.widget(i)
                if isinstance(tab_editor, TextDisplay):
                    with QMutexLocker(tab_editor.search_results_manager.results_mutex):
                        total_all_tabs += len(tab_editor.search_results_manager.results)
                    
                    # 应用过滤模式到每个标签页
                    if show_only and len(tab_editor.search_results_manager.results) > 0:
                        matching_lines = []
                        with QMutexLocker(tab_editor.search_results_manager.results_mutex):
                            for result in tab_editor.search_results_manager.results:
                                if result.line_number not in matching_lines:
                                    matching_lines.append(result.line_number)
                        
                        if matching_lines:
                            tab_editor.set_filter_mode(True, matching_lines)
            
            self.status_label.setText(
                f"✅ 全部标签搜索完成！总共找到 {total_all_tabs} 个结果，"
                f"耗时 {elapsed_time:.2f} 秒{performance_info}"
            )
        else:
            # 根据结果数量和搜索类型显示不同的状态信息
            if show_only and hasattr(editor.current_search_engine, 'enable_sampling'):
                if editor.current_search_engine.enable_sampling:
                    status_msg = f"✅ 快速预览完成！找到 {total_results} 个结果（采样模式），耗时 {elapsed_time:.2f} 秒{performance_info}"
                else:
                    status_msg = f"✅ 实时搜索完成！找到 {total_results} 个结果，耗时 {elapsed_time:.2f} 秒{performance_info}"
            else:
                status_msg = f"✅ 搜索完成！找到 {total_results} 个结果，耗时 {elapsed_time:.2f} 秒{performance_info}"
            
            self.status_label.setText(status_msg)
        
        # 如果是只显示匹配行模式且有结果，切换到过滤模式
        if show_only and total_results > 0 and not self.all_page.isChecked():
            # 收集所有匹配的行号
            matching_lines = []
            with QMutexLocker(editor.search_results_manager.results_mutex):
                for result in editor.search_results_manager.results:
                    if result.line_number not in matching_lines:
                        matching_lines.append(result.line_number)
            
            if matching_lines:
                editor.set_filter_mode(True, matching_lines)
        
        # 更新搜索结果显示
        self._update_search_results_display(editor, total_results)
        
        # 清理搜索引擎引用
        if hasattr(editor, 'current_search_engine'):
            # 从活动列表中移除
            if editor.current_search_engine in self.active_search_engines:
                self.active_search_engines.remove(editor.current_search_engine)
            # 不删除引用，让引擎自然结束
            editor.current_search_engine = None

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
        """搜索进度更新 - 增强显示信息"""
        # 根据找到的结果数量调整显示信息
        if found_count > 0:
            self.status_label.setText(f"🔍 搜索中... 进度 {progress}% - 已找到 {found_count} 个结果")
        else:
            self.status_label.setText(f"🔍 搜索中... 进度 {progress}%")
        
        # 如果是实时搜索且已找到足够结果，可以考虑提前显示
        editor = self._get_current_editor()
        if (editor and hasattr(editor, 'current_search_engine') and 
            isinstance(editor.current_search_engine, RealTimeSearchEngine) and 
            found_count >= 50):  # 实时搜索找到50个结果时就开始更新显示
            editor.update()

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
            
    # 字体缩放功能
    def _zoom_in_current_editor(self):
        """放大当前编辑器字体"""
        editor = self._get_current_editor()
        if editor:
            editor.zoom_in()
            
    def _zoom_out_current_editor(self):
        """缩小当前编辑器字体"""
        editor = self._get_current_editor()
        if editor:
            editor.zoom_out()
            
    def _reset_zoom_current_editor(self):
        """重置当前编辑器字体大小"""
        editor = self._get_current_editor()
        if editor:
            editor.reset_zoom()
            
    def _sync_font_size_all_tabs(self, font_size: int):
        """同步所有标签页的字体大小"""
        for i in range(self.tabs.count()):
            editor = self.tabs.widget(i)
            if isinstance(editor, TextDisplay):
                editor.font_size = font_size
                editor._update_font_size()
                
    # 🆕 新增的性能监控和调试方法
    def get_search_performance_info(self) -> dict:
        """获取搜索性能信息 - 用于调试和优化"""
        info = {
            'active_engines': len(self.active_search_engines),
            'engine_types': [type(engine).__name__ for engine in self.active_search_engines],
            'pending_stats': len(self.search_stats)
        }
        
        # 获取当前编辑器的详细信息
        editor = self._get_current_editor()
        if editor:
            info['current_editor'] = {
                'file_path': getattr(editor, 'file_path', None),
                'total_lines': len(getattr(editor, 'line_offsets', [])) - 1,
                'has_search_engine': hasattr(editor, 'current_search_engine'),
                'search_results_count': len(editor.search_results_manager.results) if hasattr(editor, 'search_results_manager') else 0
            }
            
            # 如果有活动的搜索引擎，获取其性能信息
            if hasattr(editor, 'current_search_engine') and editor.current_search_engine:
                engine = editor.current_search_engine
                if hasattr(engine, 'get_performance_info'):
                    info['current_engine_performance'] = engine.get_performance_info()
        
        return info
    
    def force_stop_all_searches(self):
        """强制停止所有搜索 - 紧急停止功能"""
        stopped_count = 0
        
        # 停止所有活动的搜索引擎
        for engine in self.active_search_engines[:]:
            if engine and engine.isRunning():
                try:
                    engine.stop_search()
                    if engine.wait(1000):  # 等待1秒
                        stopped_count += 1
                    else:
                        # 强制终止
                        engine.terminate()
                        engine.wait(500)
                        stopped_count += 1
                except Exception as e:
                    print(f"强制停止搜索引擎时出错: {e}")
        
        # 清理资源
        self.active_search_engines.clear()
        self.search_stats.clear()
        
        if stopped_count > 0:
            self.status_label.setText(f"🛑 已强制停止 {stopped_count} 个搜索任务")
        
        return stopped_count
    
    def optimize_search_settings_for_file_size(self, file_size_mb: float):
        """根据文件大小优化搜索设置"""
        if file_size_mb > 500:  # 大于500MB的大文件
            # 建议使用实时搜索模式
            if not self.only_match_check.isChecked():
                reply = QMessageBox.question(
                    self, "性能优化建议", 
                    f"检测到大文件 ({file_size_mb:.1f}MB)，建议启用\"只显示匹配行\"模式以提升搜索速度。\n\n是否启用？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self.only_match_check.setChecked(True)
        
        elif file_size_mb > 100:  # 100-500MB的中等文件
            # 提示用户可以使用实时搜索
            self.status_label.setText(
                f"💡 提示：大文件 ({file_size_mb:.1f}MB) 可启用\"只显示匹配行\"模式提升搜索速度"
            )
    
    def show_search_help(self):
        """显示搜索帮助信息"""
        help_text = """
            🔍 搜索功能说明：

            📌 搜索模式：
            • 完整搜索：搜索整个文件，适合精确查找
            • 实时搜索：只显示匹配行，适合大文件快速预览
            • 采样搜索：大文件快速预览，显示部分结果

            ⚡ 性能优化：
            • 小文件 (<10MB)：推荐完整搜索
            • 中等文件 (10-100MB)：可使用实时搜索
            • 大文件 (>100MB)：强烈建议使用实时搜索

            🎯 搜索技巧：
            • 使用\"包含\"和\"排除\"关键词组合过滤
            • 启用\"区分大小写\"进行精确匹配
            • 使用\"整词匹配\"避免部分匹配
            • 实时搜索会自动优化大文件性能

            ⌨️ 快捷操作：
            • 拖拽文件到窗口直接打开
            • 搜索表格支持动态开关过滤条件
            • 支持多标签页同时搜索
        """
        
        QMessageBox.information(self, "搜索帮助", help_text)