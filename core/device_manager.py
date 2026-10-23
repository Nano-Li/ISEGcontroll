"""
设备与通道管理模块

支持最多 6 台设备, 每台设备 2 个通道, 共 12 个独立通道
"""

from dataclasses import dataclass, field
from typing import Optional, List

MAX_DEVICES = 6
CHANNELS_PER_DEVICE = 2


@dataclass
class ChannelInfo:
    """单个通道的信息"""
    channel_id: int = 0                  # 全局唯一通道ID (0~11)
    device_port: str = ""                # 所属设备端口
    channel_index: int = 0               # 设备内通道编号 (1 或 2)
    v_measure: float = 0.0               # 实测电压 (V)
    i_measure: float = 0.0               # 实测电流 (A)
    v_set: float = 0.0                   # 设定电压 (V)
    v_target: float = 0.0                # 目标电压 (V) — 斜坡终点
    ramp_time: float = 10.0              # 上升/下降时间 (秒)
    ramp_vps: float = 0.0                # 斜坡速率 (V/s)
    output_on: bool = False              # 高压输出是否开启
    is_ramping: bool = False             # 是否正在升降压
    ramp_direction: int = 0              # 1=升压, -1=降压, 0=静止
    connected: bool = False              # 通道是否在线
    group_name: str = ""                 # 所属分组名
    # 每个通道独立的硬件参数（从 #1 / #2 命令读取）
    v_nominal: float = 0.0               # 该通道标称电压 (V) — 从硬件 #1/#2 命令读取
    i_nominal_x10: int = 0               # 该通道标称电流 × 10 (µA)


@dataclass
class DeviceInfo:
    """单台设备的信息"""
    port: str = ""                       # COM端口名
    name: str = ""                       # 设备显示名称
    serial: str = ""                     # 序列号
    firmware: str = ""                   # 固件版本
    v_nominal: float = 0.0               # 标称电压 (V) — 从硬件 #1/#2 命令读取
    i_nominal_x10: int = 0               # 标称电流 × 10
    connected: bool = False              # 连接状态
    channels: List[ChannelInfo] = field(default_factory=list)


class DeviceManager:
    """设备管理器 — 管理所有设备和通道"""

    def __init__(self):
        self._devices: dict[str, "DeviceInfo"] = {}
        self._next_device_name = 1
        self._next_channel_id = 0

    # ========== 设备操作 ==========

    def add_device(self, port: str, serial: str = "", firmware: str = "",
                   v_nominal: float = 0.0, i_nominal_x10: int = 0,
                   ch1_v_nominal: float = None, ch1_i_nominal_x10: int = None,
                   ch2_v_nominal: float = None, ch2_i_nominal_x10: int = None) -> Optional[DeviceInfo]:
        """添加一台设备

        参数:
          port:           串口号
          serial:         序列号
          firmware:       固件版本
          v_nominal:      设备标称电压 (V) — 未提供 per-channel 时的默认值
          i_nominal_x10:  设备标称电流 × 10
          ch1_v_nominal:  CH1 独立标称电压 (None 则使用设备值)
          ch1_i_nominal_x10: CH1 独立标称电流
          ch2_v_nominal:  CH2 独立标称电压 (None 则使用设备值)
          ch2_i_nominal_x10: CH2 独立标称电流
        """
        if len(self._devices) >= MAX_DEVICES:
            return None
        if port in self._devices:
            return None

        device = DeviceInfo(
            port=port,
            name=f"THQ #{self._next_device_name}",
            serial=serial,
            firmware=firmware,
            v_nominal=v_nominal,
            i_nominal_x10=i_nominal_x10,
            connected=True,
        )

        ch_params = [
            (ch1_v_nominal, ch1_i_nominal_x10),
            (ch2_v_nominal, ch2_i_nominal_x10),
        ]
        for i in range(CHANNELS_PER_DEVICE):
            ch_vnom = ch_params[i][0] if ch_params[i][0] is not None else v_nominal
            ch_inom = ch_params[i][1] if ch_params[i][1] is not None else i_nominal_x10
            ch = ChannelInfo(
                channel_id=self._next_channel_id,
                device_port=port,
                channel_index=i + 1,
                connected=True,
                v_nominal=ch_vnom,
                i_nominal_x10=ch_inom,
            )
            device.channels.append(ch)
            self._next_channel_id += 1

        self._devices[port] = device
        self._next_device_name += 1
        return device

    def remove_device(self, port: str) -> None:
        """移除一台设备"""
        self._devices.pop(port, None)

    def get_device(self, port: str) -> Optional[DeviceInfo]:
        """按端口名获取设备"""
        return self._devices.get(port)

    def get_all_devices(self) -> List[DeviceInfo]:
        """获取所有设备列表"""
        return sorted(self._devices.values(), key=lambda d: d.name)

    def get_connected_count(self) -> int:
        """获取已连接设备数量"""
        return sum(1 for d in self._devices.values() if d.connected)

    def set_connected(self, port: str, connected: bool) -> None:
        """设置设备连接状态"""
        dev = self._devices.get(port)
        if dev:
            dev.connected = connected
            for ch in dev.channels:
                ch.connected = connected

    # ========== 通道操作 ==========

    def get_channel(self, channel_id: int) -> Optional[ChannelInfo]:
        """按全局通道ID获取通道"""
        for dev in self._devices.values():
            for ch in dev.channels:
                if ch.channel_id == channel_id:
                    return ch
        return None

    def get_channel_by_key(self, port: str, channel_index: int) -> Optional[ChannelInfo]:
        """按端口和通道编号获取通道"""
        dev = self._devices.get(port)
        if dev:
            for ch in dev.channels:
                if ch.channel_index == channel_index:
                    return ch
        return None

    def get_all_channels(self) -> List[ChannelInfo]:
        """获取所有通道列表"""
        result = []
        for dev in self._devices.values():
            result.extend(dev.channels)
        return sorted(result, key=lambda c: c.channel_id)

    def get_connected_channels(self) -> List[ChannelInfo]:
        """获取所有已连接通道"""
        return [ch for ch in self.get_all_channels() if ch.connected]

    # ========== 分组管理 ==========

    def set_channel_group(self, channel_id: int, group_name: str) -> None:
        """设置通道分组"""
        ch = self.get_channel(channel_id)
        if ch:
            ch.group_name = group_name

    def get_groups(self) -> List[str]:
        """获取所有分组名"""
        groups = set()
        for ch in self.get_all_channels():
            if ch.group_name:
                groups.add(ch.group_name)
        return sorted(groups)

    def clear_all(self) -> None:
        """清空所有设备"""
        self._devices.clear()
        self._next_device_name = 1
        self._next_channel_id = 0
