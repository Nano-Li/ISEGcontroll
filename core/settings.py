"""本地界面偏好：仅预填控件，不执行设备操作。"""

import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


class LocalSettings:
    """配置保存在源码根目录，打包后保存在 EXE 同目录。"""

    def __init__(self, path=None):
        base = (Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)
        self.path = Path(path) if path is not None else base / "config.json"
        self.last_error = ""
        self._data = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("配置根节点必须是对象")
            self._data = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self.last_error = f"本地配置读取失败，已使用默认值：{exc}"

    @property
    def preferred_port(self) -> str:
        value = self._data.get("preferred_port", "")
        if isinstance(value, str) and re.fullmatch(r"COM[1-9][0-9]*", value.strip(), re.I):
            return value.strip().upper()
        return ""

    @staticmethod
    def _number(value, minimum: float, maximum: float, default: float) -> float:
        """拒绝布尔值、非数值、NaN/无穷大和越界值，逐项回退。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        try:
            number = float(value)
        except (ValueError, OverflowError):
            return default
        if math.isfinite(number) and minimum <= number <= maximum:
            return number
        return default

    def channel_params(self, port: str, channel_index: int,
                       v_nominal: float) -> tuple[float, float]:
        params = self._data
        for key in ("channels", port, str(channel_index)):
            params = params.get(key, {}) if isinstance(params, dict) else {}
        if not isinstance(params, dict):
            params = {}
        return (
            self._number(params.get("target_v"), 0.0, v_nominal, 0.0),
            self._number(params.get("speed_vps"), 0.1, 400.0, 10.0),
        )

    def remember_port(self, port: str) -> bool:
        if not re.fullmatch(r"COM[1-9][0-9]*", port, re.I):
            return True
        self._data["preferred_port"] = port.upper()
        return self._save()

    def remember_channel(self, port: str, channel_index: int,
                         target_v: float, speed_vps: float) -> bool:
        # 参数来自已限制范围的界面控件，按端口和硬件通道号保存。
        channels = self._data.get("channels")
        if not isinstance(channels, dict):
            channels = self._data["channels"] = {}
        device = channels.get(port)
        if not isinstance(device, dict):
            device = channels[port] = {}
        device[str(channel_index)] = {"target_v": target_v, "speed_vps": speed_vps}
        return self._save()

    def _save(self) -> bool:
        """先写临时文件再替换，保存失败时保留原文件。"""
        temporary = None
        try:
            content = json.dumps(self._data, ensure_ascii=False, indent=2, allow_nan=False)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content + "\n")
            os.replace(temporary, self.path)
            self.last_error = ""
            return True
        except (OSError, ValueError) as exc:
            self.last_error = f"本地配置保存失败，下次启动可能无法恢复设置：{exc}"
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
