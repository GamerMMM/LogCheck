#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# PyQt5导入
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt

def setup_application():
    """设置应用程序"""
    # 设置高DPI支持（PyQt5）
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    # 创建应用程序实例
    app = QApplication(sys.argv)
    
    # 设置应用程序属性
    app.setApplicationName("LogCheck - 高性能日志搜索工具")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("LSBT")
    
    return app

def check_ui_file():
    """检查UI文件是否存在"""
    ui_files = ["log_ui.ui", "ui/log_ui.ui", "../log_ui.ui"]
    
    for ui_file in ui_files:
        if os.path.exists(ui_file):
            print(f"找到UI文件: {ui_file}")
            return ui_file
    
    print("错误: 未找到log_ui.ui文件")
    print("请确保UI文件在以下位置之一:")
    for ui_file in ui_files:
        print(f"  - {os.path.abspath(ui_file)}")
    
    return None

def check_dependencies():
    """检查依赖模块"""
    missing_modules = []
    
    try:
        from widgets.code_editor import CodeEditor
        print("✓ code_editor 模块导入成功")
    except ImportError as e:
        missing_modules.append(("widgets.code_editor", str(e)))
    
    try:
        from widgets.search_table import SearchTable
        print("✓ search_table 模块导入成功")
    except ImportError as e:
        missing_modules.append(("widgets.search_table", str(e)))
    
    try:
        from logic.filter_engine import FilterEngine
        print("✓ filter_engine 模块导入成功")
    except ImportError as e:
        missing_modules.append(("logic.filter_engine", str(e)))
    
    try:
        from logic.search_manager import SearchManager
        print("✓ search_manager 模块导入成功")
    except ImportError as e:
        missing_modules.append(("logic.search_manager", str(e)))
    
    try:
        from logic.file_io import FileHandler
        print("✓ file_io 模块导入成功")
    except ImportError as e:
        missing_modules.append(("logic.file_io", str(e)))
    
    try:
        from logic.parallel_search import SearchWorker, RealTimeRegexWorker, BatchFileSearchWorker, SearchCoordinator
        print("✓ parallel_search 模块导入成功")
    except ImportError as e:
        missing_modules.append(("logic.parallel_search", str(e)))
    
    if missing_modules:
        print("\n缺少以下模块:")
        for module, error in missing_modules:
            print(f"  - {module}: {error}")
        return False
    
    return True

def main():
    """主函数"""
    print("=" * 50)
    print("LogCheck - 高性能日志搜索工具启动中...")
    print("=" * 50)
    
    try:
        # 1. 检查工作目录
        print(f"当前工作目录: {os.getcwd()}")
        print(f"Python路径: {sys.path[0]}")
        
        # 2. 检查UI文件
        ui_file = check_ui_file()
        if not ui_file:
            input("按回车键退出...")
            return 1
        
        # 3. 检查依赖模块
        print("\n检查依赖模块...")
        if not check_dependencies():
            input("按回车键退出...")
            return 1
        
        # 4. 创建应用程序实例
        print("\n创建应用程序...")
        app = setup_application()
        
        # 5. 导入主窗口类
        print("导入主窗口...")
        try:
            from ui.main_window import MainWindow
            print("✓ 主窗口类导入成功")
        except ImportError as e:
            print(f"✗ 主窗口类导入失败: {e}")
            
            # 尝试直接导入
            try:
                from main_window import MainWindow
                print("✓ 主窗口类导入成功（直接导入）")
            except ImportError as e2:
                print(f"✗ 主窗口类导入失败（直接导入）: {e2}")
                QMessageBox.critical(None, "导入错误", f"无法导入主窗口类:\n{e}\n{e2}")
                return 1
        
        # 6. 创建主窗口
        print("创建主窗口...")
        try:
            window = MainWindow()
            print("✓ 主窗口创建成功")
        except Exception as e:
            print(f"✗ 主窗口创建失败: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(None, "窗口创建错误", f"创建主窗口失败:\n{str(e)}")
            return 1
        
        # 7. 显示窗口
        print("显示主窗口...")
        try:
            window.show()
            print("✓ 主窗口显示成功")
        except Exception as e:
            print(f"✗ 主窗口显示失败: {e}")
            QMessageBox.critical(None, "显示错误", f"显示主窗口失败:\n{str(e)}")
            return 1
        
        # 8. 启动事件循环
        print("\n" + "=" * 30)
        print("应用程序启动成功！")
        print("=" * 30)
        
        exit_code = app.exec_()
        
        print(f"\n应用程序退出，退出码: {exit_code}")
        return exit_code
        
    except Exception as e:
        print(f"\n应用程序启动失败: {e}")
        import traceback
        traceback.print_exc()
        
        # 尝试显示错误对话框
        try:
            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)
            QMessageBox.critical(None, "启动错误", f"应用程序启动失败:\n{str(e)}")
        except:
            pass
        
        input("按回车键退出...")
        return 1

if __name__ == '__main__':
    # 设置异常处理
    def handle_exception(exc_type, exc_value, exc_tb):
        print(f"\n未捕获的异常: {exc_type.__name__}: {exc_value}")
        import traceback
        traceback.print_exception(exc_type, exc_value, exc_tb)
        
        # 尝试显示错误对话框
        try:
            app = QApplication.instance()
            if app:
                QMessageBox.critical(None, "程序错误", f"程序遇到未处理的错误:\n{exc_type.__name__}: {exc_value}")
        except:
            pass
    
    sys.excepthook = handle_exception
    
    # 运行主程序
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n程序被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n程序异常退出: {e}")
        sys.exit(1)