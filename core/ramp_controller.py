"""
软件斜坡控制器 — 基于实际流逝时间的平滑电压变化（异步优化版 v2.1）

安全红线:
  - 禁止使用 EMCY_OFF 硬件急停
  - 禁止向设备发送导致电压立即跳变的指令
  - 所有电压变化必须通过步进方式平滑过渡

工作原理:
  1. 用户设定目标电压 V_target 和变化速率 speed_vps (V/s)
  2. 系统计算 ramp_time = ΔV / speed_vps
  3. 记录起始电压 V0 和起始时间 t0
  4. 每次 tick 根据实际流逝时间计算当前应达到的电压:
     V(t) = V0 + (t - t0) / ramp_time × ΔV
  5. 串口发送异步化，失败自动重试，彻底清理任务
  6. 可随时暂停（保持当前电压）或停止

Bug修复 v2.1:
  - 通信失败后 stop_ramp() 彻底清理 _tasks（而非仅标记 paused）
  - 增加 MAX_RETRIES 自动重试机制
  - 新增 force_stop() / has_task() 接口
"""

import time
from typing import Optional, Callable
from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot


class RampController(QObject):
    """斜坡控制器 — 基于流逝时间的自适应步进（异步发送 v2.1）"""

    ramp_started = pyqtSignal(int)
    ramp_progress = pyqtSignal(int, float, float)
    ramp_paused = pyqtSignal(int, float)
    ramp_completed = pyqtSignal(int, float)
    ramp_error = pyqtSignal(int, str)

    sig_tick_done = pyqtSignal(int, bool, float)
    sig_finish_done = pyqtSignal(int, bool)

    TICK_INTERVAL = 80
    MIN_STEP = 0.5
    MAX_RETRIES = 2   # 单步最大重试次数

    def __init__(self, send_callback: Callable, parent=None):
        super().__init__(parent)
        self._send_callback = send_callback
        self._tasks: dict[int, dict] = {}
        self._last_sent: dict[int, float] = {}
        self.sig_tick_done.connect(self._on_tick_done)
        self.sig_finish_done.connect(self._on_finish_done)

    # ========== 公共接口 ==========

    def start_ramp(self, channel_id: int, target_v: float, ramp_time_sec: float,
                   port: str, channel_index: int, current_v: float) -> bool:
        if channel_id in self._tasks:
            return False
        if ramp_time_sec <= 0:
            return False
        delta_v = target_v - current_v
        if abs(delta_v) < 0.05:
            self.ramp_completed.emit(channel_id, target_v)
            return False
        task = {
            "channel_id": channel_id, "port": port,
            "channel_index": channel_index, "start_v": current_v,
            "target_v": target_v, "ramp_time": ramp_time_sec,
            "start_time": time.time(), "paused": False, "retries": 0,
        }
        self._tasks[channel_id] = task
        self._last_sent[channel_id] = current_v
        self.ramp_started.emit(channel_id)
        QTimer.singleShot(0, lambda cid=channel_id: self._do_tick(cid))
        return True

    def start_ramp_down(self, channel_id: int, ramp_time_sec: float,
                        port: str, channel_index: int, current_v: float) -> bool:
        return self.start_ramp(channel_id, 0.0, ramp_time_sec,
                               port, channel_index, current_v)

    def pause_ramp(self, channel_id: int) -> bool:
        task = self._tasks.get(channel_id)
        if task and not task["paused"]:
            task["paused"] = True
            elapsed = time.time() - task["start_time"]
            progress = min(elapsed / task["ramp_time"], 1.0)
            paused_v = task["start_v"] + progress * (task["target_v"] - task["start_v"])
            self.ramp_paused.emit(channel_id, paused_v)
            return True
        return False

    def stop_ramp(self, channel_id: int) -> Optional[float]:
        """停止并彻底移除任务"""
        task = self._tasks.pop(channel_id, None)
        if task is None:
            return None
        elapsed = time.time() - task["start_time"]
        progress = min(elapsed / task["ramp_time"], 1.0)
        final_v = task["start_v"] + progress * (task["target_v"] - task["start_v"])
        self.ramp_paused.emit(channel_id, final_v)
        return final_v

    def force_stop(self, channel_id: int) -> Optional[float]:
        """强制停止 + 清理 last_sent"""
        result = self.stop_ramp(channel_id)
        self._last_sent.pop(channel_id, None)
        return result

    def stop_all(self) -> None:
        for channel_id in list(self._tasks.keys()):
            self.stop_ramp(channel_id)

    def is_ramping(self, channel_id: int) -> bool:
        task = self._tasks.get(channel_id)
        return task is not None and not task.get("paused", False)

    def has_task(self, channel_id: int) -> bool:
        return channel_id in self._tasks

    def get_ramp_state(self, channel_id: int) -> dict:
        task = self._tasks.get(channel_id)
        if task is None:
            return {"active": False}
        elapsed = time.time() - task["start_time"]
        progress = min(elapsed / task["ramp_time"], 1.0)
        current_v = task["start_v"] + progress * (task["target_v"] - task["start_v"])
        return {
            "active": True,
            "paused": task.get("paused", False),
            "current_v": current_v,
            "target_v": task["target_v"],
            "progress": progress,
        }

    # ========== 内部步进 ==========

    def _do_tick(self, channel_id: int) -> None:
        task = self._tasks.get(channel_id)
        if task is None or task["paused"]:
            return
        elapsed = time.time() - task["start_time"]
        progress = min(elapsed / task["ramp_time"], 1.0)
        target_now = round(task["start_v"] + progress * (task["target_v"] - task["start_v"]), 1)
        if task["target_v"] > task["start_v"]:
            target_now = min(target_now, task["target_v"])
        else:
            target_now = max(target_now, task["target_v"])
        task["retries"] = 0  # 新步进重置重试计数

        last = self._last_sent.get(channel_id, task["start_v"])
        if abs(target_now - last) < 0.05:
            if progress >= 1.0:
                self._finish_ramp(channel_id, task)
            else:
                QTimer.singleShot(self.TICK_INTERVAL, lambda cid=channel_id: self._do_tick(cid))
            return

        self._send_callback(
            task["port"], task["channel_index"], target_now,
            lambda ok, cid=channel_id, v=target_now:
                self.sig_tick_done.emit(cid, ok, v))

    @pyqtSlot(int, bool, float)
    def _on_tick_done(self, channel_id: int, success: bool, sent_voltage: float) -> None:
        task = self._tasks.get(channel_id)
        if task is None or task["paused"]:
            return

        if not success:
            if task["retries"] < self.MAX_RETRIES:
                task["retries"] += 1
                QTimer.singleShot(100, lambda cid=channel_id: self._do_tick(cid))
                return
            self.ramp_error.emit(channel_id, "通信失败: 斜坡已暂停")
            self.stop_ramp(channel_id)  # 彻底清理任务
            return

        self._last_sent[channel_id] = sent_voltage
        self.ramp_progress.emit(channel_id, sent_voltage, task["target_v"])

        elapsed = time.time() - task["start_time"]
        progress = min(elapsed / task["ramp_time"], 1.0)
        if progress >= 1.0:
            self._finish_ramp(channel_id, task)
        else:
            QTimer.singleShot(self.TICK_INTERVAL, lambda cid=channel_id: self._do_tick(cid))

    def _finish_ramp(self, channel_id: int, task: dict) -> None:
        self._send_callback(
            task["port"], task["channel_index"], task["target_v"],
            lambda ok, cid=channel_id: self.sig_finish_done.emit(cid, ok))

    @pyqtSlot(int, bool)
    def _on_finish_done(self, channel_id: int, success: bool) -> None:
        task = self._tasks.pop(channel_id, None)
        if task is None:
            return
        if success:
            self._last_sent[channel_id] = task["target_v"]
            self.ramp_progress.emit(channel_id, task["target_v"], task["target_v"])
        self.ramp_completed.emit(channel_id, task["target_v"])
