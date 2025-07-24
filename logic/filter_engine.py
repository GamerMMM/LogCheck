import re
from widgets.code_editor import CodeEditor

class FilterEngine:
    def __init__(self):
        pass

    def apply(self, editor: CodeEditor, include_keywords: list[str], exclude_keywords: list[str],
              show_only: bool, ignore_alpha: bool, whole_pair: bool):
        editor.search_and_highlight(
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            show_only_matches=show_only,
            ignore_alpha=ignore_alpha,
            whole_pair=whole_pair
        )

    def get_regex(self, keywords: list[str]) -> str:
        if not keywords:
            return ""
        pattern = "|".join(re.escape(kw) for kw in keywords)
        return pattern
