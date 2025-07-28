# import sys
# from PyQt5.QtWidgets import QApplication, QMainWindow, QTextEdit, QPushButton, QVBoxLayout, QWidget, QDesktopWidget
# from PyQt5.QtCore import QThread, pyqtSignal, Qt
# from PyQt5.QtGui import QTextCursor

# # 多线程读取大文件
# class FileReadThread(QThread):
#     chunkRead = pyqtSignal(str)
#     fileFinished = pyqtSignal(str)

#     def __init__(self, file_path):
#         super().__init__()
#         self.file_path = file_path
#         self.chunk_size = 1024 * 1024  # 每次读取 1MB
#         self._is_running = True

#     def run(self):
#         try:
#             with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
#                 while self._is_running:
#                     chunk = f.read(self.chunk_size)
#                     if not chunk:
#                         break
#                     self.chunkRead.emit(chunk)
#             self.fileFinished.emit(f"\n✅ 文件读取完成：{self.file_path}\n")
#         except Exception as e:
#             self.fileFinished.emit(f"❌ 文件读取失败：{e}")

#     def stop(self):
#         self._is_running = False
#         self.quit()
#         self.wait()


# class DragDropTextEdit(QTextEdit):
#     def __init__(self, parent=None):
#         super().__init__(parent)
#         self.setAcceptDrops(True)
#         self.threads = []  # 保存线程引用

#     def dragEnterEvent(self, event):
#         if event.mimeData().hasUrls():
#             event.acceptProposedAction()
#         else:
#             event.ignore()

#     def dropEvent(self, event):
#         for url in event.mimeData().urls():
#             if url.isLocalFile():
#                 file_path = url.toLocalFile()
#                 if file_path.endswith('.txt'):
#                     self.append(f"📄 开始读取：{file_path}")
#                     thread = FileReadThread(file_path)
#                     thread.chunkRead.connect(self.appendChunk)
#                     thread.fileFinished.connect(self.appendChunk)
#                     thread.finished.connect(lambda: self.threads.remove(thread))
#                     self.threads.append(thread)
#                     thread.start()
#                 else:
#                     self.append(f"⚠️ 不支持的文件类型：{file_path}")

#     def appendChunk(self, text):
#         # 安全追加内容
#         self.moveCursor(QTextCursor.End)
#         self.insertPlainText(text)
#         self.ensureCursorVisible()


# class MainApp(QMainWindow):
#     def __init__(self):
#         super().__init__()
#         self.initUI()

#     def initUI(self):
#         self.setWindowTitle('大文件拖拽读取工具')
#         screen = QDesktopWidget().screenGeometry()
#         x = (screen.width() - 600) // 2
#         y = (screen.height() - 400) // 2
#         self.setGeometry(x, y, 600, 400)

#         central = QWidget(self)
#         self.setCentralWidget(central)
#         layout = QVBoxLayout(central)

#         self.textEdit = DragDropTextEdit()
#         layout.addWidget(self.textEdit)

#         self.submit_Button = QPushButton('提交文件')
#         self.submit_Button.clicked.connect(self.processFiles)
#         layout.addWidget(self.submit_Button)

#     def processFiles(self):
#         # 保留此函数做额外扩展（当前无操作）
#         print("当前文件内容预览：", self.textEdit.toPlainText()[:100])


# def main():
#     app = QApplication(sys.argv)
#     ex = MainApp()
#     ex.show()
#     sys.exit(app.exec_())


# if __name__ == '__main__':
#     main()

import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QTextEdit, QPushButton, QVBoxLayout, QWidget, QDesktopWidget

from PyQt5.QtCore import QThread, pyqtSignal

# class FileReadThread(QThread):
#     fileReadFinished = pyqtSignal(str)

#     def __init__(self, file_path):
#         super().__init__()
#         self.file_path = file_path

#     def run(self):
#         try:
#             with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
#                 content = f.read()
#             self.fileReadFinished.emit(content)
#         except Exception as e:
#             self.fileReadFinished.emit(f"读取失败：{e}")


# '''
# #1、使用以下代码中的文件拖拽功能，只需将文件或文件夹拖拽到文本编辑框中即可。如果文件是本地文件，它们将以文件路径的形式显示在文本编辑框中。
# #2、如果你想要进一步处理这些文件路径，比如复制、移动、读取或执行其他操作，你可以在 processFiles 方法中添加你的自定义代码，该方法在用户点击提交按钮后被调用。在该方法中，你可以访问文本编辑框的内容，将其拆分成文件路径，并执行相应的操作。
# '''
# #使用子类来继承父类的方法，这里的'DragDropTextEdit‘，继承自 'QTextEdit‘ ，并且添加了文件拖拽的支持。
# #这使得你可以将它用作拖拽文件的目标，以便在应用程序中方便地处理文件路径。
# class DragDropTextEdit(QTextEdit):
#     def __init__(self, parent=None):
#         super().__init__(parent)
#         self.setAcceptDrops(True)
#         self.threads = []  # 存储线程，防止被垃圾回收

#     def dragEnterEvent(self, event):
#         if event.mimeData().hasUrls():
#             event.accept()
#         else:
#             event.ignore()

#     def dropEvent(self, event):
#         for url in event.mimeData().urls():
#             if url.isLocalFile():
#                 file_path = url.toLocalFile()
#                 if file_path.endswith('.txt'):
#                     self.append(f"正在加载：{file_path}")
#                     thread = FileReadThread(file_path)
#                     thread.fileReadFinished.connect(self.displayContent)
#                     thread.finished.connect(lambda: self.threads.remove(thread))  # 线程结束后移除
#                     self.threads.append(thread)  # 存储线程引用
#                     thread.start()
#                 else:
#                     self.append(f"不支持的文件类型：{file_path}")

#     def displayContent(self, content):
#         self.append("\n" + content + "\n")


import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QVBoxLayout,
    QWidget, QPushButton, QDesktopWidget
)
from PyQt5.QtCore import Qt, QUrl

class DragDropTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super(DragDropTextEdit, self).__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                file_path = url.toLocalFile()
                if file_path.lower().endswith('.txt'):
                    try:
                        # 尝试 UTF-8
                        with open(file_path, 'r', encoding='utf-8') as file:
                            content = file.read()
                    except UnicodeDecodeError:
                        try:
                            # 尝试 GBK
                            with open(file_path, 'r', encoding='gbk') as file:
                                content = file.read()
                        except Exception as e:
                            self.append(f"读取文件失败：{file_path}\n错误：{str(e)}")
                            continue  # 跳过当前文件
                    except Exception as e:
                        self.append(f"读取文件失败：{file_path}\n错误：{str(e)}")
                        continue

                    self.append(f"\n--- 内容来自：{file_path} ---\n")
                    self.append(content)
                    self.append("\n--- 结束 ---\n")
                else:
                    self.append(f"不支持的文件类型：{file_path}")


class MainApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('文件拖拽窗口')

        screen = QDesktopWidget().screenGeometry()
        screenWidth = screen.width()
        screenHeight = screen.height()
        x = (screenWidth - self.width()) // 2
        y = (screenHeight - self.height()) // 2
        self.setGeometry(x, y, 600, 400)

        central = QWidget(self)
        self.setCentralWidget(central)
        display = QVBoxLayout(central)

        self.textEdit = DragDropTextEdit()
        display.addWidget(self.textEdit)

        self.submit_Button = QPushButton('提交文件', self)
        self.submit_Button.clicked.connect(self.processFiles)
        display.addWidget(self.submit_Button)

    def processFiles(self):
        file_content = self.textEdit.toPlainText()
        print("当前文本内容如下：")
        print(file_content)

def main():
    app = QApplication(sys.argv)
    ex = MainApp()
    ex.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
