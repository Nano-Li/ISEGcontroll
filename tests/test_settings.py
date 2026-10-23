"""本地偏好回退、持久化和界面集成测试；串口完全模拟。"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from core.settings import LocalSettings
from ui.main_window import MainWindow


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"

    def write_config(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_missing_and_malformed_config_use_defaults(self):
        for content in (None, "{broken", "[]", '"text"'):
            with self.subTest(content=content):
                if content is not None:
                    self.path.write_text(content, encoding="utf-8")
                settings = LocalSettings(self.path)
                self.assertEqual(settings.preferred_port, "")
                self.assertEqual(settings.channel_params("COM8", 1, 4000), (0, 10))

    def test_invalid_fields_fall_back_independently(self):
        for value in (None, "123", True, -1, 5000, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.write_config({"channels": {"COM8": {"1": {
                    "target_v": value, "speed_vps": 3.2,
                }}}})
                self.assertEqual(LocalSettings(self.path).channel_params("COM8", 1, 4000),
                                 (0, 3.2))
        for value in (None, "fast", False, 0, 401, float("nan")):
            self.write_config({"channels": {"COM8": {"1": {
                "target_v": 123.4, "speed_vps": value,
            }}}})
            self.assertEqual(LocalSettings(self.path).channel_params("COM8", 1, 4000),
                             (123.4, 10))

    def test_malformed_nested_settings_and_port(self):
        for channels in ([], None, {"COM8": []}, {"COM8": {"1": []}}):
            self.write_config({"preferred_port": 8, "channels": channels})
            settings = LocalSettings(self.path)
            self.assertEqual(settings.preferred_port, "")
            self.assertEqual(settings.channel_params("COM8", 1, 4000), (0, 10))
            self.assertTrue(settings.remember_channel("COM8", 1, 123.4, 3.2))

    def test_persistence_and_device_isolation(self):
        settings = LocalSettings(self.path)
        self.assertTrue(settings.remember_port("COM8"))
        self.assertTrue(settings.remember_channel("COM8", 1, 123.4, 3.2))
        self.assertTrue(settings.remember_channel("COM8", 2, 234.5, 4.3))
        self.assertTrue(settings.remember_channel("COM9", 1, 345.6, 5.4))
        restored = LocalSettings(self.path)
        self.assertEqual(restored.preferred_port, "COM8")
        self.assertEqual(restored.channel_params("COM8", 1, 4000), (123.4, 3.2))
        self.assertEqual(restored.channel_params("COM8", 2, 4000), (234.5, 4.3))
        self.assertEqual(restored.channel_params("COM9", 1, 4000), (345.6, 5.4))
        self.assertEqual(restored.channel_params("COM8", 1, 100), (0, 3.2))

    def test_failed_save_keeps_previous_file(self):
        settings = LocalSettings(self.path)
        settings.remember_port("COM8")
        before = self.path.read_bytes()
        with patch("core.settings.os.replace", side_effect=PermissionError("只读")):
            self.assertFalse(settings.remember_port("COM9"))
        self.assertTrue(settings.last_error)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_packaged_app_uses_executable_directory(self):
        executable = self.path.parent / "controller.exe"
        with patch("core.settings.sys.frozen", True, create=True), \
                patch("core.settings.sys.executable", str(executable)):
            self.assertEqual(LocalSettings().path, self.path)

    def test_window_restores_and_remembers_without_starting_voltage(self):
        settings = LocalSettings(self.path)
        settings.remember_port("COM8")
        settings.remember_channel("COM8", 1, 123.4, 3.2)
        settings.remember_channel("COM8", 2, 234.5, 4.3)
        with patch("ui.main_window.LocalSettings", side_effect=lambda: LocalSettings(self.path)), \
                patch("ui.main_window.AsyncSerialManager") as serial_class, \
                patch.object(MainWindow, "_setup_auto_log"), \
                patch("ui.main_window.time.sleep"):
            serial = serial_class.return_value
            serial.scan_ports.return_value = ["COM1", "COM8"]
            serial.is_connected.return_value = False
            serial.connect_port.return_value = True
            serial.send_sync.side_effect = lambda port, cmd: (
                "TEST;1.0;4000;305" if cmd.startswith("#") else "0")
            for expected in (123.4, 456.7):
                window = MainWindow()
                try:
                    self.assertEqual(window.device_tree._combo_ports.currentText(), "COM8")
                    serial.connect_port.assert_not_called()
                    before = self.path.read_bytes()
                    window._connect_device("COM8")
                    channels = window.device_manager.get_device("COM8").channels
                    first = window.multi_panel.get_card(channels[0].channel_id)
                    second = window.multi_panel.get_card(channels[1].channel_id)
                    self.assertEqual((first.get_target_v(), first.get_speed_vps()), (expected, 3.2))
                    self.assertEqual((second.get_target_v(), second.get_speed_vps()), (234.5, 4.3))
                    self.assertEqual(self.path.read_bytes(), before)
                    first._spin_vset.setValue(456.7)
                    self.assertEqual(LocalSettings(self.path).channel_params("COM8", 1, 4000),
                                     (456.7, 3.2))
                    serial.write_voltage_async.assert_not_called()
                    self.assertFalse(any(call.args[1].startswith("D") for call in serial.send_sync.call_args_list))
                    serial.scan_ports.return_value = ["COM1"]
                    window._scan_ports()
                    self.assertEqual(window.device_tree._combo_ports.currentText(), "COM1")
                    self.assertEqual(LocalSettings(self.path).preferred_port, "COM8")
                    serial.scan_ports.return_value = ["COM1", "COM8"]
                    window._scan_ports()
                    self.assertEqual(window.device_tree._combo_ports.currentText(), "COM8")
                    serial.scan_ports.return_value = []
                    window._scan_ports()
                    self.assertFalse(window.device_tree._btn_connect.isEnabled())
                    serial.scan_ports.return_value = ["COM1", "COM8"]
                finally:
                    window.close()
                    serial.reset_mock()
            window = MainWindow()
            try:
                window.device_tree._combo_ports.setCurrentText("COM1")
                window.device_tree._combo_ports.activated[str].emit("COM1")
                self.assertEqual(LocalSettings(self.path).preferred_port, "COM1")
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
