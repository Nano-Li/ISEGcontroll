"""
实时图表组件 — 电压/电流实时显示

使用 pyqtgraph 实现:
  - 电压曲线 (蓝色)
  - 设定电压曲线 (红色虚线)
  - 电流曲线 (绿色, 右侧Y轴)
  - 支持缩放/平移/鼠标悬停
"""

import time
import numpy as np
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QGroupBox, QCheckBox)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
import pyqtgraph as pg


class MonitorChartWidget(QWidget):
    """实时监测图表组件"""

    sig_channel_selected = pyqtSignal(int)  # channel_id, 用于图表通道切换

    # 图表线颜色
    COLOR_VOLTAGE = (50, 150, 255)     # 蓝色 — 实测电压
    COLOR_VSET = (255, 100, 100)       # 红色 — 设定电压
    COLOR_CURRENT = (50, 255, 150)     # 绿色 — 实测电流

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channel_labels: dict[int, str] = {}
        self._data_buffers: dict[int, "DataBuffer"] = {}
        self._plot_curves: dict[int, dict] = {}  # {channel_id: {v, vset, i}}
        self._selected_channel: int = -1
        self._show_vset = True
        self._show_current = True
        self._chart_enabled = True

        self._build_ui()

    def _build_ui(self) -> None:
        """构建图表 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ====== 标题栏 ======
        title_layout = QHBoxLayout()

        title_label = QLabel("📊 实时监测")
        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_layout.addWidget(title_label)

        title_layout.addStretch()

        # 通道选择器
        self._combo_channel = QComboBox()
        self._combo_channel.setMinimumWidth(150)
        self._combo_channel.currentIndexChanged.connect(self._on_channel_changed)
        title_layout.addWidget(QLabel("显示通道:"))
        title_layout.addWidget(self._combo_channel)

        # 显示选项
        self._check_vset = QCheckBox("设定电压")
        self._check_vset.setChecked(True)
        title_layout.addWidget(self._check_vset)

        self._check_current = QCheckBox("电流")
        self._check_current.setChecked(True)
        title_layout.addWidget(self._check_current)

        self._check_enable_chart = QCheckBox("实时图表")
        self._check_enable_chart.setChecked(True)
        self._check_enable_chart.toggled.connect(self._on_chart_toggle)
        title_layout.addWidget(self._check_enable_chart)

        layout.addLayout(title_layout)

        # ====== pyqtgraph 图表 ======
        self._graph_widget = pg.GraphicsLayoutWidget()

        # 电压图表 (左侧Y轴)
        self._voltage_plot = self._graph_widget.addPlot(row=0, col=0, title="电压 (V)")
        self._voltage_plot.setLabel("left", "电压", units="V")
        self._voltage_plot.setLabel("bottom", "时间", units="s")
        self._voltage_plot.showGrid(x=True, y=True, alpha=0.3)
        self._voltage_plot.addLegend(offset=(-10, 10))

        # 电压曲线
        self._curve_voltage = self._voltage_plot.plot(
            [], [], pen=pg.mkPen(color=self.COLOR_VOLTAGE, width=2),
            name="实测电压"
        )
        # 设定电压曲线 (虚线)
        self._curve_vset = self._voltage_plot.plot(
            [], [], pen=pg.mkPen(color=self.COLOR_VSET, width=1.5, style=Qt.DashLine),
            name="设定电压"
        )

        # 电流图表 (右侧Y轴, 与电压共享X轴)
        self._current_plot = pg.ViewBox()
        self._voltage_plot.scene().addItem(self._current_plot)
        self._voltage_plot.showAxis("right")
        self._voltage_plot.getAxis("right").setLabel("电流", units="A")
        self._voltage_plot.getAxis("right").linkToView(self._current_plot)
        self._current_plot.setXLink(self._voltage_plot)

        # 电流曲线
        self._curve_current = pg.PlotCurveItem(
            pen=pg.mkPen(color=self.COLOR_CURRENT, width=2)
        )
        self._current_plot.addItem(self._curve_current)

        # 同步视图范围
        def update_views():
            self._current_plot.setGeometry(self._voltage_plot.vb.sceneBoundingRect())
        self._voltage_plot.vb.sigResized.connect(update_views)
        update_views()

        layout.addWidget(self._graph_widget, 1)

        # ====== 底部状态行 ======
        status_layout = QHBoxLayout()
        self._label_latest = QLabel("最新: V=---, I=---")
        self._label_latest.setStyleSheet("color: #666; font-size: 11px;")
        status_layout.addWidget(self._label_latest)
        status_layout.addStretch()
        layout.addLayout(status_layout)

    # ========== 公共方法 ==========

    def register_channel(self, channel_id: int, label: str, data_buffer: "DataBuffer") -> None:
        """注册一个通道用于图表显示"""
        self._channel_labels[channel_id] = label
        self._data_buffers[channel_id] = data_buffer
        self._combo_channel.addItem(label, channel_id)

        if self._selected_channel < 0:
            self._selected_channel = channel_id
            idx = self._combo_channel.findData(channel_id)
            if idx >= 0:
                self._combo_channel.setCurrentIndex(idx)

    def unregister_channel(self, channel_id: int) -> None:
        """注销通道"""
        self._channel_labels.pop(channel_id, None)
        self._data_buffers.pop(channel_id, None)
        idx = self._combo_channel.findData(channel_id)
        if idx >= 0:
            self._combo_channel.removeItem(idx)
        if self._selected_channel == channel_id:
            if self._combo_channel.count() > 0:
                self._selected_channel = self._combo_channel.itemData(0)
                self._combo_channel.setCurrentIndex(0)
            else:
                self._selected_channel = -1

    def refresh_chart(self) -> None:
        """刷新图表数据"""
        if not self._chart_enabled:
            return
        if self._selected_channel < 0:
            return

        buf = self._data_buffers.get(self._selected_channel)
        if buf is None or buf.length == 0:
            return

        timestamps, voltages, currents, v_sets = buf.get_data()
        if not timestamps:
            return

        # 转换为相对时间 (秒)
        t0 = timestamps[0]
        rel_time = [t - t0 for t in timestamps]

        # 更新电压曲线
        self._curve_voltage.setData(rel_time, voltages)

        # 更新设定电压曲线
        if self._check_vset.isChecked():
            self._curve_vset.setData(rel_time, v_sets)
        else:
            self._curve_vset.setData([], [])

        # 更新电流曲线
        if self._check_current.isChecked():
            self._curve_current.setData(rel_time, currents)
        else:
            self._curve_current.setData([], [])

        # 更新最新值
        if voltages and currents:
            self._label_latest.setText(
                f"最新: V={voltages[-1]:.1f}V, I={currents[-1]*1e6:.2f}µA"
            )

        # 自动调整 Y 轴范围
        self._voltage_plot.autoRange()

    def clear_all(self) -> None:
        """清空所有图表"""
        self._curve_voltage.setData([], [])
        self._curve_vset.setData([], [])
        self._curve_current.setData([], [])
        self._combo_channel.clear()
        self._channel_labels.clear()
        self._data_buffers.clear()
        self._selected_channel = -1

    # ========== 内部方法 ==========

    def _on_chart_toggle(self, enabled: bool) -> None:
        """切换实时图表显示"""
        self._chart_enabled = enabled
        self._graph_widget.setVisible(enabled)

    def _on_channel_changed(self, index: int) -> None:
        """切换显示的通道"""
        if index < 0:
            return
        channel_id = self._combo_channel.itemData(index)
        if channel_id is None:
            return
        self._selected_channel = channel_id

        # 更新图表标题
        label = self._channel_labels.get(channel_id, f"通道 {channel_id}")
        self._voltage_plot.setTitle(f"电压 (V) — {label}")

        self.refresh_chart()
