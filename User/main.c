/**
 * @file    main.c
 * @brief   VL53L0X 激光测距 —— 定时连续测量 + 中值滤波，距离显示在 OLED 并从串口输出
 *
 *  硬件连接（本工程使用的引脚）：
 *      VL53L0X VCC  -> 3.3V      （GY-530 / 蓝板模块同时支持 3.3V 和 5V）
 *      VL53L0X GND  -> GND
 *      VL53L0X SCL  -> PA0
 *      VL53L0X SDA  -> PA1
 *      VL53L0X XSHUT-> 不接（悬空即可，模块自己有上拉）
 *      VL53L0X GPIO1-> 不接（本程序用轮询，不用中断脚）
 *
 *      OLED  SCL -> PB8，SDA -> PB9（见 Hardware\OLED.c）
 *
 *      串口  USART1：TX -> PA9，RX -> PA10，9600 8N1
 *            接 USB-TTL 模块，用串口助手打开对应 COM 口即可
 *
 *  ======================== 采样频率（降频 + 测得更稳） ========================
 *  关键结论：想让读数更稳，真正起作用的是"时间预算（积分时间）"，而不是在主
 *
 *      时间预算 33 ms + 周期  50 ms  -> 约 20 Hz   默认，抖动明显
 *      时间预算 50 ms + 周期  70 ms  -> 约 14 Hz   折中
 *      时间预算 100 ms + 周期 120 ms -> 约  8 Hz  ★本工程默认，稳且显示跟得上
 *      时间预算 200 ms + 周期 250 ms -> 约  4 Hz   很稳，适合静止目标/远距离
 *
 *  修改位置在 Hardware\VL53L0X.h 顶部的三个宏：
 *      VL53L0X_TIMING_BUDGET_MS / VL53L0X_MEASUREMENT_PERIOD_MS / VL53L0X_SIGMA_LIMIT_MM
 *
 *  另外本文件对"有效"的测量结果再做 5 点中值滤波（MedianFilter），
 *  可以把偶发的单点跳变直接压掉，OLED 上的数字基本不闪。
 *
 *  ============================== 串口协议 ====================================
 *  输出（每得到一次新测量打印一行，可以直接接串口示波器/绘图）：
 *      F=0450 R=0452 S=0
 *      F = 5 点中值滤波后的距离(mm)   R = 本次原始距离(mm)   S = RangeStatus（0=有效）
 *
 *  输入（在串口助手里敲一个字符）：
 *      p / P   打印当前采样参数
 *      r / R   重启连续测量
 *
 *  显示效果（4 行 x 16 列）：
 *      VL53L0X Ranging
 *      Dist:0452mm
 *      Status:Valid
 *      B100msP120ms
 */

#include "stm32f10x.h"                  /* Device header */
#include "Delay.h"
#include "OLED.h"
#include "VL53L0X.h"
#include "Serial.h"

/*------------------------------------------------------------------------------
 *  本文件内的两个可调参数
 *------------------------------------------------------------------------------*/
#define VL53L0X_READ_POLL_MS    10      /* 主循环轮询间隔：器件没测完时等这么久再看 */
#define FILTER_DEPTH            5       /* 中值滤波窗口：5 点（3 个以上就能压掉单点跳变） */

/*------------------------------------------------------------------------------
 *  中值滤波：只把"有效"的测量值喂进来，输出窗口内的中位数
 *    - 用中值而不是平均：平均会被一个离谱的野值拉偏，中值不会
 *    - 窗口 5 点、每秒 8 次采样 -> 约 0.6 s 的平滑，静止目标读数基本不跳
 *------------------------------------------------------------------------------*/
static uint16_t Filter_Buf[FILTER_DEPTH];
static uint8_t  Filter_Index = 0;
static uint8_t  Filter_Count = 0;

static uint16_t MedianFilter(uint16_t NewValue)//中值滤波
{
    uint16_t Temp[FILTER_DEPTH];
    uint16_t Tmp;
    uint8_t  i, j;

    /* 1. 新值入环形缓冲 */
    Filter_Buf[Filter_Index] = NewValue;
    Filter_Index = (uint8_t)((Filter_Index + 1) % FILTER_DEPTH);//改变下一个数据写入数组的位置（%循环）
    if (Filter_Count < FILTER_DEPTH)
    {
        Filter_Count++;
    }

    /* 2. 拷贝一份出来排序（5 个数，开销可以忽略） */
    for (i = 0; i < Filter_Count; i++)
    {
        Temp[i] = Filter_Buf[i];//避免直接排列原始数组
    }

    /* 3. 选择排序（升序） */  // 遍历数组排序
    for (i = 0; i + 1 < Filter_Count; i++)
    {
        for (j = (uint8_t)(i + 1); j < Filter_Count; j++)
        {
            if (Temp[j] < Temp[i])
            {
                Tmp     = Temp[i];
                Temp[i] = Temp[j];
                Temp[j] = Tmp;
            }
        }
    }

    /* 4. 取中位数 */
    return Temp[Filter_Count / 2];
}

/*------------------------------------------------------------------------------
 *  在 OLED 第 Line 行显示当前采样参数，例如：B100msP120ms
 *  （直接引用宏，改了 VL53L0X.h 里的参数，屏幕上会自动跟着变）
 *------------------------------------------------------------------------------*/
static void ShowConfig(uint8_t Line)
{
    OLED_ShowString(Line, 1,  "B");
    OLED_ShowNum(Line, 2, VL53L0X_TIMING_BUDGET_MS, 3);       /* 第 2~4 列：预算 */
    OLED_ShowString(Line, 5,  "ms");
    OLED_ShowString(Line, 7,  "P");
    OLED_ShowNum(Line, 8, VL53L0X_MEASUREMENT_PERIOD_MS, 3);  /* 第 8~10 列：周期 */
    OLED_ShowString(Line, 11, "ms");
}

int main(void)
{
    uint16_t      Raw_mm      = 0;      /* 本次原始距离 */
    uint16_t      Distance_mm = 0;      /* 滤波后的距离 */
    uint8_t       RangeStatus = 0;
    VL53L0X_Error Status;

    /*--------------------------------------------------------------------
     * 第 1 步：初始化 OLED，并提示正在初始化
     *------------------------------------------------------------------*/
    OLED_Init();
    OLED_Clear();
    OLED_ShowString(1, 1, "VL53L0X Init...");

    /*--------------------------------------------------------------------
     * 第 2 步：初始化串口（PA9=TX / PA10=RX，9600 8N1）
     *   放在最前面，后面所有初始化结果都能从串口看到
     *------------------------------------------------------------------*/
    Serial_Init();
    Serial_Printf("\r\nVL53L0X Ranging\r\n");
    Serial_Printf("Budget=%dms Period=%dms Sigma=%dmm\r\n",
                  VL53L0X_TIMING_BUDGET_MS,
                  VL53L0X_MEASUREMENT_PERIOD_MS,
                  VL53L0X_SIGMA_LIMIT_MM);

    /*--------------------------------------------------------------------
     * 第 3 步：初始化 VL53L0X
     *   内部依次执行：打开软件 I2C -> 探活 -> DataInit -> StaticInit
     *                 -> 参考校准 -> 参考 SPAD 管理，约 100~300ms
     *------------------------------------------------------------------*/
    Status = VL53L0X_Init();
    if (Status != VL53L0X_ERROR_NONE)
    {
        /* 初始化失败：把错误码同时显示到 OLED 和串口，方便对照排查 */
        OLED_Clear();
        VL53L0X_ShowError(1, Status);
        Serial_Printf("Init failed: %s (%d)\r\n",
                      VL53L0X_ErrorString(Status), (int)Status);

        /* 最常见的失败就是 I2C 没有应答，给一句提示 */
        if (Status == VL53L0X_ERROR_CONTROL_INTERFACE)
        {
            OLED_ShowString(2, 1, "No I2C ACK at");
            OLED_ShowString(3, 1, "PA0=SCL PA1=SDA");
            OLED_ShowString(4, 1, "check 0x52 wire");
            Serial_Printf("No I2C ACK: check SCL=PA0 SDA=PA1 VCC=3.3V GND\r\n");
        }

        while (1)
        {
            /* 停在这里，方便看屏幕/串口上的错误信息 */
        }
    }

    /*--------------------------------------------------------------------
     * 第 4 步：套用"稳定"参数（这就是降频、测得更稳的关键一步）
     *   时间预算 33ms -> 100ms ：积分时间约 3 倍，噪声明显下降
     *   测量周期 120ms         ：约 8 Hz，不会让显示/串口跟不上
     *   Sigma 上限 15mm        ：把抖动过大的读数拒掉（判为 SigmaFail）
     *------------------------------------------------------------------*/
    Status = VL53L0X_ConfigStableMode();
    if (Status != VL53L0X_ERROR_NONE)
    {
        OLED_Clear();
        VL53L0X_ShowError(1, Status);
        Serial_Printf("Config failed: %s (%d)\r\n",
                      VL53L0X_ErrorString(Status), (int)Status);
        while (1)
        {
        }
    }

    /*--------------------------------------------------------------------
     * 第 5 步：启动"定时"连续测量
     *   设过测量周期后，器件按固定节拍自己测，我们只管按点取结果
     *------------------------------------------------------------------*/
    Status = VL53L0X_StartContinuous();
    if (Status != VL53L0X_ERROR_NONE)
    {
        OLED_Clear();
        VL53L0X_ShowError(1, Status);
        Serial_Printf("Start failed: %s (%d)\r\n",
                      VL53L0X_ErrorString(Status), (int)Status);
        while (1)
        {
        }
    }

    Serial_Printf("Start OK, about %d Hz\r\n",
                  1000 / VL53L0X_MEASUREMENT_PERIOD_MS);

    OLED_Clear();
    OLED_ShowString(1, 1, "VL53L0X Ranging");
    VL53L0X_ShowDistance(2, 0);                 /* 先占位，避免残留字符 */
    OLED_ShowString(3, 1, "Status:Waiting ");
    ShowConfig(4);

    /*--------------------------------------------------------------------
     * 第 6 步：主循环
     *   VL53L0X_ReadContinuous() 是非阻塞的：
     *      返回 VL53L0X_ERROR_NONE   -> 读到了新数据
     *      返回 VL53L0X_NOT_READY    -> 这次还没测完，等 10ms 再来问
     *      返回其它负数              -> 出错，显示错误并重启测量
     *------------------------------------------------------------------*/
    while (1)
    {
        Status = VL53L0X_ReadContinuous(&Raw_mm, &RangeStatus);

        if (Status == VL53L0X_ERROR_NONE)
        {
            /* RangeStatus == 0 才可信，才拿去滤波；无效值保持上一次的显示 */
            if (RangeStatus == 0)
            {
                Distance_mm = MedianFilter(Raw_mm);
            }

            VL53L0X_ShowDistance(2, Distance_mm);      /* Dist:0452mm       */
            VL53L0X_ShowRangeStatus(3, RangeStatus);   /* Status:Valid      */

            Serial_Printf("F=%u R=%u S=%u\r\n",
                          (unsigned int)Distance_mm,
                          (unsigned int)Raw_mm,
                          (unsigned int)RangeStatus);
        }
        else if (Status != VL53L0X_NOT_READY)
        {
            /* 出错：先报出来，200ms 后让器件从头再来 */
            Serial_Printf("Err=%s (%d)\r\n",
                          VL53L0X_ErrorString(Status), (int)Status);
            VL53L0X_ShowError(4, Status);

            Delay_ms(200);
            VL53L0X_StopContinuous();
            VL53L0X_StartContinuous();
            ShowConfig(4);
        }

        /*---- 串口命令（用 Serial.c 里的接收标志/接收数据） -----------------*/
        if (Serial_GetRxFlag() != 0)
        {
            uint8_t Command = Serial_GetRxData();

            if (Command == 'p' || Command == 'P')
            {
                Serial_Printf("Budget=%dms Period=%dms Sigma=%dmm\r\n",
                              VL53L0X_TIMING_BUDGET_MS,
                              VL53L0X_MEASUREMENT_PERIOD_MS,
                              VL53L0X_SIGMA_LIMIT_MM);
                ShowConfig(4);
            }
            else if (Command == 'r' || Command == 'R')
            {
                VL53L0X_StopContinuous();
                VL53L0X_StartContinuous();
                Serial_Printf("Restarted\r\n");
            }
        }

        /* 10ms 问一次，比 120ms 的测量周期密得多，不会漏掉结果 */
        Delay_ms(VL53L0X_READ_POLL_MS);
    }
}


