from PyQt5.QtWidgets import QPlainTextEdit, QWidget, QTextEdit
from PyQt5.QtGui import QPainter, QTextFormat, QColor, QTextCursor, QTextCharFormat, QTextDocument
from PyQt5.QtCore import Qt, QRect, QSize
import re

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.code_editor = editor

    def sizeHint(self):
        return QSize(self.code_editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.code_editor.lineNumberAreaPaintEvent(event)

class CodeEditor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.lineNumberArea = LineNumberArea(self)

        self.original_lines = []
        self.filtered_lines = []

        self.include_keywords = []
        self.exclude_keywords = []
        self.ignore_alpha = True
        self.whole_pair = False

        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.cursorPositionChanged.connect(self.highlightCurrentLine)

        self.updateLineNumberAreaWidth(0)
        self.highlightCurrentLine()

    def load_text(self, text):
        self.original_lines = text.splitlines()
        self.filtered_lines = self.original_lines.copy()
        self.setPlainText(text)

    def search_and_highlight(self, include_keywords, exclude_keywords,
                              show_only_matches=False,
                              ignore_alpha=True,
                              whole_pair=False):
        self.include_keywords = include_keywords
        self.exclude_keywords = exclude_keywords
        self.ignore_alpha = ignore_alpha
        self.whole_pair = whole_pair

        self.clear_highlights()

        flags = 0 if ignore_alpha else re.IGNORECASE
        self.results = [line for line in self.original_lines if self.is_line_valid(line, flags)]

        display_lines = self.results if show_only_matches else self.original_lines
        self.setPlainText("\n".join(display_lines))

        self.highlight_keywords()

    def highlight_keywords(self):
        self.clear_highlights()
        cursor = QTextCursor(self.document())
        fmt = QTextCharFormat()
        colors = ["yellow", "lightgreen", "cyan", "magenta", "orange"]

        if self.whole_pair:
            self._highlight_whole_words(fmt, colors)
        else:
            self._highlight_partial(fmt, colors)

    def _highlight_partial(self, fmt, colors):
        for i, keyword in enumerate(self.include_keywords):
            if not keyword.strip():
                continue
            fmt.setBackground(QColor(colors[i % len(colors)]))
            self._apply_highlight(keyword, fmt, whole_word=False)

    def _highlight_whole_words(self, fmt, colors):
        for i, keyword in enumerate(self.include_keywords):
            if not keyword.strip():
                continue
            fmt.setBackground(QColor(colors[i % len(colors)]))
            self._apply_highlight(keyword, fmt, whole_word=True)

    def _apply_highlight(self, keyword, fmt, whole_word=False):
        search_cursor = QTextCursor(self.document())
        search_cursor.movePosition(QTextCursor.Start)

        flags = QTextDocument.FindFlags()
        if self.ignore_alpha:
            flags |= QTextDocument.FindCaseSensitively
        if whole_word:
            flags |= QTextDocument.FindWholeWords

        while True:
            search_cursor = self.document().find(keyword, search_cursor, flags)
            if search_cursor.isNull():
                break
            search_cursor.mergeCharFormat(fmt)


    def is_line_valid(self, line, flags):
        include_ok = all(self.keyword_match(line, k, flags) for k in self.include_keywords)
        exclude_ok = not any(self.keyword_match(line, k, flags) for k in self.exclude_keywords)
        return include_ok and exclude_ok

    def keyword_match(self, line, keyword, flags):
        pattern = r"(?<!\\w){}(?!\\w)".format(re.escape(keyword)) if self.whole_pair else re.escape(keyword)
        return bool(re.search(pattern, line, flags))

    def get_search_res(self, include_regex, exclude_regex):
        return (
            len(self.results),
            f"{include_regex} & {exclude_regex}",
            f"包含：{', '.join(self.include_keywords)}\n排除：{', '.join(self.exclude_keywords)}"
        )

    def clear_highlights(self):
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("transparent"))
        cursor.mergeCharFormat(fmt)

    def reset_text(self):
        self.filtered_lines = self.original_lines.copy()
        self.setPlainText("\n".join(self.original_lines))

    def lineNumberAreaWidth(self):
        digits = len(str(self.blockCount())) + 1
        return 10 + self.fontMetrics().horizontalAdvance('9') * digits

    def updateLineNumberAreaWidth(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.lineNumberArea.scroll(0, dy)
        else:
            self.lineNumberArea.update(0, rect.y(), self.lineNumberArea.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.lineNumberArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.lineNumberArea)
        painter.fillRect(event.rect(), QColor("#DADEEBF7"))

        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                painter.setPen(Qt.lightGray)
                painter.drawText(0, int(top), self.lineNumberArea.width() - 5,
                                 int(self.fontMetrics().height()), Qt.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            blockNumber += 1

    def highlightCurrentLine(self):
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(QColor("#DADEEBF7"))
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            self.setExtraSelections([selection])
