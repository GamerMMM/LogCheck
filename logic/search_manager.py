import re
from widgets.search_table import SearchTable

class SearchManager:
    def __init__(self):
        pass

    def get_keywords_from_table(self, table: SearchTable) -> tuple[list[str], list[str]]:
        include_keywords = []
        exclude_keywords = []

        for row in range(table.rowCount()):
            checkbox_widget = table.cellWidget(row, 0)
            if checkbox_widget and hasattr(checkbox_widget, 'checkbox') and checkbox_widget.checkbox.isChecked():
                pattern_item = table.item(row, 1)
                desc_item = table.item(row, 2)

                if desc_item:
                    include_part = re.search(r"\u5305\u542b\uff1a(.*?)\n", desc_item.text())
                    exclude_part = re.search(r"\u6392\u9664\uff1a(.*)", desc_item.text())

                    if include_part:
                        include_keywords += self._extract_keywords(include_part)
                    if exclude_part:
                        exclude_keywords += self._extract_keywords(exclude_part)

        return list(set(include_keywords)), list(set(exclude_keywords))

    def _extract_keywords(self, match: re.Match) -> list[str]:
        return [kw.strip() for kw in match.group(1).split(',') if kw.strip()]
