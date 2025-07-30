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



class FileIndexer(QThread):
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