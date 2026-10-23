"""
环形数据缓冲区 — 用于实时图表和历史查询
"""

import time
from collections import deque


class DataBuffer:
    """单个通道的环形数据缓冲区"""

    DEFAULT_CAPACITY = 36000  # 默认存储约1小时数据 (100ms采样 = 36000点)

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._timestamps: deque[float] = deque(maxlen=capacity)
        self._voltages: deque[float] = deque(maxlen=capacity)
        self._currents: deque[float] = deque(maxlen=capacity)
        self._v_sets: deque[float] = deque(maxlen=capacity)

    def append(self, voltage: float, current: float, v_set: float = 0.0) -> None:
        """追加一条采样数据"""
        self._timestamps.append(time.time())
        self._voltages.append(voltage)
        self._currents.append(current)
        self._v_sets.append(v_set)

    def get_data(self):
        """获取所有数据 (用于绘图)"""
        return (
            list(self._timestamps),
            list(self._voltages),
            list(self._currents),
            list(self._v_sets),
        )

    def get_recent(self, count: int = 1000):
        """获取最近N条数据"""
        return (
            list(self._timestamps)[-count:],
            list(self._voltages)[-count:],
            list(self._currents)[-count:],
            list(self._v_sets)[-count:],
        )

    @property
    def length(self) -> int:
        return len(self._timestamps)

    def clear(self) -> None:
        """清空缓冲区"""
        self._timestamps.clear()
        self._voltages.clear()
        self._currents.clear()
        self._v_sets.clear()
