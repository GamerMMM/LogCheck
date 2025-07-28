from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtGui import QTextCursor, QTextCharFormat, QColor, QFont
import re
from typing import List, Tuple, Dict, Optional
import time

class HighPerformanceCodeEditor(QTextEdit):
    """高性能代码编辑器 - 支持批量高亮和异步处理"""
    
    # 信号定义
    highlight_completed = pyqtSignal(int)  # 高亮完成，参数为匹配数量
    highlight_progress = pyqtSignal(int, int)  # 高亮进度 (current, total)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 高性能配置
        self.setLineWrapMode(QTextEdit.NoWrap)  # 禁用自动换行提升性能
        
        # 高亮配置
        self.highlight_formats = {
            'include': self._create_highlight_format(QColor(255, 255, 0), bold=True),  # 黄色背景
            'exclude': self._create_highlight_format(QColor(255, 0, 0), bold=True),    # 红色背景
            'regex': self._create_highlight_format(QColor(0, 255, 0), bold=True)       # 绿色背景
        }
        
        # 搜索结果缓存
        self._search_results_cache = {}
        self._current_highlights = []
        self._original_text = ""
        
        # 异步高亮定时器
        self._highlight_timer = QTimer()
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._apply_pending_highlights)
        self._pending_highlights = []
        
        # 性能监控
        self._last_highlight_time = 0.0
        self._highlight_stats = {"total_highlights": 0, "avg_time": 0.0}

    def _create_highlight_format(self, bg_color: QColor, fg_color: QColor = None, bold: bool = False) -> QTextCharFormat:
        """创建高亮格式"""
        format = QTextCharFormat()
        format.setBackground(bg_color)
        
        if fg_color:
            format.setForeground(fg_color)
        
        if bold:
            format.setFontWeight(QFont.Bold)
        
        return format

    def load_text(self, text: str):
        """加载文本内容"""
        self._original_text = text
        self.setPlainText(text)
        self._clear_highlights()
        self._clear_cache()

    def reset_text(self):
        """重置文本到原始状态"""
        if self._original_text:
            self.setPlainText(self._original_text)
        self._clear_highlights()

    def search_and_highlight(self, include_keywords: List[str], exclude_keywords: List[str] = None,
                           show_only_matches: bool = False, ignore_alpha: bool = False, 
                           whole_pair: bool = False):
        """传统搜索高亮方法 - 兼容性保持"""
        start_time = time.time()
        
        # 清除之前的高亮
        self._clear_highlights()
        
        if not include_keywords:
            return
        
        # 生成正则表达式
        pattern = self._build_search_pattern(include_keywords, ignore_alpha, whole_pair)
        exclude_pattern = self._build_search_pattern(exclude_keywords or [], ignore_alpha, whole_pair)
        
        # 执行搜索和高亮
        matches = self._find_all_matches(pattern, exclude_pattern)
        self._apply_highlights_batch(matches, 'include')
        
        # 处理只显示匹配行的逻辑
        if show_only_matches:
            self._show_only_matching_lines(matches)
        
        highlight_time = time.time() - start_time
        self._last_highlight_time = highlight_time
        self._update_highlight_stats(len(matches), highlight_time)
        
        print(f"🎨 高亮完成 - 耗时: {highlight_time:.3f}秒, 匹配: {len(matches)} 个")
        self.highlight_completed.emit(len(matches))

    def apply_batch_highlights(self, highlight_ranges: List[Tuple[int, int, int]], 
                             matched_lines: List[Tuple[int, str]], show_only_matches: bool = False):
        """批量应用高亮 - 高性能方法"""
        start_time = time.time()
        
        print(f"🚀 开始批量高亮 - {len(highlight_ranges)} 个范围")
        
        # 清除之前的高亮
        self._clear_highlights()
        
        if not highlight_ranges:
            return
        
        # 将范围转换为文档位置
        document_highlights = self._convert_ranges_to_document_positions(highlight_ranges)
        
        # 批量应用高亮
        self._apply_highlights_optimized(document_highlights)
        
        # 处理只显示匹配行
        if show_only_matches:
            self._show_only_matched_lines_optimized(matched_lines)
        
        highlight_time = time.time() - start_time
        self._last_highlight_time = highlight_time
        self._update_highlight_stats(len(highlight_ranges), highlight_time)
        
        print(f"✨ 批量高亮完成 - 耗时: {highlight_time:.3f}秒")
        self.highlight_completed.emit(len(highlight_ranges))

    def _build_search_pattern(self, keywords: List[str], ignore_case: bool, whole_word: bool) -> str:
        """构建搜索正则表达式"""
        if not keywords:
            return ""
        
        processed_keywords = []
        for keyword in keywords:
            if not keyword.strip():
                continue
            
            escaped = re.escape(keyword.strip())
            if whole_word:
                escaped = r'\b' + escaped + r'\b'
            
            processed_keywords.append(escaped)
        
        if not processed_keywords:
            return ""
        
        pattern = '(?:' + '|'.join(processed_keywords) + ')'
        return pattern

    def _find_all_matches(self, include_pattern: str, exclude_pattern: str = "") -> List[Tuple[int, int, int]]:
        """查找所有匹配项 - 返回 (行号, 开始位置, 结束位置)"""
        if not include_pattern:
            return []
        
        try:
            include_regex = re.compile(include_pattern, re.IGNORECASE)
            exclude_regex = re.compile(exclude_pattern, re.IGNORECASE) if exclude_pattern else None
        except re.error as e:
            print(f"❌ 正则表达式错误: {e}")
            return []
        
        text = self.toPlainText()
        lines = text.splitlines()
        matches = []
        
        for line_idx, line in enumerate(lines):
            # 检查包含条件
            include_matches = list(include_regex.finditer(line))
            if not include_matches:
                continue
            
            # 检查排除条件
            if exclude_regex and exclude_regex.search(line):
                continue
            
            # 记录所有匹配位置
            for match in include_matches:
                matches.append((line_idx, match.start(), match.end()))
        
        return matches

    def _convert_ranges_to_document_positions(self, highlight_ranges: List[Tuple[int, int, int]]) -> List[Tuple[int, int]]:
        """将行号+位置范围转换为文档绝对位置"""
        text = self.toPlainText()
        lines = text.splitlines(keepends=True)
        
        document_highlights = []
        line_starts = [0]  # 每行在文档中的起始位置
        
        # 计算每行的起始位置
        for line in lines[:-1]:
            line_starts.append(line_starts[-1] + len(line))
        
        # 转换范围
        for line_idx, start_pos, end_pos in highlight_ranges:
            if line_idx < len(line_starts):
                doc_start = line_starts[line_idx] + start_pos
                doc_end = line_starts[line_idx] + end_pos
                document_highlights.append((doc_start, doc_end))
        
        return document_highlights

    def _apply_highlights_batch(self, matches: List[Tuple[int, int, int]], highlight_type: str = 'include'):
        """批量应用高亮 - 传统方法"""
        if not matches:
            return
        
        cursor = self.textCursor()
        format = self.highlight_formats.get(highlight_type, self.highlight_formats['include'])
        
        # 按文档位置排序，避免光标跳跃
        text = self.toPlainText()
        lines = text.splitlines(keepends=True)
        line_starts = [0]
        
        for line in lines[:-1]:
            line_starts.append(line_starts[-1] + len(line))
        
        doc_positions = []
        for line_idx, start_pos, end_pos in matches:
            if line_idx < len(line_starts):
                doc_start = line_starts[line_idx] + start_pos
                doc_end = line_starts[line_idx] + end_pos
                doc_positions.append((doc_start, doc_end))
        
        # 按位置排序
        doc_positions.sort()
        
        # 批量应用格式
        for doc_start, doc_end in doc_positions:
            cursor.setPosition(doc_start)
            cursor.setPosition(doc_end, QTextCursor.KeepAnchor)
            cursor.setCharFormat(format)
            self._current_highlights.append((doc_start, doc_end, highlight_type))

    def _apply_highlights_optimized(self, document_highlights: List[Tuple[int, int]]):
        """优化的批量高亮应用"""
        if not document_highlights:
            return
        
        # 合并重叠的范围以提升性能
        merged_ranges = self._merge_overlapping_ranges(document_highlights)
        
        cursor = self.textCursor()
        format = self.highlight_formats['include']
        
        # 批量应用，减少重绘次数
        self.setUpdatesEnabled(False)
        try:
            for start_pos, end_pos in merged_ranges:
                cursor.setPosition(start_pos)
                cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
                cursor.setCharFormat(format)
                self._current_highlights.append((start_pos, end_pos, 'include'))
        finally:
            self.setUpdatesEnabled(True)

    def _merge_overlapping_ranges(self, ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """合并重叠的高亮范围"""
        if not ranges:
            return []
        
        # 按起始位置排序
        sorted_ranges = sorted(ranges)
        merged = [sorted_ranges[0]]
        
        for current_start, current_end in sorted_ranges[1:]:
            last_start, last_end = merged[-1]
            
            # 如果当前范围与最后一个范围重叠或相邻
            if current_start <= last_end + 1:
                # 合并范围
                merged[-1] = (last_start, max(last_end, current_end))
            else:
                # 添加新范围
                merged.append((current_start, current_end))
        
        return merged

    def _show_only_matching_lines(self, matches: List[Tuple[int, int, int]]):
        """只显示匹配的行 - 传统方法"""
        if not matches:
            return
        
        # 获取所有匹配的行号
        matched_line_numbers = set(match[0] for match in matches)
        
        # 构建只包含匹配行的文本
        lines = self.toPlainText().splitlines()
        filtered_lines = []
        
        for i, line in enumerate(lines):
            if i in matched_line_numbers:
                filtered_lines.append(line)
        
        # 更新编辑器内容
        self.setPlainText('\n'.join(filtered_lines))
        
        # 重新应用高亮（因为行号已改变）
        self._reapply_highlights_for_filtered_text(matches, matched_line_numbers)

    def _show_only_matched_lines_optimized(self, matched_lines: List[Tuple[int, str]]):
        """优化的只显示匹配行"""
        if not matched_lines:
            return
        
        # 直接使用匹配行的内容
        filtered_text = '\n'.join(line_content for _, line_content in matched_lines)
        self.setPlainText(filtered_text)

    def _reapply_highlights_for_filtered_text(self, original_matches: List[Tuple[int, int, int]], 
                                            matched_line_numbers: set):
        """为过滤后的文本重新应用高亮"""
        # 创建行号映射 (原行号 -> 新行号)
        line_mapping = {}
        new_line_idx = 0
        for old_line_idx in sorted(matched_line_numbers):
            line_mapping[old_line_idx] = new_line_idx
            new_line_idx += 1
        
        # 转换匹配位置
        new_matches = []
        for old_line_idx, start_pos, end_pos in original_matches:
            if old_line_idx in line_mapping:
                new_line_idx = line_mapping[old_line_idx]
                new_matches.append((new_line_idx, start_pos, end_pos))
        
        # 应用高亮到新的文本位置
        self._apply_highlights_batch(new_matches)

    def _clear_highlights(self):
        """清除所有高亮"""
        if not self._current_highlights:
            return
        
        cursor = self.textCursor()
        default_format = QTextCharFormat()
        
        self.setUpdatesEnabled(False)
        try:
            for start_pos, end_pos, _ in self._current_highlights:
                cursor.setPosition(start_pos)
                cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
                cursor.setCharFormat(default_format)
        finally:
            self.setUpdatesEnabled(True)
        
        self._current_highlights.clear()

    def _clear_cache(self):
        """清除搜索结果缓存"""
        self._search_results_cache.clear()

    def _update_highlight_stats(self, match_count: int, highlight_time: float):
        """更新高亮统计信息"""
        self._highlight_stats["total_highlights"] += match_count
        
        # 计算平均时间
        if self._highlight_stats["avg_time"] == 0:
            self._highlight_stats["avg_time"] = highlight_time
        else:
            self._highlight_stats["avg_time"] = (self._highlight_stats["avg_time"] + highlight_time) / 2

    def set_search_results(self, matched_lines: List[Tuple[int, str]], total_matches: int):
        """设置搜索结果信息"""
        # 这里可以添加搜索结果的显示逻辑
        print(f"📋 搜索结果设置: {len(matched_lines)} 行, {total_matches} 个匹配")

    def get_highlight_stats(self) -> Dict[str, float]:
        """获取高亮统计信息"""
        return {
            "last_highlight_time": self._last_highlight_time,
            "total_highlights": self._highlight_stats["total_highlights"],
            "avg_highlight_time": self._highlight_stats["avg_time"],
            "current_highlights": len(self._current_highlights)
        }

    def export_highlighted_text(self, filename: str):
        """导出高亮文本"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(self.toPlainText())
            print(f"✅ 高亮文本已导出到: {filename}")
        except Exception as e:
            print(f"❌ 导出失败: {e}")
            raise

    def _apply_pending_highlights(self):
        """应用待处理的高亮 (异步)"""
        if not self._pending_highlights:
            return
        
        highlights_to_apply = self._pending_highlights.copy()
        self._pending_highlights.clear()
        
        self._apply_highlights_optimized(highlights_to_apply)

    def schedule_async_highlight(self, document_highlights: List[Tuple[int, int]], delay_ms: int = 100):
        """安排异步高亮处理"""
        self._pending_highlights.extend(document_highlights)
        self._highlight_timer.start(delay_ms)

# 向后兼容的别名
CodeEditor = HighPerformanceCodeEditor

# from PyQt5.QtWidgets import QPlainTextEdit, QWidget, QTextEdit
# from PyQt5.QtGui import QPainter, QTextFormat, QColor, QTextCursor, QTextCharFormat, QTextDocument
# from PyQt5.QtCore import Qt, QRect, QSize
# import re

# class LineNumberArea(QWidget):
#     def __init__(self, editor):
#         super().__init__(editor)
#         self.code_editor = editor

#     def sizeHint(self):
#         return QSize(self.code_editor.lineNumberAreaWidth(), 0)

#     def paintEvent(self, event):
#         self.code_editor.lineNumberAreaPaintEvent(event)

# class CodeEditor(QPlainTextEdit):
#     def __init__(self):
#         super().__init__()
#         self.lineNumberArea = LineNumberArea(self)

#         self.original_lines = []
#         self.filtered_lines = []

#         self.include_keywords = []
#         self.exclude_keywords = []
#         self.ignore_alpha = True
#         self.whole_pair = False

#         self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
#         self.updateRequest.connect(self.updateLineNumberArea)
#         self.cursorPositionChanged.connect(self.highlightCurrentLine)

#         self.updateLineNumberAreaWidth(0)
#         self.highlightCurrentLine()

#     def load_text(self, text):
#         self.original_lines = text.splitlines()
#         self.filtered_lines = self.original_lines.copy()
#         self.setPlainText(text)

#     def search_and_highlight(self, include_keywords, exclude_keywords,
#                               show_only_matches=False,
#                               ignore_alpha=True,
#                               whole_pair=False):
#         self.include_keywords = include_keywords
#         self.exclude_keywords = exclude_keywords
#         self.ignore_alpha = ignore_alpha
#         self.whole_pair = whole_pair

#         self.clear_highlights()

#         flags = 0 if ignore_alpha else re.IGNORECASE
#         self.results = [line for line in self.original_lines if self.is_line_valid(line, flags)]

#         display_lines = self.results if show_only_matches else self.original_lines
#         self.setPlainText("\n".join(display_lines))

#         self.highlight_keywords()

#     def highlight_keywords(self):
#         self.clear_highlights()
#         cursor = QTextCursor(self.document())
#         fmt = QTextCharFormat()
#         colors = ["yellow", "lightgreen", "cyan", "magenta", "orange"]

#         if self.whole_pair:
#             self._highlight_whole_words(fmt, colors)
#         else:
#             self._highlight_partial(fmt, colors)

#     def _highlight_partial(self, fmt, colors):
#         for i, keyword in enumerate(self.include_keywords):
#             if not keyword.strip():
#                 continue
#             fmt.setBackground(QColor(colors[i % len(colors)]))
#             self._apply_highlight(keyword, fmt, whole_word=False)

#     def _highlight_whole_words(self, fmt, colors):
#         for i, keyword in enumerate(self.include_keywords):
#             if not keyword.strip():
#                 continue
#             fmt.setBackground(QColor(colors[i % len(colors)]))
#             self._apply_highlight(keyword, fmt, whole_word=True)

#     def _apply_highlight(self, keyword, fmt, whole_word=False):
#         search_cursor = QTextCursor(self.document())
#         search_cursor.movePosition(QTextCursor.Start)

#         flags = QTextDocument.FindFlags()
#         if self.ignore_alpha:
#             flags |= QTextDocument.FindCaseSensitively
#         if whole_word:
#             flags |= QTextDocument.FindWholeWords

#         while True:
#             search_cursor = self.document().find(keyword, search_cursor, flags)
#             if search_cursor.isNull():
#                 break
#             search_cursor.mergeCharFormat(fmt)


#     def is_line_valid(self, line, flags):
#         include_ok = all(self.keyword_match(line, k, flags) for k in self.include_keywords)
#         exclude_ok = not any(self.keyword_match(line, k, flags) for k in self.exclude_keywords)
#         return include_ok and exclude_ok

#     def keyword_match(self, line, keyword, flags):
#         pattern = r"(?<!\\w){}(?!\\w)".format(re.escape(keyword)) if self.whole_pair else re.escape(keyword)
#         return bool(re.search(pattern, line, flags))

#     def get_search_res(self, include_regex, exclude_regex):
#         return (
#             len(self.results),
#             f"{include_regex} & {exclude_regex}",
#             f"包含：{', '.join(self.include_keywords)}\n排除：{', '.join(self.exclude_keywords)}"
#         )

#     def clear_highlights(self):
#         cursor = QTextCursor(self.document())
#         cursor.select(QTextCursor.Document)
#         fmt = QTextCharFormat()
#         fmt.setBackground(QColor("transparent"))
#         cursor.mergeCharFormat(fmt)

#     def reset_text(self):
#         self.filtered_lines = self.original_lines.copy()
#         self.setPlainText("\n".join(self.original_lines))

#     def lineNumberAreaWidth(self):
#         digits = len(str(self.blockCount())) + 1
#         return 10 + self.fontMetrics().horizontalAdvance('9') * digits

#     def updateLineNumberAreaWidth(self, _):
#         self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

#     def updateLineNumberArea(self, rect, dy):
#         if dy:
#             self.lineNumberArea.scroll(0, dy)
#         else:
#             self.lineNumberArea.update(0, rect.y(), self.lineNumberArea.width(), rect.height())

#     def resizeEvent(self, event):
#         super().resizeEvent(event)
#         cr = self.contentsRect()
#         self.lineNumberArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

#     def lineNumberAreaPaintEvent(self, event):
#         painter = QPainter(self.lineNumberArea)
#         painter.fillRect(event.rect(), QColor("#DADEEBF7"))

#         block = self.firstVisibleBlock()
#         blockNumber = block.blockNumber()
#         top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
#         bottom = top + self.blockBoundingRect(block).height()

#         while block.isValid() and top <= event.rect().bottom():
#             if block.isVisible() and bottom >= event.rect().top():
#                 number = str(blockNumber + 1)
#                 painter.setPen(Qt.lightGray)
#                 painter.drawText(0, int(top), self.lineNumberArea.width() - 5,
#                                  int(self.fontMetrics().height()), Qt.AlignRight, number)
#             block = block.next()
#             top = bottom
#             bottom = top + self.blockBoundingRect(block).height()
#             blockNumber += 1

#     def highlightCurrentLine(self):
#         if not self.isReadOnly():
#             selection = QTextEdit.ExtraSelection()
#             selection.format.setBackground(QColor("#DADEEBF7"))
#             selection.format.setProperty(QTextFormat.FullWidthSelection, True)
#             selection.cursor = self.textCursor()
#             selection.cursor.clearSelection()
#             self.setExtraSelections([selection])
