import os
import re
from PyQt5.QtWidgets import QFileDialog
from widgets.code_editor import TextDisplay
from logic.para_loading import ParaLoadFile

import time

class FileHandler:
    def load_file(self, filepath: str, num_chunks: int=16) -> str | None:
        
        start_time = time.time()
        if not os.path.exists(filepath):
            return None
        try:
            text = ParaLoadFile.main(filepath, num_chunks)
            end_time = time.time()
            print(f"线程数：{num_chunks}，读取文件时间: {end_time - start_time} 秒")
            return text

        except Exception as e:
            print(f"读取文件失败: {e}")
            return None

    def save_filtered_result(self, editor: TextDisplay,
                             include_keywords: list[str],
                             exclude_keywords: list[str],
                             show_only: bool,
                             ignore_alpha: bool,
                             whole_pair: bool,
                             tab_name: str):

        def filter_lines(lines, includes, excludes, ignore_case=False, whole_word=False):
            """
            过滤行函数 - 修复逻辑确保包含所有include关键词
            """
            for line in lines:
                # 根据ignore_alpha参数决定是否忽略大小写
                search_line = line.lower() if ignore_case else line
                
                # 处理包含关键词 - 必须包含所有关键词
                if includes:
                    processed_includes = [kw.lower() if ignore_case else kw for kw in includes]
                    
                    if whole_word:
                        # 全词匹配模式
                        if not all(re.search(r'\b' + re.escape(kw) + r'\b', search_line) for kw in processed_includes):
                            continue
                    else:
                        # 普通包含匹配 - 必须包含所有关键词
                        if not all(kw in search_line for kw in processed_includes):
                            continue
                
                # 处理排除关键词 - 不能包含任何排除关键词
                if excludes:
                    processed_excludes = [kw.lower() if ignore_case else kw for kw in excludes]
                    
                    if whole_word:
                        # 全词匹配模式
                        if any(re.search(r'\b' + re.escape(kw) + r'\b', search_line) for kw in processed_excludes):
                            continue
                    else:
                        # 普通包含匹配
                        if any(kw in search_line for kw in processed_excludes):
                            continue
                
                yield line

        name, _ = os.path.splitext(tab_name)
        
        # 从TextDisplay获取文件内容 - 修复AttributeError
        if not hasattr(editor, 'file_path') or not editor.file_path:
            print("错误：编辑器没有关联的文件路径")
            return
            
        try:
            # 使用已有的文件加载方法读取完整文件内容
            content = self.load_file(editor.file_path)
            if content is None:
                print(f"错误：无法读取文件 {editor.file_path}")
                return
            lines = content.splitlines()
        except Exception as e:
            print(f"读取文件内容失败: {e}")
            return
        
        # 修复：使用ignore_alpha参数而不是ignore_alpha（应该是case_sensitive的反义）
        case_sensitive = not ignore_alpha
        
        # 应用过滤
        filtered_lines = list(filter_lines(lines, include_keywords, exclude_keywords, 
                                         ignore_case=ignore_alpha, whole_word=whole_pair))

        file_path, _ = QFileDialog.getSaveFileName(
            None, "保存过滤结果", f"{name}_result.txt", "Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return

        dir_path = os.path.dirname(file_path)
        info_path = os.path.join(dir_path, f"{name}_info.txt")
        result_path = os.path.join(dir_path, f"{name}_result.txt")

        # 生成更详细的pattern信息
        patterns_info = self._generate_patterns_info(include_keywords, exclude_keywords, 
                                                   ignore_alpha, whole_pair, show_only)

        # 保存过滤条件和pattern信息到info文件
        with open(info_path, 'w', encoding='utf-8') as f:
            f.write("【过滤条件】\n")
            f.write(f"包含关键词: {include_keywords if include_keywords else '无'}\n")
            f.write(f"排除关键词: {exclude_keywords if exclude_keywords else '无'}\n")
            f.write(f"忽略大小写: {'是' if ignore_alpha else '否'}\n")
            f.write(f"全词匹配: {'是' if whole_pair else '否'}\n")
            f.write(f"仅显示匹配行: {'是' if show_only else '否'}\n")
            f.write(f"匹配结果总数: {len(filtered_lines)} 行\n")
            f.write(f"原文件总行数: {len(lines)} 行\n\n")
            
            f.write("【搜索模式详情】\n")
            f.write(patterns_info)

        # 保存过滤结果 - 仅包含结果行
        with open(result_path, 'w', encoding='utf-8') as f:
            for line in filtered_lines:
                f.write(line + '\n')

        print(f"过滤条件已保存到: {info_path}")
        print(f"过滤结果已保存到: {result_path}")
        print(f"共找到 {len(filtered_lines)} 行匹配结果")

    def _generate_patterns_info(self, include_keywords: list[str], exclude_keywords: list[str], 
                          ignore_alpha: bool, whole_pair: bool, show_only: bool) -> str:
        """
        生成详细的pattern信息，包含标准正则表达式
        """
        # 生成标准正则表达式
        regex_pattern = self._build_pattern(include_keywords, exclude_keywords, whole_pair, ignore_alpha)
        
        # 生成简化的逻辑表达式（用于用户理解）
        logic_parts = []
        if include_keywords:
            if whole_pair:
                include_expr = " AND ".join([f"\\b{kw}\\b" for kw in include_keywords])
            else:
                include_expr = " AND ".join(include_keywords)
            logic_parts.append(f"({include_expr})")
        
        if exclude_keywords:
            if whole_pair:
                exclude_expr = " AND ".join([f"NOT \\b{kw}\\b" for kw in exclude_keywords])
            else:
                exclude_expr = " AND ".join([f"NOT {kw}" for kw in exclude_keywords])
            logic_parts.append(f"({exclude_expr})")
        
        final_logic = " AND ".join(logic_parts) if logic_parts else "无搜索条件"
        if ignore_alpha and logic_parts:
            final_logic += " [忽略大小写]"
        
        # 构建完整的信息输出
        info = "【搜索模式】\n"
        info += f"逻辑表达式: {final_logic}\n"
        info += f"正则表达式: {regex_pattern}\n\n"
        
        # 添加逻辑说明
        info += "【逻辑说明】\n"
        if include_keywords:
            match_type = "全词匹配" if whole_pair else "部分匹配"
            info += f"• 每行必须包含以下所有关键词({match_type}): {', '.join(include_keywords)}\n"
        if exclude_keywords:
            match_type = "全词匹配" if whole_pair else "部分匹配"
            info += f"• 每行不能包含以下任何关键词({match_type}): {', '.join(exclude_keywords)}\n"
        if ignore_alpha:
            info += "• 忽略大小写\n"
        
        # 添加正则表达式说明
        info += "\n【正则表达式说明】\n"
        if ignore_alpha:
            info += "• (?i) - 忽略大小写标志\n"
        if exclude_keywords:
            info += "• (?!.*keyword) - 负向前瞻，确保不包含指定关键词\n"
        if include_keywords:
            info += "• (?=.*keyword) - 正向前瞻，确保包含指定关键词\n"
        if whole_pair:
            info += "• \\b - 单词边界，用于全词匹配\n"
        info += "• .* - 匹配任意字符\n"
        
        return info
    
    def _build_pattern(self,includes=None, excludes=None, whole_word=False, ignore_case=False):
        includes = includes or []
        excludes = excludes or []

        flags = '(?i)' if ignore_case else ''  # 正则前缀加上忽略大小写

        # 构建包含关键词模式
        include_parts = []
        for kw in includes:
            if whole_word:
                include_parts.append(r'(?=.*\b' + re.escape(kw) + r'\b)')
            else:
                include_parts.append(r'(?=.*' + re.escape(kw) + r')')

        # 构建排除关键词模式
        exclude_parts = []
        for kw in excludes:
            if whole_word:
                exclude_parts.append(r'(?!.*\b' + re.escape(kw) + r'\b)')
            else:
                exclude_parts.append(r'(?!.*' + re.escape(kw) + r')')

        # 最终模式 = 前缀flags + 排除条件 + 包含条件 + 匹配任意内容
        pattern = flags + ''.join(exclude_parts + include_parts) + r'.*'

        return pattern