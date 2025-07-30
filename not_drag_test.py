import sys
import os
import mmap
import threading
import time
import re
import queue
import psutil
from typing import Optional, List, Tuple, Dict, Set
from dataclasses import dataclass
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                             QScrollArea, QLabel, QPushButton, QHBoxLayout, 
                             QFileDialog, QProgressBar, QLineEdit, QCheckBox,
                             QSpinBox, QGroupBox, QTextEdit, QSplitter, QComboBox,
                             QListWidget, QListWidgetItem, QMessageBox)
from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect)
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen, QBrush


@dataclass
class SearchResult:   # 已完成
    """搜索结果数据类 - 存储每个搜索匹配项的详细信息"""
    line_number: int      # 行号（从0开始）
    column_start: int     # 匹配开始列位置
    column_end: int       # 匹配结束列位置
    matched_text: str     # 匹配的文本内容
    line_content: str     # 完整的行内容（用于上下文显示）
    file_offset: int      # 在文件中的字节偏移量


class ParallelSearchEngine(QThread):    # 已加入
    """
    并行搜索引擎 - 高性能多线程文件搜索
    
    核心思想：
    1. 将大文件分割成多个块，并行搜索
    2. 使用内存映射避免大量IO操作
    3. 智能处理跨块的匹配情况
    4. 实时返回搜索结果，无需等待全部完成
    """
    
    # 信号定义 - 用于与UI线程通信
    search_progress = pyqtSignal(int, int)           # 当前进度, 总进度
    search_result_found = pyqtSignal(object)         # 找到的搜索结果
    search_finished = pyqtSignal(int, float)         # 搜索完成: 结果数量, 耗时
    search_error = pyqtSignal(str)                   # 搜索错误信息
    
    def __init__(self, file_path: str, line_offsets: List[int]):
        super().__init__()
        self.file_path = file_path
        self.line_offsets = line_offsets
        self.should_stop = False
        
        # 搜索参数
        self.search_pattern = ""
        self.case_sensitive = False
        self.use_regex = False
        self.whole_word_only = False
        
        # 性能参数
        self.num_threads = min(8, psutil.cpu_count())  # 线程数 = CPU核心数，最多8个
        self.chunk_size = 1024 * 1024 * 10            # 每个搜索块10MB
        self.overlap_size = 1024                       # 块重叠大小，处理跨块匹配
        
        # 结果管理
        self.results_queue = queue.Queue()
        self.total_results = 0
        self.search_start_time = 0
        
    def setup_search(self, pattern: str, case_sensitive: bool = False, 
                    use_regex: bool = False, whole_word_only: bool = False):
        """
        配置搜索参数
        
        Args:
            pattern: 搜索模式字符串
            case_sensitive: 是否区分大小写
            use_regex: 是否使用正则表达式
            whole_word_only: 是否仅匹配完整单词
        """
        self.search_pattern = pattern
        self.case_sensitive = case_sensitive
        self.use_regex = use_regex
        self.whole_word_only = whole_word_only
        
    def _prepare_regex_pattern(self) -> re.Pattern:
        """
        准备正则表达式模式
        
        Returns:
            编译后的正则表达式对象
        """
        pattern = self.search_pattern
        
        if not self.use_regex:
            # 如果不是正则模式，转义特殊字符
            pattern = re.escape(pattern)
            
        if self.whole_word_only:
            # 添加单词边界
            pattern = r'\b' + pattern + r'\b'
            
        # 设置标志
        flags = 0 if self.case_sensitive else re.IGNORECASE
        
        try:
            return re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"正则表达式错误: {e}")
    
    def _get_file_chunks(self) -> List[Tuple[int, int]]:
        """
        将文件分割成搜索块
        
        Returns:
            List of (start_offset, end_offset) tuples
        """
        file_size = os.path.getsize(self.file_path)
        chunks = []
        
        current_pos = 0
        while current_pos < file_size:
            end_pos = min(current_pos + self.chunk_size, file_size)
            
            # 添加重叠区域，避免跨块匹配丢失
            if end_pos < file_size:
                end_pos += self.overlap_size
                end_pos = min(end_pos, file_size)
                
            chunks.append((current_pos, end_pos))
            current_pos += self.chunk_size
            
        return chunks
        
    def _search_chunk(self, start_offset: int, end_offset: int, 
                     regex_pattern: re.Pattern) -> List[SearchResult]:
        """
        搜索单个文件块
        
        Args:
            start_offset: 块开始偏移量
            end_offset: 块结束偏移量  
            regex_pattern: 编译后的正则表达式
            
        Returns:
            该块中找到的所有搜索结果
        """
        results = []
        
        try:
            with open(self.file_path, 'rb') as file:
                file.seek(start_offset)
                chunk_data = file.read(end_offset - start_offset)
                
            # 尝试解码文本（支持多种编码）
            text_content = self._decode_chunk(chunk_data)
            
            # 执行搜索
            for match in regex_pattern.finditer(text_content):
                if self.should_stop:
                    break
                    
                # 计算在文件中的绝对位置
                absolute_offset = start_offset + match.start()
                
                # 查找匹配所在的行
                line_number = self._find_line_number(absolute_offset)
                if line_number == -1:
                    continue
                    
                # 获取行内容和列位置
                line_content = self._get_line_content(line_number)
                line_start_offset = self.line_offsets[line_number]
                column_start = absolute_offset - line_start_offset
                column_end = column_start + len(match.group())
                
                # 创建搜索结果对象
                result = SearchResult(
                    line_number=line_number,
                    column_start=column_start,
                    column_end=column_end,
                    matched_text=match.group(),
                    line_content=line_content,
                    file_offset=absolute_offset
                )
                
                results.append(result)
                
        except Exception as e:
            print(f"搜索块错误 ({start_offset}-{end_offset}): {e}")
            
        return results
    
    def _decode_chunk(self, chunk_data: bytes) -> str:
        """
        智能解码文本块，支持多种编码
        
        Args:
            chunk_data: 原始字节数据
            
        Returns:
            解码后的文本字符串
        """
        # 尝试常见编码
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin1', 'cp1252']
        
        for encoding in encodings:
            try:
                return chunk_data.decode(encoding)
            except UnicodeDecodeError:
                continue
                
        # 如果都失败，使用错误处理
        return chunk_data.decode('utf-8', errors='ignore')
    
    def _find_line_number(self, file_offset: int) -> int:
        """
        根据文件偏移量查找行号（二分查找）
        
        Args:
            file_offset: 文件中的字节偏移量
            
        Returns:
            行号（从0开始），如果找不到返回-1
        """
        left, right = 0, len(self.line_offsets) - 1
        
        while left <= right:
            mid = (left + right) // 2
            
            if mid + 1 < len(self.line_offsets):
                # 检查偏移量是否在当前行范围内
                if (self.line_offsets[mid] <= file_offset < self.line_offsets[mid + 1]):
                    return mid
                elif file_offset < self.line_offsets[mid]:
                    right = mid - 1
                else:
                    left = mid + 1
            else:
                # 最后一行
                if file_offset >= self.line_offsets[mid]:
                    return mid
                else:
                    right = mid - 1
                    
        return -1
    
    def _get_line_content(self, line_number: int) -> str:
        """
        获取指定行的完整内容
        
        Args:
            line_number: 行号（从0开始）
            
        Returns:
            该行的文本内容
        """
        if line_number >= len(self.line_offsets) - 1:
            return ""
            
        try:
            with open(self.file_path, 'rb') as file:
                start_offset = self.line_offsets[line_number]
                end_offset = (self.line_offsets[line_number + 1] 
                            if line_number + 1 < len(self.line_offsets) 
                            else os.path.getsize(self.file_path))
                
                file.seek(start_offset)
                line_data = file.read(end_offset - start_offset)
                
                return self._decode_chunk(line_data).rstrip('\n\r')
                
        except Exception:
            return ""
    
    def run(self):
        """
        主搜索线程入口 - 协调多个工作线程执行并行搜索
        """
        if not self.search_pattern:
            self.search_error.emit("搜索模式不能为空")
            return
            
        self.search_start_time = time.time()
        self.should_stop = False
        self.total_results = 0
        
        try:
            # 准备正则表达式
            regex_pattern = self._prepare_regex_pattern()
            
            # 获取文件块
            chunks = self._get_file_chunks()
            total_chunks = len(chunks)
            
            # 使用线程池执行并行搜索
            import concurrent.futures
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                # 提交所有搜索任务
                future_to_chunk = {
                    executor.submit(self._search_chunk, start, end, regex_pattern): (start, end)
                    for start, end in chunks
                }
                
                completed_chunks = 0
                
                # 处理完成的任务
                for future in concurrent.futures.as_completed(future_to_chunk):
                    if self.should_stop:
                        # 取消所有未完成的任务
                        for f in future_to_chunk:
                            f.cancel()
                        break
                        
                    try:
                        results = future.result()
                        
                        # 发送找到的结果
                        for result in results:
                            self.search_result_found.emit(result)
                            self.total_results += 1
                            
                        completed_chunks += 1
                        
                        # 更新进度
                        progress = int(completed_chunks * 100 / total_chunks)
                        self.search_progress.emit(progress, self.total_results)
                        
                    except Exception as e:
                        print(f"搜索任务执行错误: {e}")
            
            # 搜索完成
            if not self.should_stop:
                elapsed_time = time.time() - self.search_start_time
                self.search_finished.emit(self.total_results, elapsed_time)
                
        except Exception as e:
            self.search_error.emit(f"搜索引擎错误: {e}")
    
    def stop_search(self):
        """停止搜索"""
        self.should_stop = True


class SearchResultsManager(QObject):    # 修改中
    """
    搜索结果管理器 - 管理所有搜索结果，支持导航和高亮
    """
    
    # 信号定义
    current_result_changed = pyqtSignal(object)  # 当前结果变化
    
    def __init__(self):
        super().__init__()
        self.results: List[SearchResult] = []  # 所有搜索结果
        self.current_index = -1                # 当前结果索引
        self.results_mutex = QMutex()          # 线程安全锁
        
    def add_result(self, result: SearchResult):
        """
        添加搜索结果（线程安全）
        
        Args:
            result: 新的搜索结果
        """
        with QMutexLocker(self.results_mutex):
            # 插入排序，保持结果按行号排序
            insert_pos = 0
            for i, existing_result in enumerate(self.results):
                if (result.line_number < existing_result.line_number or 
                    (result.line_number == existing_result.line_number and 
                     result.column_start < existing_result.column_start)):
                    insert_pos = i
                    break
                insert_pos = i + 1
                
            self.results.insert(insert_pos, result)
            
            # 如果是第一个结果，自动选中
            if len(self.results) == 1:
                self.current_index = 0
                self.current_result_changed.emit(result)
    
    def clear_results(self):
        """清空所有搜索结果"""
        with QMutexLocker(self.results_mutex):
            self.results.clear()
            self.current_index = -1
    
    def get_result_count(self) -> int:
        """获取结果总数"""
        with QMutexLocker(self.results_mutex):
            return len(self.results)
    
    def get_current_result(self) -> Optional[SearchResult]:
        """获取当前选中的结果"""
        with QMutexLocker(self.results_mutex):
            if 0 <= self.current_index < len(self.results):
                return self.results[self.current_index]
            return None
    
    def navigate_to_next(self) -> bool:
        """
        导航到下一个结果
        
        Returns:
            是否成功导航
        """
        with QMutexLocker(self.results_mutex):
            if not self.results:
                return False
                
            self.current_index = (self.current_index + 1) % len(self.results)
            current_result = self.results[self.current_index]
            
        self.current_result_changed.emit(current_result)
        return True
    
    def navigate_to_previous(self) -> bool:
        """
        导航到上一个结果
        
        Returns:
            是否成功导航
        """
        with QMutexLocker(self.results_mutex):
            if not self.results:
                return False
                
            self.current_index = (self.current_index - 1) % len(self.results)
            current_result = self.results[self.current_index]
            
        self.current_result_changed.emit(current_result)
        return True
    
    def navigate_to_index(self, index: int) -> bool:
        """
        导航到指定索引的结果
        
        Args:
            index: 结果索引
            
        Returns:
            是否成功导航
        """
        with QMutexLocker(self.results_mutex):
            if not (0 <= index < len(self.results)):
                return False
                
            self.current_index = index
            current_result = self.results[self.current_index]
            
        self.current_result_changed.emit(current_result)
        return True


class FileIndexer(QThread): # 已完成
    """文件索引器 - 在后台建立行索引"""
    
    indexing_progress = pyqtSignal(int, int)  # 当前行数, 文件大小
    indexing_finished = pyqtSignal(list)      # 行偏移量列表
    indexing_error = pyqtSignal(str)          # 错误信息
    
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.should_stop = False
        
    def run(self):
        """建立文件的行索引 - 记录每行在文件中的字节偏移量"""
        try:
            line_offsets = [0]  # 第一行从偏移量0开始
            
            with open(self.file_path, 'rb') as file:
                file_size = os.path.getsize(self.file_path)
                current_pos = 0
                chunk_size = 1024 * 1024  # 1MB块读取
                
                while current_pos < file_size and not self.should_stop:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                        
                    # 在当前块中查找所有换行符
                    start_pos = 0
                    while True:
                        newline_pos = chunk.find(b'\n', start_pos)
                        if newline_pos == -1:
                            break
                        # 记录下一行的起始偏移量
                        line_offsets.append(current_pos + newline_pos + 1)
                        start_pos = newline_pos + 1
                    
                    current_pos += len(chunk)
                    
                    # 定期发送进度更新（每10000行）
                    if len(line_offsets) % 10000 == 0:
                        self.indexing_progress.emit(len(line_offsets), file_size)
                
                if not self.should_stop:
                    self.indexing_finished.emit(line_offsets)
                    
        except Exception as e:
            self.indexing_error.emit(str(e))
    
    def stop(self):
        """停止索引建立"""
        self.should_stop = True


class VirtualTextWidget(QWidget):   # 修改中
    """
    虚拟文本显示组件 - 只渲染可见行，支持搜索结果高亮和交互式行选择
    """
    
    scroll_changed = pyqtSignal(int)  # 滚动位置变化信号
    line_selected = pyqtSignal(int)   # 行选择信号
    
    def __init__(self):
        super().__init__()
        # 文件和显示相关
        self.file_path = ""
        self.line_offsets = []
        self.visible_lines = 50
        self.line_height = 20
        self.char_width = 8
        self.scroll_position = 0  # 当前显示的第一行行号
        self.total_lines = 0
        
        # 🆕 动态行号区域宽度
        self.line_number_width = 80  # 默认宽度
        self.min_line_number_width = 60  # 最小宽度
        
        # 缓存系统
        self.line_cache = {}      # {行号: 行内容} 的缓存
        self.cache_mutex = QMutex()
        self.max_cache_size = 1000
        
        # 文件内存映射
        self.file_mmap = None
        self.file_handle = None
        
        # 字体和显示设置
        self.font = QFont("Consolas", 10)
        self.setFont(self.font)
        self._update_font_metrics()
        
        # 交互状态
        self.selected_line = -1        # 当前选中的行号（-1表示未选中）
        self.hover_line = -1           # 鼠标悬停的行号
        self.mouse_pressed = False     # 鼠标按下状态
        
        # 🆕 滚动条交互状态
        self.scrollbar_dragging = False  # 是否正在拖拽滚动条
        self.scrollbar_rect = QRect()    # 滚动条区域
        self.scrollbar_thumb_rect = QRect()  # 滚动条滑块区域
        self.drag_start_y = 0            # 拖拽开始的Y坐标
        self.drag_start_scroll = 0       # 拖拽开始时的滚动位置
        
        # 搜索相关
        self.search_results_manager = SearchResultsManager()
        self.search_results_manager.current_result_changed.connect(self._on_search_result_selected)
        self.current_search_result = None
        
        # 🎨 高亮颜色配置
        self.search_highlight_color = QColor(255, 255, 0, 120)      # 搜索结果：亮黄色
        self.current_search_color = QColor(255, 165, 0, 180)        # 当前搜索结果：橙色
        self.selected_line_color = QColor(100, 149, 237, 80)        # 选中行：蓝色半透明
        self.hover_line_color = QColor(200, 200, 200, 50)           # 悬停行：浅灰色
        self.line_number_bg_color = QColor(248, 248, 248)           # 行号背景：浅灰
        self.line_number_selected_color = QColor(100, 149, 237, 120) # 选中行号：蓝色
        
        # 预加载线程
        self.preload_thread = None
        
        # 启用鼠标追踪（用于悬停效果）
        self.setMouseTracking(True)
        
        # 设置焦点策略（支持键盘导航）
        self.setFocusPolicy(Qt.StrongFocus)
        
    def _calculate_line_number_width(self):
        """
        根据总行数动态计算行号区域的宽度
        """
        if self.total_lines <= 0:
            self.line_number_width = self.min_line_number_width
            return
            
        # 计算最大行号的位数
        max_line_number = self.total_lines
        digits = len(str(max_line_number))
        
        # 根据字体宽度计算需要的像素宽度
        # 每个数字 + 一些边距 + 冒号和空格
        needed_width = (digits + 2) * self.char_width + 20  # 额外20像素边距
        
        # 确保不小于最小宽度
        self.line_number_width = max(self.min_line_number_width, needed_width)
        
    def _get_scrollbar_geometry(self):
        """
        计算滚动条的几何信息
        
        Returns:
            tuple: (scrollbar_rect, thumb_rect) 滚动条区域和滑块区域
        """
        if self.total_lines <= self.visible_lines:
            return QRect(), QRect()
            
        scrollbar_width = 15
        scrollbar_height = self.height() - 20
        scrollbar_x = self.width() - scrollbar_width - 5
        scrollbar_y = 10
        
        scrollbar_rect = QRect(scrollbar_x, scrollbar_y, scrollbar_width, scrollbar_height)
        
        # 计算滑块位置和大小
        thumb_height = max(20, int(scrollbar_height * self.visible_lines / self.total_lines))
        max_scroll = max(1, self.total_lines - self.visible_lines)
        thumb_y = scrollbar_y + int((self.scroll_position / max_scroll) * (scrollbar_height - thumb_height))
        
        thumb_rect = QRect(scrollbar_x + 1, thumb_y, scrollbar_width - 2, thumb_height)
        
        return scrollbar_rect, thumb_rect
    
    def _is_point_in_scrollbar(self, point):
        """
        检查点是否在滚动条区域内
        
        Args:
            point: QPoint 对象
            
        Returns:
            bool: 是否在滚动条区域
        """
        scrollbar_rect, _ = self._get_scrollbar_geometry()
        return scrollbar_rect.contains(point)
    
    def _update_font_metrics(self):
        """更新字体度量信息"""
        fm = QFontMetrics(self.font)
        self.line_height = fm.height()
        self.char_width = fm.averageCharWidth()
        self.visible_lines = max(1, self.height() // self.line_height)
        
        # 🆕 重新计算行号区域宽度
        self._calculate_line_number_width()
        """更新字体度量信息"""
        fm = QFontMetrics(self.font)
        self.line_height = fm.height()
        self.char_width = fm.averageCharWidth()
        self.visible_lines = max(1, self.height() // self.line_height)
        
    def load_file(self, file_path: str, line_offsets: List[int]) -> bool:   # 已修改
        """
        加载文件进行显示
        
        Args:
            file_path: 文件路径
            line_offsets: 行偏移量列表
            
        Returns:
            是否加载成功
        """
        self.close_file()
        
        self.file_path = file_path
        self.line_offsets = line_offsets
        self.total_lines = len(line_offsets) - 1
        
        # 建立内存映射
        try:
            self.file_handle = open(file_path, 'rb')
            self.file_mmap = mmap.mmap(
                self.file_handle.fileno(), 
                0, 
                access=mmap.ACCESS_READ
            )
        except Exception as e:
            print(f"文件映射失败: {e}")
            if self.file_handle:
                self.file_handle.close()
            return False
            
        # 重置状态
        self.scroll_position = 0
        self.line_cache.clear()
        self.search_results_manager.clear_results()
        
        # 🆕 重新计算行号区域宽度
        self._calculate_line_number_width()
        
        self.update()
        return True
        
    def close_file(self):   # 不需要
        """关闭当前文件"""
        if self.file_mmap:
            self.file_mmap.close()
            self.file_mmap = None
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None
            
    def get_line_text(self, line_number: int) -> str:
        """
        获取指定行的文本内容（带缓存）
        
        Args:
            line_number: 行号（从0开始）
            
        Returns:
            该行的文本内容
        """
        if not self.file_mmap or line_number >= self.total_lines:
            return ""
            
        # 检查缓存
        with QMutexLocker(self.cache_mutex):
            if line_number in self.line_cache:
                return self.line_cache[line_number]
        
        try:
            # 从内存映射读取
            start_offset = self.line_offsets[line_number]
            end_offset = (self.line_offsets[line_number + 1] 
                         if line_number + 1 < len(self.line_offsets) 
                         else len(self.file_mmap))
            
            line_bytes = self.file_mmap[start_offset:end_offset]
            line_text = line_bytes.decode('utf-8', errors='ignore').rstrip('\n\r')
            
            # 智能缓存管理
            with QMutexLocker(self.cache_mutex):
                if len(self.line_cache) >= self.max_cache_size:
                    # 清理远离当前位置的缓存
                    visible_start = max(0, self.scroll_position - 200)
                    visible_end = min(self.total_lines, self.scroll_position + self.visible_lines + 200)
                    
                    new_cache = {}
                    for line_num, text in self.line_cache.items():
                        if visible_start <= line_num <= visible_end:
                            new_cache[line_num] = text
                    self.line_cache = new_cache
                
                self.line_cache[line_number] = line_text
            
            return line_text
            
        except Exception as e:
            return f"[读取错误: {e}]"
    
    def scroll_to_line(self, line_number: int):
        """
        滚动到指定行
        
        Args:
            line_number: 目标行号
        """
        line_number = max(0, min(line_number, self.total_lines - self.visible_lines))
        if line_number != self.scroll_position:
            self.scroll_position = line_number
            self.scroll_changed.emit(line_number)
            self.start_preload()
            self.update()
    
    def scroll_to_search_result(self, result: SearchResult):
        """
        滚动到搜索结果位置
        
        Args:
            result: 搜索结果对象
        """
        # 计算合适的滚动位置（将结果显示在屏幕中央）
        target_line = max(0, result.line_number - self.visible_lines // 2)
        self.scroll_to_line(target_line)
        
        # 更新当前搜索结果
        self.current_search_result = result
        self.update()
    
    def _on_search_result_selected(self, result: SearchResult):
        """处理搜索结果选择事件"""
        self.scroll_to_search_result(result)
        # 同时选中搜索结果所在的行
        self.select_line(result.line_number)
    
    def select_line(self, line_number: int):
        """
        选中指定行
        
        Args:
            line_number: 要选中的行号（从0开始）
        """
        if 0 <= line_number < self.total_lines:
            old_selected = self.selected_line
            self.selected_line = line_number
            
            # 发送选择信号
            self.line_selected.emit(line_number)
            
            # 如果选中行不在可视区域，滚动到该行
            if not (self.scroll_position <= line_number < self.scroll_position + self.visible_lines):
                # 将选中行显示在屏幕中央
                target_scroll = max(0, line_number - self.visible_lines // 2)
                self.scroll_to_line(target_scroll)
            else:
                # 只更新显示
                self.update()
    
    def clear_selection(self):
        """清除行选择"""
        if self.selected_line != -1:
            self.selected_line = -1
            self.update()
    
    def get_line_number_at_position(self, y_pos: int) -> int:
        """
        根据Y坐标获取对应的行号
        
        Args:
            y_pos: Y坐标位置
            
        Returns:
            行号，如果超出范围返回-1
        """
        if y_pos < 5:  # 顶部边距
            return -1
            
        line_index = (y_pos - 5) // self.line_height
        line_number = self.scroll_position + line_index
        
        if 0 <= line_number < self.total_lines:
            return line_number
        return -1
    
    def start_preload(self):
        """启动预加载线程 - 提前加载屏幕外的内容"""
        if self.preload_thread and self.preload_thread.isRunning():
            return
            
        class PreloadThread(QThread):
            """预加载线程 - 在后台预加载文本内容"""
            def __init__(self, widget, start_line, count):
                super().__init__()
                self.widget = widget
                self.start_line = start_line
                self.count = count
                
            def run(self):
                # 预加载指定范围的行
                for i in range(self.count):
                    line_num = self.start_line + i
                    if 0 <= line_num < self.widget.total_lines:
                        self.widget.get_line_text(line_num)
                        
        # 预加载当前可见区域前后的行
        preload_start = max(0, self.scroll_position - 50)
        preload_count = min(self.visible_lines + 100, self.total_lines - preload_start)
        
        self.preload_thread = PreloadThread(self, preload_start, preload_count)
        self.preload_thread.start()
    
    def mousePressEvent(self, event):
        """
        鼠标按下事件 - 实现点击行选择和滚动条拖拽
        
        Args:
            event: 鼠标事件对象
        """
        if event.button() == Qt.LeftButton and self.file_mmap:
            self.mouse_pressed = True
            
            # 🆕 检查是否点击在滚动条区域
            if self._is_point_in_scrollbar(event.pos()):
                scrollbar_rect, thumb_rect = self._get_scrollbar_geometry()
                
                if thumb_rect.contains(event.pos()):
                    # 开始拖拽滚动条滑块
                    self.scrollbar_dragging = True
                    self.drag_start_y = event.y()
                    self.drag_start_scroll = self.scroll_position
                    self.setCursor(Qt.ClosedHandCursor)
                    return
                else:
                    # 点击滚动条区域但不在滑块上，跳转到对应位置
                    relative_y = event.y() - scrollbar_rect.y()
                    scroll_ratio = relative_y / scrollbar_rect.height()
                    target_line = int(scroll_ratio * max(1, self.total_lines - self.visible_lines))
                    self.scroll_to_line(target_line)
                    return
            
            # 原有的行选择逻辑
            clicked_line = self.get_line_number_at_position(event.y())
            
            if clicked_line != -1:
                # 选中点击的行
                self.select_line(clicked_line)
                
                # 🎯 如果点击的行有搜索结果，自动导航到该结果
                current_results = self._get_visible_search_results()
                for result in current_results:
                    if result.line_number == clicked_line:
                        # 找到对应的搜索结果索引
                        with QMutexLocker(self.search_results_manager.results_mutex):
                            try:
                                result_index = self.search_results_manager.results.index(result)
                                self.search_results_manager.current_index = result_index
                                self.current_search_result = result
                                self.update()
                                break
                            except ValueError:
                                pass
        
        super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.LeftButton:
            self.mouse_pressed = False
            
            # 🆕 结束滚动条拖拽
            if self.scrollbar_dragging:
                self.scrollbar_dragging = False
                self.setCursor(Qt.ArrowCursor)
                
        super().mouseReleaseEvent(event)
    
    def mouseMoveEvent(self, event):
        """
        鼠标移动事件 - 实现悬停高亮效果和滚动条拖拽
        
        Args:
            event: 鼠标事件对象
        """
        if self.file_mmap:
            # 🆕 处理滚动条拖拽
            if self.scrollbar_dragging:
                # 计算拖拽距离
                drag_distance = event.y() - self.drag_start_y
                
                # 计算对应的滚动距离
                scrollbar_rect, _ = self._get_scrollbar_geometry()
                if scrollbar_rect.height() > 0:
                    max_scroll_lines = max(1, self.total_lines - self.visible_lines)
                    scroll_ratio = drag_distance / scrollbar_rect.height()
                    scroll_delta = int(scroll_ratio * max_scroll_lines)
                    
                    new_scroll_position = self.drag_start_scroll + scroll_delta
                    new_scroll_position = max(0, min(new_scroll_position, max_scroll_lines))
                    
                    if new_scroll_position != self.scroll_position:
                        self.scroll_to_line(new_scroll_position)
                return
            
            # 🆕 更新鼠标光标样式
            if self._is_point_in_scrollbar(event.pos()):
                scrollbar_rect, thumb_rect = self._get_scrollbar_geometry()
                if thumb_rect.contains(event.pos()):
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
                
                # 原有的悬停高亮逻辑
                hover_line = self.get_line_number_at_position(event.y())
                
                if hover_line != self.hover_line:
                    self.hover_line = hover_line
                    self.update()  # 全局重绘
        
        super().mouseMoveEvent(event)
    
    def leaveEvent(self, event):
        """鼠标离开控件事件 - 清除悬停效果"""
        if self.hover_line != -1:
            self.hover_line = -1
            self.update()  # 全局重绘，避免局部更新问题
        
        # 🆕 重置鼠标光标
        self.setCursor(Qt.ArrowCursor)
        
        super().leaveEvent(event)
    
    def keyPressEvent(self, event):
        """
        键盘按键事件 - 支持键盘导航
        
        Args:
            event: 键盘事件对象
        """
        if not self.file_mmap:
            return
            
        if event.key() == Qt.Key_Up:
            # 上箭头：选择上一行
            if self.selected_line > 0:
                self.select_line(self.selected_line - 1)
            elif self.selected_line == -1 and self.total_lines > 0:
                # 如果没有选中行，选择当前屏幕中央的行
                center_line = self.scroll_position + self.visible_lines // 2
                self.select_line(min(center_line, self.total_lines - 1))
            event.accept()
            
        elif event.key() == Qt.Key_Down:
            # 下箭头：选择下一行
            if self.selected_line < self.total_lines - 1:
                self.select_line(self.selected_line + 1)
            elif self.selected_line == -1 and self.total_lines > 0:
                # 如果没有选中行，选择当前屏幕中央的行
                center_line = self.scroll_position + self.visible_lines // 2
                self.select_line(min(center_line, self.total_lines - 1))
            event.accept()
            
        elif event.key() == Qt.Key_PageUp:
            # Page Up：向上翻页
            new_scroll = max(0, self.scroll_position - self.visible_lines)
            self.scroll_to_line(new_scroll)
            if self.selected_line != -1:
                new_selected = max(0, self.selected_line - self.visible_lines)
                self.select_line(new_selected)
            event.accept()
            
        elif event.key() == Qt.Key_PageDown:
            # Page Down：向下翻页
            new_scroll = min(self.total_lines - self.visible_lines, 
                           self.scroll_position + self.visible_lines)
            self.scroll_to_line(new_scroll)
            if self.selected_line != -1:
                new_selected = min(self.total_lines - 1, 
                                 self.selected_line + self.visible_lines)
                self.select_line(new_selected)
            event.accept()
            
        elif event.key() == Qt.Key_Home:
            # Home：跳转到文件开头
            self.scroll_to_line(0)
            self.select_line(0)
            event.accept()
            
        elif event.key() == Qt.Key_End:
            # End：跳转到文件结尾
            last_line = self.total_lines - 1
            self.scroll_to_line(max(0, last_line - self.visible_lines + 1))
            self.select_line(last_line)
            event.accept()
            
        elif event.key() == Qt.Key_Escape:
            # Escape：清除选择
            self.clear_selection()
            event.accept()
            
        else:
            super().keyPressEvent(event)
    
    def _update_line_area(self, line_number: int):
        """
        更新指定行的显示区域
        
        Args:
            line_number: 行号
        """
        if not (self.scroll_position <= line_number < self.scroll_position + self.visible_lines):
            return
            
        line_index = line_number - self.scroll_position
        y_start = 5 + line_index * self.line_height
        update_rect = QRect(0, y_start, self.width(), self.line_height)
        self.update(update_rect)
    
    def wheelEvent(self, event):
        """鼠标滚轮事件处理"""
        if not self.file_mmap:
            return
            
        # 计算滚动行数
        delta = event.angleDelta().y()
        scroll_lines = -delta // 120 * 3  # 每次滚动3行
        
        new_position = self.scroll_position + scroll_lines
        self.scroll_to_line(new_position)
        """鼠标滚轮事件处理"""
        if not self.file_mmap:
            return
            
        # 计算滚动行数
        delta = event.angleDelta().y()
        scroll_lines = -delta // 120 * 3  # 每次滚动3行
        
        new_position = self.scroll_position + scroll_lines
        self.scroll_to_line(new_position)
    
    def resizeEvent(self, event):
        """窗口大小变化事件"""
        super().resizeEvent(event)
        self._update_font_metrics()
        self.update()
    
    def paintEvent(self, event):
        """绘制可见文本和各种高亮效果"""
        if not self.file_mmap:
            return
            
        painter = QPainter(self)
        painter.setFont(self.font)
        
        # 绘制背景
        painter.fillRect(self.rect(), QColor(255, 255, 255))
        
        # 🆕 绘制行号区域背景（使用动态宽度）
        line_number_rect = QRect(0, 0, self.line_number_width, self.height())
        painter.fillRect(line_number_rect, self.line_number_bg_color)
        
        # 获取当前屏幕内的搜索结果
        visible_search_results = self._get_visible_search_results()
        
        # 绘制每一行
        y_offset = 5
        for i in range(self.visible_lines):
            line_number = self.scroll_position + i
            if line_number >= self.total_lines:
                break
                
            line_text = self.get_line_text(line_number)
            line_rect = QRect(0, y_offset, self.width(), self.line_height)
            
            # 🎨 绘制行背景高亮（按优先级顺序）
            self._draw_line_backgrounds(painter, line_number, line_rect, y_offset)
            
            # 🎨 绘制搜索结果高亮
            self._draw_search_highlights(painter, line_number, y_offset, visible_search_results)
            
            # 绘制行号
            self._draw_line_number(painter, line_number, y_offset)
            
            # 绘制行内容
            self._draw_line_content(painter, line_text, y_offset)
            
            y_offset += self.line_height
        
        # 🆕 绘制分割线（行号区域和内容区域之间）
        painter.setPen(QColor(200, 200, 200))
        painter.drawLine(self.line_number_width - 1, 0, self.line_number_width - 1, self.height())
        
        # 🆕 绘制可拖拽的滚动条
        self._draw_interactive_scrollbar(painter)
        
        # 绘制焦点边框（当控件获得焦点时）
        if self.hasFocus():
            painter.setPen(QPen(QColor(100, 149, 237), 2))
            painter.drawRect(1, 1, self.width() - 2, self.height() - 2)
    
    def _draw_line_backgrounds(self, painter: QPainter, line_number: int, 
                              line_rect: QRect, y_offset: int):
        """
        绘制行背景高亮效果
        
        Args:
            painter: 绘图对象
            line_number: 当前行号
            line_rect: 行矩形区域
            y_offset: Y偏移量
        """
        # 🆕 使用动态计算的行号宽度
        content_rect = QRect(self.line_number_width, y_offset, 
                           self.width() - self.line_number_width, self.line_height)
        
        # 🔵 优先级1：选中行高亮（最高优先级）
        if line_number == self.selected_line:
            painter.fillRect(content_rect, self.selected_line_color)
            # 同时高亮行号区域
            line_num_rect = QRect(0, y_offset, self.line_number_width, self.line_height)
            painter.fillRect(line_num_rect, self.line_number_selected_color)
            
        # 🔘 优先级2：悬停行高亮（如果没有被选中）
        elif line_number == self.hover_line:
            painter.fillRect(content_rect, self.hover_line_color)
    
    def _draw_line_number(self, painter: QPainter, line_number: int, y_offset: int):
        """
        绘制行号
        
        Args:
            painter: 绘图对象
            line_number: 行号
            y_offset: Y偏移量
        """
        # 设置行号颜色
        if line_number == self.selected_line:
            painter.setPen(QColor(255, 255, 255))  # 选中行用白色
        else:
            painter.setPen(QColor(100, 100, 100))  # 普通行用灰色
            
        line_num_text = f"{line_number + 1}"
        
        # 🆕 右对齐绘制行号，使用动态宽度
        text_rect = QRect(5, y_offset, self.line_number_width - 10, self.line_height)
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, line_num_text)
    
    def _draw_line_content(self, painter: QPainter, line_text: str, y_offset: int):
        """
        绘制行内容文本
        
        Args:
            painter: 绘图对象
            line_text: 行文本内容
            y_offset: Y偏移量
        """
        painter.setPen(QColor(0, 0, 0))
        
        # 🆕 使用动态计算的内容区域起始位置
        content_x = self.line_number_width + 5  # 5像素左边距
        
        # 截断过长的行以提高性能
        max_chars = (self.width() - content_x - 20) // self.char_width  # 20像素右边距（给滚动条留空间）
        if len(line_text) > max_chars:
            line_text = line_text[:max_chars] + "..."
            
        # 绘制文本
        painter.drawText(content_x, y_offset + self.line_height - 5, line_text)
    
    def _get_visible_search_results(self) -> List[SearchResult]:
        """获取当前可见区域内的搜索结果"""
        visible_results = []
        
        with QMutexLocker(self.search_results_manager.results_mutex):
            for result in self.search_results_manager.results:
                if (self.scroll_position <= result.line_number < 
                    self.scroll_position + self.visible_lines):
                    visible_results.append(result)
                    
        return visible_results
    
    def _draw_search_highlights(self, painter: QPainter, line_number: int, 
                              y_offset: int, visible_results: List[SearchResult]):
        """
        绘制搜索结果高亮
        
        Args:
            painter: 绘图对象
            line_number: 当前行号
            y_offset: 当前行的Y坐标
            visible_results: 可见的搜索结果列表
        """
        # 🆕 使用动态计算的内容区域起始位置
        content_x = self.line_number_width + 5  # 内容区域起始X坐标
        
        for result in visible_results:
            if result.line_number == line_number:
                # 🎯 计算高亮区域位置
                start_x = content_x + result.column_start * self.char_width
                width = (result.column_end - result.column_start) * self.char_width
                
                # 🎨 选择高亮颜色
                if result == self.current_search_result:
                    # 当前搜索结果：使用橙色高亮
                    color = self.current_search_color
                    # 绘制额外的边框突出显示
                    highlight_rect = QRect(start_x - 1, y_offset - 1, width + 2, self.line_height + 2)
                    painter.setPen(QPen(QColor(255, 140, 0), 2))  # 橙色边框
                    painter.drawRect(highlight_rect)
                else:
                    # 普通搜索结果：使用黄色高亮
                    color = self.search_highlight_color
                
                # 🌟 绘制搜索结果背景高亮
                highlight_rect = QRect(start_x, y_offset, width, self.line_height)
                painter.fillRect(highlight_rect, color)
                
                # 🔤 为了提高可读性，在高亮文本上绘制深色边框
                if result == self.current_search_result:
                    painter.setPen(QColor(139, 69, 19))  # 深棕色文字
                else:
                    painter.setPen(QColor(0, 0, 0))      # 黑色文字
                    
                # 重新绘制高亮区域的文本，确保可读性
                highlighted_text = result.matched_text
                painter.drawText(start_x, y_offset + self.line_height - 5, highlighted_text)
    
    def _draw_interactive_scrollbar(self, painter: QPainter):
        """
        绘制可交互的滚动条
        
        Args:
            painter: 绘图对象
        """
        if self.total_lines <= self.visible_lines:
            return
            
        scrollbar_rect, thumb_rect = self._get_scrollbar_geometry()
        self.scrollbar_rect = scrollbar_rect
        self.scrollbar_thumb_rect = thumb_rect
        
        # 🎨 绘制滚动条背景
        painter.fillRect(scrollbar_rect, QColor(240, 240, 240))
        painter.setPen(QColor(200, 200, 200))
        painter.drawRect(scrollbar_rect)
        
        # 🎨 绘制滚动条滑块
        if self.scrollbar_dragging:
            # 拖拽时使用深色
            thumb_color = QColor(80, 80, 80, 200)
        elif thumb_rect.contains(self.mapFromGlobal(self.cursor().pos())):
            # 悬停时使用中等颜色
            thumb_color = QColor(120, 120, 120, 180)
        else:
            # 正常状态
            thumb_color = QColor(150, 150, 150, 160)
            
        painter.fillRect(thumb_rect, thumb_color)
        
        # 🎨 绘制滑块边框
        painter.setPen(QColor(100, 100, 100))
        painter.drawRect(thumb_rect)
        
        # 🎨 绘制滑块纹理（三条水平线）
        if thumb_rect.height() > 20:
            painter.setPen(QColor(200, 200, 200))
            center_y = thumb_rect.center().y()
            line_x1 = thumb_rect.x() + 3
            line_x2 = thumb_rect.right() - 3
            
            for i in [-3, 0, 3]:
                painter.drawLine(line_x1, center_y + i, line_x2, center_y + i)
    
    def _draw_scrollbar(self, painter: QPainter):
        """绘制滚动条指示器"""
        if self.total_lines <= self.visible_lines:
            return
            
        scrollbar_width = 12
        scrollbar_height = self.height() - 20
        scrollbar_x = self.width() - scrollbar_width - 5
        scrollbar_y = 10
        
        # 绘制滚动条背景
        bg_rect = QRect(scrollbar_x, scrollbar_y, scrollbar_width, scrollbar_height)
        painter.fillRect(bg_rect, QColor(240, 240, 240))
        
        # 计算滑块位置和大小
        thumb_height = max(20, int(scrollbar_height * self.visible_lines / self.total_lines))
        thumb_y = scrollbar_y + int((self.scroll_position / max(1, self.total_lines - self.visible_lines)) * 
                                  (scrollbar_height - thumb_height))
        
        # 绘制滑块
        thumb_rect = QRect(scrollbar_x + 1, thumb_y, scrollbar_width - 2, thumb_height)
        painter.fillRect(thumb_rect, QColor(100, 100, 100, 180))


class BigFileViewer(QMainWindow):   # 修改中
    """大文件查看器主窗口 - 集成搜索功能"""
    
    def __init__(self):
        super().__init__()
        self.indexer = None
        self.search_engine = None
        self.init_ui()
        
    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("高性能大文件搜索查看器")
        self.setGeometry(100, 100, 1400, 900)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # 工具栏
        toolbar_layout = QHBoxLayout()
        
        self.load_btn = QPushButton("📁 加载大文件")
        self.load_btn.clicked.connect(self.load_file)
        
        self.close_btn = QPushButton("❌ 关闭文件")
        self.close_btn.clicked.connect(self.close_file)
        self.close_btn.setEnabled(False)
        
        toolbar_layout.addWidget(self.load_btn)
        toolbar_layout.addWidget(self.close_btn)
        toolbar_layout.addStretch()
        
        layout.addLayout(toolbar_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # 状态信息
        self.status_label = QLabel("🚀 就绪 - 支持GB级大文件高速搜索")
        layout.addWidget(self.status_label)
        
        # 主分割器
        main_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(main_splitter)
        
        # 文本显示区域
        self.text_widget = VirtualTextWidget()
        self.text_widget.scroll_changed.connect(self.on_scroll_changed)
        main_splitter.addWidget(self.text_widget)
        
        # 右侧控制面板
        right_panel = self.create_right_panel()
        main_splitter.addWidget(right_panel)
        
        # 设置分割器比例
        main_splitter.setSizes([1000, 400])
        
    def create_right_panel(self):
        """创建右侧控制面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # 搜索组
        search_group = self.create_search_group()
        layout.addWidget(search_group)
        
        # 搜索结果组
        results_group = self.create_results_group()
        layout.addWidget(results_group)
        
        # 导航组
        nav_group = self.create_navigation_group()
        layout.addWidget(nav_group)
        
        # 文件信息组
        info_group = self.create_info_group()
        layout.addWidget(info_group)
        
        layout.addStretch()
        return panel
    
    def create_search_group(self):
        """创建搜索控制组"""
        group = QGroupBox("🔍 智能搜索")
        layout = QVBoxLayout(group)
        
        # 搜索输入
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入搜索内容... (支持正则表达式)")
        self.search_input.returnPressed.connect(self.start_search)
        layout.addWidget(self.search_input)
        
        # 搜索选项
        options_layout = QHBoxLayout()
        
        self.case_sensitive_cb = QCheckBox("区分大小写")
        self.regex_cb = QCheckBox("正则表达式")
        self.whole_word_cb = QCheckBox("完整单词")
        
        options_layout.addWidget(self.case_sensitive_cb)
        options_layout.addWidget(self.regex_cb)
        options_layout.addWidget(self.whole_word_cb)
        
        layout.addLayout(options_layout)
        
        # 搜索按钮
        button_layout = QHBoxLayout()
        
        self.search_btn = QPushButton("🚀 开始搜索")
        self.search_btn.clicked.connect(self.start_search)
        
        self.stop_search_btn = QPushButton("🛑 停止搜索")
        self.stop_search_btn.clicked.connect(self.stop_search)
        self.stop_search_btn.setEnabled(False)
        
        button_layout.addWidget(self.search_btn)
        button_layout.addWidget(self.stop_search_btn)
        
        layout.addLayout(button_layout)
        
        # 搜索进度
        self.search_progress_bar = QProgressBar()
        self.search_progress_bar.setVisible(False)
        layout.addWidget(self.search_progress_bar)
        
        # 搜索状态
        self.search_status_label = QLabel("就绪")
        layout.addWidget(self.search_status_label)
        
        return group
    
    def create_results_group(self):
        """创建搜索结果显示组"""
        group = QGroupBox("📋 搜索结果")
        layout = QVBoxLayout(group)
        
        # 结果统计
        self.results_count_label = QLabel("结果: 0")
        layout.addWidget(self.results_count_label)
        
        # 结果导航
        nav_layout = QHBoxLayout()
        
        self.prev_result_btn = QPushButton("⬆️ 上一个")
        self.prev_result_btn.clicked.connect(self.navigate_to_previous_result)
        self.prev_result_btn.setEnabled(False)
        
        self.next_result_btn = QPushButton("⬇️ 下一个")
        self.next_result_btn.clicked.connect(self.navigate_to_next_result)
        self.next_result_btn.setEnabled(False)
        
        nav_layout.addWidget(self.prev_result_btn)
        nav_layout.addWidget(self.next_result_btn)
        
        layout.addLayout(nav_layout)
        
        # 结果列表（显示部分结果）
        self.results_list = QListWidget()
        self.results_list.setMaximumHeight(200)
        self.results_list.itemClicked.connect(self.on_result_item_clicked)
        layout.addWidget(self.results_list)
        
        return group
    
    def create_navigation_group(self):
        """创建导航控制组"""
        group = QGroupBox("🧭 文档导航")
        layout = QVBoxLayout(group)
        
        # 跳转到行
        jump_layout = QHBoxLayout()
        jump_layout.addWidget(QLabel("跳转到行:"))
        
        self.line_input = QLineEdit()
        self.line_input.returnPressed.connect(self.jump_to_line)
        jump_layout.addWidget(self.line_input)
        
        self.jump_btn = QPushButton("🎯 跳转")
        self.jump_btn.clicked.connect(self.jump_to_line)
        jump_layout.addWidget(self.jump_btn)
        
        layout.addLayout(jump_layout)
        
        # 快速导航
        quick_nav_layout = QHBoxLayout()
        
        self.home_btn = QPushButton("🏠 首页")
        self.home_btn.clicked.connect(lambda: self.text_widget.scroll_to_line(0))
        
        self.end_btn = QPushButton("🔚 末页")
        self.end_btn.clicked.connect(lambda: self.text_widget.scroll_to_line(self.text_widget.total_lines))
        
        quick_nav_layout.addWidget(self.home_btn)
        quick_nav_layout.addWidget(self.end_btn)
        
        layout.addLayout(quick_nav_layout)
        
        # 🆕 行选择控制
        selection_layout = QHBoxLayout()
        
        self.clear_selection_btn = QPushButton("❌ 清除选择")
        self.clear_selection_btn.clicked.connect(self.clear_line_selection)
        self.clear_selection_btn.setToolTip("清除当前行选择 (快捷键: Esc)")
        
        selection_layout.addWidget(self.clear_selection_btn)
        
        layout.addLayout(selection_layout)
        
        return group
    
    def create_info_group(self):
        """创建信息显示组"""
        group = QGroupBox("📊 文件信息")
        layout = QVBoxLayout(group)
        
        # 文件信息
        self.file_info_label = QLabel("未加载文件")
        self.file_info_label.setWordWrap(True)
        layout.addWidget(self.file_info_label)
        
        # 位置信息
        self.position_label = QLabel("位置: 0/0")
        layout.addWidget(self.position_label)
        
        # 🆕 选中行信息
        self.selected_line_label = QLabel("选中行: 无")
        self.selected_line_label.setStyleSheet("color: blue; font-weight: bold;")
        layout.addWidget(self.selected_line_label)
        
        # 🆕 选中行内容预览
        self.line_content_preview = QTextEdit()
        self.line_content_preview.setMaximumHeight(60)
        self.line_content_preview.setReadOnly(True)
        self.line_content_preview.setPlaceholderText("选中行内容将在此显示...")
        layout.addWidget(self.line_content_preview)
        
        # 性能统计
        self.performance_label = QLabel("性能统计")
        self.performance_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.performance_label)
        
        self.cache_info_label = QLabel("缓存: 0 行")
        layout.addWidget(self.cache_info_label)
        
        self.memory_info_label = QLabel("内存: 计算中...")
        layout.addWidget(self.memory_info_label)
        
        # 定时更新统计信息
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.update_performance_stats)
        self.stats_timer.start(2000)  # 每2秒更新
        
        return group
    
    def load_file(self):
        """加载文件对话框"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择大文件", "", 
            "文本文件 (*.txt *.log *.csv *.py *.cpp *.java *.js *.json *.xml);;所有文件 (*)"
        )
        
        if not file_path:
            return
            
        # 检查文件大小
        file_size = os.path.getsize(file_path)
        size_mb = file_size / (1024 * 1024)
        
        if size_mb > 1000:  # 超过1GB警告
            reply = QMessageBox.question(
                self, "大文件警告", 
                f"文件大小为 {size_mb:.1f}MB，加载可能需要较长时间。\n确定要继续吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        
        self.status_label.setText(f"🔄 正在建立索引... 文件大小: {size_mb:.1f}MB")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.load_btn.setEnabled(False)
        
        # 清空搜索结果
        self.clear_search_results()
        
        # 启动索引线程
        self.indexer = FileIndexer(file_path)
        self.indexer.indexing_progress.connect(self.on_indexing_progress)
        self.indexer.indexing_finished.connect(self.on_indexing_finished)
        self.indexer.indexing_error.connect(self.on_indexing_error)
        self.indexer.start()
        
    def on_indexing_progress(self, lines, total_size):
        """索引进度更新"""
        progress = min(100, lines * 100 // max(1, total_size // 100))
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"🔄 建立索引中... 已处理 {lines:,} 行")
        
    def on_indexing_finished(self, line_offsets):
        """索引建立完成"""
        self.progress_bar.setVisible(False)
        self.load_btn.setEnabled(True)
        self.close_btn.setEnabled(True)
        
        if self.text_widget.load_file(self.indexer.file_path, line_offsets):
            total_lines = len(line_offsets) - 1
            file_size = os.path.getsize(self.indexer.file_path)
            size_mb = file_size / (1024 * 1024)
            
            self.file_info_label.setText(
                f"📁 {os.path.basename(self.indexer.file_path)}\n"
                f"📏 大小: {size_mb:.1f}MB\n"
                f"📄 行数: {total_lines:,}\n"
                f"📊 平均行长: {file_size // max(1, total_lines):.0f} 字节"
            )
            
            self.status_label.setText(f"✅ 文件加载完成 - {total_lines:,} 行，已准备搜索")
            
            # 启用搜索功能
            self.search_btn.setEnabled(True)
            
        else:
            self.status_label.setText("❌ 文件加载失败")
            
    def on_indexing_error(self, error_msg):
        """索引错误处理"""
        self.progress_bar.setVisible(False)
        self.load_btn.setEnabled(True)
        self.status_label.setText(f"❌ 索引错误: {error_msg}")
        
    def close_file(self):
        """关闭当前文件"""
        # 停止搜索
        self.stop_search()
        
        # 关闭文件
        self.text_widget.close_file()
        self.close_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        
        # 清空信息
        self.file_info_label.setText("未加载文件")
        self.status_label.setText("📁 文件已关闭")
        self.clear_search_results()
        self.clear_line_selection()  # 🆕 清除行选择
        
    def start_search(self): # 已修改
        """开始搜索"""
        search_pattern = self.search_input.text().strip()
        if not search_pattern:
            QMessageBox.warning(self, "搜索警告", "请输入搜索内容！")
            return
            
        if not self.text_widget.file_path:
            QMessageBox.warning(self, "搜索警告", "请先加载文件！")
            return
        
        # 清空之前的搜索结果
        self.clear_search_results()
        
        # 创建搜索引擎
        self.search_engine = ParallelSearchEngine(
            self.text_widget.file_path, 
            self.text_widget.line_offsets
        )
        
        # 配置搜索参数
        self.search_engine.setup_search(
            pattern=search_pattern,
            case_sensitive=self.case_sensitive_cb.isChecked(),
            use_regex=self.regex_cb.isChecked(),
            whole_word_only=self.whole_word_cb.isChecked()
        )
        
        # 连接信号
        self.search_engine.search_progress.connect(self.on_search_progress)
        self.search_engine.search_result_found.connect(self.on_search_result_found)
        self.search_engine.search_finished.connect(self.on_search_finished)
        self.search_engine.search_error.connect(self.on_search_error)
        
        # 更新UI状态
        self.search_btn.setEnabled(False)
        self.stop_search_btn.setEnabled(True)
        self.search_progress_bar.setVisible(True)
        self.search_progress_bar.setValue(0)
        self.search_status_label.setText("🔍 搜索中...")
        
        # 启动搜索
        self.search_engine.start()
        
    def stop_search(self):  # 不需要
        """停止搜索"""
        if self.search_engine:
            self.search_engine.stop_search()
            self.search_engine = None
            
        # 更新UI状态
        self.search_btn.setEnabled(True)
        self.stop_search_btn.setEnabled(False)
        self.search_progress_bar.setVisible(False)
        self.search_status_label.setText("🛑 搜索已停止")
        
    def clear_search_results(self): # 不需要
        """清空搜索结果"""
        self.text_widget.search_results_manager.clear_results()
        self.results_list.clear()
        self.results_count_label.setText("结果: 0")
        self.prev_result_btn.setEnabled(False)
        self.next_result_btn.setEnabled(False)
        self.text_widget.current_search_result = None  # 🆕 清除当前搜索结果
        self.text_widget.update()
        
    def on_search_progress(self, progress, found_count):    # 已修改
        """搜索进度更新"""
        self.search_progress_bar.setValue(progress)
        self.search_status_label.setText(f"🔍 搜索中... 已找到 {found_count} 个结果")
        self.results_count_label.setText(f"结果: {found_count}")
        
    def on_search_result_found(self, result: SearchResult):
        """处理找到的搜索结果"""
        # 添加到结果管理器
        self.text_widget.search_results_manager.add_result(result)
        
        # 更新结果列表（只显示前100个结果以避免界面卡顿）
        if self.results_list.count() < 100:
            item_text = f"行 {result.line_number + 1}: {result.matched_text}"
            if len(result.line_content) > 50:
                item_text += f" - {result.line_content[:50]}..."
            else:
                item_text += f" - {result.line_content}"
                
            list_item = QListWidgetItem(item_text)
            list_item.setData(Qt.UserRole, result)
            self.results_list.addItem(list_item)
        
        # 启用导航按钮
        if not self.prev_result_btn.isEnabled():
            self.prev_result_btn.setEnabled(True)
            self.next_result_btn.setEnabled(True)
            
        # 刷新显示
        self.text_widget.update()
        
    def on_search_finished(self, total_results, elapsed_time):
        """搜索完成"""
        self.search_btn.setEnabled(True)
        self.stop_search_btn.setEnabled(False)
        self.search_progress_bar.setVisible(False)
        
        self.search_status_label.setText(
            f"✅ 搜索完成！找到 {total_results} 个结果，耗时 {elapsed_time:.2f} 秒"
        )
        self.results_count_label.setText(f"结果: {total_results}")
        
        if total_results > 100:
            self.search_status_label.setText(
                self.search_status_label.text() + f"\n(列表仅显示前100个结果)"
            )
        
    def on_search_error(self, error_msg):
        """搜索错误处理"""
        self.search_btn.setEnabled(True)
        self.stop_search_btn.setEnabled(False)
        self.search_progress_bar.setVisible(False)
        self.search_status_label.setText(f"❌ 搜索错误: {error_msg}")
        
        QMessageBox.critical(self, "搜索错误", f"搜索过程中出现错误：\n{error_msg}")
        
    def navigate_to_previous_result(self):
        """导航到上一个搜索结果"""
        self.text_widget.search_results_manager.navigate_to_previous()
        
    def navigate_to_next_result(self):
        """导航到下一个搜索结果"""
        self.text_widget.search_results_manager.navigate_to_next()
        
    def on_result_item_clicked(self, item):
        """点击搜索结果列表项"""
        result = item.data(Qt.UserRole)
        if result:
            self.text_widget.search_results_manager.current_index = \
                self.text_widget.search_results_manager.results.index(result)
            self.text_widget._on_search_result_selected(result)
        
    def jump_to_line(self):
        """跳转到指定行"""
        try:
            line_number = int(self.line_input.text()) - 1  # 转换为0基索引
            if 0 <= line_number < self.text_widget.total_lines:
                self.text_widget.scroll_to_line(line_number)
            else:
                QMessageBox.warning(self, "跳转警告", f"行号超出范围！有效范围: 1-{self.text_widget.total_lines}")
        except ValueError:
            QMessageBox.warning(self, "跳转警告", "请输入有效的行号！")
            
    def on_scroll_changed(self, line_number):
        """滚动位置变化"""
        self.position_label.setText(f"位置: {line_number + 1:,}/{self.text_widget.total_lines:,}")
        
    def on_line_selected(self, line_number):
        """
        处理行选择事件
        
        Args:
            line_number: 选中的行号（从0开始）
        """
        # 更新选中行信息显示
        self.selected_line_label.setText(f"选中行: {line_number + 1:,}")
        
        # 获取并显示选中行的内容
        line_content = self.text_widget.get_line_text(line_number)
        self.line_content_preview.setPlainText(line_content)
        
        # 🎯 检查选中行是否包含搜索结果
        search_results_on_line = []
        with QMutexLocker(self.text_widget.search_results_manager.results_mutex):
            for result in self.text_widget.search_results_manager.results:
                if result.line_number == line_number:
                    search_results_on_line.append(result)
        
        # 如果该行有搜索结果，在内容预览中标出来
        if search_results_on_line:
            cursor = self.line_content_preview.textCursor()
            format_highlight = cursor.charFormat()
            format_highlight.setBackground(QColor(255, 255, 0))  # 黄色背景
            format_highlight.setForeground(QColor(0, 0, 0))      # 黑色文字
            
            # 重新设置文本并高亮搜索结果
            self.line_content_preview.setPlainText(line_content)
            
            for result in search_results_on_line:
                cursor = self.line_content_preview.textCursor()
                cursor.setPosition(result.column_start)
                cursor.setPosition(result.column_end, cursor.KeepAnchor)
                cursor.setCharFormat(format_highlight)
    
    def clear_line_selection(self):
        """清除行选择"""
        self.text_widget.clear_selection()
        self.selected_line_label.setText("选中行: 无")
        self.line_content_preview.clear()
        
    def update_performance_stats(self):
        """更新性能统计信息"""
        if hasattr(self.text_widget, 'line_cache'):
            cache_size = len(self.text_widget.line_cache)
            self.cache_info_label.setText(f"缓存: {cache_size} 行")
            
        # 内存使用统计
        try:
            process = psutil.Process()
            memory_mb = process.memory_info().rss / (1024 * 1024)
            self.memory_info_label.setText(f"内存: {memory_mb:.1f}MB")
        except:
            self.memory_info_label.setText("内存: 无法获取")


def main():
    """主函数 - 启动应用程序"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # 设置应用程序图标和信息
    app.setApplicationName("高性能大文件搜索查看器")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("BigFileViewer")
    
    viewer = BigFileViewer()
    viewer.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()