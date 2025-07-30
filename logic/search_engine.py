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

from dataform.search_result import SearchResult


class ParallelSearchEngine(QThread):
    """
    并行搜索引擎 - 高性能多线程文件搜索
    优化版本：减少卡顿，提升性能
    """
    
    # 信号定义
    search_progress = pyqtSignal(int, int)           # 当前进度, 已找到结果数
    search_result_found = pyqtSignal(object)         # 找到的搜索结果
    search_finished = pyqtSignal(int, float)         # 搜索完成: 结果数量, 耗时
    search_error = pyqtSignal(str)                   # 搜索错误信息
    
    def __init__(self, file_path: str, line_offsets: List[int]):
        super().__init__()
        self.file_path = file_path
        self.line_offsets = line_offsets
        self.should_stop = False
        
        # 搜索参数
        self.include_keywords = []
        self.exclude_keywords = []
        self.case_sensitive = False
        self.use_regex = False
        self.whole_word_only = False
        self.match_all_includes = True
        
        # 性能优化参数
        self.num_threads = min(4, psutil.cpu_count())  # 限制为4个线程，避免过载
        self.chunk_size = 1024 * 1024 * 3              # 减小为3MB，减少内存占用
        self.overlap_size = 512                         # 减小重叠大小
        self.batch_size = 50                            # 批量发送结果，减少信号开销
        
        # 结果管理
        self.results_queue = queue.Queue()
        self.total_results = 0
        self.search_start_time = 0
        self.results_buffer = []  # 结果缓冲区
        
    def setup_search(self, include_keywords: List[str] = None, 
                    exclude_keywords: List[str] = None,
                    case_sensitive: bool = False, 
                    use_regex: bool = False, 
                    whole_word_only: bool = False,
                    match_all_includes: bool = True):
        """配置搜索参数"""
        self.include_keywords = include_keywords or []
        self.exclude_keywords = exclude_keywords or []
        self.case_sensitive = case_sensitive
        self.use_regex = use_regex
        self.whole_word_only = whole_word_only
        self.match_all_includes = match_all_includes
        
    def _prepare_regex_patterns(self) -> Tuple[List[re.Pattern], List[re.Pattern]]:
        """准备正则表达式模式"""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        include_patterns = []
        exclude_patterns = []
        
        # 处理包含关键词
        for keyword in self.include_keywords:
            pattern = keyword
            
            if not self.use_regex:
                pattern = re.escape(pattern)
                
            if self.whole_word_only:
                pattern = r'\b' + pattern + r'\b'
                
            try:
                include_patterns.append(re.compile(pattern, flags))
            except re.error as e:
                raise ValueError(f"包含关键词正则表达式错误: {keyword} - {e}")
        
        # 处理排除关键词
        for keyword in self.exclude_keywords:
            pattern = keyword
            
            if not self.use_regex:
                pattern = re.escape(pattern)
                
            if self.whole_word_only:
                pattern = r'\b' + pattern + r'\b'
                
            try:
                exclude_patterns.append(re.compile(pattern, flags))
            except re.error as e:
                raise ValueError(f"排除关键词正则表达式错误: {keyword} - {e}")
                
        return include_patterns, exclude_patterns
    
    def _get_line_chunks(self) -> List[Tuple[int, int]]:
        """
        基于行号分割任务，避免跨行问题
        """
        total_lines = len(self.line_offsets) - 1
        lines_per_chunk = max(1000, total_lines // (self.num_threads * 2))  # 每个块至少1000行
        
        chunks = []
        current_line = 0
        
        while current_line < total_lines:
            end_line = min(current_line + lines_per_chunk, total_lines)
            chunks.append((current_line, end_line))
            current_line = end_line
            
        return chunks
        
    def _line_matches_criteria(self, line_content: str, 
                              include_patterns: List[re.Pattern],
                              exclude_patterns: List[re.Pattern]) -> Tuple[bool, List[re.Match]]:
        """检查行是否匹配搜索条件"""
        # 首先检查排除条件
        for exclude_pattern in exclude_patterns:
            if exclude_pattern.search(line_content):
                return False, []
        
        # 如果没有包含条件，直接返回True
        if not include_patterns:
            return True, []
        
        # 检查包含条件
        found_matches = []
        matched_patterns = 0
        
        for include_pattern in include_patterns:
            matches = list(include_pattern.finditer(line_content))
            if matches:
                found_matches.extend(matches)
                matched_patterns += 1
                
                # 如果是OR逻辑，找到一个就够了
                if not self.match_all_includes:
                    return True, found_matches
        
        # AND逻辑：需要匹配所有包含词
        if self.match_all_includes:
            return matched_patterns == len(include_patterns), found_matches
        
        return False, []
        
    def _search_line_chunk(self, start_line: int, end_line: int, 
                          include_patterns: List[re.Pattern],
                          exclude_patterns: List[re.Pattern]) -> List[SearchResult]:
        """
        搜索指定行范围
        """
        results = []
        processed_lines = 0
        
        try:
            # 使用内存映射一次性读取所需的行
            with open(self.file_path, 'rb') as file:
                for line_number in range(start_line, end_line):
                    if self.should_stop:
                        break
                    
                    if line_number >= len(self.line_offsets) - 1:
                        break
                    
                    # 读取行内容
                    start_offset = self.line_offsets[line_number]
                    end_offset = self.line_offsets[line_number + 1]
                    
                    file.seek(start_offset)
                    line_data = file.read(end_offset - start_offset)
                    
                    try:
                        line_content = line_data.decode('utf-8', errors='ignore').rstrip('\n\r')
                    except UnicodeDecodeError:
                        line_content = line_data.decode('latin1', errors='ignore').rstrip('\n\r')
                    
                    # 检查匹配条件
                    matches_criteria, matches = self._line_matches_criteria(
                        line_content, include_patterns, exclude_patterns)
                    
                    if matches_criteria:
                        if matches:
                            # 有具体匹配位置
                            for match in matches:
                                result = SearchResult(
                                    line_number=line_number,
                                    column_start=match.start(),
                                    column_end=match.end(),
                                    matched_text=match.group(),
                                    line_content=line_content,
                                    file_offset=start_offset + match.start()
                                )
                                results.append(result)
                        else:
                            # 没有具体匹配位置（如仅排除条件）
                            result = SearchResult(
                                line_number=line_number,
                                column_start=0,
                                column_end=len(line_content),
                                matched_text=line_content,
                                line_content=line_content,
                                file_offset=start_offset
                            )
                            results.append(result)
                    
                    processed_lines += 1
                    
                    # 批量发送进度更新，减少信号开销
                    if processed_lines % 500 == 0:
                        progress = int((line_number - start_line) * 100 / (end_line - start_line))
                        self.search_progress.emit(progress, len(results))
                        
        except Exception as e:
            print(f"搜索行块错误 ({start_line}-{end_line}): {e}")
            
        return results
    
    def _emit_results_batch(self, results: List[SearchResult]):
        """批量发送搜索结果"""
        for result in results:
            self.search_result_found.emit(result)
            self.total_results += 1
    
    def run(self):
        """主搜索线程入口"""
        if not self.include_keywords and not self.exclude_keywords:
            self.search_error.emit("至少需要指定包含关键词或排除关键词")
            return
            
        self.search_start_time = time.time()
        self.should_stop = False
        self.total_results = 0
        
        try:
            # 准备正则表达式
            include_patterns, exclude_patterns = self._prepare_regex_patterns()
            
            # 获取行块
            chunks = self._get_line_chunks()
            total_chunks = len(chunks)
            
            # 使用线程池执行并行搜索
            import concurrent.futures
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                # 提交所有搜索任务
                future_to_chunk = {
                    executor.submit(self._search_line_chunk, start, end, include_patterns, exclude_patterns): (start, end)
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
                        
                        # 批量发送结果
                        self._emit_results_batch(results)
                        
                        completed_chunks += 1
                        
                        # 更新进度
                        progress = int(completed_chunks * 100 / total_chunks)
                        self.search_progress.emit(progress, self.total_results)
                        
                        # 让出CPU时间，避免界面卡顿
                        self.msleep(1)
                        
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


class OptimizedSearchEngine(ParallelSearchEngine):
    """
    进一步优化的搜索引擎 - 专门用于实时搜索
    """
    
    def __init__(self, file_path: str, line_offsets: List[int]):
        super().__init__(file_path, line_offsets)
        
        # 实时搜索优化参数
        self.num_threads = 2  # 实时搜索使用更少线程
        self.chunk_size = 1024 * 1024  # 更小的块大小
        self.early_stop_threshold = 1000  # 找到足够结果就停止
        self.preview_mode = True  # 预览模式，限制结果数量
        
    def setup_preview_search(self, max_results: int = 500):
        """
        设置预览搜索模式
        
        Args:
            max_results: 最大结果数量
        """
        self.preview_mode = True
        self.early_stop_threshold = max_results
    
    def _search_line_chunk(self, start_line: int, end_line: int, 
                          include_patterns: List[re.Pattern],
                          exclude_patterns: List[re.Pattern]) -> List[SearchResult]:
        """
        优化的行块搜索 - 支持早期停止
        """
        results = []
        
        try:
            with open(self.file_path, 'rb') as file:
                for line_number in range(start_line, end_line):
                    if self.should_stop:
                        break
                    
                    # 预览模式下的早期停止
                    if self.preview_mode and self.total_results >= self.early_stop_threshold:
                        break
                    
                    if line_number >= len(self.line_offsets) - 1:
                        break
                    
                    # 读取行内容
                    start_offset = self.line_offsets[line_number]
                    end_offset = self.line_offsets[line_number + 1]
                    
                    file.seek(start_offset)
                    line_data = file.read(end_offset - start_offset)
                    
                    try:
                        line_content = line_data.decode('utf-8', errors='ignore').rstrip('\n\r')
                    except UnicodeDecodeError:
                        line_content = line_data.decode('latin1', errors='ignore').rstrip('\n\r')
                    
                    # 检查匹配条件
                    matches_criteria, matches = self._line_matches_criteria(
                        line_content, include_patterns, exclude_patterns)
                    
                    if matches_criteria:
                        if matches:
                            for match in matches:
                                result = SearchResult(
                                    line_number=line_number,
                                    column_start=match.start(),
                                    column_end=match.end(),
                                    matched_text=match.group(),
                                    line_content=line_content,
                                    file_offset=start_offset + match.start()
                                )
                                results.append(result)
                                
                                # 检查是否达到早期停止条件
                                if self.preview_mode and len(results) >= self.early_stop_threshold // self.num_threads:
                                    return results
                        else:
                            result = SearchResult(
                                line_number=line_number,
                                column_start=0,
                                column_end=len(line_content),
                                matched_text=line_content,
                                line_content=line_content,
                                file_offset=start_offset
                            )
                            results.append(result)
                    
                    # 更频繁的让出CPU时间
                    if line_number % 100 == 0:
                        self.msleep(1)
                        
        except Exception as e:
            print(f"优化搜索行块错误 ({start_line}-{end_line}): {e}")
            
        return results


# 搜索工厂类
class SearchEngineFactory:
    """搜索引擎工厂"""
    
    @staticmethod
    def create_standard_engine(file_path: str, line_offsets: List[int]) -> ParallelSearchEngine:
        """创建标准搜索引擎"""
        return ParallelSearchEngine(file_path, line_offsets)
    
    @staticmethod
    def create_realtime_engine(file_path: str, line_offsets: List[int]) -> OptimizedSearchEngine:
        """创建实时搜索引擎"""
        engine = OptimizedSearchEngine(file_path, line_offsets)
        engine.setup_preview_search(max_results=500)
        return engine
    
    @staticmethod
    def create_preview_engine(file_path: str, line_offsets: List[int], max_results: int = 200) -> OptimizedSearchEngine:
        """创建预览搜索引擎"""
        engine = OptimizedSearchEngine(file_path, line_offsets)
        engine.setup_preview_search(max_results=max_results)
        return engine