"""
ISEG THQ 高压电源控制器 — 主窗口 (Win11 异步优化版 v2.0)

=== 核心优化 ===
1. 异步串口: 所有串口 I/O 通过 AsyncSerialManager 移至后台线程
   - _send_voltage_command() 异步执行，通过回调通知完成
   - _refresh_measurements() 异步执行，结果通过队列在主线程消费
2. 页面渲染优化:
   - 测量结果消费定时器 (50ms) 批量更新 UI
   - 斜坡进度更新合并至同一帧
   - 减少不必要的 setStyleSheet 调用
3. REFRESH_INTERVAL: 300ms → 1000ms (串口负载降低 3 倍)
4. 移除额外的 D{n} 查询 (信赖本地缓存)
5. 日志写入: 独立定时器每 2s 批量 flush，替代每次刷新写入
"""

import os
import sys
import time
import threading
from datetime import datetime
from typing import Optional

from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QSplitter, QStatusBar, QAction, QMenuBar,
                             QMessageBox, QLabel, QApplication,
                             QFileDialog, QInputDialog)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal

from protocol.thq_protocol import THQProtocol
from serial_comm.port_manager import PortManager
from serial_comm.async_worker import AsyncSerialManager, CommandPacket, CmdType
from core.device_manager import DeviceManager
from core.ramp_controller import RampController
from core.settings import LocalSettings
from data.data_buffer import DataBuffer
from data.csv_exporter import CsvExporter
from ui.device_tree import DeviceTreeWidget
from ui.control_panel import MultiChannelPanelWidget
from ui.dialogs import ReconnectDialog, show_confirm_dialog


class MainWindow(QMainWindow):
    """ISEG THQ 电源控制器主窗口 (Win11 异步优化版)"""

    sig_device_disconnected = pyqtSignal(str)
    sig_device_reconnected = pyqtSignal(str, list)

    # 优化：300ms → 1000ms，降低串口负载
    REFRESH_INTERVAL = 1000     # 测量刷新间隔 (毫秒)
    WATCHDOG_INTERVAL = 2000    # 看门狗检测间隔 (毫秒)
    LOG_FLUSH_INTERVAL = 2000   # 日志刷新间隔 (毫秒)
    UI_CONSUME_INTERVAL = 50    # UI 结果消费间隔 (毫秒) — 新增
    RECONNECT_COOLDOWN = 30.0   # 续连弹窗冷却时间 (秒)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ISEG THQ 高压电源控制器 v2.0 (异步优化)")
        self.resize(1400, 900)
        self.settings = LocalSettings()

        # ====== 核心模块 ======
        # 使用 AsyncSerialManager 替代 PortManager
        self.async_serial = AsyncSerialManager(parent=self)
        self.port_manager = self.async_serial.port_manager
        self.device_manager = DeviceManager()
        self.ramp_controller = RampController(self._send_voltage_command, self)

        # 数据记录
        self._data_buffers: dict[int, DataBuffer] = {}
        self._last_dialog_time: dict[str, float] = {}

        # 自动数据记录 (.dat 文件)
        self._log_dir: str = ""
        self._auto_log_file: str = ""
        self._auto_log_fh = None

        # 日志智能暂停：电压稳定+IDLE超5s则暂停，检测到变化时恢复
        self._last_logged_v: dict[int, float] = {}
        self._last_voltage_change: dict[int, float] = {}
        self._logging_paused = False
        self._log_pause_ts = 0.0

        # 连接状态
        self._port_connected: dict[str, bool] = {}

        # 异步测量结果队列
        self._meas_queue: list[tuple] = []
        self._meas_queue_lock = threading.Lock()

        # 构建 UI
        self._build_ui()
        self._build_menu()
        self._connect_signals()

        # ====== 定时器配置 ======
        # 测量刷新 — 只负责提交查询，不阻塞
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh_measurements)
        self._refresh_timer.start(self.REFRESH_INTERVAL)

        # UI 消费定时器 — 批量处理异步结果
        self._consume_timer = QTimer()
        self._consume_timer.timeout.connect(self._consume_meas_results)
        self._consume_timer.start(self.UI_CONSUME_INTERVAL)

        self._watchdog_timer = QTimer()
        self._watchdog_timer.timeout.connect(self._watchdog_check)
        self._watchdog_timer.start(self.WATCHDOG_INTERVAL)

        # 日志定时器 — 每 2s 批量写入，替代逐次写入
        self._log_timer = QTimer()
        self._log_timer.timeout.connect(self._log_data)
        self._log_timer.start(self.LOG_FLUSH_INTERVAL)

        # 启动异步串口工作线程
        self.async_serial.start()

        # 初始端口扫描
        self._setup_auto_log()
        self._scan_ports()
        self._update_status_bar()

        if self.settings.last_error:
            self.statusBar().showMessage(self.settings.last_error, 10000)

    # ========== UI 构建 ==========

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        h_splitter = QSplitter(Qt.Horizontal)
        self.device_tree = DeviceTreeWidget(self)
        h_splitter.addWidget(self.device_tree)
        self.multi_panel = MultiChannelPanelWidget(self)
        h_splitter.addWidget(self.multi_panel)
        h_splitter.setStretchFactor(0, 2)
        h_splitter.setStretchFactor(1, 8)
        main_layout.addWidget(h_splitter)

        self._status_label = QLabel("就绪")
        self.statusBar().addWidget(self._status_label, 1)
        self._alert_label = QLabel("")
        self._alert_label.setStyleSheet(
            "color: #FF0000; font-weight: bold; font-size: 12px; "
            "background-color: #FFF0F0; padding: 4px 8px; border-radius: 3px;"
        )
        self._alert_label.setVisible(False)
        self.statusBar().addPermanentWidget(self._alert_label)

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        export_csv = QAction("导出数据为 CSV", self)
        export_csv.triggered.connect(self._export_csv)
        file_menu.addAction(export_csv)
        export_xlsx = QAction("导出数据为 Excel", self)
        export_xlsx.triggered.connect(self._export_xlsx)
        file_menu.addAction(export_xlsx)
        file_menu.addSeparator()
        exit_action = QAction("退出(&X)", self)
        exit_action.triggered.connect(self._safe_exit)
        file_menu.addAction(exit_action)

        tools_menu = menubar.addMenu("工具(&T)")
        scan_action = QAction("重新扫描端口", self)
        scan_action.triggered.connect(self._scan_ports)
        tools_menu.addAction(scan_action)
        diag_action = QAction("串口诊断", self)
        diag_action.triggered.connect(self._run_diagnostic)
        tools_menu.addAction(diag_action)
        tools_menu.addSeparator()
        set_path_action = QAction("设置日志保存路径...", self)
        set_path_action.triggered.connect(self._set_log_path)
        tools_menu.addAction(set_path_action)
        log_action = QAction("打开数据日志目录", self)
        log_action.triggered.connect(self._open_log_dir)
        tools_menu.addAction(log_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _connect_signals(self) -> None:
        self.device_tree.sig_scan_ports.connect(self._scan_ports)
        self.device_tree.sig_port_selected.connect(self._remember_port)
        self.device_tree.sig_connect.connect(self._connect_device)
        self.device_tree.sig_disconnect.connect(self._disconnect_device)
        self.device_tree.sig_channel_selected.connect(self._on_channel_selected)
        self.device_tree.sig_group_channel.connect(self._on_group_channel)
        self.multi_panel.sig_start_ramp.connect(self._on_start_ramp)
        self.multi_panel.sig_start_ramp_down.connect(self._on_start_ramp_down)
        self.multi_panel.sig_stop_ramp.connect(self._on_stop_ramp)
        self.ramp_controller.ramp_progress.connect(self._on_ramp_progress)
        self.ramp_controller.ramp_completed.connect(self._on_ramp_completed)
        self.ramp_controller.ramp_paused.connect(self._on_ramp_paused)
        self.ramp_controller.ramp_error.connect(self._on_ramp_error)
        self.sig_device_disconnected.connect(self._on_device_disconnected)
        self.sig_device_reconnected.connect(self._on_device_reconnected)

    # ========== 串口管理 ==========

    def _scan_ports(self) -> None:
        ports = self.async_serial.scan_ports()
        self.device_tree.set_ports(ports, self.settings.preferred_port)
        self._status_label.setText(f"已扫描到 {len(ports)} 个COM端口")

    def _remember_port(self, port: str) -> None:
        if not self.settings.remember_port(port):
            self.statusBar().showMessage(self.settings.last_error, 10000)

    def _remember_channel_params(self, channel_id: int, target_v: float,
                                 speed_vps: float) -> None:
        ch = self.device_manager.get_channel(channel_id)
        if ch and not self.settings.remember_channel(
            ch.device_port, ch.channel_index, target_v, speed_vps
        ):
            self.statusBar().showMessage(self.settings.last_error, 10000)

    def _connect_device(self, port: str) -> None:
        """连接设备（同步 — 仅连接时执行一次）"""
        if self.async_serial.is_connected(port):
            return

        existing = self.device_manager.get_device(port)
        if existing:
            for ch in existing.channels:
                self.ramp_controller.stop_ramp(ch.channel_id)
                self._data_buffers.pop(ch.channel_id, None)
                self.multi_panel.unregister_channel(ch.channel_id)
            self.device_manager.remove_device(port)

        success = self.async_serial.connect_port(port)
        if not success:
            QMessageBox.warning(self, "连接失败", f"无法连接到 {port}")
            return

        # ====== 读取 CH1 设备信息 (#1) ======
        resp1 = self.async_serial.send_sync(port, THQProtocol.query_id(1))
        if resp1 is None or not resp1.strip():
            time.sleep(0.1)
            resp1 = self.async_serial.send_sync(port, THQProtocol.query_id(1))

        # ====== 读取 CH2 设备信息 (#2) ======
        time.sleep(0.05)
        resp2 = self.async_serial.send_sync(port, THQProtocol.query_id(2))

        if resp1 and ";" in resp1:
            info1 = THQProtocol.parse_id(resp1)
            serial_num = info1["serial"]
            firmware = info1["firmware"]
            v_nominal = info1["v_nominal"]
            i_nominal_x10 = info1["i_nominal_x10"]

            # 解析 CH2 独立参数
            ch1_vnom = v_nominal
            ch1_inom = i_nominal_x10
            ch2_vnom = v_nominal
            ch2_inom = i_nominal_x10
            if resp2 and ";" in resp2:
                info2 = THQProtocol.parse_id(resp2)
                if info2["v_nominal"] > 0:
                    ch2_vnom = info2["v_nominal"]
                if info2["i_nominal_x10"] > 0:
                    ch2_inom = info2["i_nominal_x10"]

            device = self.device_manager.add_device(
                port=port, serial=serial_num, firmware=firmware,
                v_nominal=v_nominal, i_nominal_x10=i_nominal_x10,
                ch1_v_nominal=ch1_vnom, ch1_i_nominal_x10=ch1_inom,
                ch2_v_nominal=ch2_vnom, ch2_i_nominal_x10=ch2_inom,
            )
            if device is None:
                QMessageBox.warning(self, "错误", "已达到最大设备数 (6台)")
                self.async_serial.disconnect_port(port)
                return

            # 初始化配置（同步）
            self.async_serial.send_sync(port, THQProtocol.set_echo_mode(1, 1))
            time.sleep(0.05)
            self.async_serial.send_sync(port, THQProtocol.set_kill(1, False))
            time.sleep(0.05)
            self.async_serial.send_sync(port, THQProtocol.set_kill(2, False))
            time.sleep(0.05)

            for ch in device.channels:
                buf = DataBuffer()
                self._data_buffers[ch.channel_id] = buf
                label = f"{device.name} CH{ch.channel_index}"
                self.multi_panel.register_channel(ch.channel_id, label, ch.v_nominal)
                card = self.multi_panel.get_card(ch.channel_id)
                card.set_params(*self.settings.channel_params(
                    port, ch.channel_index, ch.v_nominal))
                # 恢复完成后再监听，避免初始化覆盖文件中的偏好。
                card.sig_params_changed.connect(self._remember_channel_params)

                # ⚠ 安全修复：重连后立即读取硬件真实电压/电流
                v_resp = self.async_serial.send_sync(
                    port, THQProtocol.query_voltage(ch.channel_index))
                if v_resp is not None:
                    v = THQProtocol.parse_float(v_resp)
                    if 0 <= v <= ch.v_nominal:
                        ch.v_measure = v
                        ch.v_set = v
                        ch.output_on = v > 10

                i_resp = self.async_serial.send_sync(
                    port, THQProtocol.query_current(ch.channel_index))
                if i_resp is not None:
                    i = THQProtocol.parse_current(i_resp)
                    if 0 <= i <= 0.1:
                        ch.i_measure = i

                s_resp = self.async_serial.send_sync(
                    port, THQProtocol.query_status(ch.channel_index))
                if s_resp:
                    status = THQProtocol.parse_status(s_resp)
                    ch.output_on = status.get("output_on", ch.output_on)

            # 更新控制面板
            for ch in device.channels:
                self.multi_panel.update_channel_measurement(
                    ch.channel_id, ch.v_measure, ch.i_measure)

            self._port_connected[port] = True
            self.device_tree.refresh_tree(self.device_manager.get_all_devices())
            self.device_tree.set_connection_state(port, True)
            self._update_status_bar()

            # 安全警告：检测到高压
            high_voltage_channels = [ch for ch in device.channels if ch.v_measure > 50]
            if high_voltage_channels:
                ch_list = ", ".join(
                    f"CH{c.channel_index}={c.v_measure:.0f}V"
                    for c in high_voltage_channels)
                QMessageBox.warning(
                    self, "⚠ 设备已带高压",
                    f"检测到设备 {device.name} 当前正处于高压输出状态：\n\n"
                    f"  {ch_list}\n\n"
                    "这是断开前设置的电压，设备一直在持续输出。\n\n"
                    "操作前请确认：\n"
                    "  1. 实验区域安全\n"
                    "  2. 软件显示的电压值与硬件前面板一致\n"
                    "  3. 如需安全降压，请使用控制面板的 ▼降压 按钮")
        else:
            QMessageBox.warning(self, "设备识别失败",
                               f"已连接至 {port} 但无法识别为 THQ 设备。响应: '{resp1}'")
            self.async_serial.disconnect_port(port)

    def _disconnect_device(self, port: str) -> None:
        if not self.async_serial.is_connected(port):
            return
        device = self.device_manager.get_device(port)
        if device:
            for ch in device.channels:
                self.ramp_controller.stop_ramp(ch.channel_id)
        self.async_serial.disconnect_port(port)
        if device:
            for ch in device.channels:
                self._data_buffers.pop(ch.channel_id, None)
                self.multi_panel.unregister_channel(ch.channel_id)
        self.device_manager.remove_device(port)
        self._port_connected.pop(port, None)
        self.device_tree.refresh_tree(self.device_manager.get_all_devices())
        self.device_tree.set_connection_state(port, False)
        self._update_status_bar()

    def _on_channel_selected(self, channel_id: int, label: str, v_nominal: float) -> None:
        pass

    def _on_group_channel(self, channel_id: int, group_name: str) -> None:
        self.device_manager.set_channel_group(channel_id, group_name)
        self.device_tree.refresh_tree(self.device_manager.get_all_devices())

    # ========== 斜坡控制（异步发送） ==========

    def _send_voltage_command(self, port: str, channel_index: int, voltage: float,
                              on_complete=None) -> Optional[bool]:
        """
        异步发送电压命令

        参数:
          on_complete: 完成回调 on_complete(success)
          如果 on_complete 为 None，同步执行并返回 bool（向后兼容）
        """
        if on_complete is None:
            cmd = THQProtocol.set_voltage(channel_index, voltage)
            resp = self.port_manager.send_command(port, cmd)
            return resp is not None

        # 异步模式 — 后台线程发送，回调通知完成
        self.async_serial.write_voltage_async(
            port, channel_index, voltage,
            callback=lambda ok, resp: on_complete(ok))

    def _on_start_ramp(self, channel_id: int, target_v: float, speed_vps: float) -> None:
        ch = self.device_manager.get_channel(channel_id)
        if ch is None:
            return
        device = self.device_manager.get_device(ch.device_port)
        if device is None or not device.connected:
            QMessageBox.warning(self, "错误", "设备未连接")
            return

        current_v = ch.v_measure
        delta_v = abs(target_v - current_v)
        if speed_vps <= 0:
            QMessageBox.warning(self, "参数错误", "变化速率必须大于 0")
            return
        ramp_time = delta_v / speed_vps

        direction = "升压" if target_v > current_v else "降压"
        confirmed = show_confirm_dialog(
            self, "确认操作",
            f"即将{direction}:\n"
            f"  通道: {device.name} CH{ch.channel_index}\n"
            f"  目标电压: {target_v:.1f} V\n"
            f"  当前电压: {current_v:.1f} V\n"
            f"  变化速率: {speed_vps:.1f} V/s\n"
            f"  预计耗时: {ramp_time:.1f} 秒\n\n"
            f"请确保实验区域安全！",
            "确认后电压将平滑变化，过程中可随时点击'停止升降压'暂停。")
        if not confirmed:
            return

        # 【修复】残留任务清理
        if self.ramp_controller.has_task(channel_id):
            self.ramp_controller.force_stop(channel_id)

        success = self.ramp_controller.start_ramp(
            channel_id, target_v, ramp_time,
            ch.device_port, ch.channel_index, current_v)
        if success:
            ch.is_ramping = True
            ch.v_target = target_v
            ch.ramp_time = ramp_time
            ch.ramp_direction = 1 if target_v > current_v else -1
            self._status_label.setText(
                f"{direction}中: CH{ch.channel_index} {current_v:.1f}V → {target_v:.1f}V")
            self.multi_panel.update_channel_ramp_state(
                channel_id, True, False, current_v, target_v)
        else:
            QMessageBox.warning(self, "启动失败",
                f"通道{device.name} CH{ch.channel_index} 启动升降压失败，请检查设备连接后重试。")

    def _on_start_ramp_down(self, channel_id: int, speed_vps: float) -> None:
        ch = self.device_manager.get_channel(channel_id)
        if ch is None:
            return
        device = self.device_manager.get_device(ch.device_port)
        if device is None or not device.connected:
            QMessageBox.warning(self, "错误", "设备未连接")
            return
        current_v = ch.v_measure
        if current_v < 1.0:
            QMessageBox.information(self, "提示", "当前电压已经接近0V，无需降压。")
            return
        if speed_vps <= 0:
            QMessageBox.warning(self, "参数错误", "变化速率必须大于 0")
            return
        ramp_time = current_v / speed_vps
        confirmed = show_confirm_dialog(
            self, "确认降压",
            f"即将安全降压至 0V:\n"
            f"  通道: {device.name} CH{ch.channel_index}\n"
            f"  当前电压: {current_v:.1f} V\n"
            f"  变化速率: {speed_vps:.1f} V/s\n"
            f"  预计耗时: {ramp_time:.1f} 秒\n\n"
            f"请确认安全区域无人员！",
            "降压过程中可随时点击'停止升降压'暂停。")
        if not confirmed:
            return
        success = self.ramp_controller.start_ramp_down(
            channel_id, ramp_time, ch.device_port, ch.channel_index, current_v)
        if success:
            ch.is_ramping = True
            ch.v_target = 0.0
            ch.ramp_time = ramp_time
            ch.ramp_direction = -1
            self._status_label.setText(
                f"降压中: CH{ch.channel_index} {current_v:.1f}V → 0V")
            self.multi_panel.update_channel_ramp_state(
                channel_id, True, False, current_v, 0.0)

    def _on_stop_ramp(self, channel_id: int) -> None:
        current_v = self.ramp_controller.stop_ramp(channel_id)
        ch = self.device_manager.get_channel(channel_id)
        if ch:
            ch.is_ramping = False
            ch.ramp_direction = 0
        if current_v is not None:
            self._status_label.setText(f"升降压已停止 @ {current_v:.1f}V")
        self.multi_panel.update_channel_ramp_state(channel_id, False)

    def _on_ramp_progress(self, channel_id: int, current_v: float, target_v: float) -> None:
        """斜坡进度 — 直接更新 UI（主线程信号）"""
        ch = self.device_manager.get_channel(channel_id)
        if ch:
            ch.v_measure = current_v
            ch.v_set = current_v
        self.device_tree.refresh_values(self.device_manager.get_all_devices())
        if ch:
            self.multi_panel.update_channel_measurement(channel_id, current_v, ch.i_measure)
            self.multi_panel.update_channel_ramp_state(
                channel_id, True, False, current_v, target_v)

    def _on_ramp_completed(self, channel_id: int, final_v: float) -> None:
        ch = self.device_manager.get_channel(channel_id)
        if ch:
            ch.is_ramping = False
            ch.ramp_direction = 0
            ch.v_set = final_v
        self._status_label.setText(f"斜坡完成 @ {final_v:.1f}V")
        self.multi_panel.update_channel_ramp_state(channel_id, False)

    def _on_ramp_paused(self, channel_id: int, paused_v: float) -> None:
        ch = self.device_manager.get_channel(channel_id)
        if ch:
            ch.is_ramping = False
            ch.ramp_direction = 0
        self.multi_panel.update_channel_ramp_state(channel_id, False)

    def _on_ramp_error(self, channel_id: int, error_msg: str) -> None:
        ch = self.device_manager.get_channel(channel_id)
        if ch:
            ch.is_ramping = False
            ch.ramp_direction = 0
        self._show_alert(f"斜坡错误: {error_msg}")
        self.multi_panel.update_channel_ramp_state(channel_id, False)
        # 【修复】确保 ramp 任务被彻底清理
        if self.ramp_controller.has_task(channel_id):
            self.ramp_controller.force_stop(channel_id)

    # ========== 实时测量（异步） ==========

    def _refresh_measurements(self) -> None:
        """异步刷新测量值 — 所有查询提交到后台线程，主线程不阻塞"""
        devices = self.device_manager.get_all_devices()
        if not devices:
            return

        for device in devices:
            if not device.connected:
                continue
            for ch in device.channels:
                if not ch.connected:
                    continue

                ch_id = ch.channel_id
                ch_idx = ch.channel_index
                port = device.port

                # 电压查询：斜坡中的通道由 _on_ramp_progress 更新，跳过
                if not ch.is_ramping:
                    cmd_u = THQProtocol.query_voltage(ch_idx)
                    pkt_u = CommandPacket(
                        CmdType.QUERY, port, cmd_u,
                        callback=lambda ok, resp, cid=ch_id:
                            self._enqueue_meas_result(cid, ok, resp, True),
                        channel_id=ch_id)
                    self.async_serial._worker.enqueue(pkt_u)

                # 【修复】斜坡期间仍查询电流，保证电流显示实时刷新
                cmd_i = THQProtocol.query_current(ch_idx)
                pkt_i = CommandPacket(
                    CmdType.QUERY, port, cmd_i,
                    callback=lambda ok, resp, cid=ch_id:
                        self._enqueue_meas_result(cid, ok, resp, False),
                    channel_id=ch_id)
                self.async_serial._worker.enqueue(pkt_i)

    def _enqueue_meas_result(self, channel_id: int, ok: bool, response: Optional[str],
                              is_voltage: bool) -> None:
        """将测量结果加入队列（在后台线程执行）"""
        with self._meas_queue_lock:
            self._meas_queue.append((channel_id, ok, response, is_voltage))

    def _consume_meas_results(self) -> None:
        """消费测量结果队列（主线程，50ms 定时器触发）"""
        items: list[tuple] = []
        with self._meas_queue_lock:
            if not self._meas_queue:
                return
            items = self._meas_queue[:]
            self._meas_queue.clear()

        if not items:
            return

        updated_channels = set()
        for channel_id, ok, response, is_voltage in items:
            if not ok or not response:
                continue
            ch = self.device_manager.get_channel(channel_id)
            if ch is None:
                continue
            if is_voltage:
                v = THQProtocol.parse_float(response)
                # 电压合理性校验：负值或大幅跳变视为无效
                if v < 0:
                    continue
                if ch.v_measure >= 50 and abs(v - ch.v_measure) > ch.v_nominal * 0.5:
                    continue
                ch.v_measure = v
                ch.v_set = v
                ch.output_on = v > 10
            else:
                i_raw = THQProtocol.parse_current(response)
                # 电流合理性校验
                if i_raw < 0 or i_raw > 0.1:  # 100mA 上限
                    continue
                ch.i_measure = i_raw
            updated_channels.add(channel_id)

        if not updated_channels:
            return

        # 批量更新 UI — 一次设备树 + 一次面板
        self.device_tree.refresh_values(self.device_manager.get_all_devices())

        for cid in updated_channels:
            ch = self.device_manager.get_channel(cid)
            if ch is None:
                continue
            buf = self._data_buffers.get(cid)
            if buf:
                buf.append(ch.v_measure, ch.i_measure, ch.v_set)
            self.multi_panel.update_channel_measurement(cid, ch.v_measure, ch.i_measure)

    # ========== 看门狗 ==========

    def _watchdog_check(self) -> None:
        for device in self.device_manager.get_all_devices():
            if not device.connected:
                continue
            port = device.port
            is_alive = self.async_serial.is_port_alive(port)
            if not is_alive and self._port_connected.get(port, False):
                self._port_connected[port] = False
                self.sig_device_disconnected.emit(port)
            elif is_alive and not self._port_connected.get(port, False):
                channel_states = []
                for ch in device.channels:
                    v_resp = self.async_serial.send_sync(
                        port, THQProtocol.query_voltage(ch.channel_index))
                    v_meas = THQProtocol.parse_float(v_resp) if v_resp else 0.0
                    vs_resp = self.async_serial.send_sync(
                        port, THQProtocol.query_set_voltage(ch.channel_index))
                    v_set = THQProtocol.parse_float(vs_resp) if vs_resp else 0.0
                    i_resp = self.async_serial.send_sync(
                        port, THQProtocol.query_current(ch.channel_index))
                    i_meas = THQProtocol.parse_current(i_resp) if i_resp else 0.0
                    channel_states.append({
                        "channel": ch.channel_index,
                        "v_measure": v_meas, "i_measure": i_meas,
                        "v_set": v_set, "output_on": v_meas > 10,
                    })
                self._port_connected[port] = True
                self.sig_device_reconnected.emit(port, channel_states)

    def _on_device_disconnected(self, port: str) -> None:
        self.device_manager.set_connected(port, False)
        self.device_tree.refresh_tree(self.device_manager.get_all_devices())
        self._show_alert(f"⚠ 设备 {port} 连接断开!")

    def _on_device_reconnected(self, port: str, channel_states: list) -> None:
        last_time = self._last_dialog_time.get(port, 0.0)
        if time.time() - last_time < self.RECONNECT_COOLDOWN:
            return
        self.device_manager.set_connected(port, True)
        self.device_tree.refresh_tree(self.device_manager.get_all_devices())
        device = self.device_manager.get_device(port)
        if device:
            for state in channel_states:
                ch = self.device_manager.get_channel_by_key(port, state["channel"])
                if ch:
                    ch.v_measure = state["v_measure"]
                    ch.i_measure = state["i_measure"]
                    ch.v_set = state["v_set"]
                    ch.output_on = state["output_on"]
        dialog = ReconnectDialog(port, channel_states, self)
        dialog.exec_()
        result = dialog.get_result()
        self._last_dialog_time[port] = time.time()
        if result == "shutdown" and device:
            for ch in device.channels:
                if ch.output_on and ch.v_measure > 10:
                    self.ramp_controller.start_ramp_down(
                        ch.channel_id, 10.0,
                        ch.device_port, ch.channel_index, ch.v_measure)
        self._show_alert("")

    # ========== 数据导出 ==========

    def _export_csv(self) -> None:
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV 文件",
            f"iseg_thq_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV 文件 (*.csv)")
        if not filepath:
            return
        labels = {}
        for ch in self.device_manager.get_all_channels():
            device = self.device_manager.get_device(ch.device_port)
            if device:
                labels[ch.channel_id] = f"{device.name}_CH{ch.channel_index}"
        result = CsvExporter.export_csv(filepath, [], self._data_buffers, labels)
        if result:
            QMessageBox.information(self, "导出成功", f"数据已导出到:\n{result}")
        else:
            QMessageBox.warning(self, "导出失败", "请检查磁盘空间。")

    def _export_xlsx(self) -> None:
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel 文件",
            f"iseg_thq_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            "Excel 文件 (*.xlsx)")
        if not filepath:
            return
        labels = {}
        for ch in self.device_manager.get_all_channels():
            device = self.device_manager.get_device(ch.device_port)
            if device:
                labels[ch.channel_id] = f"{device.name}_CH{ch.channel_index}"
        result = CsvExporter.export_excel(filepath, [], self._data_buffers, labels)
        if result:
            QMessageBox.information(self, "导出成功", f"数据已导出到:\n{result}")
        else:
            QMessageBox.warning(self, "导出失败", "请确认已安装 openpyxl 库。")

    # ========== 自动数据记录 ==========

    def _setup_auto_log(self) -> None:
        if self._log_dir:
            log_dir = self._log_dir
        else:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "iseg_data_logs")
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._auto_log_file = os.path.join(log_dir, f"iseg_data_{ts}.dat")
        try:
            self._auto_log_fh = open(self._auto_log_file, "w", encoding="utf-8")
            self._auto_log_fh.write(
                "# ISEG THQ 高压电源数据记录\n"
                f"# 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                "# 格式: 时间戳\t通道\t电压(V)\t电流(A)\t设定电压(V)\t斜坡状态\n"
            )
            self._auto_log_fh.flush()
        except Exception:
            self._auto_log_fh = None

    def _set_log_path(self) -> None:
        start_dir = self._log_dir if self._log_dir else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "选择日志保存目录", start_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks)
        if not chosen:
            return
        try:
            os.makedirs(chosen, exist_ok=True)
            test_file = os.path.join(chosen, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except (OSError, PermissionError) as e:
            QMessageBox.critical(self, "路径无效", f"无法写入指定目录:\n{chosen}\n\n错误: {e}")
            return
        self._close_auto_log()
        self._log_dir = chosen
        self._setup_auto_log()
        QMessageBox.information(
            self, "路径设置成功",
            f"日志保存路径已更新为:\n{chosen}\n\n当前日志文件:\n{self._auto_log_file}")
        self._update_status_bar()

    def _log_data(self) -> None:
        """
        智能日志写入（每 2s 触发）

        暂停规则：所有通道电压连续 5s 无变化 + 全部处于 IDLE 状态 → 暂停
        恢复规则：检测到任何通道电压变化 或 斜坡开始 → 恢复
        """
        if self._auto_log_fh is None:
            return
        all_channels = self.device_manager.get_all_channels()
        if not all_channels:
            return

        now = time.time()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # 检测电压变化
        any_voltage_changed = False
        any_ramping = False
        for ch in all_channels:
            v_now = ch.v_measure
            v_last = self._last_logged_v.get(ch.channel_id)
            if v_last is not None and abs(v_now - v_last) >= 0.05:
                self._last_voltage_change[ch.channel_id] = now
                any_voltage_changed = True
            elif v_last is None:
                self._last_voltage_change[ch.channel_id] = now
            self._last_logged_v[ch.channel_id] = v_now
            if ch.is_ramping:
                any_ramping = True

        # 判断暂停条件
        all_idle = not any(ch.is_ramping or ch.output_on for ch in all_channels)
        all_stable_5s = all_idle and all(
            now - self._last_voltage_change.get(ch.channel_id, 0) >= 5.0
            for ch in all_channels
        )
        should_pause = all_idle and all_stable_5s

        # 执行暂停/恢复
        if should_pause and not self._logging_paused:
            self._logging_paused = True
            self._log_pause_ts = now
            try:
                self._auto_log_fh.write(
                    f"# === 日志暂停: {ts} (所有通道稳定+IDLE ≥5s) ===\n")
                self._auto_log_fh.flush()
            except Exception:
                self._auto_log_fh = None
            return

        if self._logging_paused:
            if any_voltage_changed or any_ramping:
                self._logging_paused = False
                try:
                    paused_duration = now - self._log_pause_ts
                    self._auto_log_fh.write(
                        f"# === 日志恢复: {ts} (暂停 {paused_duration:.0f}s"
                        f", 检测到电压变化) ===\n")
                except Exception:
                    self._auto_log_fh = None
            else:
                return

        # 正常写入
        try:
            for ch in all_channels:
                ramp_info = ""
                if ch.is_ramping:
                    ramp_info = f"RAMP:{ch.v_target:.1f}"
                elif ch.output_on:
                    ramp_info = "HV_ON"
                else:
                    ramp_info = "IDLE"
                self._auto_log_fh.write(
                    f"{ts}\tCH{ch.channel_index}\t"
                    f"{ch.v_measure:.1f}\t{ch.i_measure:.6e}\t"
                    f"{ch.v_set:.1f}\t{ramp_info}\n")
            self._auto_log_fh.flush()
        except Exception:
            self._auto_log_fh = None

    def _close_auto_log(self) -> None:
        if self._auto_log_fh:
            self._auto_log_fh.close()
            self._auto_log_fh = None

    # ========== 诊断 ==========

    def _run_diagnostic(self) -> None:
        ports = self.port_manager.get_connected_ports()
        if not ports:
            QMessageBox.warning(self, "提示", "请先连接设备")
            return
        port = ports[0]
        lines = [f"端口: {port}", ""]
        resp = self.async_serial.send_sync(port, "#1")
        lines.append(f"THQ识别 (#1): '{resp}'")
        if resp and ";" in resp:
            info = THQProtocol.parse_id(resp)
            lines.append(f"序列号: {info['serial']}, 固件: {info['firmware']}")
            lines.append(f"Vnom: {info['v_nominal']}V, Inom: {info['i_nominal_x10']/10:.1f}µA")
        lines.append("")
        resp = self.async_serial.send_sync(port, "U1")
        lines.append(f"电压 (U1): '{resp}' → {THQProtocol.parse_float(resp):.1f}V")
        resp = self.async_serial.send_sync(port, "I1")
        lines.append(f"电流 (I1): '{resp}' → {THQProtocol.parse_current(resp)*1e6:.2f}µA")
        resp = self.async_serial.send_sync(port, "S1")
        lines.append(f"状态 (S1): '{resp}'")
        status = THQProtocol.parse_status(resp)
        lines.append(f"HV ON: {status['output_on']}, 极性: {status['polarity']}, USB: {status['usb_mode']}")
        QMessageBox.information(self, f"串口诊断 - {port}", "\n".join(lines))

    # ========== 辅助方法 ==========

    def _show_alert(self, message: str) -> None:
        if message:
            self._alert_label.setText(f"  {message}")
            self._alert_label.setVisible(True)
        else:
            self._alert_label.setVisible(False)

    def _update_status_bar(self) -> None:
        connected = self.device_manager.get_connected_count()
        total = len(self.device_manager.get_all_devices())
        log_info = ""
        if self._auto_log_file:
            log_info = f" | 数据记录: {self._auto_log_file}"
        self._status_label.setText(
            f"已连接 {connected}/{total} 台设备 | 共 {connected * 2} 个通道{log_info}")

    def _open_log_dir(self) -> None:
        if self._log_dir:
            log_dir = self._log_dir
        else:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "iseg_data_logs")
        os.makedirs(log_dir, exist_ok=True)
        os.startfile(log_dir)

    def _show_about(self) -> None:
        QMessageBox.about(self, "关于",
                         "ISEG THQ 高压电源控制器 v2.0 (异步优化版)\n\n"
                         "通过 USB 串口控制 ISEG THQ 系列高压电源\n"
                         "通信协议: THQ # 协议 (9600bps 8N1)\n\n"
                         "性能优化:\n"
                         "  • 异步串口工作线程 (主线程零阻塞)\n"
                         "  • 测量结果批量消费与UI合并更新\n"
                         "  • 异步斜坡控制 (串口I/O后台执行)\n"
                         "  • 设备树增量更新\n"
                         "  • 批量日志写入 (每2s)\n\n"
                         "系统兼容: Windows 11 + Python 3.14")

    def _safe_exit(self) -> None:
        """安全退出"""
        self._close_auto_log()
        self.ramp_controller.stop_all()

        self._consume_timer.stop()
        self._refresh_timer.stop()
        self._watchdog_timer.stop()
        self._log_timer.stop()

        self.async_serial.stop()
        self.port_manager.disconnect_all()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        self._safe_exit()
        event.accept()
