# -*- coding: utf-8 -*-
"""生成一张上位机界面的截图（用模拟数据灌进去，不需要真实硬件）。

用法： python pc_tool/screenshot_gui.py [输出路径]
"""

import ctypes
import io
import os
import sys
import time

# 必须在创建 Tk 之前声明 DPI 感知，否则窗口会被系统按 150%/125% 缩放，
# ImageGrab 抓到的 bbox 就对不上，截出来的图会被裁掉右边和下边。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laser_receiver as lr  # noqa: E402
from test_end_to_end import make_stream  # noqa: E402


def _win32_print_window(root, path):
    """用 Windows 的 PrintWindow 直接把窗口画到内存位图里再存 PNG。

    好处：窗口即使被摆在屏幕外、被别的窗口挡住，也能截到完整内容。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 高分屏下不糊
    except Exception:
        pass

    hwnd = root.winfo_id()
    hwnd = user32.GetAncestor(hwnd, 2)      # GA_ROOT = 2，取顶层窗口句柄
    if not hwnd:
        hwnd = root.winfo_id()

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False

    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    # PW_RENDERFULLCONTENT = 2，能截到 Tk 这种自绘控件
    ok = user32.PrintWindow(hwnd, memdc, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h            # 负数 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return False

    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
    img.convert("RGB").save(path)
    return True


def grab_window(root, path):
    """截取整个窗口：优先屏幕抓取（Tk 子窗口只能这样抓到），失败再试 PrintWindow。"""
    root.update_idletasks()
    root.update()
    time.sleep(0.4)
    root.update()

    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()

    try:
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(path)
        return True
    except Exception:
        pass

    try:
        return _win32_print_window(root, path)
    except Exception as exc:
        print("截图失败：%s" % exc)
        return False


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gui_screenshot.png")

    import tkinter as tk

    root = tk.Tk()
    # DPI 感知后 Tk 会按真实 DPI 放大字体；固定成 96DPI 的观感，截图才和界面设计一致
    try:
        root.tk.call("tk", "scaling", 96.0 / 72.0)
    except Exception:
        pass
    app = lr.ReceiverApp(root, port="COM5", baud=9600,
                         csv_path=os.path.join(os.path.dirname(out), "_shot.csv"))
    # 摆到屏幕外，避免打扰正在用电脑的人；窗口必须"已显示"才会真正布局
    root.geometry("+%d+%d" % (-4000, -4000))
    for _ in range(5):
        root.update_idletasks()
        root.update()
    app.status_var.set("已连接 COM5 @ 9600 8N1 —— 正在记录数据")

    # 灌入模拟数据（用和真实串口完全相同的处理路径）
    lines = [t for t in make_stream(n=120, with_errors=True).decode().splitlines() if t]
    for t in lines:
        rec = lr.parse_line(t, 0.0)
        if rec is not None:
            app._handle_line(t)
            root.update_idletasks()

    app.toggle_record()
    app._refresh_stats()
    app._draw_plot()
    root.update_idletasks()
    root.update()

    # Tk 的子窗口（Canvas/Text）用 PrintWindow 抓不到，只能真正显示在屏幕上再截。
    # 先摆到屏幕外完成布局，再挪到左上角抓一张，然后立刻关闭，闪一下就好。
    root.attributes("-topmost", True)
    root.geometry("+40+40")
    root.lift()
    for _ in range(8):
        root.update_idletasks()
        root.update()
        time.sleep(0.05)

    ok = grab_window(root, out)
    app._on_close()
    if os.path.exists(os.path.join(os.path.dirname(out), "_shot.csv")):
        os.remove(os.path.join(os.path.dirname(out), "_shot.csv"))
    print("截图已保存：%s" % out if ok else "截图未生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
