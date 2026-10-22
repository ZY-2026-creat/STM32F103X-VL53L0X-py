#ifndef __VL53L0X_H
#define __VL53L0X_H

/*==============================================================================
 *  VL53L0X 激光测距模块 —— 面向本工程的易用封装
 *  ---------------------------------------------------------------------------
 *  这一层把 ST 官方 API（vl53l0x_api.c 等）包装成和 LED.c / OLED.c 一样
 *  "一句话就能用" 的模块：
 *
 *      VL53L0X_Init();                                 // 上电初始化
 *      VL53L0X_StartContinuous();                      // 启动连续测量
 *      while (1) {
 *          if (VL53L0X_ReadContinuous(&dist, &status) == VL53L0X_ERROR_NONE) {
 *              OLED_ShowNum(1, 1, dist, 4);            // 显示距离
 *          }
 *      }
 *
 *  底层文件对应关系：
 *      ST 官方（原封不动）: vl53l0x_api.c/.h、vl53l0x_api_core.*、vl53l0x_api_calibration.*、
 *                           vl53l0x_api_ranging.*、vl53l0x_api_strings.*、
 *                           vl53l0x_def.h、vl53l0x_device.h、vl53l0x_types.h、
 *                           vl53l0x_tuning.h、vl53l0x_interrupt_threshold_settings.h、
 *                           vl53l0x_platform.h、vl53l0x_platform_log.h
 *      ST 官方（小改）    : vl53l0x_platform.c（删掉 <Windows.h>、改掉 Windows 延时）
 *                           vl53l0x_types.h  （适配 Keil ARMCC 的 stdint.h）
 *                           vl53l0x_i2c_platform.h（追加 3 个软件 I2C 函数声明）
 *      本次移植新写       : vl53l0x_i2c_platform.c（软件模拟 I2C，PA0=SCL / PA1=SDA）
 *                           本文件 VL53L0X.c/.h（应用层封装）
 *============================================================================*/

#include "stm32f10x.h"
#include "vl53l0x_def.h"        /* 为了用 VL53L0X_Error / VL53L0X_RangingMeasurementData_t 等类型 */

/*------------------------------------------------------------------------------
 *  I2C 从机地址
 *  VL53L0X 的 7 位地址固定是 0x29，ST 的 API 要求传 8 位("写地址")，即 0x29<<1 = 0x52
 *------------------------------------------------------------------------------*/
#define VL53L0X_I2C_ADDRESS_8BIT        0x52

/*------------------------------------------------------------------------------
 *  VL53L0X_ReadContinuous() 的额外返回值：
 *      0 (VL53L0X_ERROR_NONE)  = 读到了新数据，pDistance_mm / pRangeStatus 有效
 *      VL53L0X_NOT_READY       = 本次测量还没结束，没有新数据（不是错误，稍后再读）
 *      < 0                     = 真正的错误，见 VL53L0X_ERROR_* 宏
 *------------------------------------------------------------------------------*/
#define VL53L0X_NOT_READY               1

/*==============================================================================
 *  采样频率 / 稳定性参数（推荐值，改这里就够了）
 *  ---------------------------------------------------------------------------
 *  VL53L0X 的"测量频率"由两个参数共同决定，二者含义完全不同 ——
 *
 *  1) VL53L0X_TIMING_BUDGET_MS —— 单次测量的积分时间（时间预算）
 *     器件内部真正"打激光、收回波、算距离"的时长，默认 33 ms。
 *     这个值越大 -> 收光越多、信噪比越高 -> 读数越稳（抖动/跳变越小），
 *     但每次测量更慢。官方允许 20 ~ 1200 ms。
 *     ★ 想让"测得更稳"，真正起作用的是这个值，而不是在主循环里加延时。
 *
 *  2) VL53L0X_MEASUREMENT_PERIOD_MS —— 两次测量之间的间隔（定时连续测量）
 *     决定输出频率：频率 ≈ 1000 / MEASUREMENT_PERIOD_MS。
 *     必须比时间预算大（建议至少大 10 ms），否则器件还没测完就要开始下一次。
 *     它只决定"多久出一次结果"，不会单独改善单次精度。
 *
 *  推荐组合（从快到稳）：
 *      预算  33 ms + 周期  50 ms  -> 约 20 Hz   默认，抖动明显，适合快速跟随
 *      预算  50 ms + 周期  70 ms  -> 约 14 Hz   折中
 *      预算 100 ms + 周期 120 ms  -> 约  8 Hz   ★本工程默认，稳且 OLED/串口都跟得上
 *      预算 200 ms + 周期 250 ms  -> 约  4 Hz   很稳，适合静止目标/远距离
 *
 *  3) VL53L0X_SIGMA_LIMIT_MM —— 允许的最大标准差，超过就判为 SigmaFail
 *     默认 18 mm。调小可以把"抖动大"的读数直接拒掉，屏幕上只留稳定值；
 *     调得太小会频繁报 SigmaFail（尤其远距离、深色/倾斜目标）。
 *     时间预算加大后读数本身变稳，可以收紧到 10 ~ 15 mm。
 *============================================================================*/
#define VL53L0X_TIMING_BUDGET_MS        100
#define VL53L0X_MEASUREMENT_PERIOD_MS   120
#define VL53L0X_SIGMA_LIMIT_MM          15

/* 编译期检查：周期必须比积分时间长，否则编译直接报错，省得上电才发现参数不合法 */
#if (VL53L0X_MEASUREMENT_PERIOD_MS <= VL53L0X_TIMING_BUDGET_MS + 10)
#error "VL53L0X_MEASUREMENT_PERIOD_MS 必须比 VL53L0X_TIMING_BUDGET_MS 大 10ms 以上"
#endif

#if (VL53L0X_TIMING_BUDGET_MS < 20) || (VL53L0X_TIMING_BUDGET_MS > 1200)
#error "VL53L0X_TIMING_BUDGET_MS 超出官方允许范围 20 ~ 1200 ms"
#endif

/*==============================================================================
 *  函数接口
 *============================================================================*/

/**
 * @brief  初始化 VL53L0X（内部会打开软件 I2C、软件复位器件、载入官方 tuning 参数、
 *         做参考校准和参考 SPAD 管理）。整个过程约 100~300ms。
 * @return VL53L0X_ERROR_NONE(0) 成功；其它值为错误码，可用 VL53L0X_ErrorString() 转成文字
 *         常见的失败：
 *           VL53L0X_ERROR_CONTROL_INTERFACE(-20) —— I2C 没有应答，检查接线/供电/上拉电阻
 *           VL53L0X_ERROR_REF_SPAD_INIT(-50)     —— SPAD 校准失败，通常是模块损坏或电压不足
 */
VL53L0X_Error VL53L0X_Init(void);//VL53L0X_Error(uint8_t)

/**
 * @brief  探测 I2C 总线上 0x52 地址是否有应答（不读寄存器，最安全的探活方式）
 * @return VL53L0X_ERROR_NONE(0) = 有应答
 */
VL53L0X_Error VL53L0X_CheckConnection(void);//检测是否有应答（调用官方检测代码）

/**
 * @brief  单次测量（阻塞）。调用一次测一次，函数返回时结果已经拿到。
 *         最省事的用法，适合 1~10Hz 的慢速刷新。
 * @brief  调用API中的VL53L0X_PerformSingleRangingMeasurement完成单次测距
 * @param  pDistance_mm  输出：距离，单位 mm
 * @param  pRangeStatus  输出：测距状态，0 = 有效；非 0 时距离不可信
 */
VL53L0X_Error VL53L0X_ReadDistance(uint16_t *pDistance_mm, uint8_t *pRangeStatus);

/**
 * @brief  启动连续测量（器件自己在后台不停地测）
 * @return VL53L0X_ERROR_NONE(0) 成功
 */
VL53L0X_Error VL53L0X_StartContinuous(void);

/**
 * @brief  读取连续测量模式下的最新一次结果（非阻塞，可以放在主循环里随便调）
 * @return 见文件顶部 VL53L0X_NOT_READY 的说明
 */
VL53L0X_Error VL53L0X_ReadContinuous(uint16_t *pDistance_mm, uint8_t *pRangeStatus);

/**
 * @brief  停止连续测量
 */
void VL53L0X_StopContinuous(void);

/**
 * @brief  取最近一次成功读到的距离 / 状态
 */
uint16_t VL53L0X_GetLastDistance(void);
uint8_t  VL53L0X_GetLastRangeStatus(void);

/*------------------------------------------------------------------------------
 *  采样频率设置
 *  ---------------------------------------------------------------------------
 *  调用位置：VL53L0X_Init() 之后、VL53L0X_StartContinuous() 之前。
 *  三个 Set 函数都要求器件当前**不在**连续测量中（要改先在测量中先
 *  VL53L0X_StopContinuous()），否则返回 VL53L0X_ERROR_INVALID_COMMAND。
 *------------------------------------------------------------------------------*/

/**
 * @brief  设置单次测量的时间预算（积分时间），20 ~ 1200 ms
 *         越大越稳、越慢。一般直接用 VL53L0X_ConfigStableMode() 即可。
 */
VL53L0X_Error VL53L0X_SetTimingBudgetMs(uint16_t Ms);

/**
 * @brief  设置两次测量之间的间隔（定时连续测量模式），单位 ms
 *         必须大于"时间预算 + 10 ms"；设好之后 StartContinuous() 会自动
 *         进入 VL53L0X_DEVICEMODE_CONTINUOUS_TIMED_RANGING 定时模式。
 *         默认（不调用本函数）是背靠背模式，以时间预算的速度连续测量。
 */
VL53L0X_Error VL53L0X_SetMeasurementPeriodMs(uint16_t Ms);

/**
 * @brief  设置 Sigma（标准差）上限，单位 mm，默认 18
 *         超过上限的那一次测量会被判为 SigmaFail（RangeStatus = 1）。
 */
VL53L0X_Error VL53L0X_SetSigmaLimitMm(uint16_t Mm);

/**
 * @brief  一键套用本文件顶部推荐的"稳定"参数：
 *         时间预算 = VL53L0X_TIMING_BUDGET_MS，测量周期 = VL53L0X_MEASUREMENT_PERIOD_MS，
 *         Sigma 上限 = VL53L0X_SIGMA_LIMIT_MM。
 * @return VL53L0X_ERROR_NONE(0) 成功
 */
VL53L0X_Error VL53L0X_ConfigStableMode(void);

/*------------------------------------------------------------------------------
 *  下面的函数用来把结果显示到 OLED（4 行 x 16 列，行列都从 1 开始）
 *------------------------------------------------------------------------------*/
void  VL53L0X_ShowDistance(uint8_t Line, uint16_t Distance_mm);
void  VL53L0X_ShowRangeStatus(uint8_t Line, uint8_t RangeStatus);
void  VL53L0X_ShowError(uint8_t Line, VL53L0X_Error ErrorCode);

/**
 * @brief  把测距状态码翻译成不超过 10 个字符的英文短词（方便直接显示在 OLED 上）
 */
char *VL53L0X_RangeStatusString(uint8_t RangeStatus);

/**
 * @brief  把错误码翻译成不超过 11 个字符的英文短词
 */
char *VL53L0X_ErrorString(VL53L0X_Error ErrorCode);

#endif
