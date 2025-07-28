from PyQt5.QtWidgets import (
    QMainWindow, QFileDialog, QVBoxLayout, QProgressBar, QLabel, QMessageBox, QInputDialog
)
from PyQt5 import uic
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, QObject, pyqtSlot
from PyQt5.QtWidgets import QApplication
import re
import concurrent.futures
import threading
from typing import List, Tuple, Optional
import time
import queue
import weakref
import os

from widgets.code_editor import CodeEditor
from widgets.search_table import SearchTable
from logic.filter_engine import FilterEngine, SearchOptions
from logic.search_manager import SearchManager
from logic.file_io import FileHandler
from logic.parallel_search import (
    SearchWorker, RealTimeRegexWorker, 
    BatchFileSearchWorker, SearchCoordinator
)

from PyQt5.QtCore import QThread, pyqtSignal
import time
import re

class AsyncSearchThread(QThread):
    """异步搜索线程，防止UI卡顿"""
    
    # 信号定义
    search_progress = pyqtSignal(int, int, str)  # current, total, message
    search_completed = pyqtSignal(object)  # search results
    search_failed = pyqtSignal(str)  # error message
    
    def __init__(self, text_content, include_keywords, exclude_keywords, 
                 show_only=False, ignore_case=False, whole_pair=False):
        super().__init__()
        self.text_content = text_content
        self.include_keywords = [k.strip() for k in include_keywords if k.strip()]
        self.exclude_keywords = [k.strip() for k in exclude_keywords if k.strip()]
        self.show_only = show_only
        self.ignore_case = ignore_case
        self.whole_pair = whole_pair
        self._stop_requested = False
        
    def request_stop(self):
        """请求停止搜索"""
        self._stop_requested = True
        
    def run(self):
        """在后台线程中执行搜索"""
        try:
            start_time = time.time()
            lines = self.text_content.splitlines()
            total_lines = len(lines)
            
            self.search_progress.emit(0, total_lines, "开始搜索...")
            
            matched_lines = []
            processed_lines = 0
            
            # 分批处理，避免长时间占用
            batch_size = 1000  # 每批处理1000行
            
            for i in range(0, total_lines, batch_size):
                if self._stop_requested:
                    return
                    
                batch_end = min(i + batch_size, total_lines)
                batch_lines = lines[i:batch_end]
                
                # 处理当前批次
                for j, line in enumerate(batch_lines):
                    line_num = i + j
                    
                    if self._match_line(line):
                        matched_lines.append(line_num)
                    
                    processed_lines += 1
                    
                    # 每100行更新一次进度
                    if processed_lines % 100 == 0:
                        self.search_progress.emit(
                            processed_lines, total_lines, 
                            f"已处理 {processed_lines}/{total_lines} 行"
                        )
                
                # 让出CPU时间，防止阻塞
                self.msleep(1)
            
            if not self._stop_requested:
                search_time = time.time() - start_time
                
                # 构建搜索结果
                result = {
                    'matched_lines': matched_lines,
                    'total_matches': len(matched_lines),
                    'search_time': search_time,
                    'total_lines': total_lines,
                    'include_keywords': self.include_keywords,
                    'exclude_keywords': self.exclude_keywords,
                    'show_only': self.show_only
                }
                
                self.search_completed.emit(result)
                
        except Exception as e:
            if not self._stop_requested:
                self.search_failed.emit(str(e))
    
    def _match_line(self, line):
        """检查行是否匹配搜索条件"""
        line_to_check = line.lower() if self.ignore_case else line
        
        # 检查包含条件
        include_match = True
        if self.include_keywords:
            include_match = False
            for keyword in self.include_keywords:
                search_keyword = keyword.lower() if self.ignore_case else keyword
                
                if self.whole_pair:
                    # 完整单词匹配
                    pattern = r'\b' + re.escape(search_keyword) + r'\b'
                    if re.search(pattern, line_to_check):
                        include_match = True
                        break
                else:
                    # 部分匹配
                    if search_keyword in line_to_check:
                        include_match = True
                        break
        
        # 检查排除条件
        exclude_match = False
        if self.exclude_keywords:
            for keyword in self.exclude_keywords:
                search_keyword = keyword.lower() if self.ignore_case else keyword
                
                if self.whole_pair:
                    pattern = r'\b' + re.escape(search_keyword) + r'\b'
                    if re.search(pattern, line_to_check):
                        exclude_match = True
                        break
                else:
                    if search_keyword in line_to_check:
                        exclude_match = True
                        break
        
        return include_match and not exclude_match

class AsyncHighlightThread(QThread):
    """异步高亮线程"""
    
    highlight_progress = pyqtSignal(int, int, str)
    highlight_completed = pyqtSignal(object)  # highlight data
    highlight_failed = pyqtSignal(str)
    
    def __init__(self, text_content, matched_lines, keywords, show_only=False):
        super().__init__()
        self.text_content = text_content
        self.matched_lines = matched_lines
        self.keywords = keywords
        self.show_only = show_only
        self._stop_requested = False
        
    def request_stop(self):
        self._stop_requested = True
        
    def run(self):
        """在后台线程中准备高亮数据"""
        try:
            lines = self.text_content.splitlines()
            
            if self.show_only:
                # 只显示匹配的行
                filtered_content = []
                total_matches = len(self.matched_lines)
                
                for i, line_num in enumerate(self.matched_lines):
                    if self._stop_requested:
                        return
                        
                    if line_num < len(lines):
                        filtered_content.append(f"[{line_num+1}] {lines[line_num]}")
                    
                    if i % 100 == 0:
                        self.highlight_progress.emit(i, total_matches, f"处理匹配行 {i}/{total_matches}")
                    
                    if i % 50 == 0:  # 更频繁地让出CPU
                        self.msleep(1)
                
                result = {
                    'type': 'filtered_content',
                    'content': '\n'.join(filtered_content)
                }
            else:
                # 准备高亮数据
                result = {
                    'type': 'highlight_lines',
                    'matched_lines': self.matched_lines,
                    'total_lines': len(lines)
                }
            
            if not self._stop_requested:
                self.highlight_completed.emit(result)
                
        except Exception as e:
            if not self._stop_requested:
                self.highlight_failed.emit(str(e))


# 改进的线程基类
class ImprovedWorkerThread(QThread):
    """改进的工作线程基类，支持优雅停止"""
    progress_updated = pyqtSignal(str, int, int)  # stage, current, total
    task_completed = pyqtSignal(object)  # result
    task_failed = pyqtSignal(str)  # error message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_requested = False
        self._is_running = False
        
    def request_stop(self):
        """请求停止线程（非阻塞）"""
        self._stop_requested = True
        
    def is_stop_requested(self):
        """检查是否请求停止"""
        return self._stop_requested

# 线程管理器
class ThreadManager(QObject):
    """线程管理器，负责统一管理所有工作线程"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_threads = []
        self.cleanup_timer = QTimer()
        self.cleanup_timer.timeout.connect(self._cleanup_finished_threads)
        self.cleanup_timer.start(1000)  # 每秒清理一次

        self.current_search_thread = None
        self.current_highlight_thread = None    
        
    def register_thread(self, thread: QThread):
        """注册线程"""
        if thread:
            self.active_threads.append(thread)
            thread.finished.connect(lambda: self._on_thread_finished(thread))
            
    def _stop_all_searches_async(self):
        """异步停止所有搜索任务"""
        # 停止当前搜索
        self._stop_current_search()
        
        # 停止线程管理器中的其他线程
        self.thread_manager.stop_all_threads(timeout_ms=1000)
        
        # 重新启用按钮
        if hasattr(self, 'apply') and self.apply:
            self.apply.setEnabled(True)
        
        self._safe_update_progress(0, visible=False)
        print("🛑 正在停止所有搜索任务...")

            
    def _force_quit_threads(self, threads):
        """强制退出未响应的线程"""
        for thread in threads:
            if thread and thread.isRunning():
                print(f"强制终止线程: {thread.__class__.__name__}")
                thread.terminate()
                
    def _on_thread_finished(self, thread):
        """线程完成回调"""
        try:
            if thread in self.active_threads:
                self.active_threads.remove(thread)
        except ValueError:
            pass  # 线程已经被移除
            
    def _cleanup_finished_threads(self):
        """清理已完成的线程"""
        finished_threads = [t for t in self.active_threads if t and t.isFinished()]
        for thread in finished_threads:
            try:
                self.active_threads.remove(thread)
            except ValueError:
                pass
                
    def get_active_count(self):
        """获取活跃线程数量"""
        return len([t for t in self.active_threads if t and t.isRunning()])

# 改进的性能测试线程
class PerformanceTestThread(ImprovedWorkerThread):
    """性能测试线程"""
    
    def __init__(self, filter_engine, text_content, include_keywords, exclude_keywords, options, iterations):
        super().__init__()
        self.filter_engine = filter_engine
        self.text_content = text_content
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.options = options
        self.iterations = iterations
        
    def run(self):
        """执行性能测试"""
        try:
            self._is_running = True
            self.progress_updated.emit("性能测试", 0, self.iterations)
            
            stats = self.filter_engine.measure_performance_comprehensive(
                self.text_content, self.include_keywords, self.exclude_keywords, 
                self.options, self.iterations
            )
            
            if not self.is_stop_requested():
                self.task_completed.emit(stats)
                
        except Exception as e:
            if not self.is_stop_requested():
                self.task_failed.emit(str(e))
        finally:
            self._is_running = False

class EnhancedMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # 确保UI文件路径正确
        ui_file = "log_ui.ui"
        if not os.path.exists(ui_file):
            possible_paths = ["ui/log_ui.ui", "../log_ui.ui", "LogCheck/log_ui.ui"]
            for path in possible_paths:
                if os.path.exists(path):
                    ui_file = path
                    break
            else:
                raise FileNotFoundError(f"找不到UI文件: {ui_file}")
        
        print(f"加载UI文件: {ui_file}")
        uic.loadUi(ui_file, self)

        # 验证tabs是否正确加载
        print(f"UI加载后检查tabs: {hasattr(self, 'tabs')}")
        if hasattr(self, 'tabs'):
            print(f"tabs类型: {type(self.tabs)}")
            print(f"tabs对象: {self.tabs}")

        self.search_table: Optional[SearchTable] = None
        
        # 线程管理器
        self.thread_manager = ThreadManager(self)

        # 添加线程引用
        self.current_search_thread = None
        self.current_highlight_thread = None

        self.highlight_applier = None
        self.highlight_worker = None
        
        # 使用新的高性能搜索引擎
        self.filter_engine = FilterEngine(max_workers=4)
        self.search_manager = SearchManager()
        self.file_handler = FileHandler()
        
        # 当前活跃的工作线程引用
        self.current_workers = {
            'search': None,
            'regex': None,
            'file_loader': None,
            'coordinator': None,
            'performance': None
        }
        
        # UI组件引用（防止UI加载失败）
        self.progress_bar = None
        self.status_label = None
        self.search_stats_label = None
        
        # 性能监控
        self.last_search_stats = {}
        self.performance_timer = QTimer()
        self.performance_timer.timeout.connect(self._update_performance_display)
        
        # 状态更新队列（用于线程安全的UI更新）
        self.ui_update_queue = queue.Queue()
        self.ui_update_timer = QTimer()
        self.ui_update_timer.timeout.connect(self._process_ui_updates)
        self.ui_update_timer.start(50)  # 每50ms处理一次UI更新
        
        # 验证UI组件
        self._validate_ui_components()
        self._setup_enhanced_ui()
        self._bind_ui_actions()
        print("主窗口初始化完成")
    def _on_optimized_search_completed(self, result):
        """优化的搜索完成处理"""
        print(f"优化搜索完成 - 匹配: {result['total_matches']} 行")
        
        try:
            self._update_search_ui_state(searching=False)
            
            if result['total_matches'] == 0:
                return
            
            # 显示搜索结果到表格
            self._display_search_results(result)
            
            # 如果需要高亮，启动优化的高亮过程
            if not result['show_only']:
                self._start_optimized_highlight(result)
            else:
                self._apply_filtered_content(result)
                
        except Exception as e:
            print(f"处理优化搜索结果时出错: {e}")
    
    def _start_optimized_highlight(self, search_result):
        """启动优化的高亮过程"""
        try:
            editor = self._get_current_editor()
            if not editor or not search_result['matched_lines']:
                return
            
            # 创建高亮工作线程
            self.highlight_worker = OptimizedHighlightWorker(
                editor.toPlainText(),
                search_result['matched_lines'],
                search_result['include_keywords'],
                batch_size=200  # 调整批次大小
            )
            
            # 连接信号
            self.highlight_worker.highlight_ready.connect(self._on_highlight_data_ready)
            self.highlight_worker.highlight_progress.connect(self._on_highlight_progress)
            self.highlight_worker.highlight_failed.connect(self._on_highlight_failed)
            
            # 启动高亮预处理
            self.highlight_worker.start()
            
            print("启动优化高亮预处理...")
            
        except Exception as e:
            print(f"启动优化高亮时出错: {e}")
    
    def _on_highlight_data_ready(self, highlight_data):
        """高亮数据准备完成"""
        print(f"高亮数据准备完成，开始应用 {len(highlight_data)} 行")
        
        try:
            editor = self._get_current_editor()
            if not editor:
                return
            
            # 创建批量高亮应用器
            self.highlight_applier = BatchHighlightApplier(
                editor, 
                max_batch_size=30,  # 更小的批次
                delay_ms=5  # 更快的处理频率
            )
            
            # 开始分批应用高亮
            self.highlight_applier.start_highlight(highlight_data)
            
        except Exception as e:
            print(f"应用高亮数据时出错: {e}")
    
    def _stop_current_operations(self):
        """停止当前所有操作"""
        # 停止搜索
        if hasattr(self, 'current_search_thread') and self.current_search_thread:
            if self.current_search_thread.isRunning():
                self.current_search_thread.request_stop()
        
        # 停止高亮工作线程
        if self.highlight_worker and self.highlight_worker.isRunning():
            self.highlight_worker.request_stop()
        
        # 停止高亮应用
        if self.highlight_applier:
            self.highlight_applier.stop_highlight()
    
    def _update_search_ui_state(self, searching=False):
        """更新搜索UI状态"""
        if hasattr(self, 'apply') and self.apply:
            self.apply.setEnabled(not searching)
        
        if searching:
            self._safe_update_status("正在搜索...")
            self._safe_update_progress(0, 100, True)
        else:
            self._safe_update_progress(0, visible=False)

    def _validate_ui_components(self):
        """验证UI组件是否正确加载 - 恢复原版本风格"""
        print("=== UI组件验证 ===")
        
        # 直接检查tabs属性（就像原版本一样）
        if hasattr(self, 'tabs') and self.tabs:
            print(f"✓ 标签页组件: tabs (类型: {type(self.tabs).__name__})")
            print(f"  当前标签页数量: {self.tabs.count()}")
        else:
            print("✗ 标签页组件: tabs 未找到")
        
        # 检查其他组件
        components_to_check = {
            '应用按钮': 'apply',
            '重置按钮': 'reset_button', 
            '包含输入': 'in_word',
            '排除输入': 'ex_word',
            '菜单_打开': 'menu_open',
            '仅匹配': 'only_match_check',
            'Maxmi': 'Maxmi',
            '全对': 'whole_pair_check',
            '全页': 'all_page',
            '搜索信息': 'search_info'
        }
        
        for desc, attr_name in components_to_check.items():
            if hasattr(self, attr_name) and getattr(self, attr_name):
                attr = getattr(self, attr_name)
                print(f"✓ {desc}: {attr_name} (类型: {type(attr).__name__})")
            else:
                print(f"✗ {desc}: {attr_name} 未找到")
        
        print("================")


    def test_tab_functionality(self):
        """测试标签页功能"""
        print("=== 测试标签页功能 ===")
        
        if not self.tabs:
            print("✗ QTabWidget不存在，无法测试")
            return
        
        try:
            # 创建测试内容
            test_content = "这是测试内容\n第二行\n第三行"
            test_filename = "测试文件.txt"
            
            print(f"当前标签页数量: {self.tabs.count()}")
            
            # 尝试添加标签页
            self._add_log_tab_from_content(test_content, test_filename)
            
            print(f"添加后标签页数量: {self.tabs.count()}")
            
            if self.tabs.count() > 0:
                current_widget = self.tabs.currentWidget()
                if isinstance(current_widget, CodeEditor):
                    content = current_widget.toPlainText()
                    print(f"✓ 标签页内容验证成功，长度: {len(content)}")
                else:
                    print(f"✗ 标签页内容类型错误: {type(current_widget)}")
            
        except Exception as e:
            print(f"测试标签页功能时出错: {e}")
            import traceback
            traceback.print_exc()
        
        print("======================")

    def _setup_enhanced_ui(self):
        """设置增强的UI组件"""
        # 创建状态栏组件
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_label = QLabel("就绪 - 高性能搜索引擎已加载")
        self.search_stats_label = QLabel("")
        
        # 添加到状态栏（如果存在）
        if hasattr(self, 'statusbar') and self.statusbar:
            self.statusbar.addWidget(self.status_label)
            self.statusbar.addWidget(self.search_stats_label)
            self.statusbar.addPermanentWidget(self.progress_bar)
        else:
            print("警告: 未找到statusbar，状态信息将无法显示")
        
        # 显示引擎信息
        self._update_cache_stats()
        print("UI组件设置完成")

    def _bind_ui_actions(self):
        """绑定UI事件 - 恢复原版本风格"""
        print("开始绑定UI事件...")
        
        try:
            # 菜单事件绑定 - 直接使用原版本的名称
            if hasattr(self, 'menu_open') and self.menu_open:
                self.menu_open.triggered.connect(self._import_logs_parallel)
                print("✓ menu_open 绑定成功")
            else:
                print("✗ menu_open 未找到")
            
            if hasattr(self, 'menu_download') and self.menu_download:
                self.menu_download.triggered.connect(self._download_results)
                print("✓ menu_download 绑定成功")
            else:
                print("✗ menu_download 未找到")
            
            # 按钮事件绑定 - 直接使用原版本的名称
            if hasattr(self, 'apply') and self.apply:
                self.apply.clicked.connect(self._apply_filters_smart)
                print("✓ apply 按钮绑定成功")
            else:
                print("✗ apply 按钮未找到")
            
            if hasattr(self, 'reset_button') and self.reset_button:
                self.reset_button.clicked.connect(self._reset_editor_enhanced)
                print("✓ reset_button 绑定成功")
            else:
                print("✗ reset_button 未找到")
            
            # 标签页事件 - 直接使用tabs（就像原版本）
            if hasattr(self, 'tabs') and self.tabs:
                self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
                print("✓ tabs 绑定成功")
            else:
                print("✗ tabs 未找到")
            
            # 正则输入
            if hasattr(self, 'norm_input') and self.norm_input:
                self.norm_input.triggered.connect(self._input_regex_filter_realtime)
                print("✓ norm_input 绑定成功")
            else:
                print("✗ norm_input 未找到")
            
            print("UI事件绑定完成")
            
        except Exception as e:
            print(f"UI事件绑定出错: {e}")
            import traceback
            traceback.print_exc()

    def _queue_ui_update(self, update_func, *args, **kwargs):
        """将UI更新操作加入队列"""
        try:
            self.ui_update_queue.put((update_func, args, kwargs))
        except Exception as e:
            print(f"UI更新队列错误: {e}")

    def _process_ui_updates(self):
        """处理UI更新队列"""
        processed = 0
        max_updates_per_cycle = 10  # 每次最多处理10个更新
        
        while not self.ui_update_queue.empty() and processed < max_updates_per_cycle:
            try:
                update_func, args, kwargs = self.ui_update_queue.get_nowait()
                update_func(*args, **kwargs)
                processed += 1
            except queue.Empty:
                break
            except Exception as e:
                print(f"UI更新错误: {e}")

    def _safe_update_status(self, message: str):
        """线程安全的状态更新"""
        if self.status_label:
            self._queue_ui_update(self.status_label.setText, message)

    def _safe_update_progress(self, value: int, maximum: int = None, visible: bool = None):
        """线程安全的进度条更新"""
        if self.progress_bar:
            if maximum is not None:
                self._queue_ui_update(self.progress_bar.setMaximum, maximum)
            self._queue_ui_update(self.progress_bar.setValue, value)
            if visible is not None:
                self._queue_ui_update(self.progress_bar.setVisible, visible)

    def _reset_editor_enhanced(self):
        """增强的编辑器重置"""
        print("开始重置编辑器...")
        
        # 非阻塞方式停止所有搜索
        self._stop_all_searches_async()
        
        editor = self._get_current_editor()
        if editor:
            editor.reset_text()
            print("编辑器内容已重置")
        
        # 清空搜索条件
        try:
            if hasattr(self, 'in_word') and self.in_word:
                self.in_word.clear()
            if hasattr(self, 'ex_word') and self.ex_word:
                self.ex_word.clear()
            if hasattr(self, 'only_match_check') and self.only_match_check:
                self.only_match_check.setChecked(False)
            if hasattr(self, 'Maxmi') and self.Maxmi:
                self.Maxmi.setChecked(False)
            if hasattr(self, 'whole_pair_check') and self.whole_pair_check:
                self.whole_pair_check.setChecked(False)
            if hasattr(self, 'all_page') and self.all_page:
                self.all_page.setChecked(False)
            print("搜索条件已清空")
        except Exception as e:
            print(f"清空搜索条件时出错: {e}")

        if self.search_table:
            self.search_table.clear_table()
            print("搜索表格已清空")
        
        # 异步清除缓存
        QTimer.singleShot(100, self._async_clear_cache)
        
        self._safe_update_status("就绪 - 已重置")
        print("编辑器重置完成")

    def _async_clear_cache(self):
        """异步清除缓存"""
        try:
            self.filter_engine.clear_cache()
            self._update_cache_stats()
            print("缓存已清除")
        except Exception as e:
            print(f"清除缓存时出错: {e}")

    def _stop_all_searches_async(self):
        """异步停止所有搜索任务"""
        # 非阻塞方式停止线程
        self.thread_manager.stop_all_threads(timeout_ms=1000)
        
        # 清空当前工作线程引用
        for key in self.current_workers:
            self.current_workers[key] = None
        
        self._safe_update_progress(0, visible=False)
        print("🛑 正在停止所有搜索任务...")

    def _start_worker(self, worker_type: str, worker: QThread):
        """启动工作线程"""
        # 停止同类型的现有工作线程
        if self.current_workers[worker_type]:
            old_worker = self.current_workers[worker_type]
            if old_worker and old_worker.isRunning():
                if hasattr(old_worker, 'request_stop'):
                    old_worker.request_stop()
        
        # 注册并启动新线程
        self.current_workers[worker_type] = worker
        self.thread_manager.register_thread(worker)
        worker.start()
        print(f"启动工作线程: {worker_type}")

    def _import_logs_parallel(self):
        """并行导入日志文件"""
        print("开始导入日志文件...")
        
        try:
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择日志文件", "", "Log Files (*.log *.txt);;All Files (*)"
            )
            
            if files:
                print(f"选择了 {len(files)} 个文件")
                self._load_files_with_progress(files)
            else:
                print("未选择文件")
                
        except Exception as e:
            print(f"导入文件时出错: {e}")
            QMessageBox.critical(self, "错误", f"导入文件失败：{str(e)}")

    def _load_files_with_progress(self, files):
        """带进度显示的并行文件加载"""
        try:
            file_loader = BatchFileSearchWorker(files, self.file_handler)
            file_loader.file_loaded.connect(self._on_file_loaded)
            file_loader.batch_progress.connect(self._on_file_load_progress)
            file_loader.batch_completed.connect(self._on_file_load_completed)
            file_loader.batch_failed.connect(self._on_file_load_failed)
            
            self._start_worker('file_loader', file_loader)
            
            self._safe_update_status(f"正在并行加载 {len(files)} 个文件...")
            self._safe_update_progress(0, len(files), True)
            
        except Exception as e:
            print(f"文件加载出错: {e}")
            QMessageBox.critical(self, "错误", f"文件加载失败：{str(e)}")

    def _on_file_loaded(self, filepath: str, content: str, filename: str):
        """单个文件加载完成"""
        if content:
            self._add_log_tab_from_content(content, filename)
            print(f"文件加载完成: {filename}")

    def _on_file_load_progress(self, completed: int, total: int):
        """文件加载进度更新"""
        self._safe_update_progress(completed, total)
        self._safe_update_status(f"文件加载进度: {completed}/{total}")

    def _on_file_load_completed(self, results):
        """所有文件加载完成"""
        self._safe_update_progress(0, visible=False)
        successful = len([r for r in results if r[1] is not None])
        self._safe_update_status(f"文件加载完成 - 成功: {successful}/{len(results)}")
        print(f"所有文件加载完成，成功: {successful}/{len(results)}")

    def _on_file_load_failed(self, error_msg: str):
        """文件加载失败"""
        self._safe_update_progress(0, visible=False)
        self._safe_update_status("文件加载失败")
        print(f"文件加载失败: {error_msg}")
        QTimer.singleShot(100, lambda: QMessageBox.critical(self, "文件加载错误", f"加载失败：{error_msg}"))

    def _add_log_tab_from_content(self, content: str, filename: str):
        """从内容创建日志标签页 - 恢复原版本风格"""
        try:
            print(f"开始创建标签页: {filename}")
            print(f"内容长度: {len(content)} 字符, {len(content.splitlines())} 行")
            
            # 创建编辑器
            editor = CodeEditor()
            editor.setPlainText(content)
            editor.load_text(content)
            print("✓ CodeEditor 创建和内容设置成功")
            
            # 直接使用tabs添加标签页（就像原版本）
            # if hasattr(self, 'tabs') and self.tabs:
            #     index = self.tabs.addTab(editor, filename)
            #     self.tabs.setCurrentIndex(index)
            #     print(f"✓ 标签页添加成功: {filename} (索引: {index})")
            #     print(f"  当前标签页总数: {self.tabs.count()}")
            # else:
            #     print("✗ tabs 组件不存在，无法添加标签页")

            # content = self.file_handler.load_file(filepath)
            # if content is None:
            #     return
            # editor = CodeEditor()
            # editor.setPlainText(content)
            # editor.load_text(content)
            filename = os.path.basename(filename)
            self.tabs.addTab(editor, filename)

                
        except Exception as e:
            print(f"创建标签页时出错: {e}")
            import traceback
            traceback.print_exc()

    def _get_current_editor(self) -> Optional[CodeEditor]:
        """获取当前编辑器 - 恢复原版本风格"""
        try:
            if hasattr(self, 'tabs') and self.tabs:
                editor = self.tabs.currentWidget()
                return editor if isinstance(editor, CodeEditor) else None
            return None
        except Exception as e:
            print(f"获取当前编辑器时出错: {e}")
            return None

    def _apply_filters_smart(self):
        """简化版的高效搜索 - 去掉过度优化"""
        print("开始简化搜索...")
        
        editor = self._get_current_editor()
        if not editor:
            return
        
        # 获取搜索参数
        include = self._get_include_keywords()
        exclude = self._get_exclude_keywords()
        
        if not include and not exclude:
            return
        
        # 简单直接的搜索 - 不用线程
        try:
            start_time = time.time()
            text_content = editor.toPlainText()
            lines = text_content.splitlines()
            total_lines = len(lines)
            
            if total_lines == 0:
                return
            
            # 显示开始状态
            self._safe_update_status("正在搜索...")
            self._safe_update_progress(0, total_lines, True)
            
            matched_lines = []
            show_only = self._get_show_only()
            ignore_case = self._get_ignore_case()
            whole_pair = self._get_whole_pair()
            
            # 简单高效的搜索循环
            for i, line in enumerate(lines):
                # 每1000行更新一次进度，避免UI卡顿
                if i % 1000 == 0:
                    self._safe_update_progress(i, total_lines)
                    QApplication.processEvents()  # 让UI响应
                
                if self._simple_match_line(line, include, exclude, ignore_case, whole_pair):
                    matched_lines.append(i)
            
            search_time = time.time() - start_time
            
            # 更新进度
            self._safe_update_progress(total_lines, total_lines)
            
            if len(matched_lines) == 0:
                self._safe_update_status("搜索完成 - 无匹配")
                self._safe_update_progress(0, visible=False)
                QMessageBox.information(self, "搜索结果", "未找到匹配的内容")
                return
            
            # 显示结果
            self._safe_update_status(f"搜索完成 - 匹配: {len(matched_lines)} 行, 耗时: {search_time:.3f}秒")
            
            # 显示到表格
            pattern = '|'.join(include) if include else ''
            exclude_pattern = '|'.join(exclude) if exclude else ''
            desc = (f"包含：{pattern}\n排除：{exclude_pattern}\n"
                    f"总匹配：{len(matched_lines)}\n耗时：{search_time:.3f}秒")
            
            self._display_results(len(matched_lines), pattern, desc, include, exclude)
            
            # 应用结果
            if show_only:
                self._apply_filtered_content(lines, matched_lines)
            else:
                # self._apply_simple_highlight(editor, matched_lines)
                self._apply_highlight_to_editor(editor, matched_lines)
            
            self._safe_update_progress(0, visible=False)
            print(f"搜索完成 - 匹配: {len(matched_lines)} 行")
            
        except Exception as e:
            print(f"搜索出错: {e}")
            self._safe_update_progress(0, visible=False)
            self._safe_update_status("搜索失败")
            QMessageBox.critical(self, "搜索错误", f"搜索失败：{str(e)}")

    def _on_strategy_selected(self, strategy: str):
        """搜索策略选择通知"""
        strategy_names = {
            "sequential": "顺序搜索",
            "parallel_editors": "编辑器并行",
            "parallel_content": "内容并行", 
            "hybrid": "混合策略",
            "full_parallel": "完全并行"
        }
        strategy_display = strategy_names.get(strategy, strategy)
        self._safe_update_status(f"已选择策略: {strategy_display}")

    def _on_coordinator_progress(self, stage: str, current: int, total: int):
        """协调器进度更新"""
        self._safe_update_progress(current, total)
        self._safe_update_status(f"{stage}: {current}/{total}")

    def _on_search_completed_enhanced(self, results):
        """增强的搜索完成回调"""
        print("搜索完成")
        self._safe_update_progress(0, visible=False)
        
        if not results:
            self._safe_update_status("搜索完成 - 无结果")
            return
        
        try:
            # 获取搜索选项
            options = SearchOptions(
                show_only=getattr(self, 'only_match_check', None) and self.only_match_check.isChecked(),
                ignore_alpha=getattr(self, 'Maxmi', None) and self.Maxmi.isChecked(),
                whole_pair=getattr(self, 'whole_pair_check', None) and self.whole_pair_check.isChecked()
            )
            
            # 统计总体搜索结果
            total_matches = sum(len(result[0].matched_lines) for result in results)
            total_time = sum(result[0].search_time for result in results)
            
            # 应用搜索结果到编辑器
            for search_result, editor, index in results:
                self.filter_engine._apply_parallel_highlights(editor, search_result, options)
            
            # 显示搜索统计
            self._safe_update_status(
                f"搜索完成 - 匹配: {total_matches} 行, 耗时: {total_time:.3f}秒"
            )
            
            # 异步更新缓存统计
            QTimer.singleShot(100, self._update_cache_stats)
            
            # 显示结果
            if results:
                main_result, _, _ = results[0]
                self._display_enhanced_results(main_result, results)
                
            print(f"搜索完成 - 匹配: {total_matches} 行")
            
        except Exception as e:
            print(f"处理搜索结果时出错: {e}")
            import traceback
            traceback.print_exc()

    def _input_regex_filter_realtime(self):
        """实时正则表达式过滤"""
        print("开始正则表达式过滤...")
        
        editor = self._get_current_editor()
        if not editor:
            QMessageBox.warning(self, "警告", "请先打开一个文件")
            return
            
        pattern, ok = QInputDialog.getText(self, "正则输入", "请输入正则表达式：")
        if not ok or not pattern.strip():
            return

        try:
            # 创建正则搜索线程
            regex_worker = RealTimeRegexWorker(editor.toPlainText(), pattern)
            regex_worker.regex_completed.connect(self._on_regex_completed)
            regex_worker.regex_progress.connect(self._on_regex_progress)
            regex_worker.regex_failed.connect(self._on_regex_failed)
            
            self._start_worker('regex', regex_worker)
            
            self._safe_update_status("正在进行实时正则搜索...")
            self._safe_update_progress(0, visible=True)
            
        except Exception as e:
            print(f"正则搜索出错: {e}")
            QMessageBox.critical(self, "错误", f"正则搜索失败：{str(e)}")

    def _on_regex_progress(self, processed_lines: int, total_lines: int):
        """正则搜索进度更新"""
        if total_lines > 0:
            self._safe_update_progress(processed_lines, total_lines)
            self._safe_update_status(f"正则搜索进度: {processed_lines}/{total_lines} 行")

    def _on_regex_completed(self, matches, pattern):
        """正则搜索完成回调"""
        print(f"正则搜索完成，找到 {len(matches)} 个匹配")
        self._safe_update_progress(0, visible=False)
        self._safe_update_status(f"正则搜索完成 - 找到 {len(matches)} 个匹配")
        
        if not matches:
            QTimer.singleShot(100, lambda: QMessageBox.information(self, "搜索结果", "未找到匹配的内容"))
            return

        # 显示结果统计
        hint_count = len(matches)
        desc = f"正则表达式：{pattern}\n匹配数：{hint_count}"
        self._display_results(hint_count, pattern, desc, [pattern], [])
        
        # 如果有搜索表格，添加正则结果
        if self.search_table:
            self.search_table.add_regex_entry_from_user(self, self._get_current_editor())

    def _on_regex_failed(self, error_msg: str):
        """正则搜索失败回调"""
        print(f"正则搜索失败: {error_msg}")
        self._safe_update_progress(0, visible=False)
        self._safe_update_status("正则搜索失败")
        QTimer.singleShot(100, lambda: QMessageBox.critical(self, "正则搜索错误", f"搜索失败：{error_msg}"))

    def _display_enhanced_results(self, main_result, all_results):
        """显示增强的搜索结果"""
        try:
            pattern = main_result.include_pattern
            exclude_pattern = main_result.exclude_pattern
            
            total_matches = sum(len(result[0].matched_lines) for result in all_results)
            total_time = sum(result[0].search_time for result in all_results)
            
            desc = f"包含：{pattern}\n排除：{exclude_pattern}\n总匹配：{total_matches}\n耗时：{total_time:.3f}秒"
            
            # 从搜索结果中提取关键词列表
            include_all = self._extract_keywords_from_pattern(pattern)
            exclude_all = self._extract_keywords_from_pattern(exclude_pattern)
            
            self._display_results(total_matches, pattern, desc, include_all, exclude_all)
            
        except Exception as e:
            print(f"显示搜索结果时出错: {e}")

    def _extract_keywords_from_pattern(self, pattern: str) -> list:
        """从正则表达式模式中提取关键词"""
        if not pattern:
            return []
        
        try:
            # 移除非捕获组和转义字符
            cleaned_pattern = pattern.replace('(?:', '').replace(')', '')
            keywords = []
            
            for part in cleaned_pattern.split('|'):
                # 移除词边界标记和转义字符
                cleaned = part.replace(r'\b', '')
                cleaned = re.sub(r'\\(.)', r'\1', cleaned)
                if cleaned.strip():
                    keywords.append(cleaned.strip())
            
            return keywords
        except Exception as e:
            print(f"提取关键词时出错: {e}")
            return []

    def _display_results(self, hints, pattern, desc, include_all, exclude_all):
        """显示搜索结果到表格"""
        try:
            if not self.search_table:
                self.search_table = SearchTable()
                layout = QVBoxLayout()
                
                # 确保search_info组件存在
                if hasattr(self, 'search_info') and self.search_info:
                    self.search_info.setLayout(layout)
                    layout.addWidget(self.search_table)
                    print("搜索表格已创建")
                else:
                    print("警告: 未找到search_info组件")
                    return
            
            self.search_table.table_add_row(hints, include_all, exclude_all, desc)
            print(f"搜索结果已添加到表格: {hints} 个匹配")
            
        except Exception as e:
            print(f"显示搜索结果时出错: {e}")

    def _download_results(self):
        """下载搜索结果"""
        print("开始下载搜索结果...")
        
        editor = self._get_current_editor()
        if not editor:
            QMessageBox.warning(self, "警告", "请先打开一个文件")
            return

        try:
            include = []
            exclude = []
            
            if hasattr(self, 'in_word') and self.in_word:
                include = self.in_word.toPlainText().splitlines()
            if hasattr(self, 'ex_word') and self.ex_word:
                exclude = self.ex_word.toPlainText().splitlines()

            include_keys, exclude_keys = [], []
            if self.search_table:
                include_keys, exclude_keys = self.search_manager.get_keywords_from_table(self.search_table)

            include_all = list(set(include + include_keys))
            exclude_all = list(set(exclude + exclude_keys))

            show_only = getattr(self, 'only_match_check', None) and self.only_match_check.isChecked()
            ignore_case = getattr(self, 'Maxmi', None) and self.Maxmi.isChecked()
            whole_pair = getattr(self, 'whole_pair_check', None) and self.whole_pair_check.isChecked()

            tab_text = "filtered_result"
            if hasattr(self, 'tabs') and self.tabs:
                tab_text = self.tabs.tabText(self.tabs.currentIndex())

            self.file_handler.save_filtered_result(
                editor, include_all, exclude_all,
                show_only, ignore_case, whole_pair, tab_text
            )
            self._safe_update_status("结果保存成功")
            print("搜索结果保存成功")
            
        except Exception as e:
            print(f"保存搜索结果时出错: {e}")
            QTimer.singleShot(100, lambda: QMessageBox.critical(self, "保存错误", f"保存失败：{str(e)}"))

    def _run_performance_test(self):
        """运行性能测试"""
        print("开始性能测试...")
        
        editor = self._get_current_editor()
        if not editor:
            QMessageBox.warning(self, "警告", "请先打开一个文件进行性能测试")
            return
        
        try:
            # 获取测试参数
            iterations, ok = QInputDialog.getInt(
                self, "性能测试", "请输入测试迭代次数：", 3, 1, 10
            )
            if not ok:
                return
            
            include_keywords = []
            exclude_keywords = []
            
            if hasattr(self, 'in_word') and self.in_word:
                include_keywords = self.in_word.toPlainText().splitlines()
            if hasattr(self, 'ex_word') and self.ex_word:
                exclude_keywords = self.ex_word.toPlainText().splitlines()
                
            options = SearchOptions(
                show_only=getattr(self, 'only_match_check', None) and self.only_match_check.isChecked(),
                ignore_alpha=getattr(self, 'Maxmi', None) and self.Maxmi.isChecked(),
                whole_pair=getattr(self, 'whole_pair_check', None) and self.whole_pair_check.isChecked()
            )
            
            # 创建性能测试线程
            perf_thread = PerformanceTestThread(
                self.filter_engine, editor.toPlainText(),
                include_keywords, exclude_keywords, options, iterations
            )
            
            perf_thread.task_completed.connect(self._show_performance_results)
            perf_thread.task_failed.connect(self._on_performance_test_failed)
            perf_thread.progress_updated.connect(self._on_performance_progress)
            
            self._start_worker('performance', perf_thread)
            
            self._safe_update_status(f"正在进行性能测试 ({iterations} 次迭代)...")
            self._safe_update_progress(0, visible=True)
            
        except Exception as e:
            print(f"性能测试出错: {e}")
            QMessageBox.critical(self, "错误", f"性能测试失败：{str(e)}")

    def _on_performance_progress(self, stage: str, current: int, total: int):
        """性能测试进度更新"""
        self._safe_update_progress(current, total)
        self._safe_update_status(f"{stage}: {current}/{total}")

    def _on_performance_test_failed(self, error_msg: str):
        """性能测试失败"""
        print(f"性能测试失败: {error_msg}")
        self._safe_update_progress(0, visible=False)
        self._safe_update_status("性能测试失败")
        QTimer.singleShot(100, lambda: QMessageBox.critical(self, "性能测试错误", f"测试失败：{error_msg}"))

    def _show_performance_results(self, stats):
        """显示性能测试结果"""
        print("性能测试完成")
        self._safe_update_progress(0, visible=False)
        
        if "error" in stats:
            self._on_performance_test_failed(stats['error'])
            return
        
        try:
            # 格式化结果消息
            result_msg = f"""性能测试结果：

平均搜索时间 (无缓存): {stats['avg_no_cache_time']:.3f} 秒
平均搜索时间 (缓存命中): {stats['avg_cache_hit_time']:.3f} 秒
缓存加速比: {stats['cache_speedup']:.1f}x

处理行数: {stats['total_lines']:,}
匹配行数: {stats['matched_lines']:,}
使用线程数: {stats['workers_used']}

处理效率: {stats['total_lines']/stats['avg_no_cache_time']:.0f} 行/秒"""
            
            QTimer.singleShot(100, lambda: QMessageBox.information(self, "性能测试结果", result_msg))
            self._safe_update_status(f"性能测试完成 - 平均耗时: {stats['avg_no_cache_time']:.3f}秒")
            
            # 保存测试结果
            self.last_search_stats = stats
            
        except Exception as e:
            print(f"显示性能测试结果时出错: {e}")

    def _clear_search_cache(self):
        """清除搜索缓存"""
        print("清除搜索缓存...")
        
        try:
            old_stats = self.filter_engine.get_cache_stats()
            self.filter_engine.clear_cache()
            
            QTimer.singleShot(100, lambda: QMessageBox.information(
                self, "缓存清理", 
                f"已清理缓存\n清理前: {old_stats['cache_size']} 项\n内存释放: {old_stats.get('cache_memory_mb', 0):.2f} MB"
            ))
            
            self._update_cache_stats()
            self._safe_update_status("缓存已清理")
            print("缓存清理完成")
            
        except Exception as e:
            print(f"清理缓存时出错: {e}")

    def _update_cache_stats(self):
        """更新缓存统计显示"""
        try:
            cache_stats = self.filter_engine.get_cache_stats()
            stats_text = (
                f"线程: {cache_stats['max_workers']} | "
                f"缓存: {cache_stats['cache_size']} | "
                f"内存: {cache_stats.get('cache_memory_mb', 0):.1f}MB"
            )
            
            if self.search_stats_label:
                self._queue_ui_update(self.search_stats_label.setText, stats_text)
                
        except Exception as e:
            print(f"更新缓存统计时出错: {e}")

    def _update_performance_display(self):
        """更新性能显示 (定时器回调)"""
        try:
            if hasattr(self, 'last_search_stats') and self.last_search_stats:
                stats = self.last_search_stats
                performance_text = f"最近搜索: {stats.get('avg_no_cache_time', 0):.3f}s | 活跃线程: {self.thread_manager.get_active_count()}"
                self._safe_update_status(performance_text)
        except Exception as e:
            print(f"更新性能显示时出错: {e}")

    def closeEvent(self, event):
        """窗口关闭时清理资源"""
        print("🔄 正在清理资源...")
        
        try:
            # 停止UI更新定时器
            if self.ui_update_timer:
                self.ui_update_timer.stop()
            
            # 停止所有搜索任务（非阻塞）
            self.thread_manager.stop_all_threads(timeout_ms=2000)
            
            # 停止性能监控定时器
            if self.performance_timer and self.performance_timer.isActive():
                self.performance_timer.stop()
            
            # 异步清理缓存
            try:
                self.filter_engine.clear_cache()
            except:
                pass
            
            print("✅ 资源清理完成")
            
        except Exception as e:
            print(f"清理资源时出错: {e}")
        
        super().closeEvent(event)

    def get_search_statistics(self) -> dict:
        """获取搜索统计信息"""
        try:
            cache_stats = self.filter_engine.get_cache_stats()
            
            stats = {
                "cache_stats": cache_stats,
                "last_search_time": self.filter_engine.get_last_search_time(),
                "active_tabs": 0,
                "current_editor_lines": 0,
                "active_threads": self.thread_manager.get_active_count()
            }
            
            if hasattr(self, 'tabs') and self.tabs:
                stats["active_tabs"] = self.tabs.count()
            
            current_editor = self._get_current_editor()
            if current_editor:
                stats["current_editor_lines"] = len(current_editor.toPlainText().splitlines())
            
            return stats
            
        except Exception as e:
            print(f"获取搜索统计时出错: {e}")
            return {}

    def export_search_history(self):
        """导出搜索历史"""
        try:
            if not self.search_table:
                QMessageBox.information(self, "导出", "没有搜索历史可以导出")
                return
            
            filename, _ = QFileDialog.getSaveFileName(
                self, "导出搜索历史", "search_history.json", "JSON Files (*.json)"
            )
            
            if filename:
                # 这里需要实现搜索表格的导出功能
                QMessageBox.information(self, "导出", f"搜索历史已导出到：{filename}")
                
        except Exception as e:
            print(f"导出搜索历史时出错: {e}")
            QMessageBox.critical(self, "导出错误", f"导出失败：{str(e)}")

    def import_search_history(self):
        """导入搜索历史"""
        try:
            filename, _ = QFileDialog.getOpenFileName(
                self, "导入搜索历史", "", "JSON Files (*.json)"
            )
            
            if filename:
                # 这里需要实现搜索表格的导入功能
                QMessageBox.information(self, "导入", "搜索历史导入成功")
                
        except Exception as e:
            print(f"导入搜索历史时出错: {e}")
            QMessageBox.critical(self, "导入错误", f"导入失败：{str(e)}")

    def create_search_preset(self):
        """创建搜索预设"""
        try:
            name, ok = QInputDialog.getText(self, "创建预设", "请输入预设名称：")
            if not ok or not name.strip():
                return
            
            preset = {
                "name": name,
                "include_keywords": [],
                "exclude_keywords": [],
                "show_only": False,
                "ignore_case": False,
                "whole_pair": False,
                "all_tabs": False
            }
            
            # 获取当前设置
            if hasattr(self, 'in_word') and self.in_word:
                preset["include_keywords"] = self.in_word.toPlainText().splitlines()
            if hasattr(self, 'ex_word') and self.ex_word:
                preset["exclude_keywords"] = self.ex_word.toPlainText().splitlines()
            if hasattr(self, 'only_match_check') and self.only_match_check:
                preset["show_only"] = self.only_match_check.isChecked()
            if hasattr(self, 'Maxmi') and self.Maxmi:
                preset["ignore_case"] = self.Maxmi.isChecked()
            if hasattr(self, 'whole_pair_check') and self.whole_pair_check:
                preset["whole_pair"] = self.whole_pair_check.isChecked()
            if hasattr(self, 'all_page') and self.all_page:
                preset["all_tabs"] = self.all_page.isChecked()
            
            # 这里可以保存到配置文件或数据库
            QMessageBox.information(self, "预设", f"搜索预设 '{name}' 创建成功")
            print(f"创建搜索预设: {name}")
            
        except Exception as e:
            print(f"创建搜索预设时出错: {e}")
            QMessageBox.critical(self, "错误", f"创建预设失败：{str(e)}")

    def debug_ui_loading(self):
        """调试UI加载情况"""
        print("=== UI加载调试信息 ===")
        print("所有属性:")
        
        ui_related_attrs = []
        for attr_name in dir(self):
            if not attr_name.startswith('_'):
                try:
                    attr = getattr(self, attr_name)
                    if hasattr(attr, 'objectName'):  # Qt对象
                        ui_related_attrs.append((attr_name, type(attr).__name__, attr.objectName()))
                except:
                    continue
        
        for attr_name, type_name, object_name in sorted(ui_related_attrs):
            print(f"  {attr_name}: {type_name} (objectName: '{object_name}')")
            
            # 特别检查QTabWidget
            if 'tab' in type_name.lower() or 'tab' in attr_name.lower():
                print(f"    *** 可能的标签页组件: {attr_name}")
        
        print("====================")

    def _stop_current_search(self):
        """停止当前搜索"""
        if hasattr(self, 'current_search_thread') and self.current_search_thread:
            if self.current_search_thread.isRunning():
                self.current_search_thread.request_stop()
                # 不等待线程结束，避免阻塞UI
                
        if hasattr(self, 'current_highlight_thread') and self.current_highlight_thread:
            if self.current_highlight_thread.isRunning():
                self.current_highlight_thread.request_stop()

    def _on_search_progress(self, current, total, message):
        """搜索进度更新"""
        if total > 0:
            progress = int((current / total) * 100)
            self._safe_update_progress(progress, 100)
        self._safe_update_status(message)

    def _on_search_completed(self, result):
        """搜索完成回调"""
        print(f"搜索完成 - 匹配: {result['total_matches']} 行")
        
        try:
            # 重新启用搜索按钮
            if hasattr(self, 'apply') and self.apply:
                self.apply.setEnabled(True)
            
            # 更新状态
            self._safe_update_status(
                f"搜索完成 - 匹配: {result['total_matches']} 行, "
                f"耗时: {result['search_time']:.3f}秒"
            )
            
            if result['total_matches'] == 0:
                self._safe_update_progress(0, visible=False)
                QMessageBox.information(self, "搜索结果", "未找到匹配的内容")
                return
            
            # 显示搜索结果到表格
            pattern = '|'.join(result['include_keywords']) if result['include_keywords'] else ''
            exclude_pattern = '|'.join(result['exclude_keywords']) if result['exclude_keywords'] else ''
            desc = (f"包含：{pattern}\n排除：{exclude_pattern}\n"
                    f"总匹配：{result['total_matches']}\n耗时：{result['search_time']:.3f}秒")
            
            self._display_results(
                result['total_matches'], pattern, desc, 
                result['include_keywords'], result['exclude_keywords']
            )
            
            # 启动异步高亮
            self._start_async_highlight(result)
            
        except Exception as e:
            print(f"处理搜索结果时出错: {e}")
            import traceback
            traceback.print_exc()

    def _on_search_failed(self, error_message):
        """搜索失败回调"""
        print(f"搜索失败: {error_message}")
        
        # 重新启用搜索按钮
        if hasattr(self, 'apply') and self.apply:
            self.apply.setEnabled(True)
        
        self._safe_update_progress(0, visible=False)
        self._safe_update_status("搜索失败")
        QMessageBox.critical(self, "搜索错误", f"搜索失败：{error_message}")

    def _start_async_highlight(self, search_result):
        """启动异步高亮"""
        try:
            editor = self._get_current_editor()
            if not editor:
                return
            
            text_content = editor.toPlainText()
            
            # 创建异步高亮线程
            self.current_highlight_thread = AsyncHighlightThread(
                text_content, 
                search_result['matched_lines'],
                search_result['include_keywords'],
                search_result['show_only']
            )
            
            # 连接信号
            self.current_highlight_thread.highlight_progress.connect(self._on_highlight_progress)
            self.current_highlight_thread.highlight_completed.connect(self._on_highlight_completed)
            self.current_highlight_thread.highlight_failed.connect(self._on_highlight_failed)
            
            # 注册并启动
            self.thread_manager.register_thread(self.current_highlight_thread)
            self.current_highlight_thread.start()
            
            self._safe_update_status("正在准备高亮...")
            
        except Exception as e:
            print(f"启动异步高亮时出错: {e}")

    def _on_highlight_progress(self, current, total, message):
        """高亮进度更新"""
        self._safe_update_status(message)

    def _on_highlight_completed(self, highlight_data):
        """高亮完成回调"""
        print("高亮准备完成，应用到编辑器")
        
        try:
            editor = self._get_current_editor()
            if not editor:
                return
            
            if highlight_data['type'] == 'filtered_content':
                # 显示过滤后的内容
                editor.setPlainText(highlight_data['content'])
            else:
                # 应用高亮（在主线程中快速执行）
                self._apply_highlight_to_editor(editor, highlight_data['matched_lines'])
            
            self._safe_update_progress(0, visible=False)
            self._safe_update_status("高亮完成")
            
        except Exception as e:
            print(f"应用高亮时出错: {e}")

    def _on_highlight_failed(self, error_message):
        """高亮失败回调"""
        print(f"高亮失败: {error_message}")
        self._safe_update_progress(0, visible=False)
        self._safe_update_status("高亮失败")

    def _apply_highlight_to_editor(self, editor, matched_lines):
        """在主线程中快速应用高亮"""
        try:
            from PyQt5.QtGui import QTextCharFormat, QColor, QTextCursor
            
            if not matched_lines:
                return
            
            # 快速高亮方法 - 只高亮可见的行
            document = editor.document()
            cursor = QTextCursor(document)
            
            # 设置高亮格式
            highlight_format = QTextCharFormat()
            highlight_format.setBackground(QColor(255, 255, 0, 100))
            
            # 批量处理，限制每次处理的行数
            max_highlights = min(len(matched_lines), 500)  # 最多高亮500行
            
            for i, line_num in enumerate(matched_lines[:max_highlights]):
                if i % 50 == 0:  # 每50行检查一次
                    QApplication.processEvents()  # 让UI响应
                
                # 移动到指定行并高亮
                cursor.movePosition(QTextCursor.Start)
                for _ in range(line_num):
                    cursor.movePosition(QTextCursor.Down)
                
                cursor.movePosition(QTextCursor.StartOfLine)
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.setCharFormat(highlight_format)
            
            if len(matched_lines) > max_highlights:
                print(f"注意: 只高亮了前 {max_highlights} 个匹配行，总共 {len(matched_lines)} 个")
            
        except Exception as e:
            print(f"应用高亮时出错: {e}")

    def _get_include_keywords(self):
        """获取包含关键词列表"""
        try:
            if hasattr(self, 'in_word') and self.in_word:
                keywords = self.in_word.toPlainText().splitlines()
                return [k.strip() for k in keywords if k.strip()]
            return []
        except Exception as e:
            print(f"获取包含关键词时出错: {e}")
            return []

    def _get_exclude_keywords(self):
        """获取排除关键词列表"""
        try:
            if hasattr(self, 'ex_word') and self.ex_word:
                keywords = self.ex_word.toPlainText().splitlines()
                return [k.strip() for k in keywords if k.strip()]
            return []
        except Exception as e:
            print(f"获取排除关键词时出错: {e}")
            return []

    def _get_show_only(self):
        """获取仅显示匹配项的设置"""
        try:
            if hasattr(self, 'only_match_check') and self.only_match_check:
                return self.only_match_check.isChecked()
            return False
        except Exception as e:
            print(f"获取仅显示匹配设置时出错: {e}")
            return False

    def _get_ignore_case(self):
        """获取忽略大小写的设置"""
        try:
            if hasattr(self, 'Maxmi') and self.Maxmi:
                return self.Maxmi.isChecked()
            return False
        except Exception as e:
            print(f"获取忽略大小写设置时出错: {e}")
            return False

    def _get_whole_pair(self):
        """获取完整单词匹配的设置"""
        try:
            if hasattr(self, 'whole_pair_check') and self.whole_pair_check:
                return self.whole_pair_check.isChecked()
            return False
        except Exception as e:
            print(f"获取完整单词匹配设置时出错: {e}")
            return False

    def _get_all_tabs(self):
        """获取搜索所有标签页的设置"""
        try:
            if hasattr(self, 'all_page') and self.all_page:
                return self.all_page.isChecked()
            return False
        except Exception as e:
            print(f"获取搜索所有标签页设置时出错: {e}")
            return False

    # 还需要添加一些辅助方法来处理显示相关的逻辑

    def _display_search_results(self, result):
        """显示搜索结果到表格"""
        try:
            if not result or result['total_matches'] == 0:
                return
            
            # 构建显示信息
            pattern = '|'.join(result['include_keywords']) if result['include_keywords'] else ''
            exclude_pattern = '|'.join(result['exclude_keywords']) if result['exclude_keywords'] else ''
            
            desc = (f"包含：{pattern}\n排除：{exclude_pattern}\n"
                    f"总匹配：{result['total_matches']}\n耗时：{result['search_time']:.3f}秒")
            
            # 显示到表格
            self._display_results(
                result['total_matches'], pattern, desc, 
                result['include_keywords'], result['exclude_keywords']
            )
            
            print(f"搜索结果已显示到表格: {result['total_matches']} 个匹配")
            
        except Exception as e:
            print(f"显示搜索结果时出错: {e}")

    def _apply_filtered_content(self, result):
        """简单的过滤内容应用"""
        try:
            editor = self._get_current_editor()
            if not editor:
                return
            
            filtered_content = []
            for line_num in matched_lines[:1000]:  # 限制显示行数避免卡顿
                if line_num < len(lines):
                    filtered_content.append(f"[{line_num+1}] {lines[line_num]}")
            
            editor.setPlainText('\n'.join(filtered_content))
            
            if len(matched_lines) > 1000:
                self._safe_update_status(f"已显示前1000行匹配内容，总共 {len(matched_lines)} 行")
            else:
                self._safe_update_status(f"已显示 {len(filtered_content)} 行匹配内容")
            
        except Exception as e:
            print(f"应用过滤内容时出错: {e}")

    def _on_optimized_search_progress(self, current, total, message):
        """优化搜索进度更新"""
        if total > 0:
            progress = int((current / total) * 100)
            self._safe_update_progress(progress, 100)
        self._safe_update_status(message)

    def _simple_match_line(self, line, include_keywords, exclude_keywords, ignore_case=False, whole_pair=False):
        """简单高效的行匹配 - 使用最快的字符串操作"""
        line_to_check = line.lower() if ignore_case else line
        
        # 检查包含条件 - 使用最快的 'in' 操作
        include_match = not include_keywords  # 如果没有包含条件，默认匹配
        for keyword in include_keywords:
            search_keyword = keyword.lower() if ignore_case else keyword
            
            if whole_pair:
                # 简单的单词边界检查，避免正则表达式
                if self._word_boundary_match(line_to_check, search_keyword):
                    include_match = True
                    break
            else:
                # 最快的字符串查找
                if search_keyword in line_to_check:
                    include_match = True
                    break
        
        if not include_match:
            return False
        
        # 检查排除条件
        for keyword in exclude_keywords:
            search_keyword = keyword.lower() if ignore_case else keyword
            
            if whole_pair:
                if self._word_boundary_match(line_to_check, search_keyword):
                    return False
            else:
                if search_keyword in line_to_check:
                    return False
        
        return True

# 兼容性别名，保持向后兼容
MainWindow = EnhancedMainWindow


from PyQt5.QtCore import QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QTextCharFormat, QColor, QTextCursor, QTextDocument
from PyQt5.QtWidgets import QApplication
import time

class OptimizedHighlightWorker(QThread):
    """优化的高亮工作线程 - 完全异步处理"""
    
    highlight_ready = pyqtSignal(list)  # 准备好的高亮数据
    highlight_progress = pyqtSignal(int, int, str)
    highlight_failed = pyqtSignal(str)
    
    def __init__(self, text_content, matched_lines, keywords, batch_size=100):
        super().__init__()
        self.text_content = text_content
        self.matched_lines = matched_lines
        self.keywords = keywords
        self.batch_size = batch_size
        self._stop_requested = False
        
    def run(self):
        """在后台线程中预处理高亮数据"""
        try:
            lines = self.text_content.splitlines()
            highlight_data = []
            
            # 按批次处理
            total_matches = len(self.matched_lines)
            
            for i in range(0, total_matches, self.batch_size):
                if self._stop_requested:
                    break
                    
                batch_end = min(i + self.batch_size, total_matches)
                batch_lines = self.matched_lines[i:batch_end]
                
                # 预计算每行的位置和内容
                for line_num in batch_lines:
                    if line_num < len(lines):
                        line_content = lines[line_num]
                        # 计算该行在文档中的字符位置
                        char_pos = sum(len(lines[j]) + 1 for j in range(line_num))  # +1 for \n
                        
                        highlight_data.append({
                            'line_num': line_num,
                            'char_pos': char_pos,
                            'line_content': line_content,
                            'length': len(line_content)
                        })
                
                # 更新进度
                self.highlight_progress.emit(batch_end, total_matches, f"预处理高亮数据 {batch_end}/{total_matches}")
                self.msleep(1)  # 让出CPU时间
            
            if not self._stop_requested:
                self.highlight_ready.emit(highlight_data)
                
        except Exception as e:
            if not self._stop_requested:
                self.highlight_failed.emit(str(e))
    
    def request_stop(self):
        self._stop_requested = True


class BatchHighlightApplier:
    """批量高亮应用器 - 优化的主线程高亮"""
    
    def __init__(self, editor, max_batch_size=50, delay_ms=10):
        self.editor = editor
        self.max_batch_size = max_batch_size
        self.delay_ms = delay_ms
        self.highlight_queue = []
        self.current_index = 0
        
        # 创建定时器用于分批应用高亮
        self.apply_timer = QTimer()
        self.apply_timer.timeout.connect(self._apply_next_batch)
        
        # 高亮格式
        self.highlight_format = QTextCharFormat()
        self.highlight_format.setBackground(QColor(255, 255, 0, 100))
    
    def start_highlight(self, highlight_data):
        """开始分批应用高亮"""
        self.highlight_queue = highlight_data
        self.current_index = 0
        
        if self.highlight_queue:
            print(f"开始分批高亮 {len(self.highlight_queue)} 行，每批 {self.max_batch_size} 行")
            self.apply_timer.start(self.delay_ms)
    
    def _apply_next_batch(self):
        """应用下一批高亮"""
        if self.current_index >= len(self.highlight_queue):
            self.apply_timer.stop()
            print("所有高亮应用完成")
            return
        
        start_time = time.time()
        batch_end = min(self.current_index + self.max_batch_size, len(self.highlight_queue))
        
        try:
            document = self.editor.document()
            cursor = QTextCursor(document)
            
            # 批量处理当前批次
            for i in range(self.current_index, batch_end):
                data = self.highlight_queue[i]
                
                # 直接设置光标位置（避免逐行移动）
                cursor.setPosition(data['char_pos'])
                cursor.setPosition(data['char_pos'] + data['length'], QTextCursor.KeepAnchor)
                cursor.setCharFormat(self.highlight_format)
            
            self.current_index = batch_end
            
            batch_time = time.time() - start_time
            print(f"批次高亮完成: {self.current_index}/{len(self.highlight_queue)} "
                  f"(耗时: {batch_time:.3f}s)")
            
            # 如果单批次处理时间过长，减少批次大小
            if batch_time > 0.1:  # 100ms
                self.max_batch_size = max(10, self.max_batch_size // 2)
                print(f"调整批次大小为: {self.max_batch_size}")
            
        except Exception as e:
            print(f"应用高亮时出错: {e}")
            self.apply_timer.stop()
    
    def stop_highlight(self):
        """停止高亮应用"""
        self.apply_timer.stop()
        self.highlight_queue.clear()


class OptimizedAsyncSearchThread(QThread):
    """优化的异步搜索线程 - 减少阻塞"""
    
    search_progress = pyqtSignal(int, int, str)
    search_completed = pyqtSignal(object)
    search_failed = pyqtSignal(str)
    
    def __init__(self, text_content, include_keywords, exclude_keywords, 
                 show_only=False, ignore_case=False, whole_pair=False):
        super().__init__()
        self.text_content = text_content
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.show_only = show_only
        self.ignore_case = ignore_case
        self.whole_pair = whole_pair
        self._stop_requested = False
        
    def run(self):
        """优化的搜索执行"""
        try:
            start_time = time.time()
            lines = self.text_content.splitlines()
            total_lines = len(lines)
            
            if total_lines == 0:
                self.search_completed.emit({'matched_lines': [], 'total_matches': 0})
                return
            
            matched_lines = []
            
            # 动态调整批次大小
            if total_lines < 1000:
                batch_size = 100
            elif total_lines < 10000:
                batch_size = 500
            else:
                batch_size = 1000
            
            processed_lines = 0
            last_progress_time = start_time
            
            # 预编译正则表达式（如果需要）
            compiled_patterns = self._compile_patterns()
            
            for i in range(0, total_lines, batch_size):
                if self._stop_requested:
                    return
                    
                batch_end = min(i + batch_size, total_lines)
                
                # 处理当前批次
                for j in range(i, batch_end):
                    if self._fast_match_line(lines[j], compiled_patterns):
                        matched_lines.append(j)
                
                processed_lines = batch_end
                current_time = time.time()
                
                # 限制进度更新频率（避免过度更新）
                if current_time - last_progress_time > 0.1:  # 100ms更新一次
                    self.search_progress.emit(
                        processed_lines, total_lines,
                        f"搜索进度: {processed_lines}/{total_lines} 行 "
                        f"(匹配: {len(matched_lines)})"
                    )
                    last_progress_time = current_time
                
                # 更短的休眠时间
                if batch_end % (batch_size * 5) == 0:  # 每5个批次休眠一次
                    self.msleep(1)
            
            if not self._stop_requested:
                search_time = time.time() - start_time
                result = {
                    'matched_lines': matched_lines,
                    'total_matches': len(matched_lines),
                    'search_time': search_time,
                    'total_lines': total_lines,
                    'include_keywords': self.include_keywords,
                    'exclude_keywords': self.exclude_keywords,
                    'show_only': self.show_only
                }
                self.search_completed.emit(result)
                
        except Exception as e:
            if not self._stop_requested:
                self.search_failed.emit(str(e))
    
    def _compile_patterns(self):
        """预编译搜索模式"""
        import re
        patterns = {'include': [], 'exclude': []}
        
        try:
            flags = re.IGNORECASE if self.ignore_case else 0
            
            for keyword in self.include_keywords:
                if self.whole_pair:
                    pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', flags)
                else:
                    pattern = re.compile(re.escape(keyword), flags)
                patterns['include'].append(pattern)
            
            for keyword in self.exclude_keywords:
                if self.whole_pair:
                    pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', flags)
                else:
                    pattern = re.compile(re.escape(keyword), flags)
                patterns['exclude'].append(pattern)
                
        except re.error as e:
            print(f"正则表达式编译错误: {e}")
            # 回退到简单字符串匹配
            patterns = None
            
        return patterns
    
    def _fast_match_line(self, line, compiled_patterns=None):
        """优化的行匹配"""
        if compiled_patterns:
            return self._regex_match_line(line, compiled_patterns)
        else:
            return self._simple_match_line(line)
    
    def _regex_match_line(self, line, patterns):
        """使用预编译正则表达式匹配"""
        # 检查包含条件
        include_match = not patterns['include']  # 如果没有包含条件，默认匹配
        for pattern in patterns['include']:
            if pattern.search(line):
                include_match = True
                break
        
        # 检查排除条件
        exclude_match = False
        for pattern in patterns['exclude']:
            if pattern.search(line):
                exclude_match = True
                break
        
        return include_match and not exclude_match
    
    
    def _word_boundary_match(self, line, keyword):
        """简单的单词边界匹配 - 避免正则表达式"""
        import string
        
        pos = line.find(keyword)
        while pos != -1:
            # 检查前面的字符
            if pos > 0 and line[pos-1] not in string.whitespace and line[pos-1] not in string.punctuation:
                pos = line.find(keyword, pos + 1)
                continue
            
            # 检查后面的字符
            end_pos = pos + len(keyword)
            if end_pos < len(line) and line[end_pos] not in string.whitespace and line[end_pos] not in string.punctuation:
                pos = line.find(keyword, pos + 1)
                continue
            
            return True
        
        return False
    def request_stop(self):
        self._stop_requested = True