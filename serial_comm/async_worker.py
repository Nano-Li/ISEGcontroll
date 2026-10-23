"""
异步串口工作线程 — 将串口 I/O 从主线程移到后台

设计原理:
  1. 所有串口命令通过队列提交到后台工作线程
  2. 工作线程按顺序执行命令，结果通过 Qt 信号返回
  3. 主线程完全不阻塞，GUI 始终保持响应
  4. 命令按端口分组：同设备的命令可批量处理

兼容: Python 3.14 / Win11
"""

import time
import queue
from typing import Optional, Callable, List

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from .port_manager import PortManager


class CmdType:
    """串口命令类型"""
    QUERY = 0       # 查询（返回响应字符串）
    WRITE = 1       # 写入（不需要响应）
    WRITE_READ = 2  # 写入并读取响应
    BATCH = 3       # 批量标记（一组命令执行完毕）


class CommandPacket:
    """单个串口命令包"""

    __slots__ = ("cmd_type", "port", "command", "callback",
                 "channel_id", "timestamp")

    def __init__(self, cmd_type: int, port: str, command: str,
                 callback: Callable = None, channel_id: int = -1):
        self.cmd_type = cmd_type
        self.port = port
        self.command = command
        self.callback = callback      # 结果回调函数 callback(success, response)
        self.channel_id = channel_id  # 关联的 channel_id（可选）
        self.timestamp = time.time()


class SerialWorker(QObject):
    """串口后台工作线程 — 运行在独立 QThread 中"""

    # 信号：向主线程返回结果
    sig_command_result = pyqtSignal(int, str, str)   # channel_id, port, response
    sig_command_failed = pyqtSignal(int, str, str)   # channel_id, port, error
    sig_batch_done = pyqtSignal(str)                 # port
    sig_log = pyqtSignal(str, str, str)              # port, direction, content

    def __init__(self, port_manager: PortManager, parent=None):
        super().__init__(parent)
        self._pm = port_manager
        self._cmd_queue: queue.Queue = queue.Queue()
        self._running = False

    def enqueue(self, packet: CommandPacket) -> None:
        """提交命令到工作队列（线程安全，非阻塞）"""
        self._cmd_queue.put(packet)

    def enqueue_batch(self, packets: List[CommandPacket]) -> None:
        """批量提交命令（同端口批量执行）"""
        if not packets:
            return
        batch_packet = CommandPacket(CmdType.BATCH, packets[0].port, "", None)
        for p in packets:
            self._cmd_queue.put(p)
        self._cmd_queue.put(batch_packet)

    def start_worker(self) -> None:
        """启动工作线程"""
        self._running = True

    def stop_worker(self) -> None:
        """停止工作线程"""
        self._running = False
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
            except queue.Empty:
                break

    @property
    def pending_count(self) -> int:
        return self._cmd_queue.qsize()

    @pyqtSlot()
    def process_loop(self) -> None:
        """工作线程主循环 — 持续处理命令队列"""
        while self._running:
            try:
                packet = self._cmd_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if packet is None:
                break

            if packet.cmd_type == CmdType.BATCH:
                self.sig_batch_done.emit(packet.port)
                continue

            try:
                response = self._pm.send_command(packet.port, packet.command)
                if response is not None:
                    self.sig_command_result.emit(
                        packet.channel_id, packet.port, response
                    )
                    if packet.callback:
                        packet.callback(True, response)
                else:
                    self.sig_command_failed.emit(
                        packet.channel_id, packet.port, "无响应"
                    )
                    if packet.callback:
                        packet.callback(False, None)
            except Exception as e:
                self.sig_command_failed.emit(
                    packet.channel_id, packet.port, str(e)
                )
                if packet.callback:
                    packet.callback(False, None)

            self._cmd_queue.task_done()


class AsyncSerialManager:
    """异步串口管理器 — 主线程使用的包装接口

    用法:
        async_ser = AsyncSerialManager(parent=self)
        async_ser.start()

        # 异步查询
        async_ser.query_async("COM6", "U1", callback=my_callback)

        # 同步操作（连接/断开/扫描）
        async_ser.connect_port("COM6")
    """

    def __init__(self, parent: QObject):
        self.port_manager = PortManager()
        self._worker = SerialWorker(self.port_manager)
        self._thread = QThread(parent)

        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.process_loop)

        # 转发信号
        self.sig_command_result = self._worker.sig_command_result
        self.sig_command_failed = self._worker.sig_command_failed
        self.sig_batch_done = self._worker.sig_batch_done

    def start(self) -> None:
        """启动工作线程"""
        self._worker.start_worker()
        self._thread.start()

    def stop(self) -> None:
        """停止工作线程"""
        self._worker.stop_worker()
        self._thread.quit()
        self._thread.wait(1000)

    # ── 同步接口（直接调用 PortManager） ─────────

    def connect_port(self, port: str, baud: int = 9600) -> bool:
        return self.port_manager.connect(port, baud)

    def disconnect_port(self, port: str) -> None:
        self.port_manager.disconnect(port)

    def disconnect_all(self) -> None:
        self.port_manager.disconnect_all()

    def scan_ports(self) -> list[str]:
        return PortManager.scan_ports()

    def is_connected(self, port: str) -> bool:
        return self.port_manager.is_connected(port)

    def is_port_alive(self, port: str) -> bool:
        return self.port_manager.is_port_alive(port)

    def send_sync(self, port: str, cmd: str) -> Optional[str]:
        """同步发送（用于非关键路径）"""
        return self.port_manager.send_command(port, cmd)

    # ── 异步接口（后台线程执行） ─────────────────

    def query_async(self, port: str, command: str,
                    callback: Callable = None, channel_id: int = -1) -> None:
        """异步查询：立即返回，结果通过回调送达"""
        packet = CommandPacket(CmdType.QUERY, port, command, callback, channel_id)
        self._worker.enqueue(packet)

    def write_async(self, port: str, command: str,
                    callback: Callable = None, channel_id: int = -1) -> None:
        """异步写入：不等待响应"""
        packet = CommandPacket(CmdType.WRITE_READ, port, command, callback, channel_id)
        self._worker.enqueue(packet)

    def batch_async(self, packets: List[CommandPacket]) -> None:
        """批量异步执行"""
        self._worker.enqueue_batch(packets)

    # ── 快捷方法 ─────────────────────────────────

    def write_voltage_async(self, port: str, channel_index: int, voltage: float,
                            callback: Callable = None, channel_id: int = -1) -> None:
        """异步写入设定电压"""
        from protocol.thq_protocol import THQProtocol
        cmd = THQProtocol.set_voltage(channel_index, voltage)
        self.write_async(port, cmd, callback, channel_id)
