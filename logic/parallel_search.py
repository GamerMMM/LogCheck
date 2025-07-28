from PyQt5.QtCore import QThread, pyqtSignal, QMutex, QMutexLocker
from typing import List, Dict, Tuple, Optional
import concurrent.futures
import time
import re
from logic.filter_engine import SearchOptions

class SearchWorker(QThread):
    """高性能并行搜索线程 - 真正的异步搜索"""
    
    # 信号定义
    progress_updated = pyqtSignal(int, int)  # (current, total)
    search_completed = pyqtSignal(list)  # List[SearchResult]
    search_failed = pyqtSignal(str)  # error message
    partial_result_ready = pyqtSignal(object, int)  # (SearchResult, editor_index)
    
    def __init__(self, editors, include_keywords, exclude_keywords, 
                 show_only, ignore_alpha, whole_pair, filter_engine):
        super().__init__()
        self.editors = editors
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.show_only = show_only
        self.ignore_alpha = ignore_alpha
        self.whole_pair = whole_pair
        self.filter_engine = filter_engine
        
        self._stop_requested = False
        self._mutex = QMutex()
        
    def run(self):
        """真正的并行搜索执行"""
        try:
            start_time = time.time()
            print(f"🚀 启动异步并行搜索 - 处理 {len(self.editors)} 个编辑器")
            
            options = SearchOptions(self.show_only, self.ignore_alpha, self.whole_pair)
            results = []
            
            # 使用线程池并行处理多个编辑器
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(self.editors))) as executor:
                # 提交所有搜索任务
                future_to_editor = {}
                for i, editor in enumerate(self.editors):
                    if self._is_stop_requested():
                        break
                        
                    future = executor.submit(
                        self._search_single_editor,
                        editor, i, options
                    )
                    future_to_editor[future] = (editor, i)
                
                # 收集结果
                completed = 0
                for future in concurrent.futures.as_completed(future_to_editor):
                    if self._is_stop_requested():
                        break
                        
                    try:
                        search_result, editor, index = future.result()
                        results.append((search_result, editor, index))
                        
                        # 发送部分结果（允许UI实时更新）
                        self.partial_result_ready.emit(search_result, index)
                        
                        completed += 1
                        self.progress_updated.emit(completed, len(self.editors))
                        
                        print(f"✅ 编辑器 {index+1}/{len(self.editors)} 搜索完成 - "
                              f"匹配 {len(search_result.matched_lines)} 行")
                        
                    except Exception as e:
                        print(f"❌ 编辑器搜索失败: {e}")
                        self.search_failed.emit(str(e))
            
            if not self._is_stop_requested() and results:
                total_time = time.time() - start_time
                print(f"🎉 所有搜索任务完成 - 总耗时: {total_time:.3f}秒")
                self.search_completed.emit(results)
            
        except Exception as e:
            print(f"❌ 搜索工作线程错误: {e}")
            self.search_failed.emit(str(e))
    
    def _search_single_editor(self, editor, index: int, options: SearchOptions):
        """搜索单个编辑器"""
        if self._is_stop_requested():
            return None, editor, index
            
        text_content = editor.toPlainText()
        search_result = self.filter_engine.parallel_search_text(
            text_content, self.include_keywords, self.exclude_keywords, options
        )
        
        return search_result, editor, index
    
    def stop(self):
        """停止搜索"""
        with QMutexLocker(self._mutex):
            self._stop_requested = True
        print("🛑 搜索停止请求已发送")
    
    def _is_stop_requested(self) -> bool:
        """检查是否请求停止"""
        with QMutexLocker(self._mutex):
            return self._stop_requested

class RealTimeRegexWorker(QThread):
    """实时正则表达式搜索线程"""
    
    regex_completed = pyqtSignal(list, str)  # (matches, pattern)
    regex_progress = pyqtSignal(int, int)  # (processed_lines, total_lines)
    regex_failed = pyqtSignal(str)
    
    def __init__(self, text_content: str, pattern: str):
        super().__init__()
        self.text_content = text_content
        self.pattern = pattern
        self._stop_requested = False
        self._mutex = QMutex()
        
    def run(self):
        """执行正则搜索"""
        try:
            print(f"🔍 开始正则搜索: {self.pattern}")
            start_time = time.time()
            
            # 编译正则表达式
            try:
                regex = re.compile(self.pattern, re.MULTILINE | re.IGNORECASE)
            except re.error as e:
                self.regex_failed.emit(f"正则表达式错误: {e}")
                return
            
            lines = self.text_content.splitlines()
            matches = []
            
            # 分批处理，允许进度更新和停止
            batch_size = 100
            total_lines = len(lines)
            
            for i in range(0, total_lines, batch_size):
                if self._is_stop_requested():
                    break
                    
                batch_end = min(i + batch_size, total_lines)
                batch_lines = lines[i:batch_end]
                
                # 处理当前批次
                for line_idx, line in enumerate(batch_lines):
                    actual_line_idx = i + line_idx
                    
                    # 查找所有匹配
                    for match in regex.finditer(line):
                        matches.append({
                            'line_number': actual_line_idx,
                            'line_content': line,
                            'match_start': match.start(),
                            'match_end': match.end(),
                            'matched_text': match.group()
                        })
                
                # 更新进度
                self.regex_progress.emit(batch_end, total_lines)
            
            search_time = time.time() - start_time
            
            if not self._is_stop_requested():
                print(f"✅ 正则搜索完成 - 耗时: {search_time:.3f}秒, 找到 {len(matches)} 个匹配")
                self.regex_completed.emit(matches, self.pattern)
            
        except Exception as e:
            print(f"❌ 正则搜索错误: {e}")
            self.regex_failed.emit(str(e))
    
    def stop(self):
        """停止正则搜索"""
        with QMutexLocker(self._mutex):
            self._stop_requested = True
    
    def _is_stop_requested(self) -> bool:
        """检查是否请求停止"""
        with QMutexLocker(self._mutex):
            return self._stop_requested

class BatchFileSearchWorker(QThread):
    """批量文件搜索工作线程"""
    
    file_loaded = pyqtSignal(str, str, str)  # (filepath, content, filename)
    batch_progress = pyqtSignal(int, int)  # (completed, total)
    batch_completed = pyqtSignal(list)  # List of (filepath, content, filename)
    batch_failed = pyqtSignal(str)
    
    def __init__(self, file_paths: List[str], file_handler):
        super().__init__()
        self.file_paths = file_paths
        self.file_handler = file_handler
        self._stop_requested = False
        self._mutex = QMutex()
        
    def run(self):
        """并行加载多个文件"""
        try:
            print(f"📁 开始批量加载 {len(self.file_paths)} 个文件")
            start_time = time.time()
            
            results = []
            
            # 使用线程池并行加载文件
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                future_to_file = {
                    executor.submit(self._load_single_file, filepath): filepath 
                    for filepath in self.file_paths
                }
                
                completed = 0
                for future in concurrent.futures.as_completed(future_to_file):
                    if self._is_stop_requested():
                        break
                        
                    try:
                        filepath, content, filename = future.result()
                        if content is not None:
                            results.append((filepath, content, filename))
                            self.file_loaded.emit(filepath, content, filename)
                        
                        completed += 1
                        self.batch_progress.emit(completed, len(self.file_paths))
                        
                    except Exception as e:
                        print(f"❌ 文件加载失败 {future_to_file[future]}: {e}")
            
            load_time = time.time() - start_time
            
            if not self._is_stop_requested():
                print(f"✅ 批量文件加载完成 - 耗时: {load_time:.3f}秒, 成功加载 {len(results)} 个文件")
                self.batch_completed.emit(results)
                
        except Exception as e:
            print(f"❌ 批量文件加载错误: {e}")
            self.batch_failed.emit(str(e))
    
    def _load_single_file(self, filepath: str) -> Tuple[str, str, str]:
        """加载单个文件"""
        import os
        
        if self._is_stop_requested():
            return filepath, None, ""
            
        content = self.file_handler.load_file(filepath)
        filename = os.path.basename(filepath)
        return filepath, content, filename
    
    def stop(self):
        """停止文件加载"""
        with QMutexLocker(self._mutex):
            self._stop_requested = True
    
    def _is_stop_requested(self) -> bool:
        """检查是否请求停止"""
        with QMutexLocker(self._mutex):
            return self._stop_requested

class SearchCoordinator(QThread):
    """智能搜索协调器 - 根据数据量自动选择最优策略"""
    
    strategy_selected = pyqtSignal(str)  # 选择的策略名称
    coordinator_completed = pyqtSignal(object)  # 最终搜索结果
    coordinator_progress = pyqtSignal(str, int, int)  # (阶段, 当前, 总数)
    
    def __init__(self, editors, include_keywords, exclude_keywords, 
                 show_only, ignore_alpha, whole_pair, filter_engine):
        super().__init__()
        self.editors = editors
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.show_only = show_only
        self.ignore_alpha = ignore_alpha
        self.whole_pair = whole_pair
        self.filter_engine = filter_engine
        
        self._stop_requested = False
        self._mutex = QMutex()
        
    def run(self):
        """智能选择和执行搜索策略"""
        try:
            # 分析数据特征
            total_lines = sum(len(editor.toPlainText().splitlines()) for editor in self.editors)
            editor_count = len(self.editors)
            
            print(f"📊 数据分析: {editor_count} 个编辑器, 总计 {total_lines} 行")
            
            # 选择最优策略
            strategy = self._select_optimal_strategy(total_lines, editor_count)
            self.strategy_selected.emit(strategy)
            
            if strategy == "sequential":
                self._execute_sequential_search()
            elif strategy == "parallel_editors":
                self._execute_parallel_editor_search()
            elif strategy == "hybrid":
                self._execute_hybrid_search()
            else:
                self._execute_full_parallel_search()
                
        except Exception as e:
            print(f"❌ 搜索协调器错误: {e}")
            
    def _select_optimal_strategy(self, total_lines: int, editor_count: int) -> str:
        """根据数据特征选择最优搜索策略"""
        if total_lines < 1000:
            return "sequential"  # 小数据量，顺序处理
        elif editor_count == 1 and total_lines > 10000:
            return "parallel_content"  # 单个大文件，内容并行
        elif editor_count > 1 and total_lines > 5000:
            return "hybrid"  # 多文件大数据，混合策略
        else:
            return "parallel_editors"  # 多文件中等数据，编辑器并行
    
    def _execute_sequential_search(self):
        """执行顺序搜索策略"""
        print("🔄 执行顺序搜索策略")
        options = SearchOptions(self.show_only, self.ignore_alpha, self.whole_pair)
        
        results = []
        for i, editor in enumerate(self.editors):
            if self._is_stop_requested():
                break
                
            self.coordinator_progress.emit("顺序搜索", i+1, len(self.editors))
            
            text_content = editor.toPlainText()
            search_result = self.filter_engine.parallel_search_text(
                text_content, self.include_keywords, self.exclude_keywords, options
            )
            results.append((search_result, editor, i))
        
        if not self._is_stop_requested():
            self.coordinator_completed.emit(results)
    
    def _execute_parallel_editor_search(self):
        """执行编辑器并行搜索策略"""
        print("⚡ 执行编辑器并行搜索策略")
        
        # 创建高性能搜索工作线程
        worker = SearchWorker(
            self.editors, self.include_keywords, self.exclude_keywords,
            self.show_only, self.ignore_alpha, self.whole_pair, self.filter_engine
        )
        
        # 连接信号
        worker.progress_updated.connect(
            lambda c, t: self.coordinator_progress.emit("编辑器并行", c, t)
        )
        worker.search_completed.connect(self.coordinator_completed.emit)
        
        worker.start()
        worker.wait()  # 等待完成
    
    def _execute_hybrid_search(self):
        """执行混合搜索策略"""
        print("🔥 执行混合搜索策略")
        
        # 将编辑器分组，大文件单独处理，小文件批量处理
        large_editors = []
        small_editors = []
        
        for editor in self.editors:
            line_count = len(editor.toPlainText().splitlines())
            if line_count > 5000:
                large_editors.append(editor)
            else:
                small_editors.append(editor)
        
        results = []
        total_groups = len(large_editors) + (1 if small_editors else 0)
        completed_groups = 0
        
        # 处理大文件（单独并行）
        for editor in large_editors:
            if self._is_stop_requested():
                break
                
            self.coordinator_progress.emit("处理大文件", completed_groups+1, total_groups)
            
            options = SearchOptions(self.show_only, self.ignore_alpha, self.whole_pair)
            text_content = editor.toPlainText()
            search_result = self.filter_engine.parallel_search_text(
                text_content, self.include_keywords, self.exclude_keywords, options
            )
            results.append((search_result, editor, self.editors.index(editor)))
            completed_groups += 1
        
        # 处理小文件（批量并行）
        if small_editors and not self._is_stop_requested():
            self.coordinator_progress.emit("批量处理小文件", completed_groups+1, total_groups)
            
            worker = SearchWorker(
                small_editors, self.include_keywords, self.exclude_keywords,
                self.show_only, self.ignore_alpha, self.whole_pair, self.filter_engine
            )
            
            small_results = []
            worker.search_completed.connect(lambda r: small_results.extend(r))
            worker.start()
            worker.wait()
            
            results.extend(small_results)
        
        if not self._is_stop_requested():
            self.coordinator_completed.emit(results)
    
    def _execute_full_parallel_search(self):
        """执行完全并行搜索策略"""
        print("🚀 执行完全并行搜索策略")
        self._execute_parallel_editor_search()  # 复用并行编辑器搜索
    
    def stop(self):
        """停止搜索协调"""
        with QMutexLocker(self._mutex):
            self._stop_requested = True
    
    def _is_stop_requested(self) -> bool:
        """检查是否请求停止"""
        with QMutexLocker(self._mutex):
            return self._stop_requested

# 工具函数
def estimate_search_complexity(text_content: str, keywords: List[str]) -> Dict[str, float]:
    """估算搜索复杂度"""
    lines = text_content.splitlines()
    total_chars = len(text_content)
    avg_line_length = total_chars / max(len(lines), 1)
    keyword_complexity = sum(len(kw) for kw in keywords)
    
    return {
        "total_lines": len(lines),
        "total_chars": total_chars,
        "avg_line_length": avg_line_length,
        "keyword_complexity": keyword_complexity,
        "estimated_time": (len(lines) * keyword_complexity) / 1000000  # 粗略估算
    }

def optimize_search_parameters(editors, keywords) -> Dict[str, int]:
    """优化搜索参数"""
    total_lines = sum(len(editor.toPlainText().splitlines()) for editor in editors)
    
    # 根据数据量动态调整参数
    if total_lines < 1000:
        max_workers = 2
        chunk_size = 100
    elif total_lines < 10000:
        max_workers = 4
        chunk_size = 200
    else:
        max_workers = 8
        chunk_size = 500
    
    return {
        "max_workers": max_workers,
        "chunk_size": chunk_size,
        "use_cache": total_lines > 5000,
        "batch_size": min(100, total_lines // 10)
    }