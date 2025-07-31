import re
from typing import Optional, List, Tuple, Dict, Set

from widgets.search_table import SearchTable
from dataform.search_result import SearchResult

from PyQt5.QtCore import (QObject, pyqtSignal, QTimer, Qt, QThread, 
                          QMutex, QMutexLocker, QRect)

class SearchResultsManager(QObject):
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
        """导航到下一个结果"""
        with QMutexLocker(self.results_mutex):
            if not self.results:
                return False
                
            self.current_index = (self.current_index + 1) % len(self.results)
            current_result = self.results[self.current_index]
            
        self.current_result_changed.emit(current_result)
        return True
    
    def navigate_to_previous(self) -> bool:
        """导航到上一个结果"""
        with QMutexLocker(self.results_mutex):
            if not self.results:
                return False
                
            self.current_index = (self.current_index - 1) % len(self.results)
            current_result = self.results[self.current_index]
            
        self.current_result_changed.emit(current_result)
        return True
    
    def navigate_to_index(self, index: int) -> bool:
        """导航到指定索引的结果"""
        with QMutexLocker(self.results_mutex):
            if not (0 <= index < len(self.results)):
                return False
                
            self.current_index = index
            current_result = self.results[self.current_index]
            
        self.current_result_changed.emit(current_result)
        return True


class SearchManager(QObject):
    """
    搜索管理器 - 处理搜索逻辑和表格管理
    """
    
    def __init__(self):
        super().__init__()

    def get_keywords_from_table(self, table: SearchTable) -> tuple[list[str], list[str]]:
        """
        从搜索表格中获取已勾选的关键词
        
        Args:
            table: 搜索表格组件
            
        Returns:
            (include_keywords, exclude_keywords) 元组
        """
        include_keywords = []
        exclude_keywords = []

        for row in range(table.rowCount()):
            checkbox_widget = table.cellWidget(row, 0)
            if checkbox_widget and hasattr(checkbox_widget, 'checkbox') and checkbox_widget.checkbox.isChecked():
                desc_item = table.item(row, 2)

                if desc_item:
                    # 解析描述文本中的关键词
                    include_part = re.search(r"包含：(.*?)\n", desc_item.text())
                    exclude_part = re.search(r"排除：(.*)", desc_item.text())

                    if include_part:
                        include_keywords += self._extract_keywords(include_part)
                    if exclude_part:
                        exclude_keywords += self._extract_keywords(exclude_part)

        return list(set(include_keywords)), list(set(exclude_keywords))

    def _extract_keywords(self, match: re.Match) -> list[str]:
        """
        从正则匹配结果中提取关键词列表
        
        Args:
            match: 正则匹配对象
            
        Returns:
            关键词列表
        """
        keywords_text = match.group(1).strip()
        if not keywords_text or keywords_text == "无":
            return []
        return [kw.strip() for kw in keywords_text.split(',') if kw.strip()]

    def format_pattern_display(self, include_keywords: list[str], exclude_keywords: list[str]) -> str:
        """
        格式化显示搜索模式
        
        Args:
            include_keywords: 包含关键词列表
            exclude_keywords: 排除关键词列表
            
        Returns:
            格式化后的模式字符串
        """
        parts = []
        
        if include_keywords:
            include_expr = " | ".join(f"'{kw}'" for kw in include_keywords)
            parts.append(f"({include_expr})")
        
        if exclude_keywords:
            exclude_expr = " | ".join(f"'{kw}'" for kw in exclude_keywords)
            parts.append(f"&! ({exclude_expr})")
        
        return " ".join(parts) if parts else "无搜索条件"