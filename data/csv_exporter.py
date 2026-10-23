"""
CSV/Excel 数据导出模块
"""

import os
import csv
import time
from datetime import datetime
from typing import Optional


class CsvExporter:
    """数据导出器 — 支持 CSV 和 Excel 格式"""

    @staticmethod
    def export_csv(filepath: str, headers: list[str],
                   data: dict[int, "DataBuffer"], channel_labels: dict[int, str] = None) -> Optional[str]:
        """
        导出数据为 CSV 文件

        参数:
          filepath: 文件路径
          headers: 列标题列表
          data: {channel_id: DataBuffer} 字典
          channel_labels: {channel_id: "标签"} 用于列名

        返回: 成功返回文件路径, 失败返回 None
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)

                # 写入文件头
                writer.writerow([f"ISEG THQ 数据记录 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
                writer.writerow([])

                # 写入列标题
                column_names = ["时间戳"]
                for channel_id, buf in data.items():
                    label = (channel_labels or {}).get(channel_id, f"CH{channel_id}")
                    column_names.extend([
                        f"{label}_电压(V)",
                        f"{label}_电流(A)",
                        f"{label}_设定电压(V)",
                    ])
                writer.writerow(column_names)

                # 对齐所有通道数据 (按行写入)
                all_timestamps = []
                all_voltages = []
                all_currents = []
                all_v_sets = []

                for channel_id in sorted(data.keys()):
                    buf = data[channel_id]
                    ts, vs, cs, sets = buf.get_data()
                    all_timestamps.append(ts)
                    all_voltages.append(vs)
                    all_currents.append(cs)
                    all_v_sets.append(sets)

                # 找出最大行数
                max_rows = max((len(ts) for ts in all_timestamps), default=0)

                # 写入数据行
                for row_idx in range(max_rows):
                    row = []
                    # 使用第一个通道的时间戳
                    if all_timestamps and row_idx < len(all_timestamps[0]):
                        t = all_timestamps[0][row_idx]
                        row.append(datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
                    else:
                        row.append("")

                    for ch_idx in range(len(data)):
                        row.append(
                            f"{all_voltages[ch_idx][row_idx]:.3f}"
                            if ch_idx < len(all_voltages) and row_idx < len(all_voltages[ch_idx])
                            else ""
                        )
                        row.append(
                            f"{all_currents[ch_idx][row_idx]:.6e}"
                            if ch_idx < len(all_currents) and row_idx < len(all_currents[ch_idx])
                            else ""
                        )
                        row.append(
                            f"{all_v_sets[ch_idx][row_idx]:.1f}"
                            if ch_idx < len(all_v_sets) and row_idx < len(all_v_sets[ch_idx])
                            else ""
                        )
                    writer.writerow(row)

            return filepath

        except Exception as e:
            print(f"CSV导出失败: {e}")
            return None

    @staticmethod
    def export_excel(filepath: str, headers: list[str],
                     data: dict[int, "DataBuffer"], channel_labels: dict[int, str] = None) -> Optional[str]:
        """
        导出数据为 Excel (.xlsx) 文件
        需要 openpyxl 库
        """
        try:
            import openpyxl
        except ImportError:
            return CsvExporter.export_csv(filepath.replace(".xlsx", ".csv"), headers, data, channel_labels)

        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "数据记录"

            # 标题行
            ws.cell(row=1, column=1, value=f"ISEG THQ 数据记录 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # 列标题行
            row = 3
            col = 1
            ws.cell(row=row, column=col, value="时间戳"); col += 1
            for channel_id in sorted(data.keys()):
                label = (channel_labels or {}).get(channel_id, f"CH{channel_id}")
                ws.cell(row=row, column=col, value=f"{label}_电压(V)"); col += 1
                ws.cell(row=row, column=col, value=f"{label}_电流(A)"); col += 1
                ws.cell(row=row, column=col, value=f"{label}_设定电压(V)"); col += 1

            # 数据行
            buffers = [data[cid].get_data() for cid in sorted(data.keys())]
            all_ts = [b[0] for b in buffers]
            all_vs = [b[1] for b in buffers]
            all_cs = [b[2] for b in buffers]
            all_sets = [b[3] for b in buffers]
            max_rows = max((len(ts) for ts in all_ts), default=0)

            for data_row in range(max_rows):
                row = 4 + data_row
                col = 1
                if all_ts and data_row < len(all_ts[0]):
                    ws.cell(row=row, column=col,
                            value=datetime.fromtimestamp(all_ts[0][data_row]).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
                col += 1
                for ch_idx in range(len(buffers)):
                    if data_row < len(all_vs[ch_idx]):
                        ws.cell(row=row, column=col, value=round(all_vs[ch_idx][data_row], 3))
                    col += 1
                    if data_row < len(all_cs[ch_idx]):
                        ws.cell(row=row, column=col, value=all_cs[ch_idx][data_row])
                    col += 1
                    if data_row < len(all_sets[ch_idx]):
                        ws.cell(row=row, column=col, value=round(all_sets[ch_idx][data_row], 1))
                    col += 1

            wb.save(filepath)
            return filepath

        except Exception as e:
            print(f"Excel导出失败: {e}")
            return None
