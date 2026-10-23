"""
THQ # 协议封装 — 基于 ISEG THQ 技术手册和硬件实测

命令格式 (基于 THQ RemoteControl.md 及 COM6 实测结果):
  设备识别:  #1               → 序列号;固件版本;标称电压;标称电流x10
  读电压:    U1               → 实测值 (V)
  读电流:    I1               → 实测值 (A, 科学计数法)
  读设定电压: D1               → 设定值
  写设定电压: D1=1000          → 设置电压 (自动进入USB模式并开启输出)
  读设定电流: C1               → 设定电流
  写设定电流: C1=1E-3          → 设置电流限值
  读状态:    S1               → 状态码 (H=HV ON, N=负极性, U=USB模式)
  读极性:    P1               → + 或 -
  读KILL:    T1               → 0 或 1
  写KILL:    T1=0|1           → 禁用/启用KILL
  回显模式:  E1=1|2           → 1=单回显, 2=双回显

通道编号从 1 开始 (对应硬件通道号)
注意: 所有查询命令不带 * 后缀, 带 * 会导致设备返回 '????'
"""


class THQProtocol:

    TERMINATOR = "\r\n"

    # ========== 设备识别 ==========

    @staticmethod
    def query_id(channel: int = 1) -> str:
        """读取设备标识，如 #1 返回 '600765;2.11;4000;305'"""
        return f"#{channel}"

    # ========== 电压命令 ==========

    @staticmethod
    def query_voltage(channel: int) -> str:
        """读实际电压，如 U1"""
        return f"U{channel}"

    @staticmethod
    def query_set_voltage(channel: int) -> str:
        """读设定电压，如 D1"""
        return f"D{channel}"

    @staticmethod
    def set_voltage(channel: int, voltage: float) -> str:
        """
        写设定电压，如 D1=1000
        注意: 发送此命令后硬件自动进入USB模式并开启高压输出
        """
        return f"D{channel}={voltage:.1f}"

    # ========== 电流命令 ==========

    @staticmethod
    def query_current(channel: int) -> str:
        """读实际电流，如 I1"""
        return f"I{channel}"

    @staticmethod
    def query_set_current(channel: int) -> str:
        """读设定电流，如 C1"""
        return f"C{channel}"

    @staticmethod
    def set_current(channel: int, current_a: float) -> str:
        """写设定电流 (A)，如 C1=1E-3"""
        return f"C{channel}={current_a}"

    # ========== 状态命令 ==========

    @staticmethod
    def query_status(channel: int) -> str:
        """读设备状态，如 S1"""
        return f"S{channel}"

    @staticmethod
    def query_polarity(channel: int) -> str:
        """读极性，如 P1"""
        return f"P{channel}"

    @staticmethod
    def query_kill(channel: int) -> str:
        """读KILL状态，如 T1"""
        return f"T{channel}"

    @staticmethod
    def set_kill(channel: int, enable: bool) -> str:
        """写KILL, 如 T1=1 (启用) 或 T1=0 (禁用)"""
        return f"T{channel}={1 if enable else 0}"

    @staticmethod
    def set_echo_mode(channel: int, mode: int) -> str:
        """设置回显模式，E1=1 (单回显) 或 E1=2 (双回显)"""
        return f"E{channel}={mode}"

    # ========== 响应解析 ==========

    @staticmethod
    def parse_id(response: str) -> dict:
        """
        解析设备标识响应: '600765;2.11;4000;305'
        返回: {serial, firmware, v_nominal, i_nominal_x10}
        v_nominal = 标称电压(V), i_nominal_x10 = 标称电流 × 10 (µA)
        """
        result = {"serial": "", "firmware": "", "v_nominal": 0, "i_nominal_x10": 0}
        if not response or not response.strip():
            return result
        parts = response.strip().split(";")
        if len(parts) >= 1:
            result["serial"] = parts[0].strip()
        if len(parts) >= 2:
            result["firmware"] = parts[1].strip()
        if len(parts) >= 3:
            try:
                result["v_nominal"] = int(parts[2].strip())
            except ValueError:
                pass
        if len(parts) >= 4:
            try:
                result["i_nominal_x10"] = int(parts[3].strip())
            except ValueError:
                pass
        return result

    @staticmethod
    def parse_float(response: str) -> float:
        """解析浮点数响应，处理可能的回显前缀"""
        if not response or not response.strip():
            return 0.0
        s = response.strip()
        if "=" in s:
            s = s.split("=")[-1]
        s = s.rstrip("*").strip()
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def parse_current(response: str) -> float:
        """
        解析电流响应，可能为科学计数法格式: 0.028E-3
        返回: 安培 (A)
        """
        return THQProtocol.parse_float(response)

    @staticmethod
    def parse_status(response: str) -> dict:
        """
        解析设备状态码
        返回: {output_on, polarity, usb_mode, kill_enabled}
        """
        result = {"output_on": False, "polarity": "+", "usb_mode": False, "kill_enabled": False}
        if not response or not response.strip():
            return result
        s = response.strip().upper()
        if "H" in s:
            result["output_on"] = True
        if "N" in s:
            result["polarity"] = "-"
        if "U" in s:
            result["usb_mode"] = True
        if "K" in s:
            result["kill_enabled"] = True
        return result

    @staticmethod
    def parse_bool(response: str) -> bool:
        """解析布尔响应"""
        if not response:
            return False
        s = response.strip().upper()
        return s in ("1", "ON", "TRUE", "H")

    @staticmethod
    def parse_polarity(response: str) -> str:
        """解析极性响应，返回 '+' 或 '-'"""
        if not response:
            return "+"
        s = response.strip()
        if s in ("+", "-"):
            return s
        return "+"
