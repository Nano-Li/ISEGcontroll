"""
ISEG THQ 高压电源控制器 — 应用程序入口

用法: python main.py
"""

import sys
import os
import traceback
from datetime import datetime

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt

from ui.main_window import MainWindow


def setup_exception_handler():
    """设置全局异常处理器 — 将崩溃信息写入日志文件"""
    original_hook = sys.excepthook

    def exception_handler(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(f"未捕获异常: {msg}", file=sys.stderr)

        try:
            log_dir = os.path.join(os.environ.get("APPDATA", "."),
                                   "ISEG_THQ_Controller", "logs")
            os.makedirs(log_dir, exist_ok=True)
            crash_file = os.path.join(
                log_dir,
                f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            )
            with open(crash_file, "w", encoding="utf-8") as f:
                f.write(f"=== 崩溃时间: {datetime.now()} ===\n")
                f.write(f"=== 异常类型: {exc_type.__name__} ===\n")
                f.write(msg)
            print(f"崩溃日志已保存到: {crash_file}", file=sys.stderr)
        except Exception:
            pass

        # 显示错误对话框
        try:
            QMessageBox.critical(
                None, "程序错误",
                f"发生未预期的错误:\n{exc_value}\n\n"
                f"详细信息已记录到日志文件。\n"
                f"请重新启动程序。"
            )
        except Exception:
            pass

        if original_hook is not sys.__excepthook__:
            original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = exception_handler


def main():
    """主函数"""
    # 设置高DPI支持
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("ISEG THQ 电源控制器")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("ISEG THQ Controller")

    setup_exception_handler()

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
