"""
对话框模块 — 断连确认对话框和操作确认对话框
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QGroupBox, QGridLayout, QMessageBox)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


class ReconnectDialog(QDialog):
    """断连续连确认对话框 — 最高安全优先级"""

    def __init__(self, port: str, channel_states: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚠ 设备重新连接 — 安全确认")
        self.setMinimumWidth(500)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._result = "keep"  # 默认: 保持当前状态

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 警告横幅
        warning_label = QLabel("⚠ 警告: 设备连接已恢复!")
        warning_font = QFont()
        warning_font.setPointSize(14)
        warning_font.setBold(True)
        warning_label.setFont(warning_font)
        warning_label.setStyleSheet("color: #FF0000; background-color: #FFF0F0; "
                                    "padding: 10px; border: 2px solid #FF0000; "
                                    "border-radius: 4px;")
        warning_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(warning_label)

        # 设备信息
        info_label = QLabel(f"设备端口: {port}\n"
                           f"连接已恢复，请确认操作方式。\n"
                           f"⚠ 软件不会自动改变任何电压设定!")
        info_label.setStyleSheet("font-size: 12px; padding: 5px;")
        layout.addWidget(info_label)

        # 当前电源状态
        states_group = QGroupBox("当前电源状态 (实时读取)")
        states_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #ccc; "
                                   "border-radius: 4px; padding: 8px; margin-top: 8px; }"
                                   "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }")

        states_layout = QGridLayout(states_group)
        states_layout.setSpacing(6)

        for i, state in enumerate(channel_states):
            ch_idx = state.get("channel", i + 1)
            v_meas = state.get("v_measure", 0.0)
            i_meas = state.get("i_measure", 0.0)
            v_set = state.get("v_set", 0.0)
            output_on = state.get("output_on", False)

            ch_label = QLabel(f"通道 {ch_idx}:")
            ch_label.setStyleSheet("font-weight: bold;")
            states_layout.addWidget(ch_label, i, 0)

            if output_on and v_meas > 10:
                status_text = f"⚡ 高压输出中 — Vmeas={v_meas:.1f}V, Iset={v_set:.1f}V"
                status_style = "color: #FF0000; font-weight: bold;"
            else:
                status_text = f"✓ 安全 — Vmeas={v_meas:.1f}V, Iset={v_set:.1f}V"
                status_style = "color: #008000;"

            status_info = QLabel(status_text)
            status_info.setStyleSheet(status_style)
            states_layout.addWidget(status_info, i, 1)

        layout.addWidget(states_group)

        # 安全提示
        safety_label = QLabel("🔴 请根据实际实验情况选择操作方式:\n"
                             "• 如果实验正在运行中，请选择'保持当前状态'\n"
                             "• 仅在确认安全后选择'关闭高压输出'")
        safety_label.setStyleSheet("color: #CC0000; font-size: 11px; padding: 5px;")
        layout.addWidget(safety_label)

        # 按钮区域
        button_layout = QHBoxLayout()

        btn_keep = QPushButton("保持当前状态 (推荐)")
        btn_keep.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; "
                               "font-weight: bold; padding: 10px 20px; border-radius: 4px; "
                               "font-size: 13px; }"
                               "QPushButton:hover { background-color: #45a049; }")
        btn_keep.clicked.connect(lambda: self._done("keep"))

        btn_shutdown = QPushButton("安全降压至 0V")
        btn_shutdown.setStyleSheet("QPushButton { background-color: #FF9800; color: white; "
                                   "font-weight: bold; padding: 10px 20px; border-radius: 4px; "
                                   "font-size: 13px; }"
                                   "QPushButton:hover { background-color: #F57C00; }")
        btn_shutdown.clicked.connect(lambda: self._done("shutdown"))

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)

        button_layout.addWidget(btn_keep)
        button_layout.addWidget(btn_shutdown)
        button_layout.addWidget(btn_cancel)
        layout.addLayout(button_layout)

    def _done(self, result: str) -> None:
        self._result = result
        self.accept()

    def get_result(self) -> str:
        return self._result


def show_confirm_dialog(parent, title: str, message: str, detail: str = "") -> bool:
    """通用操作确认对话框"""
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle(title)
    msg_box.setIcon(QMessageBox.Warning)
    msg_box.setText(message)
    if detail:
        msg_box.setDetailedText(detail)
    msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg_box.setDefaultButton(QMessageBox.No)
    msg_box.button(QMessageBox.Yes).setText("确认")
    msg_box.button(QMessageBox.No).setText("取消")
    return msg_box.exec_() == QMessageBox.Yes
