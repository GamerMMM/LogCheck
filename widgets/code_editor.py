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
    虚拟文本显示组件 - 只渲染可见行，支持搜索结果高亮和交互式行选择
    优化版本：支持只显示匹配行功能
    """
    
    scroll_changed = pyqtSignal(int)  # 滚动位置变化信号
    line_selected = pyqtSignal(int)   # 行选择信号

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

        # 过滤相关 - 新增
        self.filtered_line_numbers = set()  # 只显示匹配行时使用
        self.display_mode = "all"  # "all" 或 "filtered"
        self.filtered_lines_mapping = []  # 过滤行到原行号的映射

        # 🆕 动态行号区域宽度
        self.line_number_width = 80  # 默认宽度
        self.min_line_number_width = 60  # 最小宽度

        # 🆕 滚动条交互状态
        self.scrollbar_dragging = False  # 是否正在拖拽滚动条
        self.scrollbar_rect = QRect()    # 滚动条区域
        self.scrollbar_thumb_rect = QRect()  # 滚动条滑块区域
        self.drag_start_y = 0            # 拖拽开始的Y坐标
        self.drag_start_scroll = 0       # 拖拽开始时的滚动位置

        self._initmanager()
        self._initEvent()
        self._initColor()
        self._initFont()    
        self._initSearchParams()    

    def _initmanager(self):
        """管理缓存与线程"""
        # 缓存系统
        self.line_cache = {}      # {行号: 行内容} 的缓存
        self.cache_mutex = QMutex()
        self.max_cache_size = 1000
        
        # 文件内存映射
        self.file_mmap = None
        self.file_handle = None
        
        # 预加载线程
        self.preload_thread = None
        
    def _initEvent(self):
        # 交互状态
        self.selected_line = -1        # 当前选中的行号（-1表示未选中）
        self.hover_line = -1           # 鼠标悬停的行号
        self.mouse_pressed = False     # 鼠标按下状态

        # 启用鼠标追踪（用于悬停效果）
        self.setMouseTracking(True)
        
        # 设置焦点策略（支持键盘导航）
        self.setFocusPolicy(Qt.StrongFocus)

    def _initFont(self):
        """字体和显示设置"""
        self.font = QFont("Consolas", 10)
        self.setFont(self.font)
        self._update_font_metrics()

    def _initColor(self):
        """初始化颜色配置"""
        # 🎨 高亮颜色配置
        self.search_highlight_color = QColor(255, 255, 0, 120)      # 搜索结果：亮黄色
        self.current_search_color = QColor(255, 165, 0, 180)        # 当前搜索结果：橙色
        self.selected_line_color = QColor(100, 149, 237, 80)        # 选中行：蓝色半透明
        self.hover_line_color = QColor(200, 200, 200, 50)           # 悬停行：浅灰色
        self.line_number_bg_color = QColor(248, 248, 248)           # 行号背景：浅灰
        self.line_number_selected_color = QColor(100, 149, 237, 120) # 选中行号：蓝色    

    def _initSearchParams(self):
        """初始化搜索所需参数"""
        self.original_lines = []
        self.filtered_lines = []

        self.include_keywords = []
        self.exclude_keywords = []
        self.ignore_alpha = True
        self.whole_pair = False

        # 搜索相关
        self.search_results_manager = SearchResultsManager()
        self.search_results_manager.current_result_changed.connect(self._on_search_result_selected)
        self.current_search_result = None

    def set_display_mode(self, mode: str):
        """
        设置显示模式
        
        Args:
            mode: "all" 显示所有行，"filtered" 只显示匹配行
        """
        if mode != self.display_mode:
            self.display_mode = mode
            if mode == "filtered":
                self._build_filtered_mapping()
            else:
                self.filtered_lines_mapping = []
            self._calculate_line_number_width()
            self.update()

    def _build_filtered_mapping(self):
        """构建过滤行映射"""
        self.filtered_lines_mapping = []
        if self.filtered_line_numbers:
            self.filtered_lines_mapping = sorted(list(self.filtered_line_numbers))

    def _get_effective_total_lines(self) -> int:
        """获取有效总行数"""
        if self.display_mode == "filtered":
            return len(self.filtered_lines_mapping)
        return self.total_lines

    def _get_actual_line_number(self, display_line: int) -> int:
        """
        根据显示行号获取实际行号
        
        Args:
            display_line: 显示的行号
            
        Returns:
            实际文件中的行号
        """
        if self.display_mode == "filtered":
            if 0 <= display_line < len(self.filtered_lines_mapping):
                return self.filtered_lines_mapping[display_line]
            return -1
        return display_line

    def _get_display_line_number(self, actual_line: int) -> int:
        """
        根据实际行号获取显示行号
        
        Args:
            actual_line: 实际文件中的行号
            
        Returns:
            显示的行号，如果不在过滤列表中返回-1
        """
        if self.display_mode == "filtered":
            try:
                return self.filtered_lines_mapping.index(actual_line)
            except ValueError:
                return -1
        return actual_line

    def load_text(self, file_path: str, line_offsets: List[int]) -> bool:
        """
        加载文件进行显示
        
        Args:
            file_path: 文件路径
            line_offsets: 行偏移量列表
            
        Returns:
            是否加载成功
        """
        
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
        self.filtered_line_numbers.clear()
        self.display_mode = "all"

        # 🆕 重新计算行号区域宽度
        self._calculate_line_number_width()

        self.update()
        return True

    def _calculate_line_number_width(self):
        """
        根据总行数动态计算行号区域的宽度
        """
        effective_total = self._get_effective_total_lines()
        if effective_total <= 0:
            self.line_number_width = self.min_line_number_width
            return
            
        # 计算最大行号的位数
        max_line_number = self.total_lines if self.display_mode == "all" else max(self.filtered_lines_mapping) if self.filtered_lines_mapping else 1
        digits = len(str(max_line_number))
        
        # 根据字体宽度计算需要的像素宽度
        needed_width = (digits + 2) * self.char_width + 20  # 额外20像素边距
        
        # 确保不小于最小宽度
        self.line_number_width = max(self.min_line_number_width, needed_width)

    def mouseMoveEvent(self, event):
        """
        鼠标移动事件 - 实现悬停效果和滚动条拖拽
        """
        if self.scrollbar_dragging:
            # 处理滚动条拖拽
            delta_y = event.y() - self.drag_start_y
            scrollbar_rect, _ = self._get_scrollbar_geometry()
            
            if scrollbar_rect.height() > 0:
                # 计算滚动比例
                scroll_ratio = delta_y / scrollbar_rect.height()
                effective_total = self._get_effective_total_lines()
                max_scroll = max(0, effective_total - self.visible_lines)
                
                new_scroll = self.drag_start_scroll + int(scroll_ratio * max_scroll)
                new_scroll = max(0, min(new_scroll, max_scroll))
                
                if new_scroll != self.scroll_position:
                    self.scroll_to_line(new_scroll)
            return
            
        # 原有的悬停逻辑
        if not self.file_mmap:
            return
            
        old_hover = self.hover_line
        
        # 检查是否在滚动条区域
        if self._is_point_in_scrollbar(event.pos()):
            self.setCursor(Qt.PointingHandCursor)
            self.hover_line = -1
        else:
            self.setCursor(Qt.ArrowCursor)
            # 获取悬停的行号
            self.hover_line = self.get_line_number_at_position(event.y())
        
        # 如果悬停行改变，更新显示
        if old_hover != self.hover_line:
            self.update()
        
        super().mouseMoveEvent(event)

    def _get_scrollbar_geometry(self):
        """
        计算滚动条的几何信息
        
        Returns:
            tuple: (scrollbar_rect, thumb_rect) 滚动条区域和滑块区域
        """
        effective_total = self._get_effective_total_lines()
        if effective_total <= self.visible_lines:
            return QRect(), QRect()
            
        scrollbar_width = 15
        scrollbar_height = self.height() - 20
        scrollbar_x = self.width() - scrollbar_width - 5
        scrollbar_y = 10
        
        scrollbar_rect = QRect(scrollbar_x, scrollbar_y, scrollbar_width, scrollbar_height)
        
        # 计算滑块位置和大小
        thumb_height = max(20, int(scrollbar_height * self.visible_lines / effective_total))
        max_scroll = max(1, effective_total - self.visible_lines)
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
            line_number: 目标行号（显示行号）
        """
        effective_total = self._get_effective_total_lines()
        line_number = max(0, min(line_number, effective_total - self.visible_lines))
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
        # 如果是过滤模式，需要转换行号
        if self.display_mode == "filtered":
            display_line = self._get_display_line_number(result.line_number)
            if display_line == -1:
                return  # 搜索结果不在过滤列表中
            target_line = max(0, display_line - self.visible_lines // 2)
        else:
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
            line_number: 要选中的行号（实际行号）
        """
        if 0 <= line_number < self.total_lines:
            old_selected = self.selected_line
            self.selected_line = line_number
            
            # 发送选择信号
            self.line_selected.emit(line_number)
            
            # 如果是过滤模式，检查行是否可见
            if self.display_mode == "filtered":
                display_line = self._get_display_line_number(line_number)
                if display_line == -1:
                    return  # 选中的行不在过滤列表中
                
                # 检查是否在可视区域
                if not (self.scroll_position <= display_line < self.scroll_position + self.visible_lines):
                    target_scroll = max(0, display_line - self.visible_lines // 2)
                    self.scroll_to_line(target_scroll)
                else:
                    self.update()
            else:
                # 原有逻辑
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
        """
        根据Y坐标获取对应的行号
        
        Args:
            y_pos: Y坐标位置
            
        Returns:
            实际行号，如果超出范围返回-1
        """
        if y_pos < 5:  # 顶部边距
            return -1
            
        line_index = (y_pos - 5) // self.line_height
        display_line_number = self.scroll_position + line_index
        
        # 转换为实际行号
        actual_line_number = self._get_actual_line_number(display_line_number)
        
        if actual_line_number != -1 and 0 <= actual_line_number < self.total_lines:
            return actual_line_number
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
                    display_line = self.start_line + i
                    actual_line = self.widget._get_actual_line_number(display_line)
                    if actual_line != -1 and 0 <= actual_line < self.widget.total_lines:
                        self.widget.get_line_text(actual_line)
                        
        # 预加载当前可见区域前后的行
        preload_start = max(0, self.scroll_position - 50)
        effective_total = self._get_effective_total_lines()
        preload_count = min(self.visible_lines + 100, effective_total - preload_start)
        
        self.preload_thread = PreloadThread(self, preload_start, preload_count)
        self.preload_thread.start()
    
    def mousePressEvent(self, event):
        """
        鼠标按下事件 - 实现点击行选择和滚动条拖拽
        """
        if event.button() == Qt.LeftButton and self.file_mmap:
            self.mouse_pressed = True
            
            # 检查是否点击在滚动条区域
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
                    effective_total = self._get_effective_total_lines()
                    target_line = int(scroll_ratio * max(1, effective_total - self.visible_lines))
                    self.scroll_to_line(target_line)
                    return
            
            # 原有的行选择逻辑
            clicked_line = self.get_line_number_at_position(event.y())
            
            if clicked_line != -1:
                # 选中点击的行
                self.select_line(clicked_line)
                
                # 如果点击的行有搜索结果，自动导航到该结果
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
            
            # 结束滚动条拖拽
            if self.scrollbar_dragging:
                self.scrollbar_dragging = False
                self.setCursor(Qt.ArrowCursor)
                
        super().mouseReleaseEvent(event)
        
    def leaveEvent(self, event):
        """鼠标离开控件事件 - 清除悬停效果"""
        if self.hover_line != -1:
            self.hover_line = -1
            self.update()
        
        # 重置鼠标光标
        self.setCursor(Qt.ArrowCursor)
        
        super().leaveEvent(event)
    
    def keyPressEvent(self, event):
        """
        键盘按键事件 - 支持键盘导航
        """
        if not self.file_mmap:
            return
            
        effective_total = self._get_effective_total_lines()
        
        if event.key() == Qt.Key_Up:
            # 上箭头：选择上一行
            if self.selected_line != -1:
                if self.display_mode == "filtered":
                    display_line = self._get_display_line_number(self.selected_line)
                    if display_line > 0:
                        new_actual = self._get_actual_line_number(display_line - 1)
                        self.select_line(new_actual)
                else:
                    if self.selected_line > 0:
                        self.select_line(self.selected_line - 1)
            elif effective_total > 0:
                # 如果没有选中行，选择当前屏幕中央的行
                center_display = self.scroll_position + self.visible_lines // 2
                center_actual = self._get_actual_line_number(center_display)
                if center_actual != -1:
                    self.select_line(center_actual)
            event.accept()
            
        elif event.key() == Qt.Key_Down:
            # 下箭头：选择下一行
            if self.selected_line != -1:
                if self.display_mode == "filtered":
                    display_line = self._get_display_line_number(self.selected_line)
                    if display_line < len(self.filtered_lines_mapping) - 1:
                        new_actual = self._get_actual_line_number(display_line + 1)
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
            # Page Up：向上翻页
            new_scroll = max(0, self.scroll_position - self.visible_lines)
            self.scroll_to_line(new_scroll)
            if self.selected_line != -1:
                if self.display_mode == "filtered":
                    display_line = self._get_display_line_number(self.selected_line)
                    new_display = max(0, display_line - self.visible_lines)
                    new_actual = self._get_actual_line_number(new_display)
                    if new_actual != -1:
                        self.select_line(new_actual)
                else:
                    new_selected = max(0, self.selected_line - self.visible_lines)
                    self.select_line(new_selected)
            event.accept()
            
        elif event.key() == Qt.Key_PageDown:
            # Page Down：向下翻页
            new_scroll = min(effective_total - self.visible_lines, 
                           self.scroll_position + self.visible_lines)
            self.scroll_to_line(new_scroll)
            if self.selected_line != -1:
                if self.display_mode == "filtered":
                    display_line = self._get_display_line_number(self.selected_line)
                    new_display = min(len(self.filtered_lines_mapping) - 1, 
                                    display_line + self.visible_lines)
                    new_actual = self._get_actual_line_number(new_display)
                    if new_actual != -1:
                        self.select_line(new_actual)
                else:
                    new_selected = min(self.total_lines - 1, 
                                     self.selected_line + self.visible_lines)
                    self.select_line(new_selected)
            event.accept()
            
        elif event.key() == Qt.Key_Home:
            # Home：跳转到文件开头
            self.scroll_to_line(0)
            first_actual = self._get_actual_line_number(0)
            if first_actual != -1:
                self.select_line(first_actual)
            event.accept()
            
        elif event.key() == Qt.Key_End:
            # End：跳转到文件结尾
            last_display = effective_total - 1
            self.scroll_to_line(max(0, last_display - self.visible_lines + 1))
            last_actual = self._get_actual_line_number(last_display)
            if last_actual != -1:
                self.select_line(last_actual)
            event.accept()
            
        elif event.key() == Qt.Key_Escape:
            # Escape：清除选择
            self.clear_selection()
            event.accept()
            
        else:
            super().keyPressEvent(event)
    
    def wheelEvent(self, event):
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
                display_line_number = self.scroll_position + i
                actual_line_number = self._get_actual_line_number(display_line_number)
                
                if actual_line_number == -1:
                    break
                    
                line_text = self.get_line_text(actual_line_number)
                line_rect = QRect(0, y_offset, self.width(), self.line_height)
                
                # 绘制行背景高亮（按优先级顺序）
                self._draw_line_backgrounds(painter, actual_line_number, line_rect, y_offset)
                
                # 绘制搜索结果高亮
                self._draw_search_highlights(painter, actual_line_number, y_offset, visible_search_results)
                
                # 绘制行号
                self._draw_line_number(painter, actual_line_number, y_offset)
                
                # 绘制行内容
                self._draw_line_content(painter, line_text, y_offset)
                
                y_offset += self.line_height
            
            # 绘制分割线（行号区域和内容区域之间）
            painter.setPen(QColor(200, 200, 200))
            painter.drawLine(self.line_number_width - 1, 0, self.line_number_width - 1, self.height())
            
            # 绘制可拖拽的滚动条
            self._draw_interactive_scrollbar(painter)
            
            # 绘制焦点边框（当控件获得焦点时）
            if self.hasFocus():
                painter.setPen(QPen(QColor(100, 149, 237), 2))
                painter.drawRect(1, 1, self.width() - 2, self.height() - 2)

        finally:
            painter.end()  
    
    def _draw_line_backgrounds(self, painter: QPainter, line_number: int, 
                              line_rect: QRect, y_offset: int):
        """
        绘制行背景高亮效果
        """
        # 使用动态计算的行号宽度
        content_rect = QRect(self.line_number_width, y_offset, 
                           self.width() - self.line_number_width, self.line_height)
        
        # 优先级1：选中行高亮
        if line_number == self.selected_line:
            painter.fillRect(content_rect, self.selected_line_color)
            # 同时高亮行号区域
            line_num_rect = QRect(0, y_offset, self.line_number_width, self.line_height)
            painter.fillRect(line_num_rect, self.line_number_selected_color)
            
        # 优先级2：悬停行高亮（如果没有被选中）
        elif line_number == self.hover_line:
            painter.fillRect(content_rect, self.hover_line_color)
    
    def _draw_line_number(self, painter: QPainter, line_number: int, y_offset: int):
        """
        绘制行号
        """
        # 设置行号颜色
        if line_number == self.selected_line:
            painter.setPen(QColor(255, 255, 255))  # 选中行用白色
        else:
            painter.setPen(QColor(100, 100, 100))  # 普通行用灰色
            
        line_num_text = f"{line_number + 1}"
        
        # 右对齐绘制行号，使用动态宽度
        text_rect = QRect(5, y_offset, self.line_number_width - 10, self.line_height)
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, line_num_text)
    
    def _draw_line_content(self, painter: QPainter, line_text: str, y_offset: int):
        """
        绘制行内容文本
        """
        painter.setPen(QColor(0, 0, 0))
        
        # 使用动态计算的内容区域起始位置
        content_x = self.line_number_width + 5  # 5像素左边距
        
        # 截断过长的行以提高性能
        max_chars = (self.width() - content_x - 20) // self.char_width  # 20像素右边距
        if len(line_text) > max_chars:
            line_text = line_text[:max_chars] + "..."
            
        # 绘制文本
        painter.drawText(content_x, y_offset + self.line_height - 5, line_text)
    
    def _get_visible_search_results(self) -> List[SearchResult]:
        """获取当前可见区域内的搜索结果"""
        visible_results = []
        
        with QMutexLocker(self.search_results_manager.results_mutex):
            for result in self.search_results_manager.results:
                # 检查结果是否在当前可见区域
                if self.display_mode == "filtered":
                    # 过滤模式：检查行是否在过滤列表中且在可见区域
                    display_line = self._get_display_line_number(result.line_number)
                    if (display_line != -1 and 
                        self.scroll_position <= display_line < self.scroll_position + self.visible_lines):
                        visible_results.append(result)
                else:
                    # 普通模式：直接检查行号
                    if (self.scroll_position <= result.line_number < 
                        self.scroll_position + self.visible_lines):
                        visible_results.append(result)
                    
        return visible_results
    
    def _draw_search_highlights(self, painter: QPainter, line_number: int, 
                              y_offset: int, visible_results: List[SearchResult]):
        """
        绘制搜索结果高亮
        """
        # 使用动态计算的内容区域起始位置
        content_x = self.line_number_width + 5  # 内容区域起始X坐标
        
        for result in visible_results:
            if result.line_number == line_number:
                # 计算高亮区域位置
                start_x = content_x + result.column_start * self.char_width
                width = (result.column_end - result.column_start) * self.char_width
                
                # 选择高亮颜色
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
                
                # 绘制搜索结果背景高亮
                highlight_rect = QRect(start_x, y_offset, width, self.line_height)
                painter.fillRect(highlight_rect, color)
                
                # 为了提高可读性，在高亮文本上绘制深色边框
                if result == self.current_search_result:
                    painter.setPen(QColor(139, 69, 19))  # 深棕色文字
                else:
                    painter.setPen(QColor(0, 0, 0))      # 黑色文字
                    
                # 重新绘制高亮区域的文本，确保可读性
                highlighted_text = result.matched_text
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
        
        # 生成搜索模式描述
        include_desc = ', '.join(self.include_keywords) if self.include_keywords else "无"
        exclude_desc = ', '.join(self.exclude_keywords) if self.exclude_keywords else "无"
        
        pattern = f"包含: {include_desc} | 排除: {exclude_desc}"
        description = f"包含：{include_desc}\n排除：{exclude_desc}\n共找到 {results_count} 个结果"
        
        return results_count, pattern, description

    def clear_filtered_display(self):
        """清除过滤显示，回到正常模式"""
        self.filtered_line_numbers.clear()
        self.set_display_mode("all")

    def update_filtered_display(self, matching_line_numbers: set):
        """
        更新过滤显示
        
        Args:
            matching_line_numbers: 匹配的行号集合
        """
        self.filtered_line_numbers = matching_line_numbers
        if matching_line_numbers:
            self.set_display_mode("filtered")
        else:
            self.set_display_mode("all")