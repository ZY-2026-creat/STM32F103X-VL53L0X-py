/**
 * @file    vl53l0x_i2c_platform.c
 * @brief   VL53L0X 平台适配层(PAL)通信实现 —— STM32F10x 标准外设库 + 软件模拟 I2C
 *
 * ============================================================================
 *  这个文件是"移植"的核心。
 *
 *  ST 官方 STSW-IMG005 里的同名文件是 Windows 版本：它靠
 *  ranging_sensor_comms.dll + CreateEvent/WaitForSingleObject 去驱动
 *  ST 自己的 Nucleo 板，在 STM32 上完全无法编译、也无法运行。
 *
 *  本文件用「GPIO 位操作模拟 I2C（软件 I2C）」重新实现了
 *  vl53l0x_i2c_platform.h 中声明的全部函数，因此：
 *      vl53l0x_api.c / vl53l0x_api_core.c / vl53l0x_api_calibration.c
 *      vl53l0x_api_ranging.c / vl53l0x_api_strings.c
 *      以及 vl53l0x_platform.c
 *  这些文件**一行都不用改**（vl53l0x_platform.c 只删掉了 <Windows.h>），
 *  整条 ST 官方 API 就能跑在 STM32 上。
 *
 *  ST API 的调用链是：
 *      vl53l0x_api.c  ──>  vl53l0x_platform.c  ──>  本文件  ──>  GPIO
 *      (业务逻辑)          (寄存器封装)            (I2C 时序)     (硬件)
 * ============================================================================
 */

#include "vl53l0x_i2c_platform.h"
#include "stm32f10x.h"
#include "Delay.h"

/*==============================================================================
 *  一、用户配置区：VL53L0X 的 I2C 引脚和速率
 *  ---------------------------------------------------------------------------
 *  本工程已有的引脚占用情况（改之前先看这张表，避免冲突）：
 *      PA1 / PA2   : LED1 / LED2      （见 Hardware\LED.c）  <-- 注意 PA1
 *      PB8 / PB9   : OLED 的 SCL/SDA  （见 Hardware\OLED.c）
 *      PB1 / PB11  : KEY1 / KEY2      （见 Hardware\Key.c）
 *      PB12        : 蜂鸣器           （见 Hardware\Buzzer.c）
 *      PB13        : 光敏传感器       （见 Hardware\LightSensor.c）
 *      PA9 / PA10  : 串口 USART1（下载/调试用）
 *
 *  >>> 想换引脚，只改下面 3 行即可，其它代码一个字都不用动。<<<
 * =============================================================================*/
#define VL53L0X_I2C_GPIO        GPIOA               /* 使用的 GPIO 端口        */
#define VL53L0X_I2C_RCC         RCC_APB2Periph_GPIOA/* 对应的时钟            */
#define VL53L0X_SCL_PIN         GPIO_Pin_0          /* PA0  -> VL53L0X 的 SCL  */
#define VL53L0X_SDA_PIN         GPIO_Pin_1          /* PA1  -> VL53L0X 的 SDA  */

/* 半周期延时(us)。2us 时 SCL 约 80~100kHz，短排线非常稳。
 * 想更快可以改成 1（约 160kHz），但不要低于 1。*/
#define VL53L0X_I2C_DELAY_US    2

/*==============================================================================
 *  二、软件 I2C 的基本宏
 *  引脚统一配置成「开漏输出 Out_OD」：
 *    - 写 1 = 释放总线，靠模块上的上拉电阻把电平拉高（真正的 I2C 行为）
 *    - 写 0 = 把总线拉低
 *    - 开漏输出时仍然可以读回引脚真实电平（STM32 的输入寄存器一直有效），
 *      所以读 ACK 直接 GPIO_ReadInputDataBit 即可，不用来回切换输入/输出模式。
 * =============================================================================*/
#define SCL_HIGH()      GPIO_SetBits(VL53L0X_I2C_GPIO, VL53L0X_SCL_PIN)
#define SCL_LOW()       GPIO_ResetBits(VL53L0X_I2C_GPIO, VL53L0X_SCL_PIN)
#define SDA_HIGH()      GPIO_SetBits(VL53L0X_I2C_GPIO, VL53L0X_SDA_PIN)
#define SDA_LOW()       GPIO_ResetBits(VL53L0X_I2C_GPIO, VL53L0X_SDA_PIN)
#define SDA_READ()      GPIO_ReadInputDataBit(VL53L0X_I2C_GPIO, VL53L0X_SDA_PIN)

/* vl53l0x_i2c_platform.h 规定的返回值：0 = 成功，1 = 失败 */
#define STATUS_OK       0
#define STATUS_FAIL     1

/*==============================================================================
 *  三、软件 I2C 时序
 * =============================================================================*/
static void VL53L0X_I2C_Delay(void)
{
    Delay_us(VL53L0X_I2C_DELAY_US);
}

/**
 * @brief  初始化 I2C 引脚（软件 I2C 的"总线初始化"）
 */
void VL53L0X_I2C_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;

    RCC_APB2PeriphClockCmd(VL53L0X_I2C_RCC, ENABLE);

    GPIO_InitStructure.GPIO_Pin   = VL53L0X_SCL_PIN | VL53L0X_SDA_PIN;
    GPIO_InitStructure.GPIO_Mode  = GPIO_Mode_Out_OD;      /* 开漏输出 */
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(VL53L0X_I2C_GPIO, &GPIO_InitStructure);

    SCL_HIGH();
    SDA_HIGH();
}

/**
 * @brief  总线恢复：如果从机把 SDA 拉死（复位瞬间/热插拔常见），
 *         主机补发最多 9 个 SCL 脉冲，再补一个 STOP，把从机状态机冲出来。
 */
void VL53L0X_I2C_BusRecovery(void)
{
    uint8_t i;

    SDA_HIGH();
    for (i = 0; i < 9; i++) {
        SCL_LOW();
        VL53L0X_I2C_Delay();
        SCL_HIGH();
        VL53L0X_I2C_Delay();
        if (SDA_READ() != 0) {        /* SDA 已经被释放，可以停了 */
            break;
        }
    }
    /* 补一个停止条件 */
    SDA_LOW();
    VL53L0X_I2C_Delay();
    SCL_HIGH();
    VL53L0X_I2C_Delay();
    SDA_HIGH();
    VL53L0X_I2C_Delay();
}

/**
 * @brief  起始条件：SCL 为高时，SDA 由高变低
 */
static void VL53L0X_I2C_Start(void)
{
    SDA_HIGH();
    SCL_HIGH();
    VL53L0X_I2C_Delay();
    SDA_LOW();
    VL53L0X_I2C_Delay();
    SCL_LOW();                 /* 钳住 SCL，准备发送数据 */
    VL53L0X_I2C_Delay();
}

/**
 * @brief  停止条件：SCL 为高时，SDA 由低变高
 */
static void VL53L0X_I2C_Stop(void)
{
    SDA_LOW();
    VL53L0X_I2C_Delay();
    SCL_HIGH();
    VL53L0X_I2C_Delay();
    SDA_HIGH();
    VL53L0X_I2C_Delay();
}

/**
 * @brief  发送 1 个字节（高位先发），并读回从机的应答
 * @return 0 = 收到 ACK，1 = 没有应答(NACK)
 */
static uint8_t VL53L0X_I2C_WriteByte(uint8_t data)
{
    uint8_t i;
    uint8_t ack;

    for (i = 0; i < 8; i++) {
        if (data & 0x80) {
            SDA_HIGH();
        } else {
            SDA_LOW();
        }
        data <<= 1;
        VL53L0X_I2C_Delay();
        SCL_HIGH();                 /* SCL 高电平期间数据必须稳定 */
        VL53L0X_I2C_Delay();
        SCL_LOW();
        VL53L0X_I2C_Delay();
    }

    /* 第 9 个时钟：主机释放 SDA，从机把它拉低表示 ACK */
    SDA_HIGH();
    VL53L0X_I2C_Delay();
    SCL_HIGH();
    VL53L0X_I2C_Delay();
    ack = (SDA_READ() == 0) ? 0 : STATUS_FAIL;
    SCL_LOW();
    VL53L0X_I2C_Delay();

    return ack;
}

/**
 * @brief  接收 1 个字节（高位先收）
 * @param  ack  = 1 收完发 ACK（还要继续读）； = 0 发 NACK（最后一个字节）
 */
static uint8_t VL53L0X_I2C_ReadByte(uint8_t ack)
{
    uint8_t i;
    uint8_t data = 0;

    SDA_HIGH();                     /* 主机释放 SDA，交给从机驱动 */
    for (i = 0; i < 8; i++) {
        data <<= 1;
        VL53L0X_I2C_Delay();
        SCL_HIGH();
        VL53L0X_I2C_Delay();
        if (SDA_READ() != 0) {
            data |= 0x01;
        }
        SCL_LOW();
        VL53L0X_I2C_Delay();
    }

    if (ack != 0) {
        SDA_LOW();                  /* ACK ：拉低 */
    } else {
        SDA_HIGH();                 /* NACK：放开 */
    }
    VL53L0X_I2C_Delay();
    SCL_HIGH();
    VL53L0X_I2C_Delay();
    SCL_LOW();
    VL53L0X_I2C_Delay();
    SDA_HIGH();

    return data;
}

/**
 * @brief  探测总线上某个地址是否有器件应答（不用读寄存器，最安全的探活方式）
 * @param  address  8 位写地址（VL53L0X 默认 0x52）
 * @return 0 = 有应答，1 = 无应答
 */
uint8_t VL53L0X_I2C_TestConnection(uint8_t address)
{
    uint8_t ack;

    VL53L0X_I2C_Start();
    ack = VL53L0X_I2C_WriteByte(address & 0xFE);
    VL53L0X_I2C_Stop();

    return ack;
}

/*==============================================================================
 *  四、vl53l0x_i2c_platform.h 要求实现的函数
 *      注意：ST 的器件是**大端**的，word/dword 必须"先高字节后低字节"
 * =============================================================================*/

/**
 * @brief  平台初始化（打开 I2C 外设/引脚）
 * @param  comms_type       I2C(0x01) 或 SPI(0x00)，本工程只支持 I2C
 * @param  comms_speed_khz  目标速率(kHz)，软件 I2C 忽略该参数
 */
int32_t VL53L0X_comms_initialise(uint8_t comms_type, uint16_t comms_speed_khz)
{
    (void)comms_speed_khz;

    if (comms_type != I2C) {
        return STATUS_FAIL;         /* 本移植只实现 I2C */
    }

    VL53L0X_I2C_Init();
    return STATUS_OK;
}

/**
 * @brief  关闭平台通信（本工程不需要做任何事）
 */
int32_t VL53L0X_comms_close(void)
{
    return STATUS_OK;
}

/**
 * @brief  给器件断电再上电。本工程不接 XSHUT，无法硬复位，故返回失败。
 */
int32_t VL53L0X_cycle_power(void)
{
    return STATUS_FAIL;
}

/**
 * @brief  向器件连续写 count 个字节：Start + 从机地址 + 寄存器号 + 数据... + Stop
 */
int32_t VL53L0X_write_multi(uint8_t address, uint8_t index, uint8_t *pdata, int32_t count)
{
    int32_t i;

    if ((count <= 0) || (count > COMMS_BUFFER_SIZE) || (pdata == 0)) {
        return STATUS_FAIL;
    }

    VL53L0X_I2C_Start();

    if (VL53L0X_I2C_WriteByte((uint8_t)(address & 0xFE)) != 0) {   /* 写地址 */
        VL53L0X_I2C_Stop();
        return STATUS_FAIL;
    }
    if (VL53L0X_I2C_WriteByte(index) != 0) {                       /* 寄存器号 */
        VL53L0X_I2C_Stop();
        return STATUS_FAIL;
    }
    for (i = 0; i < count; i++) {                                  /* 数据 */
        if (VL53L0X_I2C_WriteByte(pdata[i]) != 0) {
            VL53L0X_I2C_Stop();
            return STATUS_FAIL;
        }
    }

    VL53L0X_I2C_Stop();
    return STATUS_OK;
}

/**
 * @brief  从器件连续读 count 个字节
 *         写地址 -> 寄存器号 -> 重复起始 -> 读地址 -> 读数据... -> Stop
 *         VL53L0X 的寄存器地址会自动递增，所以连续读不需要每字节都发寄存器号。
 */
int32_t VL53L0X_read_multi(uint8_t address, uint8_t index, uint8_t *pdata, int32_t count)
{
    int32_t i;

    if ((count <= 0) || (count > COMMS_BUFFER_SIZE) || (pdata == 0)) {
        return STATUS_FAIL;
    }

    VL53L0X_I2C_Start();

    if (VL53L0X_I2C_WriteByte((uint8_t)(address & 0xFE)) != 0) {   /* 写地址 */
        VL53L0X_I2C_Stop();
        return STATUS_FAIL;
    }
    if (VL53L0X_I2C_WriteByte(index) != 0) {                       /* 寄存器号 */
        VL53L0X_I2C_Stop();
        return STATUS_FAIL;
    }

    VL53L0X_I2C_Start();                                           /* 重复起始条件 */

    if (VL53L0X_I2C_WriteByte((uint8_t)(address | 0x01)) != 0) {   /* 读地址 */
        VL53L0X_I2C_Stop();
        return STATUS_FAIL;
    }

    for (i = 0; i < count; i++) {
        /* 最后一个字节发 NACK，其余发 ACK */
        pdata[i] = VL53L0X_I2C_ReadByte((i == (count - 1)) ? 0 : 1);
    }

    VL53L0X_I2C_Stop();
    return STATUS_OK;
}

/**
 * @brief  写 1 个字节
 */
int32_t VL53L0X_write_byte(uint8_t address, uint8_t index, uint8_t data)
{
    return VL53L0X_write_multi(address, index, &data, 1);
}

/**
 * @brief  写 1 个字（16 位），**高字节在前**
 */
int32_t VL53L0X_write_word(uint8_t address, uint8_t index, uint16_t data)
{
    uint8_t buffer[BYTES_PER_WORD];

    buffer[0] = (uint8_t)(data >> 8);
    buffer[1] = (uint8_t)(data & 0x00FF);

    return VL53L0X_write_multi(address, index, buffer, BYTES_PER_WORD);
}

/**
 * @brief  写 1 个双字（32 位），**高字节在前**
 */
int32_t VL53L0X_write_dword(uint8_t address, uint8_t index, uint32_t data)
{
    uint8_t buffer[BYTES_PER_DWORD];

    buffer[0] = (uint8_t)(data >> 24);
    buffer[1] = (uint8_t)(data >> 16);
    buffer[2] = (uint8_t)(data >> 8);
    buffer[3] = (uint8_t)(data);

    return VL53L0X_write_multi(address, index, buffer, BYTES_PER_DWORD);
}

/**
 * @brief  读 1 个字节
 */
int32_t VL53L0X_read_byte(uint8_t address, uint8_t index, uint8_t *pdata)
{
    return VL53L0X_read_multi(address, index, pdata, 1);
}

/**
 * @brief  读 1 个字（16 位），**高字节在前**
 */
int32_t VL53L0X_read_word(uint8_t address, uint8_t index, uint16_t *pdata)
{
    uint8_t buffer[BYTES_PER_WORD];
    int32_t status;

    status = VL53L0X_read_multi(address, index, buffer, BYTES_PER_WORD);
    if (status == STATUS_OK) {
        *pdata = (uint16_t)(((uint16_t)buffer[0] << 8) + (uint16_t)buffer[1]);
    }

    return status;
}

/**
 * @brief  读 1 个双字（32 位），**高字节在前**
 */
int32_t VL53L0X_read_dword(uint8_t address, uint8_t index, uint32_t *pdata)
{
    uint8_t buffer[BYTES_PER_DWORD];
    int32_t status;

    status = VL53L0X_read_multi(address, index, buffer, BYTES_PER_DWORD);
    if (status == STATUS_OK) {
        *pdata = ((uint32_t)buffer[0] << 24) +
                 ((uint32_t)buffer[1] << 16) +
                 ((uint32_t)buffer[2] << 8) +
                 ((uint32_t)buffer[3]);
    }

    return status;
}

/**
 * @brief  微秒级等待
 */
int32_t VL53L0X_platform_wait_us(int32_t wait_us)
{
    if (wait_us > 0) {
        Delay_us((uint32_t)wait_us);
    }
    return STATUS_OK;
}

/**
 * @brief  毫秒级等待（VL53L0X_PollingDelay 靠它实现）
 */
int32_t VL53L0X_wait_ms(int32_t wait_ms)
{
    if (wait_ms > 0) {
        Delay_ms((uint32_t)wait_ms);
    }
    return STATUS_OK;
}

/**
 * @brief  控制器件引脚电平。ST 的 Nucleo 扩展板用它切换 I2C 电平，
 *         本工程用普通 GPIO 直连模块，不需要，返回失败即可
 *         （ST 的 API 层从不调用这些函数，不影响测距）。
 */
int32_t VL53L0X_set_gpio(uint8_t level)
{
    (void)level;
    return STATUS_FAIL;
}

int32_t VL53L0X_get_gpio(uint8_t *plevel)
{
    (void)plevel;
    return STATUS_FAIL;
}

int32_t VL53L0X_release_gpio(void)
{
    return STATUS_FAIL;
}

/**
 * @brief  时间戳定时器相关。单机测距用不到，返回失败即可。
 */
int32_t VL53L0X_get_timer_frequency(int32_t *ptimer_freq_hz)
{
    (void)ptimer_freq_hz;
    return STATUS_FAIL;
}

int32_t VL53L0X_get_timer_value(int32_t *ptimer_count)
{
    (void)ptimer_count;
    return STATUS_FAIL;
}
