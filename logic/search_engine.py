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
from functools import lru_cache
import concurrent.futures
from collections import deque
import gc

from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                             QScrollArea, QLabel, QPushButton, QHBoxLayout, 
                             QFileDialog, QProgressBar, QLineEdit, QCheckBox,
                             QSpinBox, QGroupBox, QTextEdit, QSplitter, QComboBox,
                             QListWidget, QListWidgetItem, QMessageBox)
from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect)
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen, QBrush

from dataform.search_result import SearchResult


class AdvancedSearchStats:
    """搜索统计信息"""
    def __init__(self):
        self.total_lines = 0
        self.processed_lines = 0
        self.matched_lines = 0
        self.search_time = 0.0
        self.throughput = 0.0  # 行/秒
        self.memory_usage = 0  # MB
        
    def calculate_throughput(self):
        if self.search_time > 0:
            self.throughput = self.processed_lines / self.search_time


class MemoryMappedFileReader:
    """内存映射文件读取器 - 减少IO开销"""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file = None
        self.mmap_obj = None
        self.file_size = 0
        
    def __enter__(self):
        self.file = open(self.file_path, 'rb')
        self.file_size = os.path.getsize(self.file_path)
        # 只有文件足够大时才使用mmap
        if self.file_size > 1024 * 1024:  # 1MB以上使用mmap
            self.mmap_obj = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.mmap_obj:
            self.mmap_obj.close()
        if self.file:
            self.file.close()
            
    def read_line(self, start_offset: int, end_offset: int) -> bytes:
        """读取指定偏移量的行数据"""
        if self.mmap_obj:
            return self.mmap_obj[start_offset:end_offset]
        else:
            self.file.seek(start_offset)
            return self.file.read(end_offset - start_offset)


class OptimizedPatternMatcher:
    """优化的模式匹配器"""
    
    def __init__(self, include_keywords: List[str], exclude_keywords: List[str],
                 case_sensitive: bool = False, use_regex: bool = False, 
                 whole_word_only: bool = False):
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.case_sensitive = case_sensitive
        self.use_regex = use_regex
        self.whole_word_only = whole_word_only
        
        # 编译模式并缓存
        self.include_patterns = self._compile_patterns(tuple(include_keywords))  # 转换为tuple
        self.exclude_patterns = self._compile_patterns(tuple(exclude_keywords))  # 转换为tuple
        
        # 简单字符串匹配优化
        self.use_simple_search = not use_regex and not whole_word_only
        if self.use_simple_search:
            self.include_strs = [k.lower() if not case_sensitive else k for k in include_keywords]
            self.exclude_strs = [k.lower() if not case_sensitive else k for k in exclude_keywords]
    
    @lru_cache(maxsize=1000)
    def _compile_patterns(self, keywords: tuple) -> List[re.Pattern]:
        """编译并缓存正则表达式模式"""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        patterns = []
        
        for keyword in keywords:
            pattern = keyword
            
            if not self.use_regex:
                pattern = re.escape(pattern)
                
            if self.whole_word_only:
                pattern = r'\b' + pattern + r'\b'
                
            try:
                patterns.append(re.compile(pattern, flags))
            except re.error as e:
                raise ValueError(f"正则表达式错误: {keyword} - {e}")
                
        return patterns
    
    def matches_line(self, line_content: str, match_all_includes: bool = True) -> Tuple[bool, List[re.Match]]:
        """
        检查行是否匹配 - 优化版本
        """
        # 快速排除检查
        if self.exclude_patterns:
            if self.use_simple_search:
                line_lower = line_content.lower() if not self.case_sensitive else line_content
                for exclude_str in self.exclude_strs:
                    if exclude_str in line_lower:
                        return False, []
            else:
                for exclude_pattern in self.exclude_patterns:
                    if exclude_pattern.search(line_content):
                        return False, []
        
        # 如果没有包含条件，直接返回True
        if not self.include_patterns:
            return True, []
        
        # 包含条件检查
        found_matches = []
        
        if self.use_simple_search:
            # 简单字符串搜索优化 - 修复Match对象创建
            line_lower = line_content.lower() if not self.case_sensitive else line_content
            
            if match_all_includes:
                # AND逻辑
                for include_str in self.include_strs:
                    pos = line_lower.find(include_str)
                    if pos == -1:
                        return False, []
                    # 创建简单的匹配对象
                    match_obj = SimpleMatch(pos, pos + len(include_str), 
                                          line_content[pos:pos + len(include_str)])
                    found_matches.append(match_obj)
                return True, found_matches
            else:
                # OR逻辑
                for include_str in self.include_strs:
                    pos = line_lower.find(include_str)
                    if pos != -1:
                        match_obj = SimpleMatch(pos, pos + len(include_str),
                                              line_content[pos:pos + len(include_str)])
                        found_matches.append(match_obj)
                        return True, found_matches
                return False, []
        else:
            # 正则表达式搜索
            if match_all_includes:
                matched_patterns = 0
                for include_pattern in self.include_patterns:
                    matches = list(include_pattern.finditer(line_content))
                    if matches:
                        found_matches.extend(matches)
                        matched_patterns += 1
                    else:
                        return False, []
                return matched_patterns == len(self.include_patterns), found_matches
            else:
                for include_pattern in self.include_patterns:
                    matches = list(include_pattern.finditer(line_content))
                    if matches:
                        found_matches.extend(matches)
                        return True, found_matches
                return False, []


class SimpleMatch:
    """简单的匹配对象，兼容 re.Match 接口"""
    
    def __init__(self, start_pos: int, end_pos: int, matched_text: str):
        self._start = start_pos
        self._end = end_pos
        self._matched_text = matched_text
    
    def start(self) -> int:
        return self._start
    
    def end(self) -> int:
        return self._end
    
    def group(self) -> str:
        return self._matched_text


class HighPerformanceSearchEngine(QThread):
    """
    高性能搜索引擎 - 全面优化版本
    """
    
    # 信号定义
    search_progress = pyqtSignal(int, int)           # 进度百分比, 已找到结果数
    search_result_found = pyqtSignal(object)         # 找到的搜索结果
    search_finished = pyqtSignal(int, float)         # 搜索完成: 结果数量, 耗时
    search_error = pyqtSignal(str)                   # 搜索错误信息
    search_stats = pyqtSignal(object)                # 搜索统计信息
    
    def __init__(self, file_path: str, line_offsets: List[int]):
        super().__init__()
        self.file_path = file_path
        self.line_offsets = line_offsets
        self.should_stop = False
        
        # 动态调整线程数
        cpu_count = psutil.cpu_count()
        self.num_threads = max(2, min(6, cpu_count))  # 2-6个线程之间
        
        # 智能分块策略
        self.total_lines = len(line_offsets) - 1
        self.adaptive_chunk_size = self._calculate_optimal_chunk_size()
        
        # 结果管理
        self.results_queue = queue.Queue(maxsize=1000)  # 限制队列大小防止内存爆炸
        self.total_results = 0
        self.stats = AdvancedSearchStats()
        
        # 性能优化选项
        self.enable_early_stop = True
        self.max_results = 10000  # 默认最大结果数
        self.batch_emit_size = 100  # 批量发送结果大小
        
        # 缓存和优化
        self.pattern_matcher = None
        self.decoder_cache = {}  # 编码缓存
        
    def _calculate_optimal_chunk_size(self) -> int:
        """智能计算最优分块大小"""
        if self.total_lines < 1000:
            return max(100, self.total_lines // 4)
        elif self.total_lines < 100000:
            return max(1000, self.total_lines // 20)
        else:
            return max(5000, self.total_lines // 50)
    
    def setup_search(self, include_keywords: List[str] = None, 
                    exclude_keywords: List[str] = None,
                    case_sensitive: bool = False, 
                    use_regex: bool = False, 
                    whole_word_only: bool = False,
                    match_all_includes: bool = True,
                    max_results: int = 10000):
        """配置搜索参数"""
        self.include_keywords = include_keywords or []
        self.exclude_keywords = exclude_keywords or []
        self.case_sensitive = case_sensitive
        self.use_regex = use_regex
        self.whole_word_only = whole_word_only
        self.match_all_includes = match_all_includes
        self.max_results = max_results
        
        # 创建优化的模式匹配器
        self.pattern_matcher = OptimizedPatternMatcher(
            self.include_keywords, self.exclude_keywords,
            case_sensitive, use_regex, whole_word_only
        )
    
    def _get_adaptive_chunks(self) -> List[Tuple[int, int]]:
        """自适应分块策略"""
        # 根据文件大小和CPU核心数动态调整
        target_chunks = self.num_threads * 2  # 每个线程处理2个块
        lines_per_chunk = max(self.adaptive_chunk_size, self.total_lines // target_chunks)
        
        chunks = []
        current_line = 0
        
        while current_line < self.total_lines:
            end_line = min(current_line + lines_per_chunk, self.total_lines)
            chunks.append((current_line, end_line))
            current_line = end_line
            
        return chunks
    
    def _decode_line_optimized(self, line_data: bytes) -> str:
        """优化的行解码 - 缓存编码类型"""
        # 尝试UTF-8解码
        try:
            return line_data.decode('utf-8', errors='ignore').rstrip('\n\r')
        except UnicodeDecodeError:
            # 回退到latin1
            return line_data.decode('latin1', errors='ignore').rstrip('\n\r')
    
    def _search_line_chunk_optimized(self, start_line: int, end_line: int) -> List[SearchResult]:
        """
        优化的行块搜索
        性能关键改进：
        1. 使用内存映射
        2. 批量处理
        3. 早期停止
        4. 减少对象创建
        """
        results = []
        processed_count = 0
        
        try:
            with MemoryMappedFileReader(self.file_path) as reader:
                for line_number in range(start_line, end_line):
                    if self.should_stop or (self.enable_early_stop and self.total_results >= self.max_results):
                        break
                    
                    if line_number >= len(self.line_offsets) - 1:
                        break
                    
                    # 读取行数据
                    start_offset = self.line_offsets[line_number]
                    end_offset = self.line_offsets[line_number + 1]
                    
                    line_data = reader.read_line(start_offset, end_offset)
                    line_content = self._decode_line_optimized(line_data)
                    
                    # 使用优化的模式匹配器
                    matches_criteria, matches = self.pattern_matcher.matches_line(
                        line_content, self.match_all_includes)
                    
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
                            # 只有排除条件匹配
                            result = SearchResult(
                                line_number=line_number,
                                column_start=0,
                                column_end=len(line_content),
                                matched_text=line_content,
                                line_content=line_content,
                                file_offset=start_offset
                            )
                            results.append(result)
                    
                    processed_count += 1
                    
                    # 每处理一定数量的行就检查停止条件
                    if processed_count % 200 == 0:
                        if self.should_stop:
                            break
                        # 减少让出CPU的频率
                        if processed_count % 1000 == 0:
                            self.msleep(1)
                            
        except Exception as e:
            print(f"搜索块错误 ({start_line}-{end_line}): {e}")
            
        return results
    
    def _emit_results_batch(self, results: List[SearchResult]):
        """批量发送结果 - 减少信号开销"""
        batch = []
        for result in results:
            batch.append(result)
            if len(batch) >= self.batch_emit_size:
                for r in batch:
                    self.search_result_found.emit(r)
                    self.total_results += 1
                batch.clear()
                
        # 发送剩余结果
        for r in batch:
            self.search_result_found.emit(r)
            self.total_results += 1
    
    def run(self):
        """主搜索线程"""
        if not self.include_keywords and not self.exclude_keywords:
            self.search_error.emit("至少需要指定包含关键词或排除关键词")
            return
            
        start_time = time.time()
        self.should_stop = False
        self.total_results = 0
        
        # 初始化统计信息
        self.stats = AdvancedSearchStats()
        self.stats.total_lines = self.total_lines
        
        try:
            # 获取自适应分块
            chunks = self._get_adaptive_chunks()
            total_chunks = len(chunks)
            
            print(f"开始搜索: {self.total_lines}行, {total_chunks}个块, {self.num_threads}个线程")
            
            # 使用优化的线程池
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.num_threads,
                thread_name_prefix="SearchWorker"
            ) as executor:
                
                # 提交搜索任务
                future_to_chunk = {
                    executor.submit(self._search_line_chunk_optimized, start, end): (start, end)
                    for start, end in chunks
                }
                
                completed_chunks = 0
                
                # 处理完成的任务
                for future in concurrent.futures.as_completed(future_to_chunk):
                    if self.should_stop:
                        # 快速取消剩余任务
                        for f in future_to_chunk:
                            if not f.done():
                                f.cancel()
                        break
                        
                    try:
                        results = future.result(timeout=30)  # 30秒超时
                        
                        # 批量发送结果
                        self._emit_results_batch(results)
                        
                        completed_chunks += 1
                        chunk_start, chunk_end = future_to_chunk[future]
                        self.stats.processed_lines += (chunk_end - chunk_start)
                        
                        # 更新进度
                        progress = int(completed_chunks * 100 / total_chunks)
                        self.search_progress.emit(progress, self.total_results)
                        
                        # 早期停止检查
                        if self.enable_early_stop and self.total_results >= self.max_results:
                            print(f"达到最大结果数限制 ({self.max_results})，提前停止搜索")
                            break
                            
                        # 适当让出CPU时间
                        if completed_chunks % 5 == 0:
                            self.msleep(1)
                        
                    except concurrent.futures.TimeoutError:
                        print("搜索任务超时")
                        continue
                    except Exception as e:
                        print(f"搜索任务执行错误: {e}")
                        continue
            
            # 完成统计
            if not self.should_stop:
                elapsed_time = time.time() - start_time
                self.stats.search_time = elapsed_time
                self.stats.matched_lines = self.total_results
                self.stats.calculate_throughput()
                
                # 发送统计信息
                self.search_stats.emit(self.stats)
                self.search_finished.emit(self.total_results, elapsed_time)
                
                print(f"搜索完成: {self.total_results}个结果, 耗时{elapsed_time:.2f}秒, "
                      f"处理速度{self.stats.throughput:.0f}行/秒")
                
        except Exception as e:
            self.search_error.emit(f"搜索引擎错误: {e}")
        finally:
            # 清理缓存
            self.decoder_cache.clear()
            gc.collect()  # 强制垃圾回收
    
    def stop_search(self):
        """优化的停止搜索"""
        print("正在停止搜索...")
        self.should_stop = True
        
        # 给线程一些时间自然结束
        if self.isRunning():
            self.quit()
            if not self.wait(3000):  # 等待3秒
                print("搜索线程未能及时停止")
                self.terminate()  # 强制终止
                self.wait(1000)
    
    def get_performance_info(self) -> Dict:
        """获取性能信息"""
        return {
            'total_lines': self.stats.total_lines,
            'processed_lines': self.stats.processed_lines,
            'throughput': self.stats.throughput,
            'memory_usage': self.stats.memory_usage,
            'num_threads': self.num_threads,
            'chunk_size': self.adaptive_chunk_size
        }


class RealTimeSearchEngine(HighPerformanceSearchEngine):
    """
    实时搜索引擎 - 专门为即时预览优化
    """
    
    def __init__(self, file_path: str, line_offsets: List[int]):
        super().__init__(file_path, line_offsets)
        
        # 实时搜索专用优化
        self.num_threads = 2  # 实时搜索用更少线程
        self.max_results = 200  # 限制结果数量
        self.enable_early_stop = True
        self.batch_emit_size = 20  # 更小的批次
        
        # 智能采样搜索
        self.enable_sampling = True
        self.sampling_ratio = 0.1  # 只搜索10%的内容做快速预览
        
    def setup_realtime_search(self, max_results: int = 200, sampling_ratio: float = 0.1):
        """设置实时搜索参数"""
        self.max_results = max_results
        self.sampling_ratio = sampling_ratio
        self.enable_sampling = sampling_ratio < 1.0
        
    def _get_sampling_chunks(self) -> List[Tuple[int, int]]:
        """获取采样分块 - 用于快速预览"""
        if not self.enable_sampling:
            return self._get_adaptive_chunks()
            
        # 智能采样：均匀分布取样
        total_chunks = max(4, int(self.total_lines * self.sampling_ratio / 1000))
        chunk_size = self.total_lines // total_chunks
        
        chunks = []
        for i in range(total_chunks):
            start_line = i * chunk_size
            end_line = min(start_line + 1000, self.total_lines)  # 每个采样块最大1000行
            if start_line < self.total_lines:
                chunks.append((start_line, end_line))
                
        return chunks
        
    def run(self):
        """实时搜索主逻辑"""
        if not self.include_keywords and not self.exclude_keywords:
            self.search_error.emit("至少需要指定包含关键词或排除关键词")
            return
            
        start_time = time.time()
        self.should_stop = False
        self.total_results = 0
        
        try:
            # 使用采样分块进行快速搜索
            chunks = self._get_sampling_chunks() if self.enable_sampling else self._get_adaptive_chunks()
            
            print(f"实时搜索开始: {'采样模式' if self.enable_sampling else '完整模式'}, "
                  f"{len(chunks)}个块")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                future_to_chunk = {
                    executor.submit(self._search_line_chunk_optimized, start, end): (start, end)
                    for start, end in chunks
                }
                
                completed_chunks = 0
                total_chunks = len(chunks)
                
                for future in concurrent.futures.as_completed(future_to_chunk):
                    if self.should_stop or self.total_results >= self.max_results:
                        break
                        
                    try:
                        results = future.result(timeout=10)  # 实时搜索超时时间更短
                        self._emit_results_batch(results)
                        
                        completed_chunks += 1
                        progress = int(completed_chunks * 100 / total_chunks)
                        self.search_progress.emit(progress, self.total_results)
                        
                    except Exception as e:
                        print(f"实时搜索任务错误: {e}")
                        continue
                        
            # 搜索完成
            elapsed_time = time.time() - start_time
            self.search_finished.emit(self.total_results, elapsed_time)
            
            print(f"实时搜索完成: {self.total_results}个结果, 耗时{elapsed_time:.2f}秒")
            
        except Exception as e:
            self.search_error.emit(f"实时搜索错误: {e}")


# 搜索引擎工厂 - 更新版本
class SearchEngineFactory:
    """搜索引擎工厂"""
    
    @staticmethod
    def create_high_performance_engine(file_path: str, line_offsets: List[int]) -> HighPerformanceSearchEngine:
        """创建高性能搜索引擎"""
        return HighPerformanceSearchEngine(file_path, line_offsets)
    
    @staticmethod
    def create_realtime_engine(file_path: str, line_offsets: List[int]) -> RealTimeSearchEngine:
        """创建实时搜索引擎"""
        engine = RealTimeSearchEngine(file_path, line_offsets)
        engine.setup_realtime_search(max_results=200, sampling_ratio=0.15)
        return engine
    
    @staticmethod
    def create_preview_engine(file_path: str, line_offsets: List[int], 
                            max_results: int = 100) -> RealTimeSearchEngine:
        """创建预览搜索引擎"""
        engine = RealTimeSearchEngine(file_path, line_offsets)
        engine.setup_realtime_search(max_results=max_results, sampling_ratio=0.05)
        return engine
        
    @staticmethod
    def auto_select_engine(file_path: str, line_offsets: List[int], 
                          search_type: str = "auto") -> HighPerformanceSearchEngine:
        """自动选择最适合的搜索引擎"""
        total_lines = len(line_offsets) - 1
        
        if search_type == "realtime" or total_lines > 1000000:
            # 大文件或实时搜索使用实时引擎
            return SearchEngineFactory.create_realtime_engine(file_path, line_offsets)
        elif search_type == "preview":
            return SearchEngineFactory.create_preview_engine(file_path, line_offsets)
        else:
            # 默认使用高性能引擎
            return SearchEngineFactory.create_high_performance_engine(file_path, line_offsets)