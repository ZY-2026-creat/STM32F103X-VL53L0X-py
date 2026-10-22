# -*- coding: utf-8 -*-
"""
生成《VL53L0X 激光测距移植与 OLED 显示操作流程》docx 文档。
用法: python make_doc_flow.py
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "VL53L0X激光测距_移植与OLED显示操作流程.docx")

CJK = "微软雅黑"
ASCII_F = "Segoe UI"
MONO = "Consolas"


# ----------------------------------------------------------------------------
# 基础工具
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


# w:pPr 的子元素有严格的先后顺序（ECMA-376 CT_PPr 是一个 sequence）。
# w:shd 必须排在 w:spacing / w:ind / w:jc 之前，否则 Word 会认为文档损坏。
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
    """插入一段等宽代码块（每个物理行一个段落，保持紧凑）。"""
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


def bullet(doc, text, level=0):
    p = doc.add_paragraph(text, style='List Bullet')
    p.paragraph_format.left_indent = Cm(0.75 + 0.6 * level)
    p.paragraph_format.space_after = Pt(2)
    return p


def numbered(doc, text):
    p = doc.add_paragraph(text, style='List Number')
    p.paragraph_format.space_after = Pt(2)
    return p


_TBLPR_ORDER = ['w:tblStyle', 'w:tblpPr', 'w:tblOverlap', 'w:bidiVisual',
                'w:tblStyleRowBandSize', 'w:tblStyleColBandSize', 'w:tblW', 'w:jc',
                'w:tblCellSpacing', 'w:tblInd', 'w:tblBorders', 'w:shd', 'w:tblLayout',
                'w:tblCellMar', 'w:tblLook', 'w:tblCaption', 'w:tblDescription']


def _place(tblPr, el, tag):
    """按 CT_TblPrBase 的 schema 顺序插入子元素，避免 OOXML 顺序非法。"""
    idx = _TBLPR_ORDER.index(tag)
    for child in tblPr:
        cname = 'w:' + child.tag.split('}')[-1]
        if cname in _TBLPR_ORDER and _TBLPR_ORDER.index(cname) > idx:
            child.addprevious(el)
            return
    tblPr.append(el)


def _set_grid(t, widths_cm):
    """写入固定布局 + tblW + tblGrid + 每格 tcW。

    python-docx 的 add_table 只会生成等宽 gridCol，LibreOffice/Word 以 tblGrid 为准，
    因此不重写 tblGrid 的话，table(widths=...) 的比例不会生效。
    """
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


def table(doc, header, rows, widths=None):
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
            r.font.name = ASCII_F
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

# 页面与默认字体（A4，符合国内打印习惯）
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
t = doc.add_heading("VL53L0X 激光测距模块", level=0)
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
st = doc.add_paragraph()
st.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = st.add_run("STM32 标准外设库移植 · 测距实现 · OLED 显示\n—— 操作流程汇总")
r.font.size = Pt(14)
r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
r.font.name = ASCII_F
r._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
st.paragraph_format.space_after = Pt(16)

table(doc,
      ["项目", "内容"],
      [["器件", "VL53L0X ToF 激光测距传感器（GY-530 / 蓝板模块）"],
       ["官方驱动来源", "ST STSW-IMG005 —— VL53L0X_1.0.4（API v1.0.4.4960）"],
       ["目标平台", "STM32F103C8T6 + STM32F10x 标准外设库 v3.5.0 + Keil MDK"],
       ["I2C 实现方式", "GPIO 软件模拟 I2C（SCL = PA0，SDA = PA1）"],
       ["测量方式", "连续测量（Continuous Ranging），主循环轮询刷新"],
       ["显示设备", "SSD1306 OLED，4 行 × 16 列，软件 I2C（SCL = PB8，SDA = PB9）"],
       ["工程文件", "project.uvprojx（Target 1，Device = STM32F103C8）"]],
      widths=[3.6, 12.4])

# ============================================================================
doc.add_heading("1  任务目标", level=1)
doc.add_paragraph(
    "ST 官方提供的 VL53L0X 驱动包（STSW-IMG005）是为 PC / ST 自家 Nucleo 扩展板准备的，"
    "其中平台层直接调用了 Windows API 与 ST 的 DLL，无法在 STM32 标准库工程里编译，"
    "更无法运行。本文档记录把该驱动包改造成「STM32 标准外设库可用」的完整过程与结果，"
    "并给出用这些文件驱动 VL53L0X 测距、把距离实时显示在 OLED 屏幕上的操作方法。")
doc.add_paragraph("最终达成的效果：")
bullet(doc, "上电后 OLED 显示初始化提示，约 100~300 ms 完成模块初始化；")
bullet(doc, "随后进入连续测距，OLED 第 2 行显示距离（mm），第 3 行显示本次测量是否有效；")
bullet(doc, "若初始化失败，OLED 直接显示错误短词，便于对照本文第 11 章排查。")

# ============================================================================
doc.add_heading("2  文件清单与目录结构", level=1)
doc.add_paragraph(
    "所有官方源码集中放在 Hardware\\VL53L0X\\ 目录下，避免与工程里其它模块混在一起；"
    "本工程自己的应用层封装放在 Hardware\\ 下，与 LED.c / OLED.c 同级。")

doc.add_heading("2.1  官方驱动（Hardware\\VL53L0X\\）", level=2)
table(doc,
      ["文件", "作用", "本次是否修改"],
      [["vl53l0x_api.c / .h", "高层业务 API（初始化、测量、校准的总入口）", "否"],
       ["vl53l0x_api_core.c / .h", "底层算法与寄存器序列（tuning、时序预算、DMAX 等）", "否"],
       ["vl53l0x_api_calibration.c / .h", "参考校准、SPAD 管理、偏移/串扰校准", "否"],
       ["vl53l0x_api_ranging.c / .h", "单次测距辅助函数", "否"],
       ["vl53l0x_api_strings.c / .h", "状态码转长字符串（本工程未使用）", "否"],
       ["vl53l0x_def.h", "公共宏、枚举、数据结构（含错误码、测量结果结构体）", "否"],
       ["vl53l0x_device.h", "器件寄存器地址定义", "否"],
       ["vl53l0x_tuning.h", "官方出厂 tuning 参数表", "否"],
       ["vl53l0x_interrupt_threshold_settings.h", "中断阈值配置表", "否"],
       ["vl53l0x_types.h", "整型类型定义（依赖 stdint.h）", "是"],
       ["vl53l0x_platform.h", "平台层（PAL）接口与器件对象结构体", "否"],
       ["vl53l0x_platform.c", "寄存器读写封装，转调 I2C 层", "是"],
       ["vl53l0x_platform_log.h", "日志宏（默认关闭，宏展开为空）", "否"],
       ["vl53l0x_i2c_platform.h", "I2C 通信层接口声明", "是（追加 3 个声明）"],
       ["vl53l0x_i2c_platform.c", "I2C 通信层实现（原为 Windows 版）", "是（整体重写）"]],
      widths=[5.4, 8.0, 2.6])

doc.add_heading("2.2  本工程新增的应用层", level=2)
table(doc,
      ["文件", "作用"],
      [["Hardware\\VL53L0X.h", "应用层接口：初始化、单次/连续测量、OLED 显示封装"],
       ["Hardware\\VL53L0X.c", "应用层实现：按官方例程完成 7 步初始化，封装测量与显示"],
       ["User\\main.c", "主程序：初始化 OLED 与 VL53L0X，主循环连续测距并刷新屏幕"]],
      widths=[5.4, 10.6])

doc.add_paragraph("完整的调用关系如下：")
code(doc, """
User\\main.c                ← 你的业务代码
      │  VL53L0X_Init() / VL53L0X_ReadContinuous()
      ▼
Hardware\\VL53L0X.c         ← 本工程新增的应用层封装
      │  VL53L0X_DataInit() / VL53L0X_StaticInit() / VL53L0X_GetRangingMeasurementData() ...
      ▼
vl53l0x_api.c              ← ST 官方 API（业务逻辑，未改动）
      │  VL53L0X_WrByte() / VL53L0X_RdWord() ...
      ▼
vl53l0x_platform.c         ← ST 官方平台层 PAL（仅删掉 Windows.h）
      │  VL53L0X_write_multi() / VL53L0X_read_multi() ...
      ▼
vl53l0x_i2c_platform.c     ← 本次移植重写：软件模拟 I2C
      │  GPIO_SetBits / GPIO_ReadInputDataBit
      ▼
PA0 = SCL , PA1 = SDA       ← 硬件
""")

# ============================================================================
doc.add_heading("3  移植原理：为什么必须改这三个地方", level=1)
doc.add_paragraph(
    "ST 把驱动分成了三层，只有最底下两层依赖操作系统。只要把「I2C 通信层」换成 STM32 的实现，"
    "上层的算法代码一行都不用动，整条 API 就能跑起来。这也是本次移植的核心思路。")

table(doc,
      ["层次", "官方原实现", "问题", "移植做法"],
      [["高层 API", "纯 C 算法", "无", "原封不动"],
       ["平台层 PAL", "读写寄存器封装", "包含 <Windows.h>；延时用 CreateEvent/WaitForSingleObject",
        "删除 Windows 头文件，把毫秒延时改为调用 I2C 层的 VL53L0X_wait_ms()"],
       ["I2C 通信层", "调用 ranging_sensor_comms.dll 驱动 ST 官方 Nucleo 板",
        "依赖 Windows DLL、COM 口枚举，STM32 完全无法使用",
        "整文件重写为 GPIO 位操作软件 I2C"],
       ["类型定义", "兼容多种编译器的 stdint 兜底", "与 Keil ARMCC 的 stdint.h 重复定义，编译报错",
        "在兜底分支的宏判断里追加 !defined(__ARMCC_VERSION)"]],
      widths=[2.6, 4.2, 5.2, 4.0])

doc.add_heading("3.1  一个关键结论：API 层只调用了一个平台函数", level=2)
doc.add_paragraph(
    "通读全部官方源码后确认：高层 API 对平台层的依赖极小。除了寄存器读写封装之外，"
    "真正被调用的平台函数只有 VL53L0X_PollingDelay() 一个（出现在 vl53l0x_api.c 与 "
    "vl53l0x_api_core.c 内部，用于等待器件完成内部操作）。"
    "诸如 VL53L0X_wait_ms / VL53L0X_platform_wait_us / VL53L0X_get_timer_* / "
    "VL53L0X_cycle_power / VL53L0X_set_gpio / VL53L0X_get_gpio / VL53L0X_release_gpio "
    "这些函数官方 API 层从不调用。")
doc.add_paragraph(
    "因此本移植对它们采取「有则实现、无则返回失败」的策略："
    "VL53L0X_wait_ms 与 VL53L0X_platform_wait_us 用 Delay 实现（PollingDelay 依赖前者）；"
    "其余和 Nucleo 扩展板电平转换、时间戳相关的函数直接返回失败（STATUS_FAIL），"
    "既保证了头文件里声明的符号齐全、不产生链接错误，也不会影响测距功能。")

# ============================================================================
doc.add_heading("4  逐文件修改说明", level=1)

doc.add_heading("4.1  vl53l0x_types.h —— 解决与 Keil stdint.h 的重复定义", level=2)
doc.add_paragraph("问题：文件先 #include <stdint.h>，随后用 4 个编译器私有宏判断是否需要自己兜底 "
                  "typedef 整型。Keil ARMCC / ARMCLANG 自带的 stdint.h 使用的是 __stdint_h "
                  "之类的保护宏，导致上述 4 个宏一个都不成立，兜底 typedef 被错误启用，"
                  "编译报 “uint32_t has already been declared”。")
doc.add_paragraph("改法：在判断条件里追加 !defined(__ARMCC_VERSION)（该宏由 ARM 编译器自动定义）。")
doc.add_paragraph("修改后：")
code(doc, """
#if ! defined(STDINT_H) &&  !defined(_GCC_STDINT_H) &&!defined(__STDINT_DECLS) \\
    && !defined(_GCC_WRAP_STDINT_H) && !defined(__ARMCC_VERSION)
    /* 兼容性兜底 typedef ... （Keil 下不会再进入这里） */
#endif
""")
doc.add_paragraph("另外文件末尾的 typedef uint32_t FixPoint1616_t; 必须保留，ST 大量使用该定点类型。")

doc.add_heading("4.2  vl53l0x_platform.c —— 去掉 Windows 依赖", level=2)
doc.add_paragraph("本文件除了两处 Windows 代码外，其余都是纯寄存器封装，全部转调 I2C 层的 "
                  "VL53L0X_write_multi / read_multi / write_byte / read_byte / write_word / "
                  "read_word / write_dword / read_dword，失败时返回 VL53L0X_ERROR_CONTROL_INTERFACE。")
bullet(doc, "第 40 行：删除 #include <Windows.h>。")
bullet(doc, "VL53L0X_PollingDelay()：原实现用 CreateEvent + WaitForSingleObject 等待 1 ms，"
            "改为直接调用 VL53L0X_wait_ms(1)（该函数在 I2C 层用 Delay_ms 实现），效果完全等价。")
bullet(doc, "该函数不再使用 Dev 参数，补一句 (void)Dev; 消除「参数未引用」编译警告。")
doc.add_paragraph("修改后的延时函数：")
code(doc, """
VL53L0X_Error VL53L0X_PollingDelay(VL53L0X_DEV Dev){
    VL53L0X_Error status = VL53L0X_ERROR_NONE;
    LOG_FUNCTION_START("");
    (void)Dev;              /* 本实现不使用 Dev，消除编译警告 */

    VL53L0X_wait_ms(1);     /* 原 Windows 版为 CreateEvent + WaitForSingleObject(1ms) */

    LOG_FUNCTION_END(status);
    return status;
}
""")

doc.add_heading("4.3  vl53l0x_i2c_platform.c —— 整体重写为软件 I2C（核心）", level=2)
doc.add_paragraph(
    "官方同名文件依赖 <windows.h>、ranging_sensor_comms.h、comms_platform.h 以及 "
    "RANGING_SENSOR_COMMS_* 系列 DLL 接口，在 STM32 上完全不可用。本文件按 "
    "vl53l0x_i2c_platform.h 的契约重新实现了全部函数。")

doc.add_heading("（1）用户配置区：只改 3 行就能换引脚", level=3)
code(doc, """
#define VL53L0X_I2C_GPIO        GPIOA                /* 使用的 GPIO 端口    */
#define VL53L0X_I2C_RCC         RCC_APB2Periph_GPIOA /* 对应的时钟          */
#define VL53L0X_SCL_PIN         GPIO_Pin_0           /* PA0 -> 模块 SCL     */
#define VL53L0X_SDA_PIN         GPIO_Pin_1           /* PA1 -> 模块 SDA     */

/* 半周期延时(us)。2us 时 SCL 约 80~100kHz，短排线非常稳 */
#define VL53L0X_I2C_DELAY_US    2
""")

doc.add_heading("（2）软件 I2C 时序", level=3)
doc.add_paragraph("实现了标准 I2C 时序：起始条件、停止条件、发送 1 字节并读 ACK、接收 1 字节并回 "
                  "ACK/NACK、总线恢复（最多 9 个 SCL 脉冲 + STOP）。辅助函数采用 static，"
                  "对外只暴露 VL53L0X_I2C_Init / VL53L0X_I2C_BusRecovery / "
                  "VL53L0X_I2C_TestConnection 三个（声明追加在 ST 头文件末尾的 extern \"C\" 块内）。")
doc.add_paragraph("关键实现片段：")
code(doc, """
/* 起始条件：SCL 为高时，SDA 由高变低 */
static void VL53L0X_I2C_Start(void){
    SDA_HIGH(); SCL_HIGH(); VL53L0X_I2C_Delay();
    SDA_LOW();              VL53L0X_I2C_Delay();
    SCL_LOW();              VL53L0X_I2C_Delay();
}

/* 发送 1 字节（高位先发），返回 0 = 收到 ACK */
static uint8_t VL53L0X_I2C_WriteByte(uint8_t data){
    for (i = 0; i < 8; i++) {
        if (data & 0x80) SDA_HIGH(); else SDA_LOW();
        data <<= 1;
        VL53L0X_I2C_Delay();
        SCL_HIGH(); VL53L0X_I2C_Delay();   /* SCL 高电平期间数据必须稳定 */
        SCL_LOW();  VL53L0X_I2C_Delay();
    }
    SDA_HIGH(); VL53L0X_I2C_Delay();       /* 第 9 个时钟：主机释放 SDA */
    SCL_HIGH(); VL53L0X_I2C_Delay();
    ack = (SDA_READ() == 0) ? 0 : STATUS_FAIL;
    SCL_LOW();  VL53L0X_I2C_Delay();
    return ack;
}
""")

doc.add_heading("（3）官方契约里的全部函数", level=3)
table(doc,
      ["函数", "本移植实现方式"],
      [["VL53L0X_comms_initialise(type, khz)", "只支持 I2C，内部调用 VL53L0X_I2C_Init() 配置 PA0/PA1"],
       ["VL53L0X_comms_close()", "空实现，返回成功"],
       ["VL53L0X_write_multi / read_multi", "软件 I2C 完整时序；读用重复起始条件（Repeated START）"],
       ["VL53L0X_write_byte / word / dword", "转调 write_multi；word/dword 按大端顺序拆字节"],
       ["VL53L0X_read_byte / word / dword", "转调 read_multi；word/dword 按大端顺序拼字节"],
       ["VL53L0X_platform_wait_us(us)", "转调 Delay_us()"],
       ["VL53L0X_wait_ms(ms)", "转调 Delay_ms()，供 VL53L0X_PollingDelay 使用"],
       ["VL53L0X_cycle_power()", "未接 XSHUT，无法硬复位，返回失败"],
       ["VL53L0X_set_gpio / get_gpio / release_gpio", "Nucleo 扩展板电平转换专用，返回失败（API 层不调用）"],
       ["VL53L0X_get_timer_frequency / value", "时间戳功能，本工程用不到，返回失败"]],
      widths=[6.4, 9.6])

doc.add_heading("（4）一个必须注意的坑：字节序", level=3)
doc.add_paragraph("VL53L0X 的 16/32 位寄存器是**大端**的。写的时候先写高字节，读的时候 "
                  "((uint16_t)buf[0] << 8) + buf[1]。如果这里写反，器件不会报错，"
                  "但所有参数都会离奇错乱（典型现象：初始化卡在超时、或测出来的距离恒定不变）。")

doc.add_heading("4.4  vl53l0x_i2c_platform.h —— 追加 3 个声明", level=2)
doc.add_paragraph("在文件末尾、extern \"C\" 块内部（#ifdef __cplusplus } 之前）追加了本次新增的"
                  "软件 I2C 辅助接口声明，使 ST 官方头文件保持结构完整：")
code(doc, """
void    VL53L0X_I2C_Init(void);
void    VL53L0X_I2C_BusRecovery(void);
uint8_t VL53L0X_I2C_TestConnection(uint8_t address);   /* 0 = 有应答 */
""")

doc.add_heading("4.5  Hardware\\VL53L0X.c / .h —— 新增应用层封装", level=2)
doc.add_paragraph("这一层把官方 API 包装成和 LED.c / OLED.c 一样「一句话就能用」的模块，"
                  "对外接口如下：")
table(doc,
      ["函数", "说明"],
      [["VL53L0X_Error VL53L0X_Init(void)",
        "打开软件 I2C → 探活 → DataInit → StaticInit → 参考校准 → 参考 SPAD 管理，约 100~300 ms"],
       ["VL53L0X_Error VL53L0X_CheckConnection(void)", "只做 I2C 探活，不读寄存器，最安全的连接检测"],
       ["VL53L0X_Error VL53L0X_ReadDistance(uint16_t*, uint8_t*)", "单次测量（阻塞），调用一次测一次"],
       ["VL53L0X_Error VL53L0X_StartContinuous(void)", "启动连续测量（器件在后台自行不停测量）"],
       ["VL53L0X_Error VL53L0X_ReadContinuous(uint16_t*, uint8_t*)",
        "非阻塞读取最新结果：0 = 有新数据，1（VL53L0X_NOT_READY）= 还没测完，负数 = 出错"],
       ["void VL53L0X_StopContinuous(void)", "停止连续测量"],
       ["uint16_t VL53L0X_GetLastDistance(void)", "取最近一次成功读到的距离"],
       ["void VL53L0X_ShowDistance(Line, mm)",
        "在 OLED 指定行显示 “Dist:0452mm”（固定 4 位，不足自动补 0）"],
       ["void VL53L0X_ShowRangeStatus(Line, status)", "显示 “Status:Valid” 等状态短词"],
       ["void VL53L0X_ShowError(Line, err)", "显示 “Err:I2C Fail” 等错误短词"],
       ["char *VL53L0X_RangeStatusString / VL53L0X_ErrorString", "状态码/错误码转不超过 11 字符的英文短词"]],
      widths=[6.8, 9.2])

doc.add_paragraph("内部使用一个静态器件对象（放在 BSS，上电自动清零），并通过 ST 的 "
                  "PALDevDataGet / PALDevDataSet 机制被官方 API 访问：")
code(doc, """
static VL53L0X_Dev_t   VL53L0X_Device;
static VL53L0X_DEV     pVL53L0X_Device = &VL53L0X_Device;

pVL53L0X_Device->I2cDevAddr      = 0x52;   /* 7 位地址 0x29，ST 要求传 8 位写地址 */
pVL53L0X_Device->comms_type      = 1;      /* 1 = I2C */
pVL53L0X_Device->comms_speed_khz = 400;
""")
doc.add_paragraph("说明：官方 API 提供 VL53L0X_ErrorString()（在 vl53l0x_api_strings.c 中），"
                  "但它返回的字符串很长（如 “Control Interface Error”），4×16 的 OLED 放不下，"
                  "因此应用层自己实现了一套短字符串版本。")

doc.add_heading("4.6  User\\main.c —— 主程序", level=2)
doc.add_paragraph("主程序只做四件事：初始化 OLED 并提示 → 初始化 VL53L0X（失败则显示错误码并停住）→ "
                  "启动连续测量 → 主循环轮询刷新屏幕。")
code(doc, """
int main(void)
{
    uint16_t      Distance_mm = 0;
    uint8_t       RangeStatus = 0;
    VL53L0X_Error Status;

    OLED_Init();
    OLED_Clear();
    OLED_ShowString(1, 1, "VL53L0X Init...");

    Status = VL53L0X_Init();                       /* 第 2 步：初始化模块 */
    if (Status != VL53L0X_ERROR_NONE) {
        OLED_Clear();
        VL53L0X_ShowError(1, Status);              /* 失败就把错误码显示出来 */
        while (1) { }
    }

    Status = VL53L0X_StartContinuous();            /* 第 3 步：启动连续测量 */
    if (Status != VL53L0X_ERROR_NONE) { OLED_Clear(); VL53L0X_ShowError(1, Status); while (1) { } }

    OLED_Clear();
    OLED_ShowString(1, 1, "VL53L0X Ranging");

    while (1)                                      /* 第 4 步：主循环刷新 */
    {
        Status = VL53L0X_ReadContinuous(&Distance_mm, &RangeStatus);

        if (Status == VL53L0X_ERROR_NONE) {
            VL53L0X_ShowDistance(2, Distance_mm);      /* Dist:0452mm  */
            VL53L0X_ShowRangeStatus(3, RangeStatus);   /* Status:Valid */
        } else if (Status != VL53L0X_NOT_READY) {
            VL53L0X_ShowError(4, Status);
            Delay_ms(200);
            VL53L0X_StartContinuous();                 /* 出错后尝试重启测量 */
        }
    }
}
""")

doc.add_heading("4.7  project.uvprojx —— 工程文件更新", level=2)
bullet(doc, "IncludePath 增加 .\\Hardware\\VL53L0X，"
            "变为：.\\Start;.\\Library;.\\System;.\\Hardware;.\\Hardware\\VL53L0X;.\\User")
bullet(doc, "Hardware 分组下新增 VL53L0X.c / VL53L0X.h，以及 VL53L0X\\ 目录下的 "
            "7 个 .c 与 13 个头文件；原先散放在 Hardware\\ 根目录的 11 个官方文件条目已移除。")

# ============================================================================
doc.add_page_break()
doc.add_heading("5  硬件接线", level=1)
doc.add_heading("5.1  VL53L0X 与 STM32 的连接", level=2)
table(doc,
      ["VL53L0X 模块引脚", "接到 STM32", "说明"],
      [["VCC", "3.3V", "GY-530/蓝板模块自带稳压与电平转换，接 5V 也可"],
       ["GND", "GND", "必须共地"],
       ["SCL", "PA0", "软件模拟 I2C 时钟（由 vl53l0x_i2c_platform.c 定义）"],
       ["SDA", "PA1", "软件模拟 I2C 数据（双向，开漏 + 模块上拉）"],
       ["XSHUT", "不接", "悬空即可；模块自带该脚上拉，器件默认为使能状态"],
       ["GPIO1", "不接", "中断输出脚；本程序用轮询判断数据就绪，不需要它"]],
      widths=[4.2, 3.0, 8.8])

doc.add_heading("5.2  OLED 的连接（本工程已有驱动）", level=2)
table(doc,
      ["OLED 引脚", "接到 STM32"],
      [["VCC / GND", "3.3V / GND"],
       ["SCL", "PB8"],
       ["SDA", "PB9"]],
      widths=[5.0, 5.0])

doc.add_heading("5.3  引脚冲突提示（重要）", level=2)
doc.add_paragraph("本工程现有模块占用的引脚如下，选 PA0/PA1 之前请确认没有冲突：")
table(doc,
      ["引脚", "被谁占用"],
      [["PA1 / PA2", "LED1 / LED2（Hardware\\LED.c）——注意 PA1 与 VL53L0X 的 SDA 冲突"],
       ["PB1 / PB11", "KEY1 / KEY2（Hardware\\Key.c）"],
       ["PB8 / PB9", "OLED 的 SCL / SDA（Hardware\\OLED.c）"],
       ["PB12", "蜂鸣器（Hardware\\Buzzer.c）"],
       ["PB13", "光敏传感器（Hardware\\LightSensor.c）"],
       ["PA9 / PA10", "串口 USART1（下载与调试）"]],
      widths=[3.6, 12.4])
doc.add_paragraph("处理办法（二选一）：")
bullet(doc, "本程序不使用 LED 模块，因此 PA1 与 LED1 的冲突实际不存在——直接按本文接线即可；")
bullet(doc, "如果确实要在同一个程序里同时使用 LED1，把 "
            "Hardware\\VL53L0X\\vl53l0x_i2c_platform.c 里的 VL53L0X_SDA_PIN 改到别的空闲引脚"
            "（例如 GPIO_Pin_3 = PA3），重新编译即可，其它代码无需改动。")

doc.add_heading("5.4  上拉电阻", level=2)
doc.add_paragraph("软件 I2C 依赖总线上拉。GY-530 / 蓝板模块上已经焊了 4.7kΩ 上拉电阻，"
                  "直接连即可；如果用的是只有 6 个引脚的裸板，需要在 SCL、SDA 上各加一个 "
                  "4.7kΩ 上拉到 3.3V，否则会出现「有供电但 I2C 无应答」的现象。")

# ============================================================================
doc.add_heading("6  Keil 工程配置步骤", level=1)
doc.add_paragraph("本工程的 project.uvprojx 已经改好，直接用 Keil 打开即可编译。"
                  "如果你是把这个驱动搬到别的工程里，请按下面的步骤手工配置。")

numbered(doc, "把 Hardware\\VL53L0X\\ 整个目录拷贝到新工程下，并把 VL53L0X.c / VL53L0X.h 拷到 "
              "Hardware\\ 下。")
numbered(doc, "在 Keil 左侧工程树里新建一个分组，例如 VL53L0X，然后 Add Files 添加下面 7 个 .c 文件：")
code(doc, """
vl53l0x_api.c
vl53l0x_api_calibration.c
vl53l0x_api_core.c
vl53l0x_api_ranging.c
vl53l0x_api_strings.c
vl53l0x_platform.c
vl53l0x_i2c_platform.c
""")
numbered(doc, "勾选 Options for Target → C/C++ → Include Paths，加入 .\\Hardware\\VL53L0X"
              "（同时确保 .\\Hardware 已在其中）。")
numbered(doc, "确认 Options for Target → C/C++ → Define 里有 USE_STDPERIPH_DRIVER"
              "（标准库工程必需，本工程已配置）。")
numbered(doc, "建议在 C/C++ 页面勾选 C99 Mode；官方源码含有变量声明在语句之后的写法。")
numbered(doc, "不要重复添加同名的旧文件：如果 Hardware\\ 根目录还留着 ST 官方原始的 "
              "vl53l0x_*.c，一定要从工程里移除或删掉，否则会出现重复符号（multiply defined）"
              "或编译到 Windows 版本的错误。")
numbered(doc, "编译。如果提示找不到 vl53l0x_*.h，说明第 3 步的包含路径没生效。")
numbered(doc, "下载到 STM32F103C8 后复位运行。")

doc.add_paragraph("关于日志：vl53l0x_platform_log.h 里的日志宏在默认情况下展开为空，"
                  "不需要额外文件。若要打开日志调试，需要定义 VL53L0X_LOG_ENABLE 并把官方包里的 "
                  "vl53l0x_platform_log.c 一并加入工程（还会牵涉 printf 重定向，比较占资源，"
                  "测距应用通常不需要）。")

# ============================================================================
doc.add_heading("7  软件 I2C 的关键设计要点", level=1)
table(doc,
      ["要点", "做法与原因"],
      [["引脚模式", "统一配置为 GPIO_Mode_Out_OD（开漏输出）。写 1 = 释放总线让上拉把电平拉高，"
                    "写 0 = 主动拉低，这才是真正的 I2C 行为"],
       ["如何读数据", "开漏输出时 STM32 的输入寄存器依然有效，直接 GPIO_ReadInputDataBit() 读回引脚真实电平，"
                      "不需要在输出/输入模式之间来回切换"],
       ["字节序", "word/dword 一律大端：先发高字节。写错不会报错，只会让参数全部错乱"],
       ["重复起始", "读寄存器时使用 Repeated START（不发 STOP 直接重新发起始条件），符合器件手册要求"],
       ["时钟速率", "半周期延时 2us，SCL 约 80~100kHz。VL53L0X 支持到 400kHz，但软件 I2C 用较低速率更稳；"
                    "想加速可把 VL53L0X_I2C_DELAY_US 改成 1"],
       ["总线恢复", "提供了 VL53L0X_I2C_BusRecovery()：从机异常把 SDA 拉死时补发最多 9 个 SCL 脉冲加一个 STOP，"
                    "把它冲出来（热插拔或复位瞬间偶发）"],
       ["探活方式", "VL53L0X_I2C_TestConnection() 只发地址等 ACK，不读寄存器，因此不会干扰器件状态，"
                    "适合作为初始化的第一步"]],
      widths=[3.2, 12.8])

# ============================================================================
doc.add_page_break()
doc.add_heading("8  测距工作流程", level=1)

doc.add_heading("8.1  上电初始化（7 步，对应 ST 官方例程）", level=2)
doc.add_paragraph("VL53L0X_Init() 内部严格按官方 vl53l0x_ContinuousRanging_Example.c 的顺序执行：")
table(doc,
      ["步骤", "调用", "作用"],
      [["1", "VL53L0X_comms_initialise(I2C, 400)", "打开软件 I2C：配置 PA0/PA1 为开漏输出"],
       ["2", "VL53L0X_CheckConnection()", "I2C 探活，避免后面一连串函数白白超时"],
       ["3", "填写 I2cDevAddr / comms_type / comms_speed_khz", "告诉 ST API 器件地址是 0x52、用 I2C"],
       ["4", "VL53L0X_DataInit()", "数据初始化，复位器件内部状态"],
       ["5", "VL53L0X_StaticInit()", "载入官方 tuning 参数并设置参考 SPAD"],
       ["6", "VL53L0X_PerformRefCalibration()", "参考校准（VHV 设置 + 相位校准）"],
       ["7", "VL53L0X_PerformRefSpadManagement()", "参考 SPAD 管理，保证弱回波下也能测"]],
      widths=[1.4, 6.6, 8.0])
doc.add_paragraph("其中第 1、2 步是本移植特有的（官方 Windows 版由 DLL 完成）。"
                  "整个初始化过程需要约 100~300 ms，因此 main() 里先显示 "
                  "“VL53L0X Init...” 再调用它是好习惯。")

doc.add_heading("8.2  连续测量（本工程采用）", level=2)
doc.add_paragraph("连续测量让器件在后台自行不断测量，CPU 只需要轮询「这一轮测完了吗」，"
                  "最适合放到主循环里刷新 OLED：")
code(doc, """
VL53L0X_StartContinuous();                  /* 1. 设为连续测距模式并启动 */

while (1)
{
    Status = VL53L0X_ReadContinuous(&Distance_mm, &RangeStatus);
    /* 内部：VL53L0X_GetMeasurementDataReady()  -> 测完了吗？
     *        VL53L0X_GetRangingMeasurementData() -> 把结果读出来
     *        VL53L0X_ClearInterruptMask()        -> 清中断，否则不会产生下一次就绪 */

    if (Status == VL53L0X_ERROR_NONE) {
        /* 有新数据，Distance_mm / RangeStatus 有效 */
    } else if (Status == VL53L0X_NOT_READY) {
        /* 这一轮还没测完，直接跳过，什么都不用做 */
    } else {
        /* 真正的错误 */
    }
}
""")
doc.add_paragraph("注意：连续测量模式下必须调用 VL53L0X_ClearInterruptMask() 清除 "
                  "“New Sample Ready” 中断标志，否则器件只会产生一次就绪信号，"
                  "表现为「只有第一次读数会更新」。这一点已经在 VL53L0X_ReadContinuous() "
                  "内部处理好了。")

doc.add_heading("8.3  单次测量（备选写法）", level=2)
doc.add_paragraph("如果只需要 1~10 Hz 的慢速刷新，单次测量的写法更简单，把主循环换成：")
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
doc.add_paragraph("VL53L0X_ReadDistance() 内部会先停掉连续测量，再调用官方的 "
                  "VL53L0X_PerformSingleRangingMeasurement()，该函数一次性完成 "
                  "「启动测量 → 等待完成 → 取结果 → 清中断」，是阻塞式的。")

# ============================================================================
doc.add_heading("9  OLED 显示格式", level=1)
doc.add_paragraph("屏幕为 4 行 × 16 列（行列均从 1 开始）。正常运行时的显示如下：")
code(doc, """
第1行: VL53L0X Ranging
第2行: Dist:0452mm          <- 距离，单位 mm，固定 4 位不足补 0
第3行: Status:Valid         <- 本次测量是否有效
第4行: (空)
""")
doc.add_paragraph("初始化失败时：")
code(doc, """
第1行: Err:I2C Fail         <- VL53L0X_ErrorString() 输出的短词
第2行: No I2C ACK at        <- 提示信息
第3行: PA0=SCL PA1=SDA
第4行: check 0x52 wire
""")
doc.add_paragraph("可用函数：VL53L0X_ShowDistance(行号, 距离)、"
                  "VL53L0X_ShowRangeStatus(行号, 状态)、VL53L0X_ShowError(行号, 错误码)。"
                  "它们内部调用本工程已有的 OLED_ShowString / OLED_ShowNum，"
                  "与 OLED 驱动完全解耦（换屏幕只需改这三个函数）。")

# ============================================================================
doc.add_heading("10  验证步骤", level=1)
numbered(doc, "按第 5 章接线，特别注意 SCL→PA0、SDA→PA1，且模块先上电。")
numbered(doc, "用 Keil 打开 project.uvprojx，Rebuild（全编译），确认 0 Error。")
numbered(doc, "下载程序，复位。")
numbered(doc, "观察 OLED：第 1 行先出现 “VL53L0X Init...”，随后变为 “VL53L0X Ranging”。")
numbered(doc, "把纸板或手掌在模块前方 5 cm ~ 1.2 m 之间移动，第 2 行数字应跟着变化；"
              "拿开遮挡物后数字会跳到 8000（超量程）。")
numbered(doc, "第 3 行应稳定显示 Valid；若频繁出现 SignalFail，说明目标反射率太低或距离过远。")

# ============================================================================
doc.add_page_break()
doc.add_heading("11  故障排查", level=1)

doc.add_heading("11.1  错误码对照表", level=2)
table(doc,
      ["错误码", "值", "OLED 短词", "含意与处理"],
      [["VL53L0X_ERROR_NONE", "0", "OK", "成功"],
       ["VL53L0X_ERROR_CALIBRATION_WARNING", "-1", "CalibWarn", "校准有警告，仍可测距"],
       ["VL53L0X_ERROR_MIN_CLIPPED", "-2", "MinClipped", "距离小于最小量程被裁剪"],
       ["VL53L0X_ERROR_UNDEFINED", "-3", "Undefined", "未定义错误"],
       ["VL53L0X_ERROR_INVALID_PARAMS", "-4", "InvalidPar", "传入参数非法（多为软件问题）"],
       ["VL53L0X_ERROR_NOT_SUPPORTED", "-5", "NotSupport", "器件不支持该功能"],
       ["VL53L0X_ERROR_RANGE_ERROR", "-6", "RangeError", "测距过程出错"],
       ["VL53L0X_ERROR_TIME_OUT", "-7", "TimeOut", "等待器件响应超时，通常是 I2C 不稳或供电不足"],
       ["VL53L0X_ERROR_MODE_NOT_SUPPORTED", "-8", "ModeNotSup", "不支持该测量模式"],
       ["VL53L0X_ERROR_BUFFER_TOO_SMALL", "-9", "BufTooSmall", "缓冲区过小（软件问题）"],
       ["VL53L0X_ERROR_GPIO_NOT_EXISTING", "-10", "GpioNotExi", "GPIO 不存在（本移植不涉及）"],
       ["VL53L0X_ERROR_GPIO_FUNCTIONALITY_NOT_SUPPORTED", "-11", "GpioNotSup", "GPIO 功能不支持"],
       ["VL53L0X_ERROR_INTERRUPT_NOT_CLEARED", "-12", "IntNotClear", "中断未被清除，会看不到新数据"],
       ["VL53L0X_ERROR_CONTROL_INTERFACE", "-20", "I2C Fail", "**最常见的错误**：I2C 无应答，查接线/供电/上拉"],
       ["VL53L0X_ERROR_INVALID_COMMAND", "-30", "InvalidCmd", "调用顺序错误，例如没初始化就测量"],
       ["VL53L0X_ERROR_DIVISION_BY_ZERO", "-40", "DivByZero", "除零（软件问题）"],
       ["VL53L0X_ERROR_REF_SPAD_INIT", "-50", "RefSpadFail", "SPAD 校准失败，多为模块损坏或电压不足"],
       ["VL53L0X_ERROR_NOT_IMPLEMENTED", "-99", "NotImplem", "该平台函数未实现（本移植对不用的函数返回失败）"]],
      widths=[5.6, 1.2, 2.6, 6.6])

doc.add_heading("11.2  测距状态 RangeStatus 对照表", level=2)
doc.add_paragraph("只有 0 表示距离可信，其它值表示器件自检拒绝了这次测量，"
                  "此时 RangeMilliMeter 里的数值不可采信。")
table(doc,
      ["RangeStatus", "OLED 短词", "含意"],
      [["0", "Valid", "测距有效"],
       ["1", "SigmaFail", "回波标准差过大（目标太小 / 边缘反射）"],
       ["2", "SignalFail", "回波信号太弱（目标太远或太黑）"],
       ["3", "MinRange", "目标太近，小于约 3 cm"],
       ["4", "PhaseFail", "相位超出量程范围"],
       ["5", "HardwareFail", "器件硬件故障"]],
      widths=[2.6, 3.4, 10.0])

doc.add_heading("11.3  常见现象与处理", level=2)
table(doc,
      ["现象", "可能原因", "处理"],
      [["编译报 “uint32_t has already been declared”",
        "vl53l0x_types.h 的兜底 typedef 与 Keil stdint.h 冲突",
        "确认 4.1 节的 !defined(__ARMCC_VERSION) 已加上"],
       ["编译报重复符号 multiply defined",
        "工程里同时编译了 Windows 版和移植版的同名文件",
        "移除 Hardware\\ 根目录下残留的原始 vl53l0x_*.c"],
       ["OLED 显示 Err:I2C Fail",
        "SCL/SDA 接反、模块没供电、缺上拉、地址不对",
        "核对 SCL→PA0、SDA→PA1；万用表量模块 VCC 是否为 3.3V；给 SCL/SDA 补 4.7k 上拉"],
       ["初始化通过但距离一直是 8190 / 8191",
        "前方无目标（超量程），或有玻璃/吸光材料",
        "放一张 A4 白纸在 10 cm 处测试"],
       ["距离只有第一次刷新",
        "没有清 “New Sample Ready” 中断",
        "确认调用的是 VL53L0X_ReadContinuous()（内部已清中断）"],
       ["数值跳动很大",
        "目标反射率低、环境光强、测量时间过短",
        "拉近目标；或用 VL53L0X_SetMeasurementTimingBudgetMicroSeconds() 加大测量时间预算"],
       ["偶尔读到 0 或极小值",
        "目标距离小于最小量程 3 cm",
        "看第 3 行是否显示 MinRange，属正常现象"]],
      widths=[4.4, 5.0, 6.6])

# ============================================================================
doc.add_heading("12  附录：常用官方 API 速查", level=1)
doc.add_paragraph("以下函数均来自 ST 官方 API，已被本工程编译进来，可直接在 main.c 中调用"
                  "（Dev 参数用应用层内部的那个指针，或自行定义一个 VL53L0X_Dev_t）：")
table(doc,
      ["函数", "用途"],
      [["VL53L0X_DataInit(Dev)", "数据初始化"],
       ["VL53L0X_StaticInit(Dev)", "载入 tuning 参数、设置参考 SPAD"],
       ["VL53L0X_SetDeviceMode(Dev, mode)", "设置模式：SINGLE_RANGING(0) / CONTINUOUS_RANGING(1) 等"],
       ["VL53L0X_StartMeasurement(Dev) / StopMeasurement(Dev)", "启动 / 停止测量"],
       ["VL53L0X_GetMeasurementDataReady(Dev, &ready)", "查询本次测量是否完成"],
       ["VL53L0X_GetRangingMeasurementData(Dev, &data)", "读取测量结果（RangeMilliMeter / RangeStatus 等）"],
       ["VL53L0X_PerformSingleRangingMeasurement(Dev, &data)", "一条龙：启动→等待→取结果→清中断"],
       ["VL53L0X_ClearInterruptMask(Dev, mask)", "清除中断标志（连续测量必须调用）"],
       ["VL53L0X_SetMeasurementTimingBudgetMicroSeconds(Dev, us)", "设置测量时间预算：越大越准越慢（默认约 33000us）"],
       ["VL53L0X_GetMeasurementTimingBudgetMicroSeconds(Dev, &us)", "读取当前时间预算"],
       ["VL53L0X_PerformRefCalibration(Dev, &vhv, &phase)", "参考校准"],
       ["VL53L0X_PerformRefSpadManagement(Dev, &count, &isAperture)", "参考 SPAD 管理"],
       ["VL53L0X_PerformOffsetCalibration(Dev, mm)", "偏移校准（需要已知距离的白色目标）"],
       ["VL53L0X_PerformXTalkCalibration(Dev, kcps)", "串扰校准（盖住镜头消除盖板反射）"],
       ["VL53L0X_SetInterMeasurementPeriodMilliSeconds(Dev, ms)", "连续测量时两次测量的间隔"],
       ["VL53L0X_GetVersion(&ver)", "读取 API 版本（应为 v1.0.4）"],
       ["VL53L0X_GetDeviceInfo(Dev, &info)", "读取器件型号、ID 等信息"]],
      widths=[7.4, 8.6])

doc.add_paragraph("参考文档：ST 官方《VL53L0X API v1.0.4.4960》"
                  "（STSW-IMG005\\VL53L0X_1.0.4\\doc\\VL53L0X_API_v1.0.4.4960_externalx.pdf）"
                  "以及官方示例 VL53L0X_1.0.4\\ApiExample\\examples\\src\\"
                  "vl53l0x_ContinuousRanging_Example.c。")

doc.save(OUT)
print("saved:", OUT)
