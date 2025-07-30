from dataclasses import dataclass

@dataclass
class SearchResult:
    """搜索结果数据类 - 存储每个搜索匹配项的详细信息"""
    line_number: int      # 行号（从0开始）
    column_start: int     # 匹配开始列位置
    column_end: int       # 匹配结束列位置
    matched_text: str     # 匹配的文本内容
    line_content: str     # 完整的行内容（用于上下文显示）
    file_offset: int      # 在文件中的字节偏移量