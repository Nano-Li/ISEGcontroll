"""
设备/通道树形列表 — 左侧面板

显示所有已连接设备及其通道:
  - 设备节点 (可展开/折叠)
  - 通道子节点 (可选中进行控制)
  - 实时状态指示 (连接/断开指示灯)
  - 通道选择信号
"""

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget,
                             QTreeWidgetItem, QPushButton, QComboBox, QLabel,
                             QGroupBox, QLineEdit, QInputDialog, QMessageBox,
                             QHeaderView)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QBrush, QFont


class DeviceTreeWidget(QWidget):
    """设备/通道树形列表"""

    sig_channel_selected = pyqtSignal(int, str, float)  # channel_id, label, v_nominal
    sig_scan_ports = pyqtSignal()
    sig_connect = pyqtSignal(str)
    sig_disconnect = pyqtSignal(str)
    sig_group_channel = pyqtSignal(int, str)  # channel_id, group_name

    # 【优化】缓存 QBrush/QColor 对象，避免每次刷新时重建
    _BRUSH_CONNECTED = QBrush(QColor("#4CAF50"))
    _BRUSH_DISCONNECTED = QBrush(QColor("#FF0000"))
    _BRUSH_ONLINE = QBrush(QColor("#4CAF50"))
    _BRUSH_OFFLINE = QBrush(QColor("#888888"))
    _BRUSH_HV = QBrush(QColor("#FF0000"))

    # 缓存: port -> (dev_item, {channel_index -> ch_item})
    _dev_cache: dict = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dev_cache = {}
        self._connected_ports: set[str] = set()
        self._build_ui()
        # 端口选择变化时更新按钮状态
        self._combo_ports.currentTextChanged.connect(self._on_port_changed)

    def _build_ui(self) -> None:
        """构建 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ====== 标题 ======
        title_label = QLabel("🔌 设备与通道管理")
        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        # ====== 连接控制区 ======
        conn_group = QGroupBox("串口连接")
        conn_layout = QVBoxLayout(conn_group)
        conn_layout.setSpacing(4)

        # 端口选择和扫描
        port_row = QHBoxLayout()
        self._combo_ports = QComboBox()
        self._combo_ports.setMinimumWidth(80)
        port_row.addWidget(QLabel("端口:"))
        port_row.addWidget(self._combo_ports, 1)

        self._btn_scan = QPushButton("🔍 扫描")
        self._btn_scan.setMaximumWidth(70)
        self._btn_scan.clicked.connect(lambda: self.sig_scan_ports.emit())
        port_row.addWidget(self._btn_scan)

        conn_layout.addLayout(port_row)

        # 连接/断开按钮
        btn_row = QHBoxLayout()
        self._btn_connect = QPushButton("连接")
        self._btn_connect.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
            "padding: 6px 12px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #45a049; }"
        )
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        btn_row.addWidget(self._btn_connect)

        self._btn_disconnect = QPushButton("断开")
        self._btn_disconnect.setStyleSheet(
            "QPushButton { background-color: #E53935; color: white; font-weight: bold; "
            "padding: 6px 12px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #C62828; }"
        )
        self._btn_disconnect.clicked.connect(self._on_disconnect_clicked)
        self._btn_disconnect.setEnabled(False)
        btn_row.addWidget(self._btn_disconnect)

        conn_layout.addLayout(btn_row)
        layout.addWidget(conn_group)

        # ====== 设备树 ======
        tree_group = QGroupBox("设备列表")
        tree_layout = QVBoxLayout(tree_group)
        tree_layout.setContentsMargins(2, 4, 2, 2)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["名称", "状态", "电压/电流"])
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QTreeWidget.SingleSelection)
        self._tree.itemClicked.connect(self._on_item_clicked)

        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        tree_layout.addWidget(self._tree)

        # 分组管理按钮
        group_btn_layout = QHBoxLayout()
        btn_add_group = QPushButton("+ 添加到分组")
        btn_add_group.clicked.connect(self._on_add_to_group)
        group_btn_layout.addWidget(btn_add_group)

        btn_remove_group = QPushButton("- 从分组移除")
        btn_remove_group.clicked.connect(self._on_remove_from_group)
        group_btn_layout.addWidget(btn_remove_group)

        tree_layout.addLayout(group_btn_layout)
        layout.addWidget(tree_group)

    # ========== 公共方法 ==========

    def set_ports(self, ports: list[str]) -> None:
        """刷新 COM 端口列表"""
        current = self._combo_ports.currentText()
        self._combo_ports.clear()
        self._combo_ports.addItems(ports)
        if current in ports:
            self._combo_ports.setCurrentText(current)

    def refresh_tree(self, devices: list["DeviceInfo"]) -> None:
        """
        全量重建设备树（仅当拓扑变化时调用）
        优化：使用缓存画笔，避免每次创建新对象
        """
        selected_data = None
        current = self._tree.currentItem()
        if current:
            data = current.data(0, Qt.UserRole)
            if data:
                selected_data = data

        self._tree.clear()
        self._dev_cache.clear()

        for device in devices:
            dev_item = QTreeWidgetItem(self._tree)
            dev_item.setText(0, f"{device.name} ({device.port})")
            dev_item.setData(0, Qt.UserRole, ("device", device.port))

            if device.connected:
                dev_item.setText(1, "● 已连接")
                dev_item.setForeground(1, self._BRUSH_CONNECTED)
            else:
                dev_item.setText(1, "● 已断开")
                dev_item.setForeground(1, self._BRUSH_DISCONNECTED)

            dev_item.setText(2, f"Vnom={device.v_nominal}V")
            dev_font = QFont()
            dev_font.setBold(True)
            dev_item.setFont(0, dev_font)

            channel_items = {}
            for channel in device.channels:
                ch_item = QTreeWidgetItem(dev_item)
                label = f"  通道 {channel.channel_index}  (Vnom={channel.v_nominal:.0f}V)"
                if channel.group_name:
                    label += f" [{channel.group_name}]"
                ch_item.setText(0, label)
                ch_item.setData(0, Qt.UserRole, ("channel", channel.channel_id, channel.v_nominal))

                if channel.connected:
                    status_text = "● 在线"
                    ch_item.setForeground(1, self._BRUSH_ONLINE)
                else:
                    status_text = "● 离线"
                    ch_item.setForeground(1, self._BRUSH_OFFLINE)

                if channel.output_on and channel.v_measure > 10:
                    status_text += " ⚡HV"
                    ch_item.setForeground(1, self._BRUSH_HV)

                if channel.is_ramping:
                    status_text += " ↗"

                ch_item.setText(1, status_text)
                ch_item.setText(2, f"{channel.v_measure:.1f}V / {channel.i_measure*1e6:.1f}µA")

                if selected_data and selected_data[0] == "channel" and selected_data[1] == channel.channel_id:
                    self._tree.setCurrentItem(ch_item)

                channel_items[channel.channel_index] = ch_item

            dev_item.setExpanded(True)
            self._dev_cache[device.port] = (dev_item, channel_items)

    def refresh_values(self, devices: list["DeviceInfo"]) -> None:
        """
        【优化】增量更新测量值 — 不清除树，O(1) 每通道
        仅在拓扑变化时重建（缓存失效时自动 fallback 到 refresh_tree）
        """
        for device in devices:
            cached = self._dev_cache.get(device.port)
            if cached is None:
                self.refresh_tree(devices)
                return
            dev_item, channel_items = cached

            if device.connected:
                dev_item.setText(1, "● 已连接")
                dev_item.setForeground(1, self._BRUSH_CONNECTED)
            else:
                dev_item.setText(1, "● 已断开")
                dev_item.setForeground(1, self._BRUSH_DISCONNECTED)

            for channel in device.channels:
                ch_item = channel_items.get(channel.channel_index)
                if ch_item is None:
                    self.refresh_tree(devices)
                    return

                if channel.connected:
                    status_text = "● 在线"
                else:
                    status_text = "● 离线"

                if channel.output_on and channel.v_measure > 10:
                    status_text += " ⚡HV"
                    ch_item.setForeground(1, self._BRUSH_HV)
                elif channel.connected:
                    ch_item.setForeground(1, self._BRUSH_ONLINE)
                else:
                    ch_item.setForeground(1, self._BRUSH_OFFLINE)

                if channel.is_ramping:
                    status_text += " ↗"

                ch_item.setText(1, status_text)
                ch_item.setText(2, f"{channel.v_measure:.1f}V / {channel.i_measure*1e6:.1f}µA")

    def set_connection_state(self, port: str, connected: bool) -> None:
        """更新连接按钮状态"""
        if connected:
            self._connected_ports.add(port)
        else:
            self._connected_ports.discard(port)
        # 同步更新当前选中端口的按钮状态
        current_port = self._combo_ports.currentText()
        is_cur_connected = current_port in self._connected_ports
        self._btn_connect.setEnabled(bool(current_port) and not is_cur_connected)
        self._btn_disconnect.setEnabled(is_cur_connected)

    def _on_port_changed(self, port: str) -> None:
        """端口选择变化时更新按钮状态"""
        is_connected = port in self._connected_ports
        self._btn_connect.setEnabled(bool(port) and not is_connected)
        self._btn_disconnect.setEnabled(is_connected)

    # ========== 内部方法 ==========

    def _on_connect_clicked(self) -> None:
        """点击连接按钮"""
        port = self._combo_ports.currentText()
        if port:
            self.sig_connect.emit(port)

    def _on_disconnect_clicked(self) -> None:
        """点击断开按钮"""
        port = self._combo_ports.currentText()
        if port:
            self.sig_disconnect.emit(port)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """点击树节点"""
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        item_type = data[0]
        item_id = data[1]

        if item_type == "channel":
            # 从 item 数据中提取 v_nominal
            v_nominal = data[2] if len(data) >= 3 else 0.0
            self.sig_channel_selected.emit(item_id, item.text(0).strip(), v_nominal)

    def _on_add_to_group(self) -> None:
        """添加到分组"""
        current = self._tree.currentItem()
        if current is None:
            return
        data = current.data(0, Qt.UserRole)
        if data is None or data[0] != "channel":
            return

        channel_id = data[1]
        group_name, ok = QInputDialog.getText(
            self, "添加到分组", "请输入分组名称:",
            text="实验组1"
        )
        if ok and group_name.strip():
            self.sig_group_channel.emit(channel_id, group_name.strip())

    def _on_remove_from_group(self) -> None:
        """从分组移除"""
        current = self._tree.currentItem()
        if current is None:
            return
        data = current.data(0, Qt.UserRole)
        if data is None or data[0] != "channel":
            return

        channel_id = data[1]
        self.sig_group_channel.emit(channel_id, "")
