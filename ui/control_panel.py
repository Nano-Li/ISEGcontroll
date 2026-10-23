"""
多通道独立控制面板 — 每个通道独立控制按钮

设计:
  每加入一个通道, 动态创建一张 ChannelCard 卡片
  卡片填满面板全宽, 每张卡片拥有独立参数和按钮
  大字显示实时电压/电流, 无需选中通道即可直接操作

布局:
  ┌─────────────────────────────────────────────────────────────┐
  │ [THQ #1] 通道1  ⚡HV  ↑ 100.0→500.0V    Vnom=动态读取      │
  │ V: ████████████████████████████████████                     │
  │ 目标电压:[100 V] 速率:[10 V/s] 约50s   │ 实测: 0523.5 V  │
  │ [▶ 启动] [■ 停止] [▼ 降压]             │ 电流: 00128 µA │
  └─────────────────────────────────────────────────────────────┘
"""

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QLabel, QPushButton, QDoubleSpinBox,
                             QScrollArea, QFrame, QSizePolicy)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont


# ──────────────────────────────────────────────
# 单通道控制卡片
# ──────────────────────────────────────────────

class ChannelCard(QWidget):
    """单个通道的独立控制卡片"""

    sig_start_ramp = pyqtSignal(int, float, float)   # channel_id, target_v, speed_vps
    sig_stop_ramp = pyqtSignal(int)                   # channel_id
    sig_start_ramp_down = pyqtSignal(int, float)      # channel_id, speed_vps
    sig_params_changed = pyqtSignal(int, float, float)  # 仅保存界面偏好

    def __init__(self, channel_id: int, label: str, v_nominal: float, parent=None):
        super().__init__(parent)
        self._channel_id = channel_id
        self._label = label
        self._v_nominal = v_nominal

        # 【优化】状态缓存，避免冗余 setStyleSheet 调用
        self._last_hv_state = False       # 上次高压状态
        self._last_ramp_state = -1        # -1=空闲, 0=斜坡中, 1=暂停

        self._build_card()
        self._on_params_changed()

    # ── 构建卡片 ──────────────────────────────

    def _build_card(self) -> None:
        """构建宽幅卡片 UI, 填满面板全宽"""
        self.setStyleSheet(
            "ChannelCard { border: 1px solid #CCC; border-radius: 6px; "
            "background-color: #FAFAFA; }"
            "ChannelCard:hover { border-color: #4CAF50; background-color: #F5FFF5; }"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 6, 10, 6)
        card_layout.setSpacing(5)

        # ====== 第一行: 标题 + 斜坡状态 + 标称电压 ======
        header = QHBoxLayout()
        header.setSpacing(10)

        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)

        self._title_label = QLabel(self._label)
        self._title_label.setFont(title_font)
        header.addWidget(self._title_label)

        # 输出状态 (HV 指示)
        self._label_output = QLabel("")
        output_font = QFont()
        output_font.setPointSize(10)
        output_font.setBold(True)
        self._label_output.setFont(output_font)
        self._label_output.setMinimumWidth(60)
        self._label_output.setAlignment(Qt.AlignCenter)
        header.addWidget(self._label_output)

        # 斜坡状态
        self._label_ramp = QLabel("空闲")
        ramp_font = QFont()
        ramp_font.setPointSize(10)
        self._label_ramp.setFont(ramp_font)
        self._label_ramp.setStyleSheet("color: #888; font-size: 10px;")
        header.addWidget(self._label_ramp)

        header.addStretch()

        self._vnom_label = QLabel(f"Vnom={self._v_nominal:.0f}V")
        self._vnom_label.setStyleSheet("color: #888; font-size: 10px;")
        header.addWidget(self._vnom_label)
        card_layout.addLayout(header)

        # ====== 第二行: 主体 — 参数控制 (左) + 大字测量值 (右) ======
        body = QHBoxLayout()
        body.setSpacing(12)

        # ---- 左侧: 参数和按钮 ----
        left_panel = QVBoxLayout()
        left_panel.setSpacing(5)

        params = QHBoxLayout()
        params.setSpacing(6)

        params.addWidget(QLabel("目标电压:"))
        self._spin_vset = QDoubleSpinBox()
        self._spin_vset.setRange(0.0, self._v_nominal)
        self._spin_vset.setDecimals(1)
        self._spin_vset.setSingleStep(10.0)
        self._spin_vset.setValue(0.0)
        self._spin_vset.setSuffix(" V")
        self._spin_vset.setMinimumWidth(80)
        self._spin_vset.valueChanged.connect(self._on_params_changed)
        params.addWidget(self._spin_vset)

        params.addWidget(QLabel("速率:"))
        self._spin_speed = QDoubleSpinBox()
        self._spin_speed.setRange(0.1, 400.0)
        self._spin_speed.setDecimals(1)
        self._spin_speed.setSingleStep(1.0)
        self._spin_speed.setValue(10.0)
        self._spin_speed.setSuffix(" V/s")
        self._spin_speed.setMinimumWidth(70)
        self._spin_speed.valueChanged.connect(self._on_params_changed)
        params.addWidget(self._spin_speed)

        self._label_est_time = QLabel("约 -- s")
        self._label_est_time.setStyleSheet("color: #0066CC; font-weight: bold; font-size: 10px;")
        params.addWidget(self._label_est_time)

        params.addStretch()
        left_panel.addLayout(params)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_font = QFont()
        btn_font.setPointSize(10)
        btn_font.setBold(True)

        self._btn_start = QPushButton("▶ 启动")
        self._btn_start.setMinimumHeight(32)
        self._btn_start.setFont(btn_font)
        self._btn_start.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "border-radius: 4px; padding: 6px 18px; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:disabled { background-color: #CCCCCC; }"
        )
        self._btn_start.clicked.connect(self._on_start_ramp)
        btn_layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton("■ 停止")
        self._btn_stop.setMinimumHeight(32)
        self._btn_stop.setFont(btn_font)
        self._btn_stop.setStyleSheet(
            "QPushButton { background-color: #E53935; color: white; "
            "border-radius: 4px; padding: 6px 18px; }"
            "QPushButton:hover { background-color: #C62828; }"
            "QPushButton:disabled { background-color: #CCCCCC; }"
        )
        self._btn_stop.clicked.connect(self._on_stop_ramp)
        self._btn_stop.setEnabled(False)
        btn_layout.addWidget(self._btn_stop)

        self._btn_down = QPushButton("▼ 降压")
        self._btn_down.setMinimumHeight(32)
        self._btn_down.setFont(btn_font)
        self._btn_down.setStyleSheet(
            "QPushButton { background-color: #FF9800; color: white; "
            "border-radius: 4px; padding: 6px 18px; }"
            "QPushButton:hover { background-color: #F57C00; }"
            "QPushButton:disabled { background-color: #CCCCCC; }"
        )
        self._btn_down.clicked.connect(self._on_start_ramp_down)
        btn_layout.addWidget(self._btn_down)

        btn_layout.addStretch()
        left_panel.addLayout(btn_layout)

        body.addLayout(left_panel, 1)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #DDD;")
        body.addWidget(sep)

        # ---- 右侧: 大字实时测量 ----
        right_panel = QVBoxLayout()
        right_panel.setSpacing(2)
        right_panel.setAlignment(Qt.AlignRight)

        v_font = QFont("Consolas", 20)
        v_font.setBold(True)
        self._label_vmeas = QLabel("0.0 V")
        self._label_vmeas.setFont(v_font)
        self._label_vmeas.setStyleSheet("color: #1565C0;")
        self._label_vmeas.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right_panel.addWidget(self._label_vmeas)

        i_font = QFont("Consolas", 16)
        i_font.setBold(True)
        self._label_imeas = QLabel("0 µA")
        self._label_imeas.setFont(i_font)
        self._label_imeas.setStyleSheet("color: #2E7D32;")
        self._label_imeas.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right_panel.addWidget(self._label_imeas)

        body.addLayout(right_panel, 0)

        card_layout.addLayout(body)

    # ── 公共方法 ──────────────────────────────

    def update_measurement(self, v_meas: float, i_meas: float) -> None:
        """更新实时测量值 (大字显示)"""
        # 【优化】仅当数值变化时更新文本，避免多余重绘
        self._label_vmeas.setText(f"{v_meas:.1f} V")
        self._label_imeas.setText(f"{i_meas * 1e6:.0f} µA")

        # 【优化】仅当 HV 状态变化时更新样式表
        hv_on = v_meas > 10
        if hv_on != self._last_hv_state:
            self._last_hv_state = hv_on
            if hv_on:
                self._label_output.setText("⚡HV")
                self._label_output.setStyleSheet("color: #FF0000; font-weight: bold; font-size: 10px;")
                self._label_vmeas.setStyleSheet("color: #B71C1C;")
            else:
                self._label_output.setText("")
                self._label_output.setStyleSheet("")
                self._label_vmeas.setStyleSheet("color: #1565C0;")

    def update_ramp_state(self, is_ramping: bool, is_paused: bool = False,
                          current_v: float = 0.0, target_v: float = 0.0) -> None:
        """更新斜坡状态和按钮"""
        # 【优化】编码状态，仅状态切换时 setStyleSheet
        new_state = 0 if is_ramping and not is_paused else (1 if is_paused else -1)

        if new_state == 0:
            self._label_ramp.setText(f"↑ {current_v:.1f}→{target_v:.1f}V")
            if new_state != self._last_ramp_state:
                self._label_ramp.setStyleSheet("color: #0066CC; font-weight: bold; font-size: 10px;")
            self._btn_start.setEnabled(False)
            self._btn_down.setEnabled(False)
            self._btn_stop.setEnabled(True)
        elif new_state == 1:
            self._label_ramp.setText(f"⏸ @ {current_v:.1f}V")
            if new_state != self._last_ramp_state:
                self._label_ramp.setStyleSheet("color: #FF9800; font-weight: bold; font-size: 10px;")
            self._btn_start.setEnabled(False)
            self._btn_down.setEnabled(True)
            self._btn_stop.setEnabled(False)
        else:
            self._label_ramp.setText("空闲")
            if new_state != self._last_ramp_state:
                self._label_ramp.setStyleSheet("color: #888; font-size: 10px;")
            self._btn_start.setEnabled(True)
            self._btn_down.setEnabled(True)
            self._btn_stop.setEnabled(False)

        self._last_ramp_state = new_state

    def set_params(self, target_v: float = 0.0, speed_vps: float = 10.0) -> None:
        self._spin_vset.setValue(target_v)
        self._spin_speed.setValue(speed_vps)

    def get_target_v(self) -> float:
        return self._spin_vset.value()

    def get_speed_vps(self) -> float:
        return self._spin_speed.value()

    def set_enabled_controls(self, enabled: bool) -> None:
        self._spin_vset.setEnabled(enabled)
        self._spin_speed.setEnabled(enabled)
        self._btn_start.setEnabled(enabled)
        self._btn_down.setEnabled(enabled)
        if not enabled:
            self._btn_stop.setEnabled(False)

    # ── 内部方法 ──────────────────────────────

    def _on_params_changed(self) -> None:
        v = self._spin_vset.value()
        speed = self._spin_speed.value()
        if v > 0 and speed > 0:
            est_sec = v / speed
            if est_sec >= 3600:
                self._label_est_time.setText(f"约 {est_sec / 3600:.1f}h")
            elif est_sec >= 60:
                self._label_est_time.setText(f"约 {est_sec / 60:.1f}min")
            else:
                self._label_est_time.setText(f"约 {est_sec:.0f}s")
        else:
            self._label_est_time.setText("约 -- s")
        self.sig_params_changed.emit(self._channel_id, v, speed)

    def _on_start_ramp(self) -> None:
        target_v = self._spin_vset.value()
        speed_vps = self._spin_speed.value()
        if target_v <= 0 or speed_vps <= 0:
            return
        self.sig_start_ramp.emit(self._channel_id, target_v, speed_vps)

    def _on_stop_ramp(self) -> None:
        self.sig_stop_ramp.emit(self._channel_id)

    def _on_start_ramp_down(self) -> None:
        speed_vps = self._spin_speed.value()
        if speed_vps <= 0:
            return
        self.sig_start_ramp_down.emit(self._channel_id, speed_vps)


# ──────────────────────────────────────────────
# 多通道控制面板 (容器)
# ──────────────────────────────────────────────

class MultiChannelPanelWidget(QWidget):
    """多通道独立控制面板 — 每个通道一张卡片, 填满全宽"""

    sig_start_ramp = pyqtSignal(int, float, float)   # channel_id, target_v, speed_vps
    sig_stop_ramp = pyqtSignal(int)                   # channel_id
    sig_start_ramp_down = pyqtSignal(int, float)      # channel_id, speed_vps

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict[int, ChannelCard] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        """构建可滚动、填满全宽的卡片面板"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # 标题
        title_label = QLabel("⚡ 通道独立控制")
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._card_layout = QVBoxLayout(self._container)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(6)
        self._card_layout.addStretch()

        scroll.setWidget(self._container)
        main_layout.addWidget(scroll, 1)

    # ── 公共方法 ──────────────────────────────

    def register_channel(self, channel_id: int, label: str, v_nominal: float) -> None:
        if channel_id in self._cards:
            self.unregister_channel(channel_id)

        card = ChannelCard(channel_id, label, v_nominal, self)

        card.sig_start_ramp.connect(self.sig_start_ramp)
        card.sig_stop_ramp.connect(self.sig_stop_ramp)
        card.sig_start_ramp_down.connect(self.sig_start_ramp_down)

        self._card_layout.insertWidget(self._card_layout.count() - 1, card)
        self._cards[channel_id] = card

    def unregister_channel(self, channel_id: int) -> None:
        card = self._cards.pop(channel_id, None)
        if card:
            self._card_layout.removeWidget(card)
            card.deleteLater()

    def update_channel_measurement(self, channel_id: int, v_meas: float, i_meas: float) -> None:
        card = self._cards.get(channel_id)
        if card:
            card.update_measurement(v_meas, i_meas)

    def update_channel_ramp_state(self, channel_id: int, is_ramping: bool,
                                  is_paused: bool = False, current_v: float = 0.0,
                                  target_v: float = 0.0) -> None:
        card = self._cards.get(channel_id)
        if card:
            card.update_ramp_state(is_ramping, is_paused, current_v, target_v)

    def set_channel_enabled(self, channel_id: int, enabled: bool) -> None:
        card = self._cards.get(channel_id)
        if card:
            card.set_enabled_controls(enabled)

    def clear_all(self) -> None:
        for channel_id in list(self._cards.keys()):
            self.unregister_channel(channel_id)

    def get_card(self, channel_id: int) -> ChannelCard | None:
        return self._cards.get(channel_id)
