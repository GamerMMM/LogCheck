from PyQt5.QtWidgets import QPlainTextEdit, QWidget, QTextEdit
from PyQt5.QtGui import QPainter, QTextFormat, QColor, QTextCursor, QTextCharFormat, QTextDocument
from PyQt5.QtCore import Qt, QRect, QSize
import re

import sys
import os
import mmap
import threading
import time
from typing import Optional, List, Tuple
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                             QScrollArea, QLabel, QPushButton, QHBoxLayout, 
                             QFileDialog, QProgressBar, QLineEdit, QCheckBox,
                             QSpinBox, QGroupBox, QTextEdit, QSplitter)
from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect)
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen

from dataform.search_result import SearchResult
from logic.search_manager import SearchResultsManager

class TextDisplay(QWidget):
    """
    虚拟文本显示组件 - 只渲染可见行，支持搜索结果高亮、交互式行选择和文本换行
    """
    
    scroll_changed = pyqtSignal(int)  # 滚动位置变化信号
    line_selected = pyqtSignal(int)   # 行选择信号
    font_size_changed = pyqtSignal(int)  # 字体大小变化信号

    def __init__(self):
        super().__init__()
        self._initSearchParams()
        self._multiThreadPre()

    def _multiThreadPre(self):
        """初始化动态显示可能需要的参量"""
        # 文件和显示相关
        self.file_path = ""
        self.line_offsets = []
        self.visible_lines = 50
        self.line_height = 20
        self.char_width = 8
        self.scroll_position = 0  # 当前显示的第一行行号
        self.total_lines = 0

        # 文件编码相关
        self.detected_encoding = 'utf-8'  # 默认编码

        # 文本换行相关
        self.wrap_enabled = True  # 启用文本换行
        self.content_width = 0    # 内容区域宽度

        # 过滤相关
        self.filter_mode = False
        self.filtered_line_numbers = []
        self.line_number_to_display_index = {}

        # 动态行号区域宽度
        self.line_number_width = 80
        self.min_line_number_width = 60

        # 滚动条交互状态（只保留垂直滚动条）
        self.scrollbar_dragging = False
        self.scrollbar_rect = QRect()
        self.scrollbar_thumb_rect = QRect()
        self.drag_start_y = 0
        self.drag_start_scroll = 0

        self._initmanager()
        self._initEvent()
        self._initColor()
        self._initFont()    
        self._initSearchParams()    

    def _initmanager(self):
        """管理缓存与线程"""
        self.line_cache = {}
        self.cache_mutex = QMutex()
        self.max_cache_size = 1000
        
        self.file_mmap = None
        self.file_handle = None
        self.preload_thread = None
        
    def _initEvent(self):
        # 交互状态
        self.selected_line = -1
        self.hover_line = -1
        self.mouse_pressed = False

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def _initFont(self):
        """字体和显示设置"""
        self.font = QFont("Consolas", 10)
        self.font_size = 10
        self.min_font_size = 6
        self.max_font_size = 72
        self.setFont(self.font)
        self._update_font_metrics()

    def _initColor(self):
        """初始化颜色配置"""
        self.search_highlight_color = QColor(255, 255, 0, 120)
        self.current_search_color = QColor(255, 165, 0, 180)
        self.selected_line_color = QColor(100, 149, 237, 80)
        self.hover_line_color = QColor(200, 200, 200, 50)
        self.line_number_bg_color = QColor(248, 248, 248)
        self.line_number_selected_color = QColor(100, 149, 237, 120)

    def _initSearchParams(self):
        """初始化搜索所需参数"""
        self.original_lines = []
        self.filtered_lines = []
        self.include_keywords = []
        self.exclude_keywords = []
        self.ignore_alpha = True
        self.whole_pair = False

        self.search_results_manager = SearchResultsManager()
        self.search_results_manager.current_result_changed.connect(self._on_search_result_selected)
        self.current_search_result = None

    def set_filter_mode(self, enabled: bool, matching_lines: List[int] = None):
        """设置过滤模式"""
        self.filter_mode = enabled
        
        if enabled and matching_lines:
            self.filtered_line_numbers = sorted(matching_lines)
            self.line_number_to_display_index = {
                line_num: idx for idx, line_num in enumerate(self.filtered_line_numbers)
            }
        else:
            self.filtered_line_numbers = []
            self.line_number_to_display_index = {}
        
        self.scroll_position = 0
        self._calculate_line_number_width()
        self._calculate_content_dimensions()
        self.update()

    def _get_effective_total_lines(self) -> int:
        """获取有效总行数"""
        if self.filter_mode:
            return len(self.filtered_line_numbers)
        return self.total_lines

    def _get_actual_line_number(self, display_index: int) -> int:
        """根据显示索引获取实际行号"""
        if self.filter_mode:
            if 0 <= display_index < len(self.filtered_line_numbers):
                return self.filtered_line_numbers[display_index]
            return -1
        else:
            return display_index if 0 <= display_index < self.total_lines else -1

    def _get_display_index(self, actual_line: int) -> int:
        """根据实际行号获取显示索引"""
        if self.filter_mode:
            return self.line_number_to_display_index.get(actual_line, -1)
        else:
            return actual_line if 0 <= actual_line < self.total_lines else -1

    def __del__(self):
        """析构函数 - 确保资源正确释放"""
        self.cleanup_resources()

    def cleanup_resources(self):
        """清理资源"""
        if self.preload_thread and self.preload_thread.isRunning():
            self.preload_thread.quit()
            self.preload_thread.wait(1000)
        
        if self.file_mmap:
            self.file_mmap.close()
            self.file_mmap = None
        
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

    def load_text(self, file_path: str, line_offsets: List[int]) -> bool:
        """加载文件进行显示 - 改进编码检测"""
        self.cleanup_resources()
        
        self.file_path = file_path
        self.line_offsets = line_offsets
        self.total_lines = len(line_offsets) - 1
        
        try:
            self.file_handle = open(file_path, 'rb')
            self.file_mmap = mmap.mmap(
                self.file_handle.fileno(), 
                0, 
                access=mmap.ACCESS_READ
            )
            
            # 检测文件编码
            self._detect_file_encoding()
            
        except Exception as e:
            print(f"文件映射失败: {e}")
            if self.file_handle:
                self.file_handle.close()
            return False
            
        # 重置状态
        self.scroll_position = 0
        self.line_cache.clear()
        self.search_results_manager.clear_results()
        
        # 重置过滤状态
        self.filter_mode = False
        self.filtered_line_numbers = []
        self.line_number_to_display_index = {}

        self._calculate_line_number_width()
        self._calculate_content_dimensions()
        self.update()
        return True

    def _detect_file_encoding(self):
        """
        检测文件编码
        """
        if not self.file_mmap:
            self.detected_encoding = 'utf-8'
            return
        
        # 读取文件开头的一些字节进行编码检测
        sample_size = min(1024 * 10, len(self.file_mmap))  # 最多读取10KB
        sample_bytes = self.file_mmap[:sample_size]
        
        # 检查BOM标记
        if sample_bytes.startswith(b'\xef\xbb\xbf'):
            self.detected_encoding = 'utf-8-sig'
            print(f"检测到UTF-8 BOM编码")
            return
        elif sample_bytes.startswith(b'\xff\xfe'):
            self.detected_encoding = 'utf-16le'
            print(f"检测到UTF-16LE编码")
            return
        elif sample_bytes.startswith(b'\xfe\xff'):
            self.detected_encoding = 'utf-16be'
            print(f"检测到UTF-16BE编码")
            return
        
        # 尝试不同编码解码样本
        encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'big5', 'latin1']
        
        for encoding in encodings_to_try:
            try:
                decoded = sample_bytes.decode(encoding)
                # 简单的启发式检测：如果解码成功且包含可打印字符
                if self._is_likely_text(decoded):
                    self.detected_encoding = encoding
                    print(f"检测到文件编码: {encoding}")
                    return
            except (UnicodeDecodeError, LookupError):
                continue
        
        # 默认使用UTF-8
        self.detected_encoding = 'utf-8'
        print(f"使用默认编码: utf-8")

    def _is_likely_text(self, text: str) -> bool:
        """
        判断解码后的文本是否像是正常的文本内容
        
        Args:
            text: 解码后的文本
            
        Returns:
            是否像是正常文本
        """
        if not text:
            return False
        
        # 计算可打印字符的比例
        printable_chars = sum(1 for c in text if c.isprintable() or c.isspace())
        ratio = printable_chars / len(text)
        
        # 如果可打印字符比例大于90%，认为是正常文本
        return ratio > 0.9

    def _calculate_line_number_width(self):
        """根据总行数动态计算行号区域的宽度"""
        effective_total = self._get_effective_total_lines()
        if effective_total <= 0:
            self.line_number_width = self.min_line_number_width
            return
            
        if self.filter_mode and self.filtered_line_numbers:
            max_line_number = max(self.filtered_line_numbers)
        else:
            max_line_number = self.total_lines
            
        digits = len(str(max_line_number))
        needed_width = (digits + 2) * self.char_width + 20
        self.line_number_width = max(self.min_line_number_width, needed_width)

    def _calculate_content_dimensions(self):
        """计算内容区域尺寸"""
        scrollbar_width = 20 if self._get_effective_total_lines() > self.visible_lines else 0
        self.content_width = max(100, self.width() - self.line_number_width - scrollbar_width - 10)

    def _wrap_text(self, text: str) -> List[str]:
        """
        将长文本按照可用宽度进行换行
        
        Args:
            text: 原始文本
            
        Returns:
            换行后的文本行列表
        """
        if not text or not self.wrap_enabled:
            return [text]
        
        # 计算每行可显示的字符数
        if self.content_width <= 0 or self.char_width <= 0:
            return [text]
            
        chars_per_line = max(10, (self.content_width - 10) // self.char_width)  # 留10像素边距
        
        if len(text) <= chars_per_line:
            return [text]
        
        # 按字符数简单换行（保持代码简单）
        wrapped_lines = []
        start = 0
        while start < len(text):
            end = min(start + chars_per_line, len(text))
            wrapped_lines.append(text[start:end])
            start = end
            
        return wrapped_lines

    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.scrollbar_dragging:
            delta_y = event.y() - self.drag_start_y
            scrollbar_rect, _ = self._get_scrollbar_geometry()
            
            if scrollbar_rect.height() > 0:
                scroll_ratio = delta_y / scrollbar_rect.height()
                effective_total = self._get_effective_total_lines()
                max_scroll = max(0, effective_total - self.visible_lines)
                
                new_scroll = self.drag_start_scroll + int(scroll_ratio * max_scroll)
                new_scroll = max(0, min(new_scroll, max_scroll))
                
                if new_scroll != self.scroll_position:
                    self.scroll_to_line(new_scroll)
            return

        if not self.file_mmap:
            return
            
        old_hover = self.hover_line
        
        if self._is_point_in_scrollbar(event.pos()):
            self.setCursor(Qt.PointingHandCursor)
            self.hover_line = -1
        else:
            self.setCursor(Qt.ArrowCursor)
            self.hover_line = self.get_line_number_at_position(event.y())
        
        if old_hover != self.hover_line:
            self.update()
        
        super().mouseMoveEvent(event)

    def _get_scrollbar_geometry(self):
        """计算垂直滚动条的几何信息"""
        effective_total = self._get_effective_total_lines()
        if effective_total <= self.visible_lines:
            return QRect(), QRect()
            
        scrollbar_width = 15
        scrollbar_height = self.height() - 20
        scrollbar_x = self.width() - scrollbar_width - 5
        scrollbar_y = 10
        
        scrollbar_rect = QRect(scrollbar_x, scrollbar_y, scrollbar_width, scrollbar_height)
        
        thumb_height = max(20, int(scrollbar_height * self.visible_lines / effective_total))
        max_scroll = max(1, effective_total - self.visible_lines)
        thumb_y = scrollbar_y + int((self.scroll_position / max_scroll) * (scrollbar_height - thumb_height))
        
        thumb_rect = QRect(scrollbar_x + 1, thumb_y, scrollbar_width - 2, thumb_height)
        return scrollbar_rect, thumb_rect
    
    def _is_point_in_scrollbar(self, point):
        """检查点是否在滚动条区域内"""
        scrollbar_rect, _ = self._get_scrollbar_geometry()
        return scrollbar_rect.contains(point)
    
    def _update_font_metrics(self):
        """更新字体度量信息"""
        fm = QFontMetrics(self.font)
        self.line_height = fm.height()
        self.char_width = fm.averageCharWidth()
        self.visible_lines = max(1, self.height() // self.line_height)
        self._calculate_line_number_width()
        self._calculate_content_dimensions()
            
    def get_line_text(self, line_number: int) -> str:
        """获取指定行的文本内容（带缓存）- 改进编码处理"""
        if not self.file_mmap or line_number >= self.total_lines:
            return ""
            
        with QMutexLocker(self.cache_mutex):
            if line_number in self.line_cache:
                return self.line_cache[line_number]
        
        try:
            start_offset = self.line_offsets[line_number]
            end_offset = (self.line_offsets[line_number + 1] 
                         if line_number + 1 < len(self.line_offsets) 
                         else len(self.file_mmap))
            
            line_bytes = self.file_mmap[start_offset:end_offset]
            
            # 改进的编码检测和处理
            line_text = self._decode_line_bytes(line_bytes)
            line_text = line_text.rstrip('\n\r')
            
            with QMutexLocker(self.cache_mutex):
                if len(self.line_cache) >= self.max_cache_size:
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

    def _decode_line_bytes(self, line_bytes: bytes) -> str:
        """
        智能解码字节数据，支持多种编码格式
        
        Args:
            line_bytes: 原始字节数据
            
        Returns:
            解码后的字符串
        """
        # 优先使用检测到的编码
        if hasattr(self, 'detected_encoding') and self.detected_encoding:
            try:
                return line_bytes.decode(self.detected_encoding)
            except (UnicodeDecodeError, LookupError):
                pass  # 如果检测到的编码失败，继续尝试其他编码
        
        # 尝试常见编码，按优先级排序
        encodings = [
            'utf-8',       # 优先尝试UTF-8
            'gbk',         # 中文GBK编码
            'gb2312',      # 简体中文编码
            'big5',        # 繁体中文编码
            'latin1',      # 西欧编码
            'cp1252',      # Windows西欧编码
            'iso-8859-1',  # 通用单字节编码
        ]
        
        for encoding in encodings:
            try:
                return line_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        
        # 如果所有编码都失败，使用UTF-8并忽略错误
        try:
            return line_bytes.decode('utf-8', errors='replace')
        except:
            # 最后的备选方案：转换为可显示的十六进制
            return f"[二进制数据: {line_bytes[:50].hex()}{'...' if len(line_bytes) > 50 else ''}]"
    
    def scroll_to_line(self, line_number: int):
        """滚动到指定行"""
        effective_total = self._get_effective_total_lines()
        line_number = max(0, min(line_number, effective_total - self.visible_lines))
        if line_number != self.scroll_position:
            self.scroll_position = line_number
            self.scroll_changed.emit(line_number)
            self.start_preload()
            self.update()
    
    def scroll_to_search_result(self, result: SearchResult):
        """滚动到搜索结果位置"""
        if self.filter_mode:
            display_index = self._get_display_index(result.line_number)
            if display_index == -1:
                return
            target_index = max(0, display_index - self.visible_lines // 2)
        else:
            target_index = max(0, result.line_number - self.visible_lines // 2)
            
        self.scroll_to_line(target_index)
        self.current_search_result = result
        self.update()
    
    def _on_search_result_selected(self, result: SearchResult):
        """处理搜索结果选择事件"""
        self.scroll_to_search_result(result)
        self.select_line(result.line_number)
    
    def select_line(self, line_number: int):
        """选中指定行"""
        if 0 <= line_number < self.total_lines:
            old_selected = self.selected_line
            self.selected_line = line_number
            self.line_selected.emit(line_number)
            
            if self.filter_mode:
                display_index = self._get_display_index(line_number)
                if display_index == -1:
                    return
                
                if not (self.scroll_position <= display_index < self.scroll_position + self.visible_lines):
                    target_scroll = max(0, display_index - self.visible_lines // 2)
                    self.scroll_to_line(target_scroll)
                else:
                    self.update()
            else:
                if not (self.scroll_position <= line_number < self.scroll_position + self.visible_lines):
                    target_scroll = max(0, line_number - self.visible_lines // 2)
                    self.scroll_to_line(target_scroll)
                else:
                    self.update()
    
    def clear_selection(self):
        """清除行选择"""
        if self.selected_line != -1:
            self.selected_line = -1
            self.update()
    
    def get_line_number_at_position(self, y_pos: int) -> int:
        """根据Y坐标获取对应的行号"""
        if y_pos < 5:
            return -1
            
        line_index = (y_pos - 5) // self.line_height
        display_index = self.scroll_position + line_index
        return self._get_actual_line_number(display_index)
    
    def start_preload(self):
        """启动预加载线程"""
        if self.preload_thread and self.preload_thread.isRunning():
            self.preload_thread.quit()
            self.preload_thread.wait(500)
            
        class PreloadThread(QThread):
            def __init__(self, widget, start_line, count):
                super().__init__()
                self.widget = widget
                self.start_line = start_line
                self.count = count
                self.should_stop = False
                
            def run(self):
                for i in range(self.count):
                    if self.should_stop:
                        break
                    display_index = self.start_line + i
                    actual_line = self.widget._get_actual_line_number(display_index)
                    if actual_line != -1 and 0 <= actual_line < self.widget.total_lines:
                        self.widget.get_line_text(actual_line)
                        
            def stop(self):
                self.should_stop = True
                        
        preload_start = max(0, self.scroll_position - 50)
        effective_total = self._get_effective_total_lines()
        preload_count = min(self.visible_lines + 100, effective_total - preload_start)
        
        if preload_count > 0:
            self.preload_thread = PreloadThread(self, preload_start, preload_count)
            self.preload_thread.start()
    
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.LeftButton and self.file_mmap:
            self.mouse_pressed = True
            
            if self._is_point_in_scrollbar(event.pos()):
                scrollbar_rect, thumb_rect = self._get_scrollbar_geometry()
                
                if thumb_rect.contains(event.pos()):
                    self.scrollbar_dragging = True
                    self.drag_start_y = event.y()
                    self.drag_start_scroll = self.scroll_position
                    self.setCursor(Qt.ClosedHandCursor)
                    return
                else:
                    relative_y = event.y() - scrollbar_rect.y()
                    scroll_ratio = relative_y / scrollbar_rect.height()
                    effective_total = self._get_effective_total_lines()
                    target_line = int(scroll_ratio * max(1, effective_total - self.visible_lines))
                    self.scroll_to_line(target_line)
                    return
            
            clicked_line = self.get_line_number_at_position(event.y())
            
            if clicked_line != -1:
                self.select_line(clicked_line)
                
                current_results = self._get_visible_search_results()
                for result in current_results:
                    if result.line_number == clicked_line:
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
            
            if self.scrollbar_dragging:
                self.scrollbar_dragging = False
                self.setCursor(Qt.ArrowCursor)
                
        super().mouseReleaseEvent(event)
        
    def leaveEvent(self, event):
        """鼠标离开控件事件"""
        if self.hover_line != -1:
            self.hover_line = -1
            self.update()
        
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)
    
    def keyPressEvent(self, event):
        """键盘按键事件"""
        if not self.file_mmap:
            return
            
        effective_total = self._get_effective_total_lines()
        
        if event.key() == Qt.Key_Up:
            if self.selected_line != -1:
                if self.filter_mode:
                    display_index = self._get_display_index(self.selected_line)
                    if display_index > 0:
                        new_actual = self._get_actual_line_number(display_index - 1)
                        if new_actual != -1:
                            self.select_line(new_actual)
                else:
                    if self.selected_line > 0:
                        self.select_line(self.selected_line - 1)
            elif effective_total > 0:
                center_display = self.scroll_position + self.visible_lines // 2
                center_actual = self._get_actual_line_number(center_display)
                if center_actual != -1:
                    self.select_line(center_actual)
            event.accept()
            
        elif event.key() == Qt.Key_Down:
            if self.selected_line != -1:
                if self.filter_mode:
                    display_index = self._get_display_index(self.selected_line)
                    if display_index < len(self.filtered_line_numbers) - 1:
                        new_actual = self._get_actual_line_number(display_index + 1)
                        if new_actual != -1:
                            self.select_line(new_actual)
                else:
                    if self.selected_line < self.total_lines - 1:
                        self.select_line(self.selected_line + 1)
            elif effective_total > 0:
                center_display = self.scroll_position + self.visible_lines // 2
                center_actual = self._get_actual_line_number(center_display)
                if center_actual != -1:
                    self.select_line(center_actual)
            event.accept()
            
        elif event.key() == Qt.Key_PageUp:
            new_scroll = max(0, self.scroll_position - self.visible_lines)
            self.scroll_to_line(new_scroll)
            if self.selected_line != -1:
                if self.filter_mode:
                    display_index = self._get_display_index(self.selected_line)
                    new_display = max(0, display_index - self.visible_lines)
                    new_actual = self._get_actual_line_number(new_display)
                    if new_actual != -1:
                        self.select_line(new_actual)
                else:
                    new_selected = max(0, self.selected_line - self.visible_lines)
                    self.select_line(new_selected)
            event.accept()
            
        elif event.key() == Qt.Key_PageDown:
            new_scroll = min(effective_total - self.visible_lines, 
                           self.scroll_position + self.visible_lines)
            self.scroll_to_line(new_scroll)
            if self.selected_line != -1:
                if self.filter_mode:
                    display_index = self._get_display_index(self.selected_line)
                    new_display = min(len(self.filtered_line_numbers) - 1, 
                                    display_index + self.visible_lines)
                    new_actual = self._get_actual_line_number(new_display)
                    if new_actual != -1:
                        self.select_line(new_actual)
                else:
                    new_selected = min(self.total_lines - 1, 
                                     self.selected_line + self.visible_lines)
                    self.select_line(new_selected)
            event.accept()
            
        elif event.key() == Qt.Key_Home:
            self.scroll_to_line(0)
            first_actual = self._get_actual_line_number(0)
            if first_actual != -1:
                self.select_line(first_actual)
            event.accept()
            
        elif event.key() == Qt.Key_End:
            effective_total = self._get_effective_total_lines()
            last_index = effective_total - 1
            self.scroll_to_line(max(0, last_index - self.visible_lines + 1))
            last_actual = self._get_actual_line_number(last_index)
            if last_actual != -1:
                self.select_line(last_actual)
            event.accept()
            
        elif event.key() == Qt.Key_Escape:
            self.clear_selection()
            event.accept()
            
        elif event.key() == Qt.Key_Plus and event.modifiers() & Qt.ControlModifier:
            self.zoom_in()
            event.accept()
            
        elif event.key() == Qt.Key_Minus and event.modifiers() & Qt.ControlModifier:
            self.zoom_out()
            event.accept()
            
        elif event.key() == Qt.Key_0 and event.modifiers() & Qt.ControlModifier:
            self.reset_zoom()
            event.accept()
            
        else:
            super().keyPressEvent(event)
    
    def wheelEvent(self, event):
        """鼠标滚轮事件处理"""
        if not self.file_mmap:
            return
        
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            delta = event.angleDelta().y()
            scroll_lines = -delta // 120 * 3
            
            new_position = self.scroll_position + scroll_lines
            self.scroll_to_line(new_position)
            event.accept()

    def zoom_in(self):
        """放大字体"""
        if self.font_size < self.max_font_size:
            self.font_size += 1
            self._update_font_size()

    def zoom_out(self):
        """缩小字体"""
        if self.font_size > self.min_font_size:
            self.font_size -= 1
            self._update_font_size()

    def reset_zoom(self):
        """重置字体大小"""
        self.font_size = 10
        self._update_font_size()

    def _update_font_size(self):
        """更新字体大小并重新计算相关参数"""
        self.font.setPointSize(self.font_size)
        self.setFont(self.font)
        self._update_font_metrics()
        
        old_visible_lines = self.visible_lines
        self.visible_lines = max(1, self.height() // self.line_height)
        
        if old_visible_lines != self.visible_lines:
            current_center = self.scroll_position + old_visible_lines // 2
            new_scroll = max(0, current_center - self.visible_lines // 2)
            self.scroll_position = new_scroll
        
        self._calculate_line_number_width()
        self.font_size_changed.emit(self.font_size)
        self.update()
    
    def resizeEvent(self, event):
        """窗口大小变化事件"""
        super().resizeEvent(event)
        self._update_font_metrics()
        self._calculate_content_dimensions()
        self.update()
    
    def paintEvent(self, event):
        """绘制可见文本和各种高亮效果"""
        if not self.file_mmap:
            return
            
        painter = QPainter(self)
        try:
            painter.setFont(self.font)
            
            # 绘制背景
            painter.fillRect(self.rect(), QColor(255, 255, 255))
            
            # 绘制行号区域背景
            line_number_rect = QRect(0, 0, self.line_number_width, self.height())
            painter.fillRect(line_number_rect, self.line_number_bg_color)
            
            # 获取当前屏幕内的搜索结果
            visible_search_results = self._get_visible_search_results()
            
            # 绘制每一行
            y_offset = 5
            for i in range(self.visible_lines):
                display_index = self.scroll_position + i
                actual_line_number = self._get_actual_line_number(display_index)
                
                if actual_line_number == -1:
                    break
                    
                line_text = self.get_line_text(actual_line_number)
                
                # 检查是否需要换行
                wrapped_lines = self._wrap_text(line_text)
                
                # 绘制这一逻辑行的所有物理行
                for wrap_index, wrapped_line in enumerate(wrapped_lines):
                    if y_offset + self.line_height > self.height():
                        break
                    
                    # 绘制行背景高亮（只在第一个换行行显示）
                    if wrap_index == 0:
                        self._draw_line_backgrounds(painter, actual_line_number, y_offset)
                    
                    # 绘制搜索结果高亮
                    self._draw_search_highlights(painter, actual_line_number, y_offset, 
                                                visible_search_results, wrapped_line, wrap_index, len(wrapped_lines))
                    
                    # 绘制行号（只在第一个换行行显示）
                    if wrap_index == 0:
                        self._draw_line_number(painter, actual_line_number, y_offset)
                    
                    # 绘制行内容
                    self._draw_line_content(painter, wrapped_line, y_offset)
                    
                    y_offset += self.line_height
                    
                    # 如果已经超出可见区域，停止绘制
                    if y_offset >= self.height():
                        break
            
            # 绘制分割线（行号区域和内容区域之间）
            painter.setPen(QColor(200, 200, 200))
            painter.drawLine(self.line_number_width - 1, 0, self.line_number_width - 1, self.height())
            
            # 绘制滚动条
            self._draw_interactive_scrollbar(painter)
            
            # 绘制焦点边框
            if self.hasFocus():
                painter.setPen(QPen(QColor(100, 149, 237), 2))
                painter.drawRect(1, 1, self.width() - 2, self.height() - 2)

        finally:
            painter.end()  
    
    def _draw_line_backgrounds(self, painter: QPainter, line_number: int, y_offset: int):
        """绘制行背景高亮效果"""
        content_rect = QRect(self.line_number_width, y_offset, 
                           self.width() - self.line_number_width, self.line_height)
        
        if line_number == self.selected_line:
            painter.fillRect(content_rect, self.selected_line_color)
            line_num_rect = QRect(0, y_offset, self.line_number_width, self.line_height)
            painter.fillRect(line_num_rect, self.line_number_selected_color)
            
        elif line_number == self.hover_line:
            painter.fillRect(content_rect, self.hover_line_color)
    
    def _draw_line_number(self, painter: QPainter, line_number: int, y_offset: int):
        """绘制行号"""
        if line_number == self.selected_line:
            painter.setPen(QColor(255, 255, 255))
        else:
            painter.setPen(QColor(100, 100, 100))
            
        line_num_text = f"{line_number + 1}"
        text_rect = QRect(5, y_offset, self.line_number_width - 10, self.line_height)
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, line_num_text)
    
    def _draw_line_content(self, painter: QPainter, line_text: str, y_offset: int):
        """绘制行内容文本"""
        painter.setPen(QColor(0, 0, 0))
        content_x = self.line_number_width + 5
        
        # 计算可用宽度
        scrollbar_width = 20 if self._get_effective_total_lines() > self.visible_lines else 0
        available_width = self.width() - content_x - scrollbar_width - 10
        
        # 绘制文本
        text_rect = QRect(content_x, y_offset, available_width, self.line_height)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, line_text)
    
    def _get_visible_search_results(self) -> List[SearchResult]:
        """获取当前可见区域内的搜索结果"""
        visible_results = []
        
        with QMutexLocker(self.search_results_manager.results_mutex):
            for result in self.search_results_manager.results:
                if self.filter_mode:
                    display_index = self._get_display_index(result.line_number)
                    if (display_index != -1 and 
                        self.scroll_position <= display_index < self.scroll_position + self.visible_lines):
                        visible_results.append(result)
                else:
                    if (self.scroll_position <= result.line_number < 
                        self.scroll_position + self.visible_lines):
                        visible_results.append(result)
                    
        return visible_results
    
    def _draw_search_highlights(self, painter: QPainter, line_number: int, 
                              y_offset: int, visible_results: List[SearchResult],
                              wrapped_line: str, wrap_index: int, total_wraps: int):
        """绘制搜索结果高亮 - 支持换行文本"""
        content_x = self.line_number_width + 5
        
        for result in visible_results:
            if result.line_number == line_number:
                # 计算在当前换行中的匹配位置
                chars_per_line = max(10, (self.content_width - 10) // self.char_width)
                
                # 计算这个换行段在原始文本中的起始和结束位置
                wrap_start = wrap_index * chars_per_line
                wrap_end = min(wrap_start + chars_per_line, len(result.line_content))
                
                # 检查搜索结果是否在当前换行段中
                if (result.column_start < wrap_end and result.column_end > wrap_start):
                    # 计算在当前换行段中的相对位置
                    highlight_start = max(0, result.column_start - wrap_start)
                    highlight_end = min(len(wrapped_line), result.column_end - wrap_start)
                    
                    if highlight_start < highlight_end:
                        # 计算高亮区域位置
                        start_x = content_x + highlight_start * self.char_width
                        width = (highlight_end - highlight_start) * self.char_width
                        
                        # 选择高亮颜色
                        if result == self.current_search_result:
                            color = self.current_search_color
                            highlight_rect = QRect(start_x - 1, y_offset - 1, width + 2, self.line_height + 2)
                            painter.setPen(QPen(QColor(255, 140, 0), 2))
                            painter.drawRect(highlight_rect)
                        else:
                            color = self.search_highlight_color
                        
                        # 绘制搜索结果背景高亮
                        highlight_rect = QRect(start_x, y_offset, width, self.line_height)
                        painter.fillRect(highlight_rect, color)
                        
                        # 重新绘制高亮区域的文本
                        if result == self.current_search_result:
                            painter.setPen(QColor(139, 69, 19))
                        else:
                            painter.setPen(QColor(0, 0, 0))
                            
                        highlighted_text = wrapped_line[highlight_start:highlight_end]
                        painter.drawText(start_x, y_offset + self.line_height - 5, highlighted_text)
    
    def _draw_interactive_scrollbar(self, painter: QPainter):
        """绘制交互式滚动条"""
        effective_total = self._get_effective_total_lines()
        if effective_total <= self.visible_lines:
            return
            
        scrollbar_rect, thumb_rect = self._get_scrollbar_geometry()
        self.scrollbar_rect = scrollbar_rect
        self.scrollbar_thumb_rect = thumb_rect
        
        # 绘制滚动条背景
        painter.fillRect(scrollbar_rect, QColor(240, 240, 240))
        painter.setPen(QColor(200, 200, 200))
        painter.drawRect(scrollbar_rect)
        
        # 绘制滚动条滑块
        if self.scrollbar_dragging:
            thumb_color = QColor(80, 80, 80, 200)
        else:
            thumb_color = QColor(150, 150, 150, 160)
            
        painter.fillRect(thumb_rect, thumb_color)
        painter.setPen(QColor(100, 100, 100))
        painter.drawRect(thumb_rect)

    def get_search_res(self) -> tuple[int, str, str]:
        """获取搜索结果摘要"""
        results_count = len(self.search_results_manager.results)
        
        include_desc = ', '.join(self.include_keywords) if self.include_keywords else "无"
        exclude_desc = ', '.join(self.exclude_keywords) if self.exclude_keywords else "无"
        
        pattern = f"包含: {include_desc} | 排除: {exclude_desc}"
        description = f"包含：{include_desc}\n排除：{exclude_desc}\n共找到 {results_count} 个结果"
        
        return results_count, pattern, description

    def clear_filtered_display(self):
        """清除过滤显示，回到正常模式"""
        self.set_filter_mode(False)

    def update_filtered_display(self, matching_line_numbers: List[int]):
        """更新过滤显示"""
        if matching_line_numbers:
            self.set_filter_mode(True, matching_line_numbers)
        else:
            self.set_filter_mode(False)

    def set_encoding(self, encoding: str):
        """
        手动设置文件编码
        
        Args:
            encoding: 编码名称（如 'utf-8', 'gbk', 'latin1' 等）
        """
        try:
            # 测试编码是否有效
            test_bytes = b'test'
            test_bytes.decode(encoding)
            
            self.detected_encoding = encoding
            print(f"手动设置编码为: {encoding}")
            
            # 清除缓存，强制重新解码
            self.line_cache.clear()
            self.update()
            
        except (UnicodeDecodeError, LookupError):
            print(f"无效的编码: {encoding}")

    def get_current_encoding(self) -> str:
        """获取当前使用的编码"""
        return getattr(self, 'detected_encoding', 'utf-8')

    def toggle_text_wrap(self):
        """切换文本换行模式"""
        self.wrap_enabled = not self.wrap_enabled
        self.update()

    def set_text_wrap(self, enabled: bool):
        """设置文本换行模式"""
        if self.wrap_enabled != enabled:
            self.wrap_enabled = enabled
            self.update()
        """切换文本换行模式"""
        self.wrap_enabled = not self.wrap_enabled
        self.update()

    def set_text_wrap(self, enabled: bool):
        """设置文本换行模式"""
        if self.wrap_enabled != enabled:
            self.wrap_enabled = enabled
            self.update()