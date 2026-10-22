# -*- coding: utf-8 -*-
"""
生成《VL53L0X 测距实现 · 函数关系解析》docx 文档。
重点：main.c / VL53L0X.c / VL53L0X.h 三个文件的函数关系；其它文件简要叙述。
用法: python make_doc_arch.py
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "VL53L0X测距实现_函数关系解析.docx")

CJK = "微软雅黑"
ASCII_F = "Segoe UI"
MONO = "Consolas"


# ----------------------------------------------------------------------------
# 基础工具（与 make_doc_flow.py 保持一致）
# ----------------------------------------------------------------------------
def style_font(style, ascii_font, cjk_font, size=None, bold=None):
    style.font.name = ascii_font
    if size is not None:
        style.font.size = size
    if bold is not None:
        style.font.bold = bold
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn('w:ascii'), ascii_font)
    rfonts.set(qn('w:hAnsi'), ascii_font)
    rfonts.set(qn('w:eastAsia'), cjk_font)


_PPR_AFTER_SHD = {
    'w:tabs', 'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap', 'w:overflowPunct',
    'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN', 'w:bidi', 'w:adjustRightInd',
    'w:snapToGrid', 'w:spacing', 'w:ind', 'w:contextualSpacing', 'w:mirrorIndents',
    'w:suppressOverlap', 'w:jc', 'w:textDirection', 'w:textAlignment',
    'w:textboxTightWrap', 'w:outlineLvl', 'w:divId', 'w:cnfStyle',
    'w:rPr', 'w:sectPr', 'w:pPrChange',
}


def shade(paragraph, hexfill="F2F2F2"):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hexfill)
    for child in pPr:
        if child.tag in {qn(t) for t in _PPR_AFTER_SHD}:
            child.addprevious(shd)
            return
    pPr.append(shd)


def code(doc, text, fill="F4F4F4"):
    lines = text.strip("\n").split("\n")
    for i, ln in enumerate(lines):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(3 if i == 0 else 0)
        pf.space_after = Pt(3 if i == len(lines) - 1 else 0)
        pf.line_spacing = 1.0
        pf.left_indent = Cm(0.4)
        r = p.add_run(ln if ln else " ")
        r.font.name = MONO
        r.font.size = Pt(8.5)
        r._element.rPr.rFonts.set(qn('w:eastAsia'), MONO)
        shade(p, fill)


def note(doc, text, fill="EAF1F8"):
    """强调块。"""
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(4)
    pf.space_after = Pt(6)
    pf.left_indent = Cm(0.3)
    r = p.add_run(text)
    r.font.size = Pt(9.5)
    r.font.name = ASCII_F
    r._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    shade(p, fill)


def bullet(doc, text, level=0):
    p = doc.add_paragraph(text, style='List Bullet')
    p.paragraph_format.left_indent = Cm(0.75 + 0.6 * level)
    p.paragraph_format.space_after = Pt(2)
    return p


_TBLPR_ORDER = ['w:tblStyle', 'w:tblpPr', 'w:tblOverlap', 'w:bidiVisual',
                'w:tblStyleRowBandSize', 'w:tblStyleColBandSize', 'w:tblW', 'w:jc',
                'w:tblCellSpacing', 'w:tblInd', 'w:tblBorders', 'w:shd', 'w:tblLayout',
                'w:tblCellMar', 'w:tblLook', 'w:tblCaption', 'w:tblDescription']


def _place(tblPr, el, tag):
    idx = _TBLPR_ORDER.index(tag)
    for child in tblPr:
        cname = 'w:' + child.tag.split('}')[-1]
        if cname in _TBLPR_ORDER and _TBLPR_ORDER.index(cname) > idx:
            child.addprevious(el)
            return
    tblPr.append(el)


def _set_grid(t, widths_cm):
    """写入固定布局 + tblW + tblGrid + 每格 tcW（否则列宽比例不生效）。"""
    tbl = t._tbl
    tblPr = tbl.tblPr
    tw = lambda cm: str(int(round(cm * 566.93)))

    for tag in ('w:tblLayout', 'w:tblW'):
        old = tblPr.find(qn(tag))
        if old is not None:
            tblPr.remove(old)

    tblW = OxmlElement('w:tblW')
    tblW.set(qn('w:type'), 'dxa')
    tblW.set(qn('w:w'), str(sum(int(round(w * 566.93)) for w in widths_cm)))
    _place(tblPr, tblW, 'w:tblW')

    layout = OxmlElement('w:tblLayout')
    layout.set(qn('w:type'), 'fixed')
    _place(tblPr, layout, 'w:tblLayout')

    old_grid = tbl.find(qn('w:tblGrid'))
    if old_grid is not None:
        tbl.remove(old_grid)
    grid = OxmlElement('w:tblGrid')
    for w in widths_cm:
        gc = OxmlElement('w:gridCol')
        gc.set(qn('w:w'), tw(w))
        grid.append(gc)
    tblPr.addnext(grid)

    for row in t.rows:
        for i, cell in enumerate(row.cells):
            tcPr = cell._tc.get_or_add_tcPr()
            old = tcPr.find(qn('w:tcW'))
            if old is not None:
                tcPr.remove(old)
            tcW = OxmlElement('w:tcW')
            tcW.set(qn('w:type'), 'dxa')
            tcW.set(qn('w:w'), tw(widths_cm[i]))
            tcPr.insert(0, tcW)


def table(doc, header, rows, widths=None, mono_cols=()):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = 'Light Grid Accent 1'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    hdr = t.rows[0].cells
    for i, h in enumerate(header):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        r = p.add_run(h)
        r.font.bold = True
        r.font.size = Pt(9.5)
        r.font.name = ASCII_F
        r._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            r = p.add_run(str(v))
            r.font.size = Pt(9)
            r.font.name = MONO if i in mono_cols else ASCII_F
            r._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    if widths:
        _set_grid(t, widths)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.font.size = Pt(8.5)
    r.font.italic = True
    r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    r.font.name = ASCII_F
    r._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    p.paragraph_format.space_after = Pt(8)


# ----------------------------------------------------------------------------
doc = Document()

for s in doc.sections:
    s.page_width = Cm(21.0)
    s.page_height = Cm(29.7)
    s.left_margin = Cm(2.2)
    s.right_margin = Cm(2.2)
    s.top_margin = Cm(2.0)
    s.bottom_margin = Cm(2.0)

style_font(doc.styles['Normal'], ASCII_F, CJK, Pt(10.5))
for name, sz in (('Heading 1', 16), ('Heading 2', 13.5), ('Heading 3', 11.5)):
    try:
        style_font(doc.styles[name], ASCII_F, CJK, Pt(sz), bold=True)
    except KeyError:
        pass
try:
    style_font(doc.styles['Title'], ASCII_F, CJK, Pt(22), bold=True)
except KeyError:
    pass

# ============================================================================
# 封面
# ============================================================================
t = doc.add_heading("VL53L0X 测距实现 · 函数关系解析", level=0)
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
st = doc.add_paragraph()
st.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = st.add_run("main.c → VL53L0X.h → VL53L0X.c 调用主线\n与下层驱动的协作关系")
r.font.size = Pt(14)
r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
r.font.name = ASCII_F
r._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
st.paragraph_format.space_after = Pt(16)

table(doc,
      ["项目", "内容"],
      [["文档目的", "讲清「VL53L0X 是怎么测出距离的」，重点拆解 main.c / VL53L0X.c / VL53L0X.h 三个文件里函数的调用关系"],
       ["重点分析对象", "User\\main.c（123 行）、Hardware\\VL53L0X.c（315 行）、Hardware\\VL53L0X.h（116 行）"],
       ["简要涉及", "OLED.c、Delay.c，以及 VL53L0X.c 下层调用的 7 个 ST 官方驱动文件"],
       ["代码基准", "ST STSW-IMG005 / VL53L0X_1.0.4（API v1.0.4.4960）+ STM32F10x 标准外设库 v3.5.0"],
       ["硬件平台", "STM32F103C8T6；传感器 SCL=PA0 / SDA=PA1（软件 I2C）；OLED SCL=PB8 / SDA=PB9"],
       ["配套文档", "《VL53L0X激光测距_移植与OLED显示操作流程.docx》—— 讲「怎么移植、怎么接线」；本文讲「代码内部怎么串起来」"]],
      widths=[3.4, 12.6])

# ============================================================================
doc.add_heading("1  三个文件的分工：一句话就能分清", level=1)

doc.add_paragraph(
    "这三个文件构成最典型的「单头文件接口」写法，职责边界非常干净：")

bullet(doc, "VL53L0X.h —— 说「能做什么」。只有声明，一行代码都不产生。main.c 只认它。")
bullet(doc, "VL53L0X.c —— 说「怎么做」。13 个函数的具体实现，6 个 static 变量藏在里面，外界看不到。")
bullet(doc, "main.c —— 说「什么时候做」。按照「初始化 → 启动测量 → 主循环取数 → 显示」的时序调用上面两组函数。")

table(doc,
      ["文件", "角色", "代码规模", "对外暴露的东西"],
      [["VL53L0X.h", "接口契约", "116 行", "13 个函数原型 + 2 个宏 + 2 个 #include"],
       ["VL53L0X.c", "唯一实现", "315 行", "无（全部通过 .h 暴露；6 个内部变量为 static）"],
       ["main.c", "调用者 / 时序控制", "123 行", "main() 入口"]],
      widths=[2.8, 3.0, 2.2, 8.0])

note(doc,
     "关键点：main.c 里 #include 的是 \"VL53L0X.h\"，它完全不知道下面还压着 vl53l0x_api.c、"
     "vl53l0x_platform.c、vl53l0x_i2c_platform.c 等 19 个文件，也不知道 I2C 怎么翻转电平。"
     "这正是分层带来好处：换传感器、换总线，main.c 不用改。")

# ============================================================================
doc.add_heading("2  VL53L0X.h：只声明，不实现", level=1)

doc.add_heading("2.1  它引入了什么", level=2)
code(doc, """
#include "stm32f10x.h"     /* uint8_t/uint16_t 等基础类型、GPIO 相关定义 */
#include "vl53l0x_def.h"   /* VL53L0X_Error 枚举、VL53L0X_RangingMeasurementData_t */
""")
doc.add_paragraph(
    "这里特意只引 vl53l0x_def.h 而没引 vl53l0x_api.h：def.h 里只有类型和地址定义，"
    "不会把一整串 ST 的函数声明和 platform 依赖带进来，main.c 的编译依赖因此保持最小。")

doc.add_heading("2.2  两个宏", level=2)
table(doc,
      ["宏", "值", "作用与理由"],
      [["VL53L0X_I2C_ADDRESS_8BIT", "0x52",
        "VL53L0X 的 7 位地址固定是 0x29，ST 的 API 全程要求传「8 位写地址」，即 0x29<<1 = 0x52。"
        "VL53L0X.c 在 Init 里把它赋给 Dev->I2cDevAddr"],
       ["VL53L0X_NOT_READY", "1",
        "ReadContinuous 专用的「这次还没测完」返回值。它是正数 1，而所有 VL53L0X_Error 错误码都是负数，"
        "因此调用方可以用「==0 / ==1 / 其它」三分支安全区分，不会和真错误混淆"]],
      widths=[5.2, 1.6, 9.2], mono_cols=(0,))

doc.add_heading("2.3  13 个函数原型，按用途分 4 组", level=2)
doc.add_paragraph(
    "头文件里的函数顺序就是功能分组的顺序，理解分组比记住顺序更重要：")
table(doc,
      ["组", "函数原型", "作用", "谁调用"],
      [["初始化", "VL53L0X_Init", "7 步初始化，约 100~300ms", "main"],
       ["初始化", "VL53L0X_CheckConnection", "I2C 探活（只发地址，不读寄存器）", "Init 内部 + 用户自检"],
       ["测量", "VL53L0X_ReadDistance", "单次测量，阻塞往返一次", "用户（可选）"],
       ["测量", "VL53L0X_StartContinuous", "启动连续测量", "main"],
       ["测量", "VL53L0X_ReadContinuous", "非阻塞取最新结果", "main 主循环"],
       ["测量", "VL53L0X_StopContinuous", "停止连续测量", "ReadDistance 内部 + 用户"],
       ["取值", "VL53L0X_GetLastDistance", "读最近一次距离缓存", "调试 Watch / 上层"],
       ["取值", "VL53L0X_GetLastRangeStatus", "读最近一次状态缓存", "调试 Watch / 上层"],
       ["显示", "VL53L0X_ShowDistance", "把距离写进 OLED 指定行", "main"],
       ["显示", "VL53L0X_ShowRangeStatus", "把状态写进 OLED 指定行", "main"],
       ["显示", "VL53L0X_ShowError", "把错误码写进 OLED 指定行", "main 错误分支"],
       ["显示", "VL53L0X_RangeStatusString", "测距状态码 → 英文短词", "ShowRangeStatus 内部"],
       ["显示", "VL53L0X_ErrorString", "错误码 → 英文短词", "ShowError 内部"]],
      widths=[1.6, 5.6, 5.4, 3.4], mono_cols=(1,))

doc.add_heading("2.4  头文件与 .c 的一一对应", level=2)
bullet(doc, "13 个原型 ↔ VL53L0X.c 里 13 个同名函数，不多不少，没有悬空声明。")
bullet(doc, "头文件里没有任何 extern 变量 —— VL53L0X.c 的 6 个 static 变量对外完全不可见（信息隐藏）。")
bullet(doc, "没有 inline 函数、没有宏函数，头文件里也没有逻辑，纯粹是清单。")

# ============================================================================
doc.add_heading("3  VL53L0X.c：四组函数如何互相咬合", level=1)

doc.add_heading("3.1  先看它藏起来的 6 个 static 变量", level=2)
doc.add_paragraph(
    "四组函数不是各写各的，它们通过这 6 个模块级变量串成一个整体：")
table(doc,
      ["变量", "类型", "初值", "作用"],
      [["VL53L0X_Device", "VL53L0X_Dev_t", "0（BSS 自动清零）", "ST 的器件对象，装载全部运行时参数"],
       ["pVL53L0X_Device", "VL53L0X_DEV", "&VL53L0X_Device", "ST API 统一用指针操作器件，所有调用都传它"],
       ["VL53L0X_Ready", "uint8_t", "0", "初始化完成标志，所有测量类函数的「第一道门」"],
       ["VL53L0X_ContinuousMode", "uint8_t", "0", "连续测量是否正在运行，决定 ReadContinuous 能否使用"],
       ["VL53L0X_LastDistance", "uint16_t", "0", "最近一次距离缓存，供 GetLastDistance 读取"],
       ["VL53L0X_LastStatus", "uint8_t", "0", "最近一次状态缓存，供 GetLastRangeStatus 读取"]],
      widths=[4.4, 3.0, 3.6, 5.0], mono_cols=(0,))

doc.add_paragraph("这三个状态决定了每个函数会不会「提前返回」：")
table(doc,
      ["状态", "Ready", "ContinuousMode", "怎么进入", "此时哪些函数可用"],
      [["未初始化", "0", "0", "上电复位后", "只有 VL53L0X_Init / CheckConnection / Show* / String*"],
       ["已初始化 · 空闲", "1", "0", "Init 成功返回后", "全部；ReadDistance 可直接用"],
       ["连续测量中", "1", "1", "StartContinuous 成功后", "ReadContinuous 可用；ReadDistance 会先自动停掉连续模式"]],
      widths=[3.4, 1.6, 3.0, 3.6, 4.4])

doc.add_heading("3.2  第 1 组：初始化 —— Init 内部串起 8 个动作", level=2)
doc.add_paragraph(
    "VL53L0X_Init() 本身就是一条调用链，它内部既调用本文件的公开函数，也调用 ST 的下层函数：")
table(doc,
      ["步", "VL53L0X.c 里的动作", "最终转调的下层函数", "目的"],
      [["1", "VL53L0X_comms_initialise(I2C, 400)",
        "vl53l0x_i2c_platform.c", "把 PA0/PA1 配置成开漏输出，设定约 400kHz"],
       ["2", "VL53L0X_CheckConnection()",
        "VL53L0X_I2C_TestConnection(0x52)", "先探活，避免后面一堆函数白白超时等待"],
       ["3", "填 I2cDevAddr / comms_type / comms_speed_khz",
        "—", "告诉 ST API 器件地址 0x52、总线为 I2C、速率 400kHz"],
       ["4", "VL53L0X_DataInit()",
        "vl53l0x_api.c", "软复位器件内部状态、装载默认参数"],
       ["5", "VL53L0X_StaticInit()",
        "vl53l0x_api.c → vl53l0x_api_core.c", "载入官方 tuning 参数、设置参考 SPAD"],
       ["6", "VL53L0X_PerformRefCalibration()",
        "vl53l0x_api_calibration.c", "参考校准（VHV 设置 + 相位校准值）"],
       ["7", "VL53L0X_PerformRefSpadManagement()",
        "vl53l0x_api_calibration.c", "参考 SPAD 管理，保证弱回波下也能测"],
       ["8", "VL53L0X_Ready = 1",
        "—", "放行后面的测量函数"]],
      widths=[0.9, 6.2, 5.2, 3.7], mono_cols=(1, 2))

note(doc,
     "注意第 2 步：Init 内部调用了 CheckConnection —— 也就是「公开函数被同一文件的另一个公开函数复用」。"
     "这是三文件关系里唯一一处「组内互调」出现在初始化路径上。")

doc.add_heading("3.3  第 2 组：单次测量 —— ReadDistance 的完整调用链", level=2)
code(doc, """
VL53L0X_ReadDistance()                              /* VL53L0X.c:105 */
  |
  +-- if (VL53L0X_Ready == 0) return ERROR_INVALID_COMMAND;
  |
  +-- if (VL53L0X_ContinuousMode != 0)               /* 连续测量正在跑？ */
  |       VL53L0X_StopContinuous();                  /* ← 函数间调用，先切回空闲 */
  |
  +-- VL53L0X_SetDeviceMode(Dev, SINGLE_RANGING);    /* → vl53l0x_api.c */
  |
  +-- VL53L0X_PerformSingleRangingMeasurement(Dev, &Data);   /* ST 的"一条龙"接口 */
  |       内部自动完成：StartMeasurement
  |                     → 轮询 GetMeasurementDataReady
  |                     → GetRangingMeasurementData
  |                     → ClearInterruptMask
  |
  +-- 缓存 LastDistance / LastStatus
  |
  +-- 回填 *pDistance_mm / *pRangeStatus（传 NULL 也安全）
""")
doc.add_paragraph(
    "因为 PerformSingleRangingMeasurement 把「启动—等待—取数—清中断」全包了，"
    "ReadDistance 才只需要 4 行有效代码。代价是它会阻塞，单次约 33ms。")

doc.add_heading("3.4  第 2 组：连续测量 —— 三个函数的分工", level=2)
table(doc,
      ["函数", "入口检查", "做的事", "出口时改了什么状态"],
      [["VL53L0X_StartContinuous", "Ready==0 → 拒绝；已在连续模式 → 直接返回成功（幂等）",
        "SetDeviceMode(CONTINUOUS_RANGING) + StartMeasurement", "ContinuousMode = 1"],
       ["VL53L0X_ReadContinuous", "ContinuousMode==0 → 返回 INVALID_COMMAND",
        "GetMeasurementDataReady → 未就绪返回 NOT_READY → GetRangingMeasurementData → "
        "ClearInterruptMask(NEW_SAMPLE_READY) → 更新缓存", "不改状态（只更新 2 个缓存变量）"],
       ["VL53L0X_StopContinuous", "ContinuousMode==0 → 直接返回",
        "StopMeasurement + ClearInterruptMask", "ContinuousMode = 0"]],
      widths=[4.6, 4.6, 4.6, 2.2], mono_cols=(0,))

doc.add_paragraph(
    "三者的关系可以这样记：Start / Stop 是开关，Read 是取数窗口。"
    "Read 绝不改变开关状态，所以主循环里可以无限次调用它而不会把测量搞停。"
    "而 ReadDistance 复用 StopContinuous 来「强制关掉开关」，这样单次测量才有机会执行。")

note(doc,
     "四条硬约束（都在代码里显式检查）：\n"
     "① Ready==0 时所有测量函数直接拒绝服务；\n"
     "② 不先 StartContinuous 就调 ReadContinuous 会返回 INVALID_COMMAND；\n"
     "③ 每次取完数据必须清中断，否则器件不再产生下一次 New Sample Ready（现象：只测到一次，数字卡死）；\n"
     "④ 单次测量和连续测量不能同时进行。")

doc.add_heading("3.5  第 4 组：显示函数 —— 纯输出，不碰硬件状态", level=2)
code(doc, """
VL53L0X_ShowDistance(Line, mm)                /* VL53L0X.c:248 */
  +-- OLED_ShowString(Line, 1,  "Dist:")      /* → Hardware\\OLED.c */
  +-- OLED_ShowNum   (Line, 6,  mm, 4)        /* 固定 4 位，452 显示为 0452 */
  +-- OLED_ShowString(Line, 10, "mm  ")

VL53L0X_ShowRangeStatus(Line, st)             /* VL53L0X.c:257 */
  +-- OLED_ShowString(Line, 1, "Status:")
  +-- OLED_ShowString(Line, 8, VL53L0X_RangeStatusString(st))   /* ← 函数间调用 */

VL53L0X_ShowError(Line, err)                  /* VL53L0X.c:266 */
  +-- OLED_ShowString(Line, 1, "Err:")
  +-- OLED_ShowString(Line, 5, VL53L0X_ErrorString(err))        /* ← 函数间调用 */
""")
doc.add_paragraph(
    "Show* 三个函数是「纯输出」：不改变任何内部状态，也不发起任何传感器 I2C 事务"
    "（它们只写 OLED 自己那条 PB8/PB9 总线）。所以放在主循环里随便调是安全的。"
    "它们各自依赖一个 String 函数把状态码/错误码翻译成不超过 11 个字符的英文短词，"
    "因为 OLED 一行只有 16 列。")

doc.add_heading("3.6  第 3 组：取值函数 —— 只读内存", level=2)
doc.add_paragraph(
    "VL53L0X_GetLastDistance() / VL53L0X_GetLastRangeStatus() 直接返回那两个 static 缓存变量，"
    "既不访问 I2C 也不改状态。它们的真正用途是调试：在 Keil 的 Watch 窗口里直接看这两个值，"
    "就能判断测距到底有没有工作，完全不用依赖 OLED 是否正常。")

doc.add_heading("3.7  VL53L0X.c 的完整调用树", level=2)
table(doc,
      ["层", "被调用的函数", "所在文件"],
      [["L0 应用", "main()", "main.c"],
       ["L1 封装", "VL53L0X_Init / CheckConnection / ReadDistance / StartContinuous / "
        "ReadContinuous / StopContinuous / ShowDistance / ShowRangeStatus / ShowError", "VL53L0X.c"],
       ["L2 官方 API", "VL53L0X_DataInit / StaticInit / SetDeviceMode / StartMeasurement / "
        "GetMeasurementDataReady / GetRangingMeasurementData / ClearInterruptMask / StopMeasurement / "
        "PerformSingleRangingMeasurement", "vl53l0x_api.c"],
       ["L2 官方校准", "VL53L0X_PerformRefCalibration / PerformRefSpadManagement", "vl53l0x_api_calibration.c"],
       ["L2 总线", "VL53L0X_comms_initialise / VL53L0X_I2C_TestConnection", "vl53l0x_i2c_platform.c"],
       ["L3 寄存器封装", "VL53L0X_ReadMulti / WriteMulti / RdByte / WrByte / WrWord / RdWord ...",
        "vl53l0x_platform.c"],
       ["L4 软件 I2C", "VL53L0X_read_multi / write_multi / read_byte / write_byte ...",
        "vl53l0x_i2c_platform.c"],
       ["L5 GPIO / 延时", "GPIO_WriteBit / GPIO_ReadInputDataBit / Delay_us", "标准外设库 + System\\Delay.c"],
       ["L2' 显示分支", "OLED_ShowString / OLED_ShowNum / OLED_Clear", "Hardware\\OLED.c"]],
      widths=[2.6, 9.4, 4.0])

# ============================================================================
doc.add_heading("4  main.c：它其实只做了 4 件事", level=1)

doc.add_heading("4.1  main() 的顺序结构", level=2)
table(doc,
      ["步", "main.c 里的代码", "调用的 VL53L0X.c 函数", "目的"],
      [["1", "OLED_Init(); OLED_Clear();\nOLED_ShowString(1,1,\"VL53L0X Init...\")", "—", "先把屏幕点亮给反馈，避免白屏等待"],
       ["2", "Status = VL53L0X_Init()", "VL53L0X_Init", "初始化，约 100~300ms"],
       ["3", "Status = VL53L0X_StartContinuous()", "VL53L0X_StartContinuous", "启动后台连续测量"],
       ["4", "OLED_Clear(); OLED_ShowString(1,1,\"VL53L0X Ranging\")", "—", "清屏，准备第 2/3/4 行显示数据"],
       ["5", "while(1) { … }", "VL53L0X_ReadContinuous\nVL53L0X_ShowDistance\nVL53L0X_ShowRangeStatus",
        "主循环取数并刷新"]],
      widths=[0.9, 6.8, 4.4, 3.9], mono_cols=(1, 2))

doc.add_heading("4.2  主循环的三种分支", level=2)
doc.add_paragraph(
    "while(1) 里只有一个 VL53L0X_ReadContinuous() 调用，返回值的三种情况各走一条路：")
table(doc,
      ["ReadContinuous 返回", "含义", "main 的动作"],
      [["VL53L0X_ERROR_NONE (0)", "拿到了一次新的测量结果",
        "VL53L0X_ShowDistance(2, dist) + VL53L0X_ShowRangeStatus(3, status)"],
       ["VL53L0X_NOT_READY (1)", "这一次还没测完（不是错误）", "什么都不做，直接进入下一轮 while"],
       ["其它（负数）", "真正的错误",
        "VL53L0X_ShowError(4, status) → Delay_ms(200) → VL53L0X_StartContinuous() 尝试自愈"]],
      widths=[4.6, 4.4, 7.0], mono_cols=(0,))

note(doc,
     "NOT_READY 就是这里的设计核心：ReadContinuous 是非阻塞的，没测完立刻返回，"
     "主循环不会被 33ms 的测量周期卡住。如果写成阻塞式（像 ReadDistance 那样），"
     "主循环每一轮都要等满一个测量周期，按键、蜂鸣器之类的响应会变迟钝。")

doc.add_heading("4.3  main.c 与 VL53L0X.c 的耦合面只有 6 个函数", level=2)
doc.add_paragraph("main.c 全文只调用了这些 VL53L0X 的公开成员：")
bullet(doc, "VL53L0X_Init")
bullet(doc, "VL53L0X_StartContinuous")
bullet(doc, "VL53L0X_ReadContinuous")
bullet(doc, "VL53L0X_ShowDistance")
bullet(doc, "VL53L0X_ShowRangeStatus")
bullet(doc, "VL53L0X_ShowError")
doc.add_paragraph(
    "另外还用到 VL53L0X_ERROR_NONE 和 VL53L0X_NOT_READY 两个宏。"
    "main.c 里没有出现 VL53L0X_Device、没有 I2C 操作、没有寄存器名 —— 分层是干净的。"
    "反过来，VL53L0X.c 里也完全没有任何对 main 的引用，两者是单向依赖。")

doc.add_heading("4.4  想改成单次测量？只换主循环", level=2)
code(doc, """
while (1)
{
    if (VL53L0X_ReadDistance(&Distance_mm, &RangeStatus) == VL53L0X_ERROR_NONE)
    {
        VL53L0X_ShowDistance(2, Distance_mm);
        VL53L0X_ShowRangeStatus(3, RangeStatus);
    }
}
""")
doc.add_paragraph(
    "与连续模式的差别：① 不需要第 3 步 StartContinuous；"
    "② ReadDistance 内部会自己 SetDeviceMode(SINGLE_RANGING) 并阻塞等待结果；"
    "③ 刷新率从约 30Hz 降到约 30Hz 单次（每轮一次往返，约 33ms）。"
    "main.c 文件末尾的注释里就保留了这段代码。")

# ============================================================================
doc.add_heading("5  一次测距的完整时序：从 while(1) 到 I2C 波形", level=1)
doc.add_paragraph(
    "把三个文件和下层驱动串起来看，一次成功取数会经过这些环节：")
table(doc,
      ["步", "执行者（文件）", "动作", "数据形态"],
      [["1", "main.c", "VL53L0X_ReadContinuous(&dist, &st)", "—"],
       ["2", "VL53L0X.c", "检查 VL53L0X_ContinuousMode != 0", "—"],
       ["3", "VL53L0X.c → vl53l0x_api.c", "VL53L0X_GetMeasurementDataReady(Dev, &ready)", "—"],
       ["4", "api.c → platform.c → i2c_platform.c",
        "读 RESULT_INTERRUPT_STATUS（0x13），判断 bit0~2 是否 0x04(NEW_SAMPLE_READY)", "1 字节"],
       ["5", "vl53l0x_i2c_platform.c",
        "软件 I2C 时序：START → 0x52+W → 0x13 → 重复 START → 0x53+R → 读 1 字节 → NACK → STOP",
        "PA0/PA1 电平"],
       ["6", "VL53L0X.c", "ready==0 → return VL53L0X_NOT_READY（main 本轮跳过）", "—"],
       ["7", "VL53L0X.c → vl53l0x_api.c", "VL53L0X_GetRangingMeasurementData(Dev, &Data)", "—"],
       ["8", "vl53l0x_api.c", "VL53L0X_ReadMulti(Dev, 0x14, buf, 12) —— 从 0x14 起连读 12 字节",
        "12 字节缓冲"],
       ["9", "vl53l0x_api.c",
        "RangeMilliMeter = VL53L0X_MAKEUINT16(buf[11], buf[10]) = (buf[10]<<8) + buf[11]",
        "uint16（mm）"],
       ["10", "vl53l0x_api.c", "RangeStatus = get_pal_range_status(buf[0]) 换算", "uint8"],
       ["11", "vl53l0x_api.c → platform.c", "VL53L0X_ClearInterruptMask(NEW_SAMPLE_READY) 写 0x0B", "—"],
       ["12", "VL53L0X.c", "缓存 LastDistance / LastStatus，回填 *pDistance_mm / *pRangeStatus", "—"],
       ["13", "main.c → VL53L0X.c → OLED.c", "VL53L0X_ShowDistance → OLED_ShowString/OLED_ShowNum → PB8/PB9 软件 I2C",
        "屏幕像素"]],
      widths=[0.9, 4.6, 7.6, 2.9])

note(doc,
     "第 9 步是个容易看反的地方：ST 的宏定义是 VL53L0X_MAKEUINT16(lsb, msb)，第一个参数是低字节。"
     "所以调用写成 MAKEUINT16(buf[11], buf[10])，实际等于 (buf[10]<<8) + buf[11]，"
     "即 0x14+10 号寄存器是距离高字节。改写底层时如果按「先读到的是高字节」去拼，距离会变成乱值。")

# ============================================================================
doc.add_heading("6  与其他文件的关系（简要）", level=1)
doc.add_paragraph(
    "VL53L0X.c 下面压着 7 个 ST 官方驱动文件，旁边还依赖 OLED.c 和 Delay.c。"
    "它们的分工如下：")
table(doc,
      ["文件", "谁调用它", "它提供什么", "本次是否改动"],
      [["Hardware\\OLED.c", "VL53L0X.c 的 Show*、main.c",
        "OLED_Init / OLED_Clear / OLED_ShowString / OLED_ShowNum", "未改"],
       ["System\\Delay.c", "main.c、vl53l0x_i2c_platform.c",
        "Delay_us / Delay_ms（按 72MHz 计算）", "未改"],
       ["vl53l0x_api.c", "VL53L0X.c",
        "全部高层 API：DataInit / StaticInit / SetDeviceMode / StartMeasurement / "
        "GetMeasurementDataReady / GetRangingMeasurementData / ClearInterruptMask / "
        "PerformSingleRangingMeasurement 等", "原封不动"],
       ["vl53l0x_api_core.c", "仅 vl53l0x_api.c 内部",
        "时序预算计算、tuning 参数载入、pal range status 换算", "原封不动"],
       ["vl53l0x_api_calibration.c", "vl53l0x_api.c", "参考校准、参考 SPAD 管理", "原封不动"],
       ["vl53l0x_api_strings.c", "（本工程未调用）", "状态码/错误码的官方长字符串",
        "原封不动（OLED 只有 16 列，改用自己写的短词）"],
       ["vl53l0x_platform.c", "vl53l0x_api.c / api_core.c",
        "寄存器级封装：ReadMulti / WriteMulti / RdByte / WrByte / RdWord / WrWord / UpdateByte",
        "删掉 <Windows.h>，改掉 Windows 延时"],
       ["vl53l0x_i2c_platform.c", "vl53l0x_platform.c、VL53L0X.c",
        "VL53L0X_write_multi / read_multi / comms_initialise / I2C_TestConnection 等 18 个函数",
        "整体重写为 PA0/PA1 软件 I2C"],
       ["vl53l0x_def.h、vl53l0x_device.h、vl53l0x_types.h",
        "所有上层文件", "类型定义、错误码枚举、寄存器地址", "types.h 做了 ARMCC 适配"]],
      widths=[4.6, 3.6, 5.6, 2.2])

doc.add_paragraph(
    "一句话概括分层原则：真正碰硬件的只有两层 —— vl53l0x_i2c_platform.c（传感器总线）和 OLED.c（显示总线），"
    "其余全部是与硬件无关的纯逻辑，所以移植时只动了这两处 + 一个延时。")

# ============================================================================
doc.add_heading("7  13 个函数速查表", level=1)
table(doc,
      ["函数", "位置", "一句话作用", "副作用"],
      [["VL53L0X_Init", "VL53L0X.c:38", "7 步初始化", "大量 I2C 读写，约 100~300ms"],
       ["VL53L0X_CheckConnection", "VL53L0X.c:93", "I2C 探活", "1 次地址探测"],
       ["VL53L0X_ReadDistance", "VL53L0X.c:105", "单次阻塞测量", "切模式 + 测量 + 清中断"],
       ["VL53L0X_StartContinuous", "VL53L0X.c:147", "启动连续测量", "置 ContinuousMode=1"],
       ["VL53L0X_ReadContinuous", "VL53L0X.c:176", "非阻塞取最新结果", "清中断、更新 2 个缓存变量"],
       ["VL53L0X_StopContinuous", "VL53L0X.c:219", "停止连续测量", "置 ContinuousMode=0"],
       ["VL53L0X_GetLastDistance", "VL53L0X.c:233", "读距离缓存", "无（纯内存读）"],
       ["VL53L0X_GetLastRangeStatus", "VL53L0X.c:239", "读状态缓存", "无（纯内存读）"],
       ["VL53L0X_ShowDistance", "VL53L0X.c:248", "显示距离", "只写 OLED"],
       ["VL53L0X_ShowRangeStatus", "VL53L0X.c:257", "显示测距状态", "只写 OLED"],
       ["VL53L0X_ShowError", "VL53L0X.c:266", "显示错误码", "只写 OLED"],
       ["VL53L0X_RangeStatusString", "VL53L0X.c:278", "状态码 → 英文短词", "无"],
       ["VL53L0X_ErrorString", "VL53L0X.c:292", "错误码 → 英文短词", "无"]],
      widths=[5.2, 2.6, 4.4, 3.8], mono_cols=(0, 1))

# ============================================================================
doc.add_heading("8  关键约束与易错点", level=1)

bullet(doc, "只有 RangeStatus == 0 距离才可信。1=SigmaFail（标准差过大）、2=SignalFail（回波太弱）、"
            "3=MinRange（目标太近，<3cm）、4=PhaseFail（相位超范围）、5=HardwareFail。")
bullet(doc, "忘记 ClearInterruptMask 的后果：器件不再产生 New Sample Ready，现象是「只测到一次，数字卡死」。"
            "已在 VL53L0X.c:202 处理。")
bullet(doc, "Ready 未置位就调测量函数，会立刻返回 VL53L0X_ERROR_INVALID_COMMAND，不是 I2C 问题。")
bullet(doc, "ShowDistance 固定显示 4 位，452mm 会显示成 0452；一行 16 列里 Dist:+4 位+mm 共占 13 格，留 3 格余量。")
bullet(doc, "ShowRangeStatus 从第 8 列开始写状态词，因为 \"Status:\" 占 1~7 列。")
bullet(doc, "PA1 同时是 LED1 的引脚，本程序没用 LED 模块所以不冲突；若要用 LED1，把 "
            "vl53l0x_i2c_platform.c 里的 SDA 改到其它空闲引脚。")
bullet(doc, "Delay_us 按 72MHz 实现，若系统时钟不是 72MHz，软件 I2C 的位速率会跟着变，"
            "过慢会让传感器超时，过快可能读到错数据。")

# ============================================================================
doc.add_heading("9  三句话记住这张关系网", level=1)
table(doc,
      ["问题", "答案"],
      [["「谁能被谁调？」",
        "main.c 只认 VL53L0X.h 里的 13 个函数；VL53L0X.c 认 ST 的 vl53l0x_api.c；"
        "vl53l0x_api.c 认 vl53l0x_platform.c；vl53l0x_platform.c 认 vl53l0x_i2c_platform.c 的软件 I2C。"
        "单向依赖，没有反向调用。"],
       ["「状态放在哪？」",
        "全部在 VL53L0X.c 的 6 个 static 变量里。main.c 看不见也改不了，"
        "只能通过函数间接影响，因此不会出现「上层乱改标志位导致下层行为异常」。"],
       ["「测距到底哪一步算出距离？」",
        "vl53l0x_api.c 的 VL53L0X_GetRangingMeasurementData()：从 0x14 连读 12 字节，"
        "用 (buf[10]<<8)+buf[11] 拼出 mm 值，再用 buf[0] 换算成 RangeStatus。"
        "VL53L0X.c 只是把这两个字段搬到缓存和 OLED 上。"]],
      widths=[3.6, 12.4])

doc.save(OUT)
print("saved:", OUT)
