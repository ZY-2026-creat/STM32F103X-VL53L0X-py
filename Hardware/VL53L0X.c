/**
 * @file    VL53L0X.c
 * @brief   VL53L0X 激光测距模块应用层（基于 ST 官方 API v1.0.4 + 本工程的软件 I2C）
 *
 *  初始化流程完全按照 ST 官方例程
 *  STSW-IMG005\VL53L0X_1.0.4\ApiExample\examples\src\vl53l0x_ContinuousRanging_Example.c：
 *      1. VL53L0X_comms_initialise()        打开 I2C
 *      2. VL53L0X_DataInit()                数据初始化（复位器件内部状态）
 *      3. VL53L0X_StaticInit()              载入官方 tuning 参数 + 设置参考 SPAD
 *      4. VL53L0X_PerformRefCalibration()   参考校准（VHV / 相位）
 *      5. VL53L0X_PerformRefSpadManagement()参考 SPAD 管理
 *      6. VL53L0X_SetDeviceMode()           设置测量模式
 *      7. VL53L0X_StartMeasurement()        启动测量
 */

#include "stm32f10x.h"
#include "Delay.h"
#include "OLED.h"
#include "VL53L0X.h"
#include "vl53l0x_api.h"
#include "vl53l0x_i2c_platform.h"

/*------------------------------------------------------------------------------
 *  模块内部变量
 *------------------------------------------------------------------------------*/
static VL53L0X_Dev_t   VL53L0X_Device;                       /* 器件对象（放在 BSS，上电自动清零） */
static VL53L0X_DEV     pVL53L0X_Device = &VL53L0X_Device;    /* ST API 统一用指针操作器件     */

static uint8_t         VL53L0X_Ready          = 0;           /* 1 = 初始化完成 */
static uint8_t         VL53L0X_ContinuousMode = 0;           /* 1 = 正在连续测量 */
static uint16_t        VL53L0X_LastDistance   = 0;
static uint8_t         VL53L0X_LastStatus     = 0;
static uint16_t        VL53L0X_TimingBudgetMs = 33;          /* 当前时间预算，用来校验测量周期 */
static uint16_t        VL53L0X_PeriodMs       = 0;           /* 0 = 背靠背连续测量；非 0 = 定时连续测量周期 */


/*==============================================================================
 *  一、初始化
 *============================================================================*/
VL53L0X_Error VL53L0X_Init(void)
{
    VL53L0X_Error Status = VL53L0X_ERROR_NONE;
    uint8_t  VhvSettings   = 0;      /* 参考校准输出：VHV 设置 */
    uint8_t  PhaseCal      = 0;      /* 参考校准输出：相位校准值 */
    uint32_t refSpadCount  = 0;      /* 参考 SPAD 管理输出 */
    uint8_t  isApertureSpads = 0;    /* 参考 SPAD 管理输出 */

    VL53L0X_Ready          = 0;
    VL53L0X_ContinuousMode = 0;
    VL53L0X_TimingBudgetMs = 33;     /* DataInit 之后器件回到默认值 */
    VL53L0X_PeriodMs       = 0;      /* 默认背靠背，不用定时模式 */

    /* ---- 步骤 1：打开软件 I2C（配置 PA0/PA1） ---------------------------- */
    if (VL53L0X_comms_initialise(I2C, 400) != 0) {
        return VL53L0X_ERROR_CONTROL_INTERFACE;
    }

    /* ---- 步骤 2：先探活，避免后面一大堆函数白白超时 --------------------- */
    if (VL53L0X_CheckConnection() != VL53L0X_ERROR_NONE) {
        return VL53L0X_ERROR_CONTROL_INTERFACE;
    }

    /* ---- 步骤 3：填写器件参数 ------------------------------------------- */
    pVL53L0X_Device->I2cDevAddr      = VL53L0X_I2C_ADDRESS_8BIT;   /* 0x52 */
    pVL53L0X_Device->comms_type      = 1;                          /* 1 = I2C */
    pVL53L0X_Device->comms_speed_khz = 400;//设定通常值

    /* ---- 步骤 4：数据初始化 --------------------------------------------- */
    Status = VL53L0X_DataInit(pVL53L0X_Device);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    /* ---- 步骤 5：静态初始化（载入官方 tuning 参数、设置参考 SPAD） ------ */
    Status = VL53L0X_StaticInit(pVL53L0X_Device);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    /* ---- 步骤 6：参考校准（VHV + 相位） -------------------------------- */
    Status = VL53L0X_PerformRefCalibration(pVL53L0X_Device, &VhvSettings, &PhaseCal);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    /* ---- 步骤 7：参考 SPAD 管理（官方例程要求，保证弱信号下也能测） ----- */
    Status = VL53L0X_PerformRefSpadManagement(pVL53L0X_Device, &refSpadCount, &isApertureSpads);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    VL53L0X_Ready = 1;
    return VL53L0X_ERROR_NONE;
}


VL53L0X_Error VL53L0X_CheckConnection(void)
{
    if (VL53L0X_I2C_TestConnection(VL53L0X_I2C_ADDRESS_8BIT) == 0) {
        return VL53L0X_ERROR_NONE;
    }
    return VL53L0X_ERROR_CONTROL_INTERFACE;
}


/*==============================================================================
 *  二、单次测量（阻塞）
 *============================================================================*/
VL53L0X_Error VL53L0X_ReadDistance(uint16_t *pDistance_mm, uint8_t *pRangeStatus)
{
    VL53L0X_Error Status = VL53L0X_ERROR_NONE;
    VL53L0X_RangingMeasurementData_t RangingMeasurementData;

    if (VL53L0X_Ready == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }

    /* 先停掉连续测量，否则器件不接受单次测量命令 */
    if (VL53L0X_ContinuousMode != 0) {
        VL53L0X_StopContinuous();
    }

    Status = VL53L0X_SetDeviceMode(pVL53L0X_Device, VL53L0X_DEVICEMODE_SINGLE_RANGING);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    /* ST 的"一条龙"接口：内部自动完成 启动测量->等待完成->取结果->清中断 */
    Status = VL53L0X_PerformSingleRangingMeasurement(pVL53L0X_Device, &RangingMeasurementData);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    VL53L0X_LastDistance = RangingMeasurementData.RangeMilliMeter;
    VL53L0X_LastStatus   = RangingMeasurementData.RangeStatus;

    if (pDistance_mm != 0) {
        *pDistance_mm = VL53L0X_LastDistance;
    }
    if (pRangeStatus != 0) {
        *pRangeStatus = VL53L0X_LastStatus;
    }

    return VL53L0X_ERROR_NONE;
}


/*==============================================================================
 *  三、连续测量
 *============================================================================*/
VL53L0X_Error VL53L0X_StartContinuous(void)
{
    VL53L0X_Error Status = VL53L0X_ERROR_NONE;

    if (VL53L0X_Ready == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }

    if (VL53L0X_ContinuousMode != 0) {
        return VL53L0X_ERROR_NONE;          /* 已经在连续测量了 */
    }

    /* 设测量模式：
     *   配过测量周期 -> 定时连续测量（每 PeriodMs 测一次，频率固定）
     *   没配         -> 背靠背连续测量（测完立刻测下一次，最快）
     * 注意：定时模式下，"周期"寄存器由 VL53L0X_SetMeasurementPeriodMs() 写入，
     *       这里只需要把器件模式改成 CONTINUOUS_TIMED_RANGING。 */
    if (VL53L0X_PeriodMs != 0) {
        Status = VL53L0X_SetDeviceMode(pVL53L0X_Device,
                                        VL53L0X_DEVICEMODE_CONTINUOUS_TIMED_RANGING);
    } else {
        Status = VL53L0X_SetDeviceMode(pVL53L0X_Device,
                                        VL53L0X_DEVICEMODE_CONTINUOUS_RANGING);
    }
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    /* 告诉器件开始测量 */
    Status = VL53L0X_StartMeasurement(pVL53L0X_Device);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    VL53L0X_ContinuousMode = 1;
    return VL53L0X_ERROR_NONE;
}


VL53L0X_Error VL53L0X_ReadContinuous(uint16_t *pDistance_mm, uint8_t *pRangeStatus)
{
    VL53L0X_Error Status = VL53L0X_ERROR_NONE;
    VL53L0X_RangingMeasurementData_t RangingMeasurementData;
    uint8_t DataReady = 0;

    if (VL53L0X_ContinuousMode == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }

    /* 问器件：这一次测完了吗？ */
    Status = VL53L0X_GetMeasurementDataReady(pVL53L0X_Device, &DataReady);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }
    if (DataReady == 0) {
        return VL53L0X_NOT_READY;           /* 没测完，不是错误 */
    }

    /* 把结果读出来 */
    Status = VL53L0X_GetRangingMeasurementData(pVL53L0X_Device, &RangingMeasurementData);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    /* 必须清中断！否则器件不会再产生下一次 "New Sample Ready" */
    VL53L0X_ClearInterruptMask(pVL53L0X_Device,
        VL53L0X_REG_SYSTEM_INTERRUPT_GPIO_NEW_SAMPLE_READY);

    VL53L0X_LastDistance = RangingMeasurementData.RangeMilliMeter;
    VL53L0X_LastStatus   = RangingMeasurementData.RangeStatus;

    if (pDistance_mm != 0) {
        *pDistance_mm = VL53L0X_LastDistance;
    }
    if (pRangeStatus != 0) {
        *pRangeStatus = VL53L0X_LastStatus;
    }

    return VL53L0X_ERROR_NONE;
}


void VL53L0X_StopContinuous(void)
{
    if (VL53L0X_ContinuousMode == 0) {
        return;
    }

    VL53L0X_StopMeasurement(pVL53L0X_Device);
    VL53L0X_ClearInterruptMask(pVL53L0X_Device,
        VL53L0X_REG_SYSTEM_INTERRUPT_GPIO_NEW_SAMPLE_READY);

    VL53L0X_ContinuousMode = 0;
}


uint16_t VL53L0X_GetLastDistance(void)
{
    return VL53L0X_LastDistance;
}


uint8_t VL53L0X_GetLastRangeStatus(void)
{
    return VL53L0X_LastStatus;
}


/*==============================================================================
 *  四、采样频率设置
 *  ---------------------------------------------------------------------------
 *  三个 Set 函数都必须"器件没在测"的时候调用（VL53L0X_StartContinuous 之前，
 *  或者先 VL53L0X_StopContinuous）。改完再 Start，新参数才生效。
 *============================================================================*/
VL53L0X_Error VL53L0X_SetTimingBudgetMs(uint16_t Ms)
{
    VL53L0X_Error Status;

    if (VL53L0X_Ready == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }
    if (VL53L0X_ContinuousMode != 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;       /* 先 Stop，再改参数 */
    }
    if (Ms < 20 || Ms > 1200) {
        return VL53L0X_ERROR_INVALID_PARAMS;        /* 官方允许 20 ms ~ 1.2 s */
    }

    Status = VL53L0X_SetMeasurementTimingBudgetMicroSeconds(pVL53L0X_Device,
                 (uint32_t)Ms * 1000U);
    if (Status == VL53L0X_ERROR_NONE) {
        VL53L0X_TimingBudgetMs = Ms;
    }

    return Status;
}


VL53L0X_Error VL53L0X_SetMeasurementPeriodMs(uint16_t Ms)
{
    VL53L0X_Error Status;

    if (VL53L0X_Ready == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }
    if (VL53L0X_ContinuousMode != 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;       /* 先 Stop，再改参数 */
    }
    /* 周期必须比积分时间长：器件要先测完，才谈得上"下一次" */
    if (Ms <= (uint16_t)(VL53L0X_TimingBudgetMs + 10U)) {
        return VL53L0X_ERROR_INVALID_PARAMS;
    }

    /* 写 SYSTEM_INTERMEASUREMENT_PERIOD 寄存器（内部会乘 OSC 校准值） */
    Status = VL53L0X_SetInterMeasurementPeriodMilliSeconds(pVL53L0X_Device,
                 (uint32_t)Ms);
    if (Status == VL53L0X_ERROR_NONE) {
        VL53L0X_PeriodMs = Ms;                      /* 非 0 -> Start 时用定时模式 */
    }

    return Status;
}


VL53L0X_Error VL53L0X_SetSigmaLimitMm(uint16_t Mm)
{
    if (VL53L0X_Ready == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }
    if (Mm == 0 || Mm > 100) {
        return VL53L0X_ERROR_INVALID_PARAMS;
    }

    /* FixPoint1616：整数部分在 bit16 以上，所以要 << 16 */
    return VL53L0X_SetLimitCheckValue(pVL53L0X_Device,
               VL53L0X_CHECKENABLE_SIGMA_FINAL_RANGE,
               (FixPoint1616_t)Mm * 65536U);
}


VL53L0X_Error VL53L0X_ConfigStableMode(void)
{
    VL53L0X_Error Status;

    if (VL53L0X_Ready == 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }
    if (VL53L0X_ContinuousMode != 0) {
        return VL53L0X_ERROR_INVALID_COMMAND;
    }

    Status = VL53L0X_SetTimingBudgetMs(VL53L0X_TIMING_BUDGET_MS);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    Status = VL53L0X_SetMeasurementPeriodMs(VL53L0X_MEASUREMENT_PERIOD_MS);
    if (Status != VL53L0X_ERROR_NONE) {
        return Status;
    }

    return VL53L0X_SetSigmaLimitMm(VL53L0X_SIGMA_LIMIT_MM);
}


/*==============================================================================
 *  五、显示到 OLED（行列都从 1 开始，屏幕是 4 行 x 16 列）
 *============================================================================*/
void VL53L0X_ShowDistance(uint8_t Line, uint16_t Distance_mm)
{
    /* 例如：Dist:1234mm */
    OLED_ShowString(Line, 1, "Dist:");
    OLED_ShowNum(Line, 6, Distance_mm, 4);      /* 固定 4 位，小于 1000 会自动补 0 */
    OLED_ShowString(Line, 10, "mm  ");
}


void VL53L0X_ShowRangeStatus(uint8_t Line, uint8_t RangeStatus)
{
    /* 例如：Status:Valid */
    OLED_ShowString(Line, 1, "Status:");
    OLED_ShowString(Line, 8, VL53L0X_RangeStatusString(RangeStatus));
    OLED_ShowString(Line, 16, " ");
}


void VL53L0X_ShowError(uint8_t Line, VL53L0X_Error ErrorCode)
{
    /* 例如：Err:CtrlIface */
    OLED_ShowString(Line, 1, "Err:");
    OLED_ShowString(Line, 5, VL53L0X_ErrorString(ErrorCode));
    OLED_ShowString(Line, 16, " ");
}


/*------------------------------------------------------------------------------
 *  只有 0 表示"距离有效"；其它值表示这次测量被器件的自检拒绝，距离不可信
 *------------------------------------------------------------------------------*/
char *VL53L0X_RangeStatusString(uint8_t RangeStatus)
{
    switch (RangeStatus) {
        case 0:  return "Valid";        /* 测距有效           */
        case 1:  return "SigmaFail";    /* 标准差过大         */
        case 2:  return "SignalFail";   /* 回波信号太弱       */
        case 3:  return "MinRange";     /* 目标太近(<3cm)     */
        case 4:  return "PhaseFail";    /* 相位超范围         */
        case 5:  return "HardwareFail"; /* 硬件故障           */
        default: return "Unknown";
    }
}


char *VL53L0X_ErrorString(VL53L0X_Error ErrorCode)
{
    switch (ErrorCode) {
        case VL53L0X_ERROR_NONE:                        return "OK";
        case VL53L0X_ERROR_CALIBRATION_WARNING:         return "CalibWarn";
        case VL53L0X_ERROR_MIN_CLIPPED:                 return "MinClipped";
        case VL53L0X_ERROR_UNDEFINED:                   return "Undefined";
        case VL53L0X_ERROR_INVALID_PARAMS:              return "InvalidPar";
        case VL53L0X_ERROR_NOT_SUPPORTED:               return "NotSupport";
        case VL53L0X_ERROR_RANGE_ERROR:                 return "RangeError";
        case VL53L0X_ERROR_TIME_OUT:                    return "TimeOut";
        case VL53L0X_ERROR_MODE_NOT_SUPPORTED:          return "ModeNotSup";
        case VL53L0X_ERROR_BUFFER_TOO_SMALL:            return "BufTooSmall";
        case VL53L0X_ERROR_GPIO_NOT_EXISTING:           return "GpioNotExi";
        case VL53L0X_ERROR_GPIO_FUNCTIONALITY_NOT_SUPPORTED: return "GpioNotSup";
        case VL53L0X_ERROR_INTERRUPT_NOT_CLEARED:       return "IntNotClear";
        case VL53L0X_ERROR_CONTROL_INTERFACE:           return "I2C Fail";
        case VL53L0X_ERROR_INVALID_COMMAND:             return "InvalidCmd";
        case VL53L0X_ERROR_DIVISION_BY_ZERO:            return "DivByZero";
        case VL53L0X_ERROR_REF_SPAD_INIT:               return "RefSpadFail";
        case VL53L0X_ERROR_NOT_IMPLEMENTED:             return "NotImplem";
        default:                                        return "UnknownErr";
    }
}
