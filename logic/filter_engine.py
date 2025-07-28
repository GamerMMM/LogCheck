import re
import concurrent.futures
from typing import List, Dict, Tuple, Optional, NamedTuple, Set
from dataclasses import dataclass
from threading import Lock
import time
import multiprocessing as mp
from functools import partial

@dataclass
class SearchOptions:
    """搜索选项配置"""
    show_only: bool = False
    ignore_alpha: bool = False
    whole_pair: bool = False

class SearchResult(NamedTuple):
    """搜索结果数据结构"""
    matched_lines: List[Tuple[int, str]]  # (行号, 行内容)
    total_matches: int
    filtered_lines: List[Tuple[int, str]]  # 过滤后的行
    include_pattern: str
    exclude_pattern: str
    statistics: Dict[str, int]
    search_time: float = 0.0
    highlight_ranges: List[Tuple[int, int, int]] = None  # (行号, 开始位置, 结束位置)

class LineSearchResult(NamedTuple):
    """单行搜索结果"""
    line_idx: int
    content: str
    is_match: bool
    highlight_positions: List[Tuple[int, int]]  # [(start, end), ...]

class ChunkSearchResult(NamedTuple):
    """分块搜索结果"""
    matched_lines: List[Tuple[int, str]]
    filtered_lines: List[Tuple[int, str]]
    total_matches: int
    highlight_ranges: List[Tuple[int, int, int]]
    statistics: Dict[str, int]

class FilterEngine:
    """真正的高性能并行搜索引擎"""
    
    def __init__(self, max_workers: Optional[int] = None):
        # 使用CPU核心数，但限制最大值避免过度创建线程
        self.max_workers = min(max_workers or mp.cpu_count(), 8)
        self._search_cache = {}
        self._cache_lock = Lock()
        self._last_search_time = 0.0
        
        # 性能参数
        self.MIN_CHUNK_SIZE = 50  # 最小分块大小
        self.MAX_CHUNK_SIZE = 500  # 最大分块大小
        self.SMALL_FILE_THRESHOLD = 1000  # 小文件阈值，不进行并行处理

    def apply(self, editor, include_keywords: List[str], exclude_keywords: List[str],
              show_only: bool, ignore_alpha: bool, whole_pair: bool):
        """主要搜索接口 - 真正的并行化处理"""
        print("🚀 启动高性能并行搜索...")
        start_time = time.time()
        
        options = SearchOptions(show_only, ignore_alpha, whole_pair)
        text_content = editor.toPlainText()
        
        # 执行真正的并行搜索
        search_result = self.parallel_search_text(text_content, include_keywords, exclude_keywords, options)
        
        # 在主线程中应用高亮结果
        self._apply_parallel_highlights(editor, search_result, options)
        
        total_time = time.time() - start_time
        self._last_search_time = total_time
        
        self._print_performance_stats(search_result, total_time)
        
    def parallel_search_text(self, text_content: str, include_keywords: List[str], 
                           exclude_keywords: List[str], options: SearchOptions) -> SearchResult:
        """真正的并行搜索实现"""
        search_start_time = time.time()
        
        # 检查缓存
        cache_key = self._generate_cache_key(text_content, include_keywords, exclude_keywords, options)
        with self._cache_lock:
            if cache_key in self._search_cache:
                cached_result = self._search_cache[cache_key]
                print("✅ 缓存命中")
                return SearchResult(*cached_result[:-2], search_time=0.0, highlight_ranges=cached_result[-1])
        
        lines = text_content.splitlines()
        total_lines = len(lines)
        
        if total_lines == 0:
            return SearchResult([], 0, [], "", "", {}, 0.0, [])
        
        # 编译正则表达式
        include_pattern = self.get_regex(include_keywords, options.ignore_alpha, options.whole_pair)
        exclude_pattern = self.get_regex(exclude_keywords, options.ignore_alpha, options.whole_pair)
        
        include_regex = re.compile(include_pattern, re.IGNORECASE if options.ignore_alpha else 0) if include_pattern else None
        exclude_regex = re.compile(exclude_pattern, re.IGNORECASE if options.ignore_alpha else 0) if exclude_pattern else None
        
        # 根据文件大小选择处理策略
        if total_lines < self.SMALL_FILE_THRESHOLD:
            print(f"📄 小文件处理 ({total_lines} 行)")
            result = self._search_lines_sequential(lines, include_regex, exclude_regex, options)
        else:
            print(f"🔥 大文件并行处理 ({total_lines} 行)")
            result = self._search_lines_parallel(lines, include_regex, exclude_regex, options)
        
        search_time = time.time() - search_start_time
        
        # 构建最终结果
        final_result = SearchResult(
            matched_lines=result.matched_lines,
            total_matches=result.total_matches,
            filtered_lines=result.filtered_lines,
            include_pattern=include_pattern,
            exclude_pattern=exclude_pattern,
            statistics=result.statistics,
            search_time=search_time,
            highlight_ranges=result.highlight_ranges
        )
        
        # 缓存结果
        with self._cache_lock:
            self._search_cache[cache_key] = (
                result.matched_lines, result.total_matches, result.filtered_lines,
                include_pattern, exclude_pattern, result.statistics, result.highlight_ranges
            )
        
        return final_result

    def _search_lines_parallel(self, lines: List[str], include_regex: Optional[re.Pattern], 
                             exclude_regex: Optional[re.Pattern], options: SearchOptions) -> ChunkSearchResult:
        """真正的并行搜索实现 - 多进程/线程处理"""
        total_lines = len(lines)
        
        # 智能分块：根据CPU核心数和文件大小动态调整
        optimal_chunk_size = max(
            self.MIN_CHUNK_SIZE,
            min(self.MAX_CHUNK_SIZE, total_lines // (self.max_workers * 2))
        )
        
        chunks = []
        for i in range(0, total_lines, optimal_chunk_size):
            end_idx = min(i + optimal_chunk_size, total_lines)
            chunks.append((lines[i:end_idx], i))
        
        print(f"📊 分块策略: {len(chunks)} 个块，每块约 {optimal_chunk_size} 行")
        
        all_matched_lines = []
        all_filtered_lines = []
        all_highlight_ranges = []
        total_matches = 0
        statistics = {"total_lines": total_lines, "processed_chunks": len(chunks), "chunk_size": optimal_chunk_size}
        
        # 使用ThreadPoolExecutor进行真正的并行处理
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 创建搜索任务 - 使用偏函数避免重复传参
            search_func = partial(
                self._search_chunk_worker,
                include_regex=include_regex,
                exclude_regex=exclude_regex,
                options=options
            )
            
            # 提交所有任务
            future_to_chunk = {
                executor.submit(search_func, chunk_lines, start_idx): (chunk_lines, start_idx)
                for chunk_lines, start_idx in chunks
            }
            
            # 收集结果 - 使用as_completed确保最快完成的任务先处理
            completed_chunks = 0
            for future in concurrent.futures.as_completed(future_to_chunk):
                try:
                    chunk_result = future.result()
                    
                    # 合并结果
                    all_matched_lines.extend(chunk_result.matched_lines)
                    all_filtered_lines.extend(chunk_result.filtered_lines)
                    all_highlight_ranges.extend(chunk_result.highlight_ranges)
                    total_matches += chunk_result.total_matches
                    
                    # 合并统计信息
                    for key, value in chunk_result.statistics.items():
                        if key in statistics:
                            statistics[key] += value
                        else:
                            statistics[key] = value
                    
                    completed_chunks += 1
                    if completed_chunks % max(1, len(chunks) // 10) == 0:
                        print(f"⏳ 处理进度: {completed_chunks}/{len(chunks)} 块完成")
                        
                except Exception as e:
                    print(f"❌ 搜索块处理错误: {e}")
        
        # 按行号排序结果
        all_matched_lines.sort(key=lambda x: x[0])
        all_filtered_lines.sort(key=lambda x: x[0])
        all_highlight_ranges.sort(key=lambda x: (x[0], x[1]))
        
        return ChunkSearchResult(
            matched_lines=all_matched_lines,
            filtered_lines=all_filtered_lines,
            total_matches=total_matches,
            highlight_ranges=all_highlight_ranges,
            statistics=statistics
        )

    def _search_chunk_worker(self, chunk_lines: List[str], start_line_idx: int,
                           include_regex: Optional[re.Pattern], exclude_regex: Optional[re.Pattern],
                           options: SearchOptions) -> ChunkSearchResult:
        """并行工作线程 - 处理单个文本块"""
        matched_lines = []
        filtered_lines = []
        highlight_ranges = []
        total_matches = 0
        
        for i, line in enumerate(chunk_lines):
            actual_line_idx = start_line_idx + i
            line_stripped = line.strip()
            
            if not line_stripped:
                continue
            
            # 检查包含条件并记录高亮位置
            include_match = True
            include_positions = []
            if include_regex:
                matches = list(include_regex.finditer(line))
                include_match = len(matches) > 0
                include_positions = [(m.start(), m.end()) for m in matches]
            
            # 检查排除条件
            exclude_match = False
            if exclude_regex:
                exclude_match = bool(exclude_regex.search(line))
            
            # 应用过滤逻辑
            if include_match and not exclude_match:
                matched_lines.append((actual_line_idx, line))
                total_matches += 1
                
                # 记录高亮范围
                for start_pos, end_pos in include_positions:
                    highlight_ranges.append((actual_line_idx, start_pos, end_pos))
                
                if options.show_only:
                    filtered_lines.append((actual_line_idx, line))
            elif not options.show_only:
                filtered_lines.append((actual_line_idx, line))
        
        statistics = {
            "processed_lines": len(chunk_lines),
            "matched_lines": len(matched_lines),
            "total_matches": total_matches
        }
        
        return ChunkSearchResult(
            matched_lines=matched_lines,
            filtered_lines=filtered_lines,
            total_matches=total_matches,
            highlight_ranges=highlight_ranges,
            statistics=statistics
        )

    def _search_lines_sequential(self, lines: List[str], include_regex: Optional[re.Pattern], 
                               exclude_regex: Optional[re.Pattern], options: SearchOptions) -> ChunkSearchResult:
        """小文件的顺序搜索"""
        return self._search_chunk_worker(lines, 0, include_regex, exclude_regex, options)

    def _apply_parallel_highlights(self, editor, result: SearchResult, options: SearchOptions):
        """并行应用高亮结果到编辑器"""
        try:
            # 如果编辑器支持批量高亮，使用批量方法
            if hasattr(editor, 'apply_batch_highlights'):
                editor.apply_batch_highlights(
                    highlight_ranges=result.highlight_ranges,
                    matched_lines=result.matched_lines,
                    show_only_matches=options.show_only
                )
            else:
                # 回退到原有方法
                include_keywords = self._extract_keywords_from_pattern(result.include_pattern)
                exclude_keywords = self._extract_keywords_from_pattern(result.exclude_pattern)
                
                editor.search_and_highlight(
                    include_keywords=include_keywords,
                    exclude_keywords=exclude_keywords,
                    show_only_matches=options.show_only,
                    ignore_alpha=options.ignore_alpha,
                    whole_pair=options.whole_pair
                )
            
            # 设置搜索结果
            if hasattr(editor, 'set_search_results'):
                editor.set_search_results(result.matched_lines, result.total_matches)
                
        except Exception as e:
            print(f"❌ 应用搜索结果到编辑器时出错: {e}")

    def batch_search_multiple_texts(self, text_contents: List[str], include_keywords: List[str], 
                                  exclude_keywords: List[str], options: SearchOptions) -> List[SearchResult]:
        """真正的多文本并行搜索"""
        if not text_contents:
            return []
        
        print(f"🔄 开始批量搜索 {len(text_contents)} 个文本...")
        
        # 使用进程池进行更高效的并行处理
        with concurrent.futures.ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # 创建搜索任务
            search_func = partial(
                self._search_single_text_worker,
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
                options=options
            )
            
            futures = [executor.submit(search_func, text) for text in text_contents]
            
            results = []
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                try:
                    result = future.result()
                    results.append(result)
                    print(f"✅ 文本 {i+1}/{len(text_contents)} 搜索完成")
                except Exception as e:
                    print(f"❌ 批量搜索错误: {e}")
                    results.append(SearchResult([], 0, [], "", "", {}, 0.0, []))
            
            return results

    def _search_single_text_worker(self, text_content: str, include_keywords: List[str], 
                                 exclude_keywords: List[str], options: SearchOptions) -> SearchResult:
        """单文本搜索工作函数 - 用于进程池"""
        # 创建临时引擎实例（避免进程间共享问题）
        temp_engine = HighPerformanceFilterEngine(max_workers=2)  # 进程内使用较少线程
        return temp_engine.parallel_search_text(text_content, include_keywords, exclude_keywords, options)

    def get_regex(self, keywords: List[str], ignore_case: bool = False, whole_word: bool = False) -> str:
        """优化的正则表达式生成"""
        if not keywords:
            return ""
        
        # 过滤并预处理关键词
        processed_keywords = []
        for kw in keywords:
            kw = kw.strip()
            if not kw:
                continue
            
            # 转义特殊字符
            escaped = re.escape(kw)
            
            if whole_word:
                # 整词匹配 - 优化边界检测
                escaped = r'\b' + escaped + r'\b'
            
            processed_keywords.append(escaped)
        
        if not processed_keywords:
            return ""
        
        # 使用非捕获组优化性能
        pattern = "(?:" + "|".join(processed_keywords) + ")"
        return pattern

    def measure_performance_comprehensive(self, text_content: str, include_keywords: List[str], 
                                        exclude_keywords: List[str], options: SearchOptions, 
                                        iterations: int = 5) -> Dict[str, float]:
        """全面的性能测试"""
        print(f"🔬 开始全面性能测试 - {iterations} 次迭代...")
        
        times = []
        cache_hit_times = []
        
        for i in range(iterations):
            # 测试无缓存性能
            self.clear_cache()
            start_time = time.time()
            result = self.parallel_search_text(text_content, include_keywords, exclude_keywords, options)
            no_cache_time = time.time() - start_time
            times.append(no_cache_time)
            
            # 测试缓存命中性能
            start_time = time.time()
            cached_result = self.parallel_search_text(text_content, include_keywords, exclude_keywords, options)
            cache_time = time.time() - start_time
            cache_hit_times.append(cache_time)
            
            print(f"   第 {i+1} 次: 无缓存 {no_cache_time:.3f}s, 缓存 {cache_time:.3f}s, 匹配 {len(result.matched_lines)} 行")
        
        stats = {
            "avg_no_cache_time": sum(times) / len(times),
            "min_no_cache_time": min(times),
            "max_no_cache_time": max(times),
            "avg_cache_hit_time": sum(cache_hit_times) / len(cache_hit_times),
            "cache_speedup": (sum(times) / len(times)) / (sum(cache_hit_times) / len(cache_hit_times)),
            "total_lines": len(text_content.splitlines()),
            "matched_lines": len(result.matched_lines),
            "workers_used": self.max_workers
        }
        
        print(f"📈 性能测试结果:")
        print(f"   - 平均搜索时间 (无缓存): {stats['avg_no_cache_time']:.3f}s")
        print(f"   - 平均搜索时间 (缓存命中): {stats['avg_cache_hit_time']:.3f}s")
        print(f"   - 缓存加速比: {stats['cache_speedup']:.1f}x")
        print(f"   - 使用线程数: {stats['workers_used']}")
        print(f"   - 处理效率: {stats['total_lines']/stats['avg_no_cache_time']:.0f} 行/秒")
        
        return stats

    def _generate_cache_key(self, text_content: str, include_keywords: List[str], 
                          exclude_keywords: List[str], options: SearchOptions) -> str:
        """优化的缓存键生成"""
        # 使用更高效的哈希方法
        content_hash = hash(text_content)
        include_hash = hash(tuple(sorted(include_keywords)))
        exclude_hash = hash(tuple(sorted(exclude_keywords)))
        options_hash = hash((options.show_only, options.ignore_alpha, options.whole_pair))
        
        return f"{content_hash}_{include_hash}_{exclude_hash}_{options_hash}"

    def _extract_keywords_from_pattern(self, pattern: str) -> List[str]:
        """从正则表达式模式中提取原始关键词"""
        if not pattern:
            return []
        
        # 移除非捕获组标记
        pattern = pattern.replace('(?:', '').replace(')', '')
        
        keywords = []
        for part in pattern.split('|'):
            # 移除词边界和转义字符
            cleaned = part.replace(r'\b', '')
            cleaned = re.sub(r'\\(.)', r'\1', cleaned)
            if cleaned.strip():
                keywords.append(cleaned.strip())
        
        return keywords

    def _print_performance_stats(self, result: SearchResult, total_time: float):
        """打印性能统计信息"""
        print(f"🎯 搜索性能统计:")
        print(f"   - 总耗时: {total_time:.3f}秒")
        print(f"   - 搜索处理时间: {result.search_time:.3f}秒")
        print(f"   - UI应用时间: {total_time - result.search_time:.3f}秒")
        print(f"   - 匹配行数: {len(result.matched_lines)}")
        print(f"   - 总行数: {result.statistics.get('total_lines', 0)}")
        print(f"   - 处理效率: {result.statistics.get('total_lines', 0)/result.search_time:.0f} 行/秒")
        print(f"   - 使用线程数: {self.max_workers}")
        
        if 'processed_chunks' in result.statistics:
            print(f"   - 处理块数: {result.statistics['processed_chunks']}")
            print(f"   - 平均块大小: {result.statistics.get('chunk_size', 0)} 行")

    def clear_cache(self):
        """线程安全的缓存清理"""
        with self._cache_lock:
            self._search_cache.clear()

    def get_cache_stats(self) -> Dict[str, int]:
        """获取缓存统计信息"""
        with self._cache_lock:
            return {
                "cache_size": len(self._search_cache),
                "max_workers": self.max_workers,
                "cache_memory_mb": sum(len(str(v)) for v in self._search_cache.values()) / (1024*1024)
            }

    def get_last_search_time(self) -> float:
        """获取最后一次搜索的耗时"""
        return self._last_search_time