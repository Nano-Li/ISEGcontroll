"""
串口通信管理器 — 基于 ISEG 官方 serial.cpp 的回显同步协议

通信参数:
  波特率: 9600 bps
  数据位: 8
  奇偶校验: 无
  停止位: 1
  流控: 无

回显同步流程 (参考 serial.cpp):
  1. 清空输入缓冲区
  2. 逐字节发送命令字符
  3. 每发送一个字节后等待回显确认 (超时 ≈100ms)
  4. 如果回显不匹配, 返回通信错误
  5. 命令以 \r\n 结尾
  6. 发送完成后等待 20ms
  7. 读取响应, 直到收到 \r\n 或超时
"""

import os
import time
import threading
from datetime import datetime
from typing import Optional

import serial
import serial.tools.list_ports


def _get_log_path() -> str:
    """获取日志文件路径"""
    log_dir = os.path.join(os.environ.get("APPDATA", "."),
                           "ISEG_THQ_Controller", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir,
                        f"thq_commands_{datetime.now().strftime('%Y%m%d')}.log")


class PortManager:
    """串口连接管理器 — 支持最多6台设备同时连接"""

    MAX_PORTS = 6

    ECHO_TIMEOUT = 0.1      # 单字节回显超时 (秒)
    ECHO_POLL_INTERVAL = 0.005  # 回显轮询间隔 (秒)
    POST_WRITE_DELAY = 0.02     # 写完后等待设备处理 (秒)
    RESPONSE_TIMEOUT = 1.0      # 响应总超时 (秒)

    def __init__(self):
        self._connections: dict[str, serial.Serial] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._log_path = _get_log_path()
        self._log_lock = threading.Lock()

    # ========== 日志 ==========

    def _log(self, port: str, direction: str, content: str) -> None:
        """写入通信日志"""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] [{port}] {direction} {content}\n"
        try:
            with self._log_lock:
                current_path = _get_log_path()
                if current_path != self._log_path:
                    self._log_path = current_path
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
        except Exception:
            pass

    # ========== 端口扫描 ==========

    @staticmethod
    def scan_ports() -> list[str]:
        """扫描系统中所有可用COM端口"""
        ports = serial.tools.list_ports.comports()
        return sorted([p.device for p in ports])

    # ========== 连接管理 ==========

    def connect(self, port_name: str, baud: int = 9600) -> bool:
        """连接指定串口端口"""
        with self._global_lock:
            if len(self._connections) >= self.MAX_PORTS:
                return False
            if port_name in self._connections:
                return True
            try:
                ser = serial.Serial(
                    port=port_name,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.1,
                    write_timeout=1.0,
                )
                self._connections[port_name] = ser
                self._locks[port_name] = threading.Lock()
                self._log(port_name, "CONNECT", f"波特率={baud} 成功")
                return True
            except (serial.SerialException, OSError) as e:
                self._log(port_name, "CONNECT", f"失败: {e}")
                return False

    def disconnect(self, port_name: str) -> None:
        """断开指定端口"""
        with self._global_lock:
            ser = self._connections.pop(port_name, None)
            self._locks.pop(port_name, None)
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
        self._log(port_name, "DISCONNECT", "")

    def disconnect_all(self) -> None:
        """断开所有连接"""
        for port_name in list(self._connections.keys()):
            self.disconnect(port_name)

    def is_connected(self, port_name: str) -> bool:
        """检查端口是否已连接"""
        return port_name in self._connections

    def get_connected_ports(self) -> list[str]:
        """获取所有已连接端口列表"""
        with self._global_lock:
            return sorted(self._connections.keys())

    # ========== 命令收发 ==========

    def _write_command_with_echo(self, ser: serial.Serial, cmd: str) -> bool:
        """
        逐字节发送命令并验证回显 (参考 serial.cpp writeCommand)
        返回 True 表示所有字节回显正确
        """
        cmd_bytes = cmd.encode("ascii")
        for byte_val in cmd_bytes:
            ser.write(bytes([byte_val]))

            deadline = time.time() + self.ECHO_TIMEOUT
            echoed = False
            while time.time() < deadline:
                if ser.in_waiting > 0:
                    echo = ser.read(1)
                    if len(echo) > 0 and echo[0] == byte_val:
                        echoed = True
                        break
                time.sleep(self.ECHO_POLL_INTERVAL)

            if not echoed:
                return False

        return True

    def _read_response(self, ser: serial.Serial) -> str:
        """
        读取设备响应 (参考 serial.cpp writeCommandAndReadAnswer)
        持续读取直到收到 \r\n 或超时
        """
        response_parts = []
        deadline = time.time() + self.RESPONSE_TIMEOUT

        while time.time() < deadline:
            if ser.in_waiting > 0:
                chunk = ser.read(ser.in_waiting).decode("ascii", errors="replace")
                response_parts.append(chunk)
                if "\n" in chunk:
                    break
            time.sleep(0.01)

        raw_response = "".join(response_parts)

        # 去除尾部 \r\n
        if raw_response.endswith("\r\n"):
            raw_response = raw_response[:-2]
        elif raw_response.endswith("\n"):
            raw_response = raw_response[:-1]

        return raw_response.strip()

    def send_command(self, port_name: str, cmd: str) -> Optional[str]:
        """
        发送命令并读取响应 (线程安全)

        流程:
          1. 构建完整命令 (添加 \r\n 终止符)
          2. 清空输入缓冲区
          3. 逐字节发送 + 回显验证
          4. 等待 20ms
          5. 读取响应直到 \r\n 或超时

        返回: 设备响应字符串 (已去除尾部 \r\n), 失败返回 None
        """
        port_lock = self._locks.get(port_name)
        if port_lock is None:
            return None

        with port_lock:
            with self._global_lock:
                ser = self._connections.get(port_name)
            if ser is None:
                return None

            try:
                # 确保命令以 \r\n 结尾
                cmd_clean = cmd.strip()
                full_cmd = cmd_clean + "\r\n"

                # 清空输入缓冲区
                ser.reset_input_buffer()

                # 逐字节发送 + 回显验证
                success = self._write_command_with_echo(ser, full_cmd)
                if not success:
                    self._log(port_name, ">>", f"'{cmd_clean}'")
                    self._log(port_name, "<<", "回显失败")
                    return None

                # 等待设备处理
                time.sleep(self.POST_WRITE_DELAY)

                # 读取响应
                response = self._read_response(ser)

                # 过滤回显: 如果响应就是发送的命令本身, 可能没有实际响应, 再读一次
                if response == cmd_clean or response == full_cmd.strip():
                    time.sleep(0.05)
                    response = self._read_response(ser)

                self._log(port_name, ">>", f"'{cmd_clean}'")
                self._log(port_name, "<<", f"'{response}'")

                return response

            except (serial.SerialException, OSError) as e:
                self._log(port_name, ">>", f"'{cmd.strip()}'")
                self._log(port_name, "ERROR", str(e))
                return None

    def is_port_alive(self, port_name: str) -> bool:
        """检查端口是否仍然活跃 (用于看门狗检测)"""
        if not self.is_connected(port_name):
            return False
        try:
            with self._global_lock:
                ser = self._connections.get(port_name)
            if ser is None:
                return False
            return ser.is_open
        except Exception:
            return False
