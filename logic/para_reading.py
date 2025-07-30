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
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor


class FileIndexer(QThread):
    """文件索引器 - 在后台建立行索引"""
    
    indexing_progress = pyqtSignal(int, int)  # current_lines, total_size
    indexing_finished = pyqtSignal(list)  # line_offsets
    indexing_error = pyqtSignal(str)
    
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.should_stop = False
        
    def run(self):
        """建立文件的行索引"""
        try:
            line_offsets = [0]  # 第一行从0开始
            
            with open(self.file_path, 'rb') as file:
                file_size = os.path.getsize(self.file_path)
                current_pos = 0
                chunk_size = 1024 * 1024  # 1MB chunks
                
                while current_pos < file_size and not self.should_stop:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                        
                    # 查找换行符
                    start_pos = 0
                    while True:
                        newline_pos = chunk.find(b'\n', start_pos)
                        if newline_pos == -1:
                            break
                        line_offsets.append(current_pos + newline_pos + 1)
                        start_pos = newline_pos + 1
                    
                    current_pos += len(chunk)
                    
                    # 发送进度
                    if len(line_offsets) % 10000 == 0:  # 每10000行更新一次
                        self.indexing_progress.emit(len(line_offsets), file_size)
                
                if not self.should_stop:
                    self.indexing_finished.emit(line_offsets)
                    
        except Exception as e:
            self.indexing_error.emit(str(e))
    
    def stop(self):
        self.should_stop = True


class VirtualTextWidget(QWidget):
    """虚拟文本显示组件 - 只渲染可见行"""
    
    scroll_changed = pyqtSignal(int)  # 滚动位置变化
    
    def __init__(self):
        super().__init__()
        self.file_path = ""
        self.line_offsets = []  # 每行的文件偏移量
        self.visible_lines = 50  # 可见行数
        self.line_height = 20
        self.char_width = 8
        self.scroll_position = 0  # 当前滚动到的行号
        self.total_lines = 0
        
        # 缓存
        self.line_cache = {}  # {line_number: line_text}
        self.cache_mutex = QMutex()
        self.max_cache_size = 1000
        
        # 文件映射
        self.file_mmap = None
        self.file_handle = None
        
        # 字体设置
        self.font = QFont("Consolas", 10)
        self.setFont(self.font)
        self._update_font_metrics()
        
        # 预加载线程
        self.preload_thread = None
        
    def _update_font_metrics(self):
        """更新字体度量"""
        fm = QFontMetrics(self.font)
        self.line_height = fm.height()
        self.char_width = fm.averageCharWidth()
        self.visible_lines = max(1, self.height() // self.line_height)
        
    def load_file(self, file_path: str, line_offsets: List[int]):
        """加载文件"""
        self.close_file()
        
        self.file_path = file_path
        self.line_offsets = line_offsets
        self.total_lines = len(line_offsets) - 1
        
        # 打开文件映射
        try:
            self.file_handle = open(file_path, 'rb')
            self.file_mmap = mmap.mmap(self.file_handle.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception as e:
            print(f"文件映射失败: {e}")
            if self.file_handle:
                self.file_handle.close()
            return False
            
        self.scroll_position = 0
        self.line_cache.clear()
        self.update()
        return True
        
    def close_file(self):
        """关闭文件"""
        if self.file_mmap:
            self.file_mmap.close()
            self.file_mmap = None
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None
            
    def get_line_text(self, line_number: int) -> str:
        """获取指定行的文本"""
        if not self.file_mmap or line_number >= self.total_lines:
            return ""
            
        with QMutexLocker(self.cache_mutex):
            # 检查缓存
            if line_number in self.line_cache:
                return self.line_cache[line_number]
        
        try:
            # 从文件映射读取
            start_offset = self.line_offsets[line_number]
            end_offset = self.line_offsets[line_number + 1] if line_number + 1 < len(self.line_offsets) else len(self.file_mmap)
            
            line_bytes = self.file_mmap[start_offset:end_offset]
            line_text = line_bytes.decode('utf-8', errors='ignore').rstrip('\n\r')
            
            # 缓存管理
            with QMutexLocker(self.cache_mutex):
                if len(self.line_cache) >= self.max_cache_size:
                    # 清理缓存 - 保留当前可见区域附近的行
                    visible_start = max(0, self.scroll_position - 100)
                    visible_end = min(self.total_lines, self.scroll_position + self.visible_lines + 100)
                    
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
        """滚动到指定行"""
        line_number = max(0, min(line_number, self.total_lines - self.visible_lines))
        if line_number != self.scroll_position:
            self.scroll_position = line_number
            self.scroll_changed.emit(line_number)
            self.start_preload()
            self.update()
    
    def start_preload(self):
        """启动预加载线程"""
        if self.preload_thread and self.preload_thread.isRunning():
            return
            
        class PreloadThread(QThread):
            def __init__(self, widget, start_line, count):
                super().__init__()
                self.widget = widget
                self.start_line = start_line
                self.count = count
                
            def run(self):
                # 预加载当前屏幕前后的行
                for i in range(self.count):
                    line_num = self.start_line + i
                    if 0 <= line_num < self.widget.total_lines:
                        self.widget.get_line_text(line_num)
                        
        # 预加载当前可见区域前后的行
        preload_start = max(0, self.scroll_position - 50)
        preload_count = min(self.visible_lines + 100, self.total_lines - preload_start)
        
        self.preload_thread = PreloadThread(self, preload_start, preload_count)
        self.preload_thread.start()
    
    def wheelEvent(self, event):
        """鼠标滚轮事件"""
        if not self.file_mmap:
            return
            
        # 计算滚动行数
        delta = event.angleDelta().y()
        scroll_lines = -delta // 120 * 3  # 每次滚动3行
        
        new_position = self.scroll_position + scroll_lines
        self.scroll_to_line(new_position)
    
    def resizeEvent(self, event):
        """窗口大小变化"""
        super().resizeEvent(event)
        self._update_font_metrics()
        self.update()
    
    def paintEvent(self, event):
        """绘制可见文本"""
        if not self.file_mmap:
            return
            
        painter = QPainter(self)
        painter.setFont(self.font)
        
        # 背景
        painter.fillRect(self.rect(), QColor(255, 255, 255))
        
        # 绘制可见行
        y_offset = 5
        for i in range(self.visible_lines):
            line_number = self.scroll_position + i
            if line_number >= self.total_lines:
                break
                
            line_text = self.get_line_text(line_number)
            
            # 行号
            line_num_text = f"{line_number + 1:6d}: "
            painter.setPen(QColor(100, 100, 100))
            painter.drawText(5, y_offset + self.line_height - 5, line_num_text)
            
            # 行内容
            painter.setPen(QColor(0, 0, 0))
            content_x = 80
            
            # 截断过长的行以提高性能
            max_chars = (self.width() - content_x) // self.char_width
            if len(line_text) > max_chars:
                line_text = line_text[:max_chars] + "..."
                
            painter.drawText(content_x, y_offset + self.line_height - 5, line_text)
            
            y_offset += self.line_height
        
        # 滚动条指示器
        if self.total_lines > self.visible_lines:
            scrollbar_height = self.height() - 20
            scrollbar_y = 10 + int((self.scroll_position / max(1, self.total_lines - self.visible_lines)) * scrollbar_height)
            painter.fillRect(self.width() - 15, scrollbar_y, 10, 30, QColor(100, 100, 100, 128))


class BigFileViewer(QMainWindow):
    """大文件查看器主窗口"""
    
    def __init__(self):
        super().__init__()
        self.indexer = None
        self.init_ui()
        
    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("大文件查看器 - 内存优化版")
        self.setGeometry(100, 100, 1200, 800)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # 工具栏
        toolbar_layout = QHBoxLayout()
        
        self.load_btn = QPushButton("加载大文件")
        self.load_btn.clicked.connect(self.load_file)
        
        self.close_btn = QPushButton("关闭文件")
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
        self.status_label = QLabel("就绪 - 支持GB级大文件")
        layout.addWidget(self.status_label)
        
        # 分割器
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        
        # 虚拟文本显示
        self.text_widget = VirtualTextWidget()
        self.text_widget.scroll_changed.connect(self.on_scroll_changed)
        splitter.addWidget(self.text_widget)
        
        # 控制面板
        control_panel = self.create_control_panel()
        splitter.addWidget(control_panel)
        
        # 设置分割器比例
        splitter.setSizes([800, 400])
        
    def create_control_panel(self):
        """创建控制面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # 导航组
        nav_group = QGroupBox("导航")
        nav_layout = QVBoxLayout(nav_group)
        
        # 跳转到行
        jump_layout = QHBoxLayout()
        jump_layout.addWidget(QLabel("跳转到行:"))
        self.line_input = QLineEdit()
        self.line_input.returnPressed.connect(self.jump_to_line)
        jump_layout.addWidget(self.line_input)
        
        self.jump_btn = QPushButton("跳转")
        self.jump_btn.clicked.connect(self.jump_to_line)
        jump_layout.addWidget(self.jump_btn)
        
        nav_layout.addLayout(jump_layout)
        
        # 快速导航
        quick_nav_layout = QHBoxLayout()
        self.home_btn = QPushButton("首页")
        self.home_btn.clicked.connect(lambda: self.text_widget.scroll_to_line(0))
        
        self.end_btn = QPushButton("末页")
        self.end_btn.clicked.connect(lambda: self.text_widget.scroll_to_line(self.text_widget.total_lines))
        
        quick_nav_layout.addWidget(self.home_btn)
        quick_nav_layout.addWidget(self.end_btn)
        nav_layout.addLayout(quick_nav_layout)
        
        layout.addWidget(nav_group)
        
        # 搜索组
        search_group = QGroupBox("搜索")
        search_layout = QVBoxLayout(search_group)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入搜索内容...")
        self.search_input.returnPressed.connect(self.search_text)
        search_layout.addWidget(self.search_input)
        
        search_btn_layout = QHBoxLayout()
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self.search_text)
        
        self.search_next_btn = QPushButton("下一个")
        self.search_next_btn.clicked.connect(self.search_next)
        
        search_btn_layout.addWidget(self.search_btn)
        search_btn_layout.addWidget(self.search_next_btn)
        search_layout.addLayout(search_btn_layout)
        
        self.case_sensitive_cb = QCheckBox("区分大小写")
        search_layout.addWidget(self.case_sensitive_cb)
        
        layout.addWidget(search_group)
        
        # 文件信息
        info_group = QGroupBox("文件信息")
        info_layout = QVBoxLayout(info_group)
        
        self.file_info_label = QLabel("未加载文件")
        self.file_info_label.setWordWrap(True)
        info_layout.addWidget(self.file_info_label)
        
        self.position_label = QLabel("位置: 0/0")
        info_layout.addWidget(self.position_label)
        
        layout.addWidget(info_group)
        
        # 性能统计
        perf_group = QGroupBox("性能统计")
        perf_layout = QVBoxLayout(perf_group)
        
        self.cache_info_label = QLabel("缓存: 0 行")
        perf_layout.addWidget(self.cache_info_label)
        
        self.memory_info_label = QLabel("内存: 计算中...")
        perf_layout.addWidget(self.memory_info_label)
        
        layout.addWidget(perf_group)
        
        layout.addStretch()
        
        # 定时更新统计信息
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.update_stats)
        self.stats_timer.start(2000)  # 每2秒更新
        
        return panel
    
    def load_file(self):
        """加载文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择大文件", "", 
            "文本文件 (*.txt *.log *.csv *.py *.cpp *.java *.js);;所有文件 (*)"
        )
        
        if not file_path:
            return
            
        # 显示文件大小警告
        file_size = os.path.getsize(file_path)
        size_mb = file_size / (1024 * 1024)
        
        self.status_label.setText(f"正在建立索引... 文件大小: {size_mb:.1f}MB")
        self.progress_bar.setVisible(True)
        self.load_btn.setEnabled(False)
        
        # 启动索引线程
        self.indexer = FileIndexer(file_path)
        self.indexer.indexing_progress.connect(self.on_indexing_progress)
        self.indexer.indexing_finished.connect(self.on_indexing_finished)
        self.indexer.indexing_error.connect(self.on_indexing_error)
        self.indexer.start()
        
    def on_indexing_progress(self, lines, total_size):
        """索引进度更新"""
        progress = min(100, lines * 100 // max(1, total_size // 50))  # 估算进度
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"建立索引中... 已处理 {lines:,} 行")
        
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
                f"文件: {os.path.basename(self.indexer.file_path)}\n"
                f"大小: {size_mb:.1f}MB\n"
                f"行数: {total_lines:,}\n"
                f"平均行长: {file_size // max(1, total_lines):.0f} 字节"
            )
            
            self.status_label.setText(f"文件加载完成 - {total_lines:,} 行")
        else:
            self.status_label.setText("文件加载失败")
            
    def on_indexing_error(self, error_msg):
        """索引错误"""
        self.progress_bar.setVisible(False)
        self.load_btn.setEnabled(True)
        self.status_label.setText(f"索引错误: {error_msg}")
        
    def close_file(self):
        """关闭文件"""
        self.text_widget.close_file()
        self.close_btn.setEnabled(False)
        self.file_info_label.setText("未加载文件")
        self.status_label.setText("文件已关闭")
        
    def jump_to_line(self):
        """跳转到指定行"""
        try:
            line_number = int(self.line_input.text()) - 1  # 转换为0基索引
            self.text_widget.scroll_to_line(line_number)
        except ValueError:
            pass
            
    def search_text(self):
        """搜索文本"""
        # TODO: 实现大文件搜索算法
        search_term = self.search_input.text()
        if not search_term:
            return
            
        self.status_label.setText(f"搜索功能开发中... 搜索: {search_term}")
        
    def search_next(self):
        """搜索下一个"""
        # TODO: 实现搜索下一个
        pass
        
    def on_scroll_changed(self, line_number):
        """滚动位置变化"""
        self.position_label.setText(f"位置: {line_number + 1:,}/{self.text_widget.total_lines:,}")
        
    def update_stats(self):
        """更新统计信息"""
        if hasattr(self.text_widget, 'line_cache'):
            cache_size = len(self.text_widget.line_cache)
            self.cache_info_label.setText(f"缓存: {cache_size} 行")
            
        # 简单的内存估算
        import psutil
        process = psutil.Process()
        memory_mb = process.memory_info().rss / (1024 * 1024)
        self.memory_info_label.setText(f"内存: {memory_mb:.1f}MB")


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    viewer = BigFileViewer()
    viewer.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()