#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
laser_receiver.py —— 激光测距上位机（收距离数据 + 实时曲线 + 存 CSV）

配套单片机工程：本目录上一级的 Keil 工程（STM32F103 + VL53L0X）。
单片机通过 USART1（PA9=TX）以 9600 8N1 持续输出测距结果，
本程序负责接收、显示、画曲线，并可把数据存成 Excel 能直接打开的 CSV。

--------------------------------------------------------------------------
单片机发出的数据格式（每完成一次测量发一行，CR+LF 结束）
    F=450 R=452 S=0

    F = 滤波后的距离(mm)     R = 本次原始距离(mm)     S = RangeStatus
    S=0 本次有效；S=1 标准差过大；S=2 回波太弱；S=3 目标太近(<30mm)；
    S=4 相位超范围；S=5 硬件故障。只有 S=0 才算有效距离，其它只记录原因。

    开机 / 出错 / 命令回显等说明行不符合上面的格式，本程序只在下方日志里
    显示，不参与统计。例如：
        VL53L0X Ranging
        Budget=100ms Period=120ms Sigma=15mm
        Start OK, about 8 Hz
        Init failed: ... / Config failed: ... / Start failed: ... / Err=...
        Restarted

    兼容旧固件格式（仍然能解析，方便换板子时复用）：
        [DIST],dist=1234,mm=1234,status=11,quality=GOOD,alarm=0,err=0,raw=1234,seq=12
        [ERR],dist=----,mm=----,tag=NO_TARGET,status=11,quality=BAD,alarm=3,err=5,raw=8190,seq=13
        Dist: 1234mm status=11 quality=0
    以 '#' 开头的行是说明信息，本程序自动忽略。

--------------------------------------------------------------------------
环境准备（只需一次）
    pip install pyserial

运行
    图形界面（推荐）：   python laser_receiver.py
    指定串口和波特率：   python laser_receiver.py --port COM5 --baud 9600
    只看有哪些串口：     python laser_receiver.py --list
    无界面存数据：       python laser_receiver.py --port COM5 --no-gui --csv data.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime

# --------------------------------------------------------------------------
# 依赖检查：pyserial 缺失时给出人话提示（tkinter 由 Python 自带）
# --------------------------------------------------------------------------
try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "\n[错误] 缺少 pyserial 库。请先安装：\n"
        "       pip install pyserial\n"
        "       若使用国内网络，可加镜像：\n"
        "       pip install pyserial -i https://pypi.tuna.tsinghua.edu.cn/simple\n\n"
    )
    raise SystemExit(2)

APP_TITLE = "激光测距上位机 - VL53L0X 距离数据接收"
DEFAULT_BAUD = 9600
MAX_POINTS = 400          # 曲线最多显示最近多少个点
STATS_WINDOW = 50         # 滑动平均用最近多少个有效数据

OBSTACLE_DROP_MM = 30     # 距离比上一帧下降超过这个值 -> 判为有障碍物
OBSTACLE_CLEAR_MM = 30    # 距离从最低点回升超过这个值 -> 解除提示

ALARM_TEXT = {0: "正常", 1: "注意", 2: "警告", 3: "危险"}
ALARM_COLOR = {0: "#1b8a3a", 1: "#b8860b", 2: "#d2691e", 3: "#c62828"}
QUALITY_TEXT = {"GOOD": "良好", "WEAK": "偏弱", "BAD": "无效"}

# 本工程固件的 RangeStatus -> (标签, 质量)
#   0 有效 / 1 标准差过大 / 2 回波太弱 / 3 目标太近 / 4 相位超范围 / 5 硬件故障
RANGE_STATUS_INFO = {
    0: ("OK", "GOOD"),
    1: ("SIGMA_FAIL", "WEAK"),
    2: ("SIGNAL_WEAK", "WEAK"),
    3: ("MIN_RANGE", "BAD"),
    4: ("PHASE_FAIL", "BAD"),
    5: ("DEVICE_HW", "BAD"),
}

# 固件一行数据：F=450 R=452 S=0（等号两边允许有空格）
FRAME_RE = re.compile(r"^F\s*=\s*(\d+)\s+R\s*=\s*(\d+)\s+S\s*=\s*(\d+)\s*$")


# ==========================================================================
# 1. 数据解析
# ==========================================================================
class Record:
    """一条解析后的测距结果。"""

    __slots__ = ("time", "mono", "tag", "kind", "distance_mm", "status", "quality",
                 "alarm", "err", "raw", "seq", "text")

    def __init__(self, time_s, mono_s, tag, kind, distance_mm, status, quality,
                 alarm, err, raw, seq, text):
        self.time = time_s
        self.mono = mono_s
        self.tag = tag
        self.kind = kind
        self.distance_mm = distance_mm
        self.status = status
        self.quality = quality
        self.alarm = alarm
        self.err = err
        self.raw = raw
        self.seq = seq
        self.text = text

    @property
    def valid(self) -> bool:
        return self.kind == "DIST" and self.distance_mm is not None


def _to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_dist(value):
    """'1234' -> 1234；'----' 或 'mm=----' -> None。"""
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.startswith("-"):
        return None
    return _to_int(value, None)


def parse_line(line: str, mono_s: float):
    """
    解析一行文本，返回 Record 或 None（说明行 / 空行 / 不认识的格式）。

    三种格式都支持：
        本工程：F=450 R=452 S=0
        新：[DIST],dist=1234,mm=1234,status=...
        旧：Dist: 1234mm status=11 quality=0（早期版本固件）
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    now = datetime.now()

    # ---------- 本工程固件：F=450 R=452 S=0 ----------
    m = FRAME_RE.match(line)
    if m:
        dist = int(m.group(1))
        raw = int(m.group(2))
        status = int(m.group(3))
        tag, quality = RANGE_STATUS_INFO.get(status, ("UNKNOWN_STATUS", "BAD"))
        valid = status == 0
        return Record(
            time_s=now, mono_s=mono_s,
            kind="DIST" if valid else "ERR",
            tag="OK" if valid else tag,
            # S!=0 表示器件自己判了这次测量不合格，距离不可信 -> 按无效帧处理
            distance_mm=dist if valid else None,
            status=status, quality=quality,
            alarm=0, err=0, raw=raw, seq=None, text=line,
        )

    # ---------- 新格式：[DIST] / [ERR] ----------
    if line.startswith("[") and "]" in line:
        head, _, rest = line.partition("]")
        kind = head[1:].strip().upper()
        if kind not in ("DIST", "ERR"):
            return None

        fields = {}
        for item in rest.lstrip(",").split(","):
            if "=" in item:
                key, _, value = item.partition("=")
                fields[key.strip().lower()] = value.strip()

        return Record(
            time_s=now, mono_s=mono_s, kind=kind,
            tag=fields.get("tag", "OK" if kind == "DIST" else "UNKNOWN"),
            distance_mm=_to_dist(fields.get("dist", fields.get("mm"))),
            status=_to_int(fields.get("status")),
            quality=fields.get("quality", "BAD").upper(),
            alarm=_to_int(fields.get("alarm"), 0),
            err=_to_int(fields.get("err"), 0),
            raw=_to_int(fields.get("raw")),
            seq=_to_int(fields.get("seq")),
            text=line,
        )

    # ---------- 旧格式：Dist: 1234mm status=11 quality=0 ----------
    if line.startswith("Dist:"):
        body = line[5:].strip()
        num, _, tail = body.partition("mm")
        distance = _to_dist(num.strip())
        status = None
        quality = None
        for item in tail.replace("  ", " ").split():
            if item.startswith("status="):
                status = _to_int(item[7:])
            elif item.startswith("quality="):
                q = _to_int(item[8:])
                quality = {0: "GOOD", 1: "WEAK", 2: "BAD"}.get(q, "BAD")
        return Record(
            time_s=now, mono_s=mono_s,
            kind="DIST" if distance is not None else "ERR",
            tag="OK" if distance is not None else "UNKNOWN",
            distance_mm=distance, status=status, quality=quality or "BAD",
            alarm=0, err=0, raw=distance, seq=None, text=line,
        )

    return None


# ==========================================================================
# 2. 串口读取线程
# ==========================================================================
class SerialReader(threading.Thread):
    """后台读串口，把原始行丢进队列；主线程负责解析，避免跨线程碰界面。

    stream 参数只用于自测：传一个带 read() 的对象（例如 BytesIO）就能在
    没有真实硬件的情况下跑通"收字节 -> 切行 -> 队列 -> 解析"整条链路。
    """

    def __init__(self, port, baud, out_queue: queue.Queue, stream=None):
        super().__init__(daemon=True, name="serial-reader")
        self.port = port
        self.baud = baud
        self.queue = out_queue
        self.stream = stream
        self._stop = threading.Event()
        self.ser = None
        self.error = None

    def _read(self, size):
        if self.stream is not None:
            return self.stream.read(size)
        return self.ser.read(size)

    def run(self):
        if self.stream is None:
            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.2,
                )
            except Exception as exc:                  # 端口占用 / 拔掉 / 驱动缺失
                self.error = "打开串口失败: %s" % exc
                return

        self.queue.put(("status", "已连接 %s @ %d 8N1" % (self.port, self.baud)))

        buf = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._read(256)
                except Exception as exc:
                    self.queue.put(("error", "读取中断: %s" % exc))
                    break
                if not chunk:
                    if self.stream is not None:
                        break                         # 自测流读完了就结束
                    continue
                buf.extend(chunk)
                # 固件严格用 CR+LF 结尾，这里也按 LF 兜底
                while True:
                    idx = buf.find(b"\n")
                    if idx < 0:
                        if len(buf) > 4096:          # 收到垃圾数据不无限增长
                            del buf[:-256]
                        break
                    raw = bytes(buf[:idx])
                    del buf[:idx + 1]
                    text = raw.decode("ascii", errors="replace").strip("\r\n \t")
                    if text:
                        self.queue.put(("line", text))
        finally:
            try:
                if self.ser is not None and self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass
            self.queue.put(("status", "已断开"))

    def stop(self):
        self._stop.set()


# ==========================================================================
# 3. 无界面模式：直接存 CSV
# ==========================================================================
def run_headless(port, baud, csv_path, duration):
    """不做界面，只把数据写进 CSV；Ctrl+C 结束。"""
    out_queue: queue.Queue = queue.Queue()
    reader = SerialReader(port, baud, out_queue)
    reader.start()

    start = time.monotonic()
    file_obj = open(csv_path, "w", newline="", encoding="utf-8-sig")
    writer = csv.writer(file_obj)
    writer.writerow(CSV_HEADER)
    file_obj.flush()
    print("正在接收 %s @ %d，数据写入 %s（Ctrl+C 结束）" % (port, baud, csv_path))

    count = 0
    try:
        while True:
            if duration and (time.monotonic() - start) >= duration:
                break
            try:
                kind, payload = out_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "status":
                print("[%s]" % payload)
            elif kind == "error":
                print("[%s]" % payload, file=sys.stderr)
            else:
                rec = parse_line(payload, time.monotonic() - start)
                if rec is None:
                    continue
                writer.writerow(record_to_row(rec))
                count += 1
                if count % 10 == 0:
                    file_obj.flush()
                if rec.valid:
                    print("t=%7.3fs  dist=%5d mm  quality=%-4s alarm=%s"
                          % (rec.mono, rec.distance_mm, rec.quality,
                             ALARM_TEXT.get(rec.alarm, rec.alarm)))
                else:
                    print("t=%7.3fs  ---- (tag=%s err=%s)" % (rec.mono, rec.tag, rec.err))
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止接收。")
    finally:
        reader.stop()
        reader.join(timeout=1.0)
        file_obj.close()
        print("共记录 %d 条数据 -> %s" % (count, csv_path))


CSV_HEADER = ["时间", "运行秒", "距离mm", "状态码", "质量", "预警等级", "预警",
              "错误码", "原始值", "序号", "标签", "有效", "原始行"]


def record_to_row(rec: Record):
    return [
        rec.time.strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (rec.time.microsecond // 1000),
        "%.3f" % rec.mono,
        "" if rec.distance_mm is None else rec.distance_mm,
        "" if rec.status is None else rec.status,
        rec.quality,
        rec.alarm,
        ALARM_TEXT.get(rec.alarm, rec.alarm),
        rec.err,
        "" if rec.raw is None else rec.raw,
        "" if rec.seq is None else rec.seq,
        rec.tag,
        1 if rec.valid else 0,
        rec.text,
    ]


# ==========================================================================
# 4. 图形界面
# ==========================================================================
class ReceiverApp:
    def __init__(self, root, port=None, baud=DEFAULT_BAUD, csv_path=None, auto_start=False):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1060x700")
        root.minsize(900, 580)

        self.queue: queue.Queue = queue.Queue()
        self.reader: SerialReader | None = None
        self.records = deque(maxlen=MAX_POINTS)     # 曲线用
        self.valid_window = deque(maxlen=STATS_WINDOW)
        self.count_total = 0
        self.count_valid = 0
        self.count_err = 0
        self.last_seq = None
        self.lost_frames = 0
        self.min_mm = None
        self.max_mm = None
        self.sum_mm = 0
        self.csv_file = None
        self.csv_writer = None
        self.csv_rows = 0
        self.csv_path = csv_path
        self._redraw_pending = False
        self.t0 = time.monotonic()

        # 障碍物提示状态：距离比上一有效帧下降 >30mm 点亮，回升 >30mm 熄灭
        self.prev_valid_mm = None
        self.obstacle_active = False
        self.obstacle_base = 0        # 触发那一刻的基准距离
        self.obstacle_ref = 0         # 提示期间出现过的最小距离

        self._build_ui(port, baud)
        self.root.after(50, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if auto_start and port:
            self.root.after(300, self.toggle_connect)

    # ------------------------------ 界面搭建 ------------------------------
    def _build_ui(self, port, baud):
        tk, ttk = self.tk, self.ttk
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # ---- 顶部工具条 ----
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="串口:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=port or "")
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=14, values=[])
        self.port_box.pack(side=tk.LEFT, padx=(2, 4))

        ttk.Button(bar, text="刷新", width=6, command=self.refresh_ports).pack(side=tk.LEFT)

        ttk.Label(bar, text="波特率:").pack(side=tk.LEFT, padx=(10, 0))
        self.baud_var = tk.StringVar(value=str(baud))
        ttk.Combobox(bar, textvariable=self.baud_var, width=9,
                     values=("9600", "19200", "38400", "57600", "115200", "230400", "460800")
                     ).pack(side=tk.LEFT, padx=(2, 8))

        self.connect_btn = ttk.Button(bar, text="连接", width=9, command=self.toggle_connect)
        self.connect_btn.pack(side=tk.LEFT)

        self.record_btn = ttk.Button(bar, text="开始记录", width=10, command=self.toggle_record)
        self.record_btn.pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(bar, text="报警阈值(mm):").pack(side=tk.LEFT, padx=(10, 0))
        self.alarm_var = tk.StringVar(value="100")
        ttk.Spinbox(bar, from_=0, to=4000, increment=10, width=6,
                    textvariable=self.alarm_var).pack(side=tk.LEFT, padx=(2, 0))
        self.beep_var = tk.IntVar(value=0)
        ttk.Checkbutton(bar, text="声音提示", variable=self.beep_var).pack(side=tk.LEFT, padx=(8, 0))

        # ---- 距离大字 + 状态卡片 ----
        top = ttk.Frame(self.root, padding=(8, 2))
        top.pack(side=tk.TOP, fill=tk.X)

        dist_box = ttk.LabelFrame(top, text="当前距离", padding=(12, 6))
        dist_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)
        self.dist_label = tk.Label(dist_box, text="----", font=("Consolas", 46, "bold"),
                                   fg="#444444", width=6, anchor="e")
        self.dist_label.pack(side=tk.LEFT)
        tk.Label(dist_box, text="mm", font=("Microsoft YaHei", 14)).pack(side=tk.LEFT,
                                                                        anchor="s", pady=(0, 10))

        info = ttk.LabelFrame(top, text="本次结果", padding=(10, 6))
        info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.info_label = tk.Label(info, text="等待数据……", font=("Microsoft YaHei", 13),
                                   justify=tk.LEFT, anchor="w")
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ---- 统计 ----
        stats = ttk.LabelFrame(self.root, text="统计", padding=(8, 6))
        stats.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(4, 0))
        self.stat_labels = {}
        names = [("total", "收到帧"), ("valid", "有效距离"), ("err", "无效/错误"),
                 ("lost", "丢帧"), ("cur", "最近50帧均值"), ("min", "最小"),
                 ("max", "最大"), ("rate", "帧率")]
        for i, (key, text) in enumerate(names):
            cell = ttk.Frame(stats)
            cell.grid(row=0, column=i, padx=10, sticky="w")
            ttk.Label(cell, text=text, foreground="#666666").pack(anchor="w")
            lbl = tk.Label(cell, text="-", font=("Consolas", 14, "bold"))
            lbl.pack(anchor="w")
            self.stat_labels[key] = lbl

        # ---- 曲线 ----
        plot_box = ttk.LabelFrame(self.root, text="距离曲线（最近 %d 个点）" % MAX_POINTS,
                                  padding=(6, 4))
        plot_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(4, 0))
        self.canvas = tk.Canvas(plot_box, background="#fbfbfb", highlightthickness=1,
                                highlightbackground="#cccccc")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._request_redraw())

        # ---- 底部：原始数据 ----
        bottom = ttk.Frame(self.root, padding=(8, 4))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bottom, text="状态:").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="未连接 —— 请选择串口后点【连接】")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#0b5394").pack(
            side=tk.LEFT, padx=(4, 0))

        self.log_text = tk.Text(self.root, height=6, font=("Consolas", 9), wrap="none")
        self.log_text.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))
        self.log_text.configure(state="disabled")

        self.refresh_ports()

    # ------------------------------ 串口操作 ------------------------------
    def refresh_ports(self):
        ports = []
        try:
            for p in list_ports.comports():
                ports.append(p.device)
        except Exception:
            pass
        ports.sort()
        self.port_box.configure(values=ports)
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
        if not ports:
            self.status_var.set("没有检测到串口 —— 请插好 USB 转 TTL 模块后点【刷新】")

    def toggle_connect(self):
        if self.reader is not None:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get().strip()
        if not port:
            self.status_var.set("请先选择串口（点【刷新】可重新扫描）")
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            baud = DEFAULT_BAUD

        self.t0 = time.monotonic()
        self.reader = SerialReader(port, baud, self.queue)
        self.reader.start()
        self.connect_btn.configure(text="断开")
        self.status_var.set("正在打开 %s ……" % port)

    def disconnect(self):
        if self.reader is not None:
            self.reader.stop()
            self.reader.join(timeout=1.0)
            self.reader = None
        self.connect_btn.configure(text="连接")
        self.status_var.set("已断开")

    # ------------------------------ CSV 记录 ------------------------------
    def toggle_record(self):
        if self.csv_file is not None:
            self._close_csv()
            return

        path = self.csv_path
        if not path:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                title="保存测距数据",
                defaultextension=".csv",
                initialfile="laser_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S"),
                filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            )
        if not path:
            return
        self.csv_path = path
        self.csv_file = open(path, "w", newline="", encoding="utf-8-sig")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(CSV_HEADER)
        self.csv_file.flush()
        self.csv_rows = 0
        self.record_btn.configure(text="停止记录")
        self.status_var.set("正在记录 -> %s" % path)

    def _close_csv(self):
        if self.csv_file is not None:
            try:
                self.csv_file.flush()
                self.csv_file.close()
            except Exception:
                pass
            self.csv_file = None
            self.csv_writer = None
        self.record_btn.configure(text="开始记录")
        self.status_var.set("已停止记录，共 %d 条 -> %s" % (self.csv_rows, self.csv_path))

    # ------------------------------ 主循环 ------------------------------
    def _pump(self):
        """每 50ms 处理一次队列：解析数据、刷新界面、写 CSV。"""
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "error":
                    self.status_var.set(payload)
                else:
                    self._handle_line(payload)
        except queue.Empty:
            pass
        except Exception as exc:                       # 解析异常不能让界面崩掉
            self.status_var.set("解析出错: %s" % exc)

        if self.reader is not None and not self.reader.is_alive():
            err = getattr(self.reader, "error", None)
            if err:
                self.status_var.set(err)
            self.reader = None
            self.connect_btn.configure(text="连接")

        self._refresh_stats()
        if self._redraw_pending:
            self._redraw_pending = False
            self._draw_plot()

        self.root.after(50, self._pump)

    def _handle_line(self, line: str):
        rec = parse_line(line, time.monotonic() - self.t0)
        if rec is None:
            self._log(line)
            return

        self.count_total += 1
        if rec.valid:
            self.count_valid += 1
            self.records.append(rec)
            self.valid_window.append(rec.distance_mm)
            self.sum_mm += rec.distance_mm
            if self.min_mm is None or rec.distance_mm < self.min_mm:
                self.min_mm = rec.distance_mm
            if self.max_mm is None or rec.distance_mm > self.max_mm:
                self.max_mm = rec.distance_mm
        else:
            self.count_err += 1

        if rec.seq is not None:
            if self.last_seq is not None and rec.seq > self.last_seq + 1:
                self.lost_frames += rec.seq - self.last_seq - 1
            self.last_seq = rec.seq

        # CSV：有效和无效都记录，方便事后分析"为什么没数据"
        if self.csv_writer is not None:
            try:
                self.csv_writer.writerow(record_to_row(rec))
                self.csv_rows += 1
                if self.csv_rows % 20 == 0:
                    self.csv_file.flush()
            except Exception as exc:
                self.status_var.set("写 CSV 出错: %s" % exc)
                self._close_csv()

        self._track_obstacle(rec)
        self._update_big_number(rec)
        self._log(line)
        self._request_redraw()

        # 距离过近时可选声音提示
        if (self.beep_var.get() and rec.valid
                and rec.distance_mm is not None
                and rec.distance_mm <= self._alarm_threshold()):
            try:
                self.root.bell()
            except Exception:
                pass

    def _alarm_threshold(self):
        try:
            return int(self.alarm_var.get())
        except (TypeError, ValueError):
            return 0

    def _track_obstacle(self, rec: Record):
        """距离比上一有效帧下降超过 30mm -> 判定本次有障碍物。

        提示会一直保留（每帧都显示），直到距离从这期间的最低点回升超过
        30mm 才解除；距离变大（远离）本身不会触发。
        """
        if not rec.valid or rec.distance_mm is None:
            return
        cur = rec.distance_mm
        prev = self.prev_valid_mm
        self.prev_valid_mm = cur

        if not self.obstacle_active:
            if prev is not None and prev - cur > OBSTACLE_DROP_MM:
                self.obstacle_active = True
                self.obstacle_base = prev
                self.obstacle_ref = cur
            return

        if cur < self.obstacle_ref:
            self.obstacle_ref = cur
        if cur > self.obstacle_ref + OBSTACLE_CLEAR_MM:
            self.obstacle_active = False

    def _update_big_number(self, rec: Record):
        if rec.valid:
            alarm = rec.alarm if rec.alarm is not None else 0
            color = ALARM_COLOR.get(alarm, "#333333")
            self.dist_label.configure(text=str(rec.distance_mm), fg=color)
            extra = []
            if rec.quality == "WEAK":
                extra.append("回波偏弱，数据仅供参考")
            elif rec.quality == "BAD":
                extra.append("数据不可信，不应参与判断")
            if alarm:
                extra.append("预警：%s" % ALARM_TEXT.get(alarm, alarm))
            if rec.tag and rec.tag != "OK":
                extra.append("标签：%s" % rec.tag)
            info = "状态码 %s   质量 %s   预警 %s\n%s" % (
                rec.status, QUALITY_TEXT.get(rec.quality, rec.quality),
                ALARM_TEXT.get(alarm, alarm),
                "   ".join(extra) if extra else "数据正常",
            )
        else:
            color = "#c62828"
            self.dist_label.configure(text="----", fg=color)
            info = ("本次没有有效距离\n原因标签：%s（错误码 %s，状态码 %s）\n%s"
                    % (rec.tag, rec.err, rec.status, describe_tag(rec.tag)))

        if self.obstacle_active:
            info += "\n■ 有障碍物（距离下降 %d mm）" % (
                self.obstacle_base - self.obstacle_ref)

        self.info_label.configure(text=info, fg=color)

    def _refresh_stats(self):
        self.stat_labels["total"].configure(text=str(self.count_total))
        self.stat_labels["valid"].configure(text=str(self.count_valid))
        self.stat_labels["err"].configure(text=str(self.count_err))
        self.stat_labels["lost"].configure(text=str(self.lost_frames))
        if self.valid_window:
            avg = sum(self.valid_window) / len(self.valid_window)
            self.stat_labels["cur"].configure(text="%.1f" % avg)
        self.stat_labels["min"].configure(text="-" if self.min_mm is None else str(self.min_mm))
        self.stat_labels["max"].configure(text="-" if self.max_mm is None else str(self.max_mm))
        elapsed = max(1e-6, time.monotonic() - self.t0)
        self.stat_labels["rate"].configure(text="%.1f/s" % (self.count_total / elapsed))

    def _log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        # 只保留最近 500 行
        if int(self.log_text.index("end-1c").split(".")[0]) > 500:
            self.log_text.delete("1.0", "100.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------ 绘图 ------------------------------
    def _request_redraw(self):
        self._redraw_pending = True

    def _draw_plot(self):
        cv = self.canvas
        w = max(1, cv.winfo_width())
        h = max(1, cv.winfo_height())
        cv.delete("all")

        left, right, top, bottom = 62, 14, 12, 24
        x0, x1 = left, w - right
        y0, y1 = top, h - bottom
        if x1 - x0 < 40 or y1 - y0 < 40:
            return

        cv.create_rectangle(x0, y0, x1, y1, outline="#bbbbbb")

        pts = [r.distance_mm for r in self.records if r.distance_mm is not None]
        if not pts:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="等待距离数据……",
                           fill="#999999", font=("Microsoft YaHei", 12))
            return

        lo, hi = min(pts), max(pts)
        if hi - lo < 20:                     # 数据很平时给一个可视的最小量程
            mid = (hi + lo) / 2.0
            lo, hi = mid - 10, mid + 10
        pad = (hi - lo) * 0.1
        lo = max(0.0, lo - pad)
        hi = hi + pad

        # Y 轴刻度与网格
        ticks = 5
        for i in range(ticks + 1):
            v = lo + (hi - lo) * i / ticks
            y = y1 - (y1 - y0) * i / ticks
            cv.create_line(x0, y, x1, y, fill="#eeeeee")
            cv.create_text(x0 - 6, y, text="%d" % round(v), anchor="e",
                           fill="#666666", font=("Consolas", 9))
        cv.create_text(x0 - 6, y0 - 2, text="mm", anchor="e",
                       fill="#666666", font=("Consolas", 9))

        # X 轴刻度（按点序号）
        n = len(pts)
        for i in range(0, 6):
            frac = i / 5.0
            x = x0 + (x1 - x0) * frac
            cv.create_line(x, y1, x, y1 + 4, fill="#999999")
            cv.create_text(x, y1 + 14, text=str(int(round(n * frac))), fill="#666666",
                           font=("Consolas", 9))

        def to_xy(index, value):
            x = x0 if n <= 1 else x0 + (x1 - x0) * index / (n - 1)
            y = y1 - (y1 - y0) * (value - lo) / (hi - lo)
            return x, y

        # 折线 + 数据点；预警等级高的点用红色
        for i, rec in enumerate(self.records):
            if rec.distance_mm is None:
                # 无有效距离的那一帧：画一条红色竖线标出来
                x = x0 if n <= 1 else x0 + (x1 - x0) * i / (n - 1)
                cv.create_line(x, y0, x, y1, fill="#f2b8b8")

        coords = []
        for i, rec in enumerate(self.records):
            if rec.distance_mm is None:
                continue
            coords.extend(to_xy(i, rec.distance_mm))
        if len(coords) >= 4:
            cv.create_line(*coords, fill="#1a73e8", width=2, smooth=False)

        for i, rec in enumerate(self.records):
            if rec.distance_mm is None:
                continue
            x, y = to_xy(i, rec.distance_mm)
            alarm = rec.alarm or 0
            if alarm >= 2:
                cv.create_oval(x - 3, y - 3, x + 3, y + 3,
                               fill=ALARM_COLOR.get(alarm, "#c62828"), outline="")
            elif i == len(self.records) - 1:
                cv.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#0b5394", outline="")

        # 最近一个点的数值
        last = self.records[-1]
        if last.distance_mm is not None:
            x, y = to_xy(len(self.records) - 1, last.distance_mm)
            cv.create_text(min(x + 8, x1 - 30), max(y - 12, y0 + 10),
                           text="%d mm" % last.distance_mm, anchor="w",
                           fill="#0b5394", font=("Consolas", 10, "bold"))

    # ------------------------------ 收尾 ------------------------------
    def _on_close(self):
        self.disconnect()
        if self.csv_file is not None:
            self._close_csv()
        self.root.destroy()


TAG_HELP = {
    "SIGMA_FAIL": "距离抖动过大：本次标准差超过 15mm 上限，换个角度或等目标稳定再测",
    "MIN_RANGE": "目标太近：VL53L0X 最近约 30mm，贴太近测不出来",
    "UNKNOWN_STATUS": "器件返回了本程序不认识的 RangeStatus，检查固件版本",
    "NO_TARGET": "没有收到有效回波：目标太远或反射太弱，换白纸、距离 30~1000mm 试试",
    "SIGNAL_WEAK": "回波太弱：目标太暗/太远，或环境光太强",
    "PHASE_FAIL": "相位不一致：测量环境干扰大，重试即可",
    "OUT_OF_RANGE": "超出量程：目标太远或贴得太近（VL53L0X 最近约 30mm）",
    "DATA_INVALID": "数据被判为无效，属于噪声",
    "TIMEOUT": "测距超时：模块没在规定时间内完成一次测量",
    "I2C_ERR": "I2C 通信错误：检查 PA0/PA1 接线、上拉电阻、共地",
    "NO_DEVICE": "器件无应答：模块供电/共地/焊接问题",
    "NO_ACK": "总线电气正常但模块不应答：先量 3.3V 供电和 GND",
    "ID_MISMATCH": "读到的芯片 ID 不是 0xEE：可能不是 VL53L0X 或芯片损坏",
    "DEVICE_HW": "模块内部硬件故障（VCSEL/信号）：模块可能已损坏",
    "BUS_SDA_LOW": "SDA 一直被拉低：焊锡搭到地或模块把线拉死",
    "BUS_SCL_LOW": "SCL 一直被拉低：同上",
    "NO_PULLUP_SDA": "SDA 释放后回不到高：上拉电阻没焊 / 走线开路 / 虚焊",
    "NO_PULLUP_SCL": "SCL 释放后回不到高：同上",
    "BUS_SHORTED": "SDA 与 SCL 被短接：检查焊锡搭桥",
    "PARAM_ERR": "参数错误（代码问题，不是硬件）",
}


def describe_tag(tag):
    if not tag:
        return ""
    return TAG_HELP.get(tag.upper(), "")


# ==========================================================================
# 5. 命令行入口
# ==========================================================================
def list_serial_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("没有检测到任何串口。请确认 USB 转 TTL 模块已插好、驱动已安装。")
        return
    print("检测到以下串口：")
    for p in ports:
        print("  %-10s %s" % (p.device, p.description))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="VL53L0X 激光测距上位机：接收 STM32 串口输出的距离数据，实时显示并存 CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-p", "--port", help="串口名，例如 COM5 或 /dev/ttyUSB0")
    parser.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD, help="波特率，默认 9600")
    parser.add_argument("-c", "--csv", help="CSV 保存路径（给了就自动开始记录）")
    parser.add_argument("--list", action="store_true", help="列出所有可用串口后退出")
    parser.add_argument("--no-gui", action="store_true", help="不使用图形界面，只在终端打印/存盘")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="无界面模式下接收多少秒后自动停止（0=一直收）")
    args = parser.parse_args(argv)

    if args.list:
        list_serial_ports()
        return 0

    if args.no_gui:
        port = args.port
        if not port:
            ports = [p.device for p in list_ports.comports()]
            if not ports:
                sys.stderr.write("没有检测到串口，请用 --port 指定。\n")
                return 2
            port = ports[0]
            print("未指定 --port，自动使用 %s" % port)
        csv_path = args.csv or ("laser_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        run_headless(port, args.baud, csv_path, args.duration)
        return 0

    try:
        import tkinter as tk
    except ImportError:
        sys.stderr.write(
            "[错误] 当前 Python 没有 tkinter，无法显示图形界面。\n"
            "       可改用无界面模式：python laser_receiver.py --no-gui --port COM5 --csv data.csv\n"
        )
        return 2

    root = tk.Tk()
    app = ReceiverApp(root, port=args.port, baud=args.baud, csv_path=args.csv,
                      auto_start=bool(args.port))
    if args.csv:
        # 命令行指定了 CSV：连上后自动开始记录
        root.after(600, app.toggle_record)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
