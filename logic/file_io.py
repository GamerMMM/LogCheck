import os
import re
from PyQt5.QtWidgets import QFileDialog
from widgets.code_editor import TextDisplay
from logic.para_loading import ParaLoadFile

import time

class FileHandler:
    def load_file(self, filepath: str, num_chunks: int=8) -> str | None:
        
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

        def filter_lines(lines, includes, excludes):
            for line in lines:
                if includes and not any(kw in line for kw in includes):
                    continue
                if excludes and any(kw in line for kw in excludes):
                    continue
                yield line

        name, _ = os.path.splitext(tab_name)
        content = editor.toPlainText()
        lines = content.splitlines()
        include_regex = " | ".join(f"'{kw}'" for kw in include_keywords) if include_keywords else ""
        exclude_regex = " &! (" + " | ".join(f"'{kw}'" for kw in exclude_keywords) + ")" if exclude_keywords else ""
        
        filtered = filter_lines(lines, include_keywords, exclude_keywords)

        file_path, _ = QFileDialog.getSaveFileName(
            None, "保存过滤结果", f"{name}_result.txt", "Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return

        dir_path = os.path.dirname(file_path)
        info_path = os.path.join(dir_path, f"{name}_info.txt")
        result_path = os.path.join(dir_path, f"{name}_result.txt")

        with open(info_path, 'w', encoding='utf-8') as f:
            f.write("【过滤条件】\n")
            f.write(f"包含: {', '.join(include_keywords)}\n")
            f.write(f"排除: {', '.join(exclude_keywords)}\n")
            f.write(f"其它: show_only={show_only}, ignore_alpha={ignore_alpha}, whole_pair={whole_pair}\n")
            f.write(f"表达式: ({include_regex}) &! ({exclude_regex})\n")

        with open(result_path, 'w', encoding='utf-8') as f:
            for line in filtered:
                f.write(line + '\n')

        print(f"过滤条件已保存到: {info_path}")
        print(f"过滤结果已保存到: {result_path}")
