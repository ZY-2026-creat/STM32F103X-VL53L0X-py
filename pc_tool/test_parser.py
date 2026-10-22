# -*- coding: utf-8 -*-
"""模拟单片机串口输出，验证 pc_tool/laser_receiver.py 的解析与 CSV 逻辑。

用法： python pc_tool/test_parser.py
这个脚本不碰真实串口，只把固件会发出的每一行原样喂给解析函数。
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laser_receiver as lr  # noqa: E402

# 本工程固件（User/main.c）真实会发出的行：开机说明 + F/R/S 数据 + 各种 RangeStatus
DEVICE_STREAM = [
    b"\r\nVL53L0X Ranging\r\n",
    b"Budget=100ms Period=120ms Sigma=15mm\r\n",
    b"Start OK, about 8 Hz\r\n",
    b"# Lines starting with '#' are comments: ignore them in the host tool\r\n",
    b"F=456 R=456 S=0\r\n",
    b"F=457 R=457 S=0\r\n",
    b"F=0 R=0 S=0\r\n",                 # 0mm 也是合法距离
    b"F=12345 R=12345 S=0\r\n",         # 远处的大数字也要能解析
    b"F=400  R=401  S=0\r\n",           # 等号两边多个空格也要能解析
    b"F=520 R=548 S=1\r\n",             # 标准差过大
    b"F=900 R=890 S=2\r\n",             # 回波太弱
    b"F=25 R=25 S=3\r\n",               # 目标太近
    b"F=300 R=300 S=4\r\n",             # 相位超范围
    b"F=300 R=300 S=5\r\n",             # 硬件故障
    b"F=780 R=780 S=0\r\n",
    b"Dist: 512mm status=11 quality=0\r\n",                                  # 旧固件格式 1
    b"[DIST],dist=600,mm=600,status=11,quality=GOOD,alarm=0,err=0,raw=600,seq=7\r\n",  # 旧固件格式 2
    b"\r\n",                            # 空行
    b"garbage-not-a-frame\r\n",         # 垃圾数据
]


def feed(data: bytes):
    """把字节流按 LF 切行，走和串口线程一样的解码/解析路径。"""
    out = []
    for raw in data.split(b"\n"):
        text = raw.decode("ascii", errors="replace").strip("\r\n \t")
        if text:
            out.append(text)
    return out


def main():
    lines = feed(b"".join(DEVICE_STREAM))
    recs = [r for r in (lr.parse_line(t, i * 0.2) for i, t in enumerate(lines)) if r]

    # 18 条非空行：3 条开机说明 + 1 条 '#' 注释 + 1 条垃圾行被忽略，剩下 13 条数据行
    comments = sum(1 for t in lines if t.startswith("#"))
    assert comments == 1, comments
    assert len(lines) == 18, len(lines)
    print("收到非空行 %d 条（其中 %d 条 '#' 说明行被忽略），解析出 %d 条数据记录"
          % (len(lines), comments, len(recs)))
    assert len(recs) == 13, "解析条数不对：%d" % len(recs)

    kinds = [r.kind for r in recs]
    assert kinds == ["DIST"] * 5 + ["ERR"] * 5 + ["DIST"] * 3, kinds
    print("  类型序列 OK:", kinds)

    d1 = recs[0]
    assert d1.distance_mm == 456 and d1.status == 0 and d1.quality == "GOOD"
    assert d1.raw == 456 and d1.seq is None and d1.valid and d1.tag == "OK"
    print("  F/R/S 解析 OK:", d1.distance_mm, d1.raw, d1.status, d1.quality)

    assert recs[2].distance_mm == 0 and recs[2].valid, "0mm 是有效距离"
    assert recs[3].distance_mm == 12345
    assert recs[4].distance_mm == 400, "等号两边多个空格没解析对"
    print("  0mm / 12345mm / 多空格 边界 OK")

    expect = [("SIGMA_FAIL", 1), ("SIGNAL_WEAK", 2), ("MIN_RANGE", 3),
              ("PHASE_FAIL", 4), ("DEVICE_HW", 5)]
    for rec, (tag, status) in zip(recs[5:10], expect):
        assert rec.kind == "ERR" and not rec.valid, rec.text
        assert rec.tag == tag, (rec.tag, tag)
        assert rec.status == status, (rec.status, status)
        assert rec.distance_mm is None, "S!=0 时距离应当按无效处理"
        assert rec.quality == "BAD" or rec.quality == "WEAK", rec.quality
    print("  S=1..5 映射 OK:", [r.tag for r in recs[5:10]])

    e1 = recs[5]
    assert e1.raw == 548 and e1.err == 0
    print("  无效帧仍保留原始距离 raw OK:", e1.raw)

    legacy = recs[11]
    assert legacy.distance_mm == 512 and legacy.quality == "GOOD", legacy.distance_mm

    seq_rec = recs[12]
    assert seq_rec.distance_mm == 600 and seq_rec.seq == 7
    print("  旧格式兼容 OK:", legacy.distance_mm, "| [DIST] seq =", seq_rec.seq)

    # ---- CSV 导出 ----
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(lr.CSV_HEADER)
    for r in recs:
        writer.writerow(lr.record_to_row(r))
    buf.seek(0)
    rows = list(csv.reader(buf))
    assert rows[0] == lr.CSV_HEADER
    assert len(rows) == len(recs) + 1, len(rows)
    assert all(len(row) == len(lr.CSV_HEADER) for row in rows), "CSV 列数不一致"
    print("  CSV 导出 OK: %d 列 x %d 行" % (len(lr.CSV_HEADER), len(rows) - 1))
    print("  CSV 首行:", ",".join(rows[1]))

    # ---- 丢帧统计逻辑（复算 GUI 里的算法）----
    # 本工程固件没有 seq，只有旧格式那条 [DIST] 有 seq=7，因此不应产生"丢帧"
    last, lost = None, 0
    for r in recs:
        if r.seq is not None:
            if last is not None and r.seq > last + 1:
                lost += r.seq - last - 1
            last = r.seq
    assert lost == 0, lost

    # 故意跳一个序号，算法应当能算出丢了 2 帧
    fake = [lr.parse_line("[DIST],dist=1,mm=1,status=11,quality=GOOD,"
                          "alarm=0,err=0,raw=1,seq=%d" % s, 0.0) for s in (10, 13)]
    last, lost = None, 0
    for r in fake:
        if last is not None and r.seq > last + 1:
            lost += r.seq - last - 1
        last = r.seq
    assert lost == 2, lost
    print("  丢帧统计 OK: 正常流 lost=%d，故意跳过 seq 10->13 算出丢失 2 帧" % 0)

    # ---- tag 说明覆盖 ----
    missing = [r.tag for r in recs if not r.valid and not lr.describe_tag(r.tag)]
    assert not missing, "以下标签没有中文说明: %s" % missing
    print("  所有错误标签都有中文说明 OK")

    # ---- 波特率余量检查：一行最多多少字节、9600 下够不够 ----
    longest = max(len(t) for t in lines)
    need = longest * 10 / 0.12          # 一行 10 bit/字节，测量周期 120ms
    print("  最长一行 %d 字节；120ms 一行 => 需要 %.0f bit/s，9600 %s"
          % (longest, need, "余量充足" if need < 9600 * 0.8 else "偏紧"))
    assert need < 9600, "9600 波特率下塞不下最长的一行"

    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
