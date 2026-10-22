# -*- coding: utf-8 -*-
"""端到端自测：用一段"固件真实输出"的字节流，跑通整条链路。

覆盖内容：
    1. SerialReader 读字节 -> 按 LF 切行 -> 进队列（与真实串口同一套代码路径）
    2. ReceiverApp 的解析、统计、CSV 落盘
    3. "距离下降超过 30mm -> 本次结果显示有障碍物 -> 回升 30mm 后解除" 的逻辑
    4. Tkinter 界面能否真正创建、曲线能否绘制（不需要真实串口/硬件）

用法： python pc_tool/test_end_to_end.py
"""

import csv
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laser_receiver as lr  # noqa: E402


def make_stream(n=60, with_errors=True):
    """造一段看起来像真实模块输出的字节流（与 User/main.c 的输出格式一致）。"""
    out = bytearray()
    out += b"\r\nVL53L0X Ranging\r\n"
    out += b"Budget=100ms Period=120ms Sigma=15mm\r\n"
    out += b"Start OK, about 8 Hz\r\n"
    for i in range(n):
        base = 400 + 6 * (i % 20)
        if with_errors and i in (10, 11, 30):
            out += ("F=%d R=%d S=2\r\n" % (base, base + 20)).encode()
        else:
            out += ("F=%d R=%d S=0\r\n" % (base, base)).encode()
    out += b"Restarted\r\n"
    return bytes(out)


def test_reader_pipeline():
    """第 1 步：读字节流 -> 队列里的行，必须和真实串口行为一致。"""
    q = lr.queue.Queue()
    reader = lr.SerialReader("TEST", 9600, q, stream=io.BytesIO(make_stream()))
    reader.start()
    reader.join(timeout=5.0)
    assert not reader.is_alive(), "读取线程没有结束"

    items = []
    while not q.empty():
        items.append(q.get())

    lines = [p for k, p in items if k == "line"]
    kinds = [k for k, _ in items]
    assert kinds[0] == "status", kinds[:3]
    assert kinds[-1] == "status", kinds[-3:]
    assert len(lines) == 60 + 4, "行数不对：%d" % len(lines)
    assert lines[0] == "VL53L0X Ranging"
    assert lines[-1] == "Restarted"
    print("[1/4] 字节流 -> 行 OK：共 %d 行（60 条数据行 + 4 条说明行）" % len(lines))
    return lines


def test_parse_and_csv(lines, csv_path):
    """第 2 步：解析 + 统计 + CSV 落盘，复算界面里那套算法。"""
    t0 = time.monotonic()
    recs = [r for r in (lr.parse_line(t, time.monotonic() - t0) for t in lines) if r]
    assert len(recs) == 60, len(recs)

    data = [r for r in recs if r.valid]
    errs = [r for r in recs if not r.valid]
    assert len(data) == 57 and len(errs) == 3, (len(data), len(errs))
    assert all(r.tag == "SIGNAL_WEAK" and r.status == 2 for r in errs)

    distances = [r.distance_mm for r in data]
    assert min(distances) >= 400 and max(distances) <= 520, (min(distances), max(distances))

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(lr.CSV_HEADER)
        for r in recs:
            w.writerow(lr.record_to_row(r))

    with open(csv_path, "r", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == lr.CSV_HEADER
    assert len(rows) == len(recs) + 1
    assert all(len(r) == len(lr.CSV_HEADER) for r in rows), "CSV 列数不一致"
    # 无效帧在 CSV 里距离列应为空，标签列应有原因
    err_row = [r for r in rows[1:] if r[10] == "SIGNAL_WEAK"][0]
    assert err_row[2] == "" and err_row[11] == "0", err_row
    print("[2/4] 解析/统计/CSV OK：%d 帧（%d 有效 / %d 无效），min=%d max=%d，CSV %d 行 -> %s"
          % (len(recs), len(data), len(errs), min(distances), max(distances),
             len(rows) - 1, csv_path))
    return recs


def test_obstacle(app):
    """第 3 步：障碍物判定（与界面无关的纯逻辑，但用它检查界面上的文字）。"""
    app.prev_valid_mm = None
    app.obstacle_active = False

    # 先建立基准 400mm，再降到 350mm：下降 50mm > 30mm，判定有障碍物
    app._handle_line("F=400 R=400 S=0")
    assert not app.obstacle_active, "第一次采样没有可比较的基准，不应触发"
    app._handle_line("F=350 R=350 S=0")
    assert app.obstacle_active, "下降 50mm 应当触发"
    text = app.info_label.cget("text")
    assert "有障碍物" in text and "下降 50 mm" in text, text
    print("[3/4] 障碍物判定 OK：下拉 50mm 触发，本次结果 -> %r"
          % text.splitlines()[-1])

    # 回升 10mm 还不够，提示继续保持
    app._handle_line("F=360 R=360 S=0")
    assert app.obstacle_active, "回升 10mm 不应解除"

    # 回升 35mm > 30mm，解除提示
    app._handle_line("F=385 R=385 S=0")
    assert not app.obstacle_active, "回升 35mm 应当解除"
    assert "有障碍物" not in app.info_label.cget("text")

    # 距离变大（远离）本身不触发
    app.prev_valid_mm = 350
    app._handle_line("F=500 R=500 S=0")
    assert not app.obstacle_active, "远离不应触发"
    print("     回升 >30mm 解除提示 OK；远离不触发 OK")


def test_gui(recs):
    """第 4 步：真的把界面建起来，把数据灌进去，确认不抛异常。"""
    try:
        import tkinter as tk
    except ImportError:
        print("[4/4] 跳过界面测试：当前 Python 没有 tkinter")
        return

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("[4/4] 跳过界面测试：没有可用显示环境（%s）" % exc)
        return

    root.withdraw()                      # 先隐藏，避免弹窗打扰
    app = lr.ReceiverApp(root, port="TEST", baud=9600)
    # 隐藏的窗口不会被布局，Canvas 会停在 1x1；把窗口摆到屏幕外再显示，
    # 这样控件才会按真实尺寸布局（截图脚本用的是同一招）。
    root.geometry("+%d+%d" % (-4000, -4000))
    root.deiconify()
    for _ in range(5):
        root.update_idletasks()
        root.update()

    app.count_total = 0
    app.count_valid = 0
    app.count_err = 0
    app.last_seq = None
    app.lost_frames = 0
    app.min_mm = None
    app.max_mm = None

    for rec in recs:
        app._handle_line(rec.text)

    for _ in range(3):
        root.update_idletasks()
        root.update()
    app._refresh_stats()
    app._draw_plot()
    root.update()

    assert app.count_total == 60, app.count_total
    assert app.count_valid == 57, app.count_valid
    assert app.count_err == 3, app.count_err
    assert app.lost_frames == 0, app.lost_frames
    assert app.min_mm is not None and app.max_mm is not None
    assert app.dist_label.cget("text").isdigit(), app.dist_label.cget("text")
    assert len(app.canvas.find_all()) > 10, "曲线画布上没有内容"
    stats = {k: v.cget("text") for k, v in app.stat_labels.items()}
    assert stats["total"] == "60" and stats["err"] == "3", stats
    print("[4/4] Tkinter 界面 OK：大字=%s mm，统计=%s，画布元素 %d 个"
          % (app.dist_label.cget("text"), stats, len(app.canvas.find_all())))

    test_obstacle(app)

    # 再试一下 CSV 按钮那条路径（不弹文件对话框，直接给路径）
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gui_test.csv")
    app.csv_path = csv_path
    app.toggle_record()
    assert app.csv_file is not None
    for rec in recs[:5]:
        app._handle_line(rec.text)
    app._close_csv()
    assert os.path.getsize(csv_path) > 0
    os.remove(csv_path)
    print("     界面里的【开始记录】按钮路径 OK（已生成并删除临时 CSV）")

    app._on_close()


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(here, "_e2e_test.csv")

    lines = test_reader_pipeline()
    recs = test_parse_and_csv(lines, csv_path)
    test_gui(recs)

    os.remove(csv_path)
    print("\n端到端自测全部通过 ✅")


if __name__ == "__main__":
    main()
