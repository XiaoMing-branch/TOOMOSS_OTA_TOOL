# Toomoss LIN UDS OTA Tool (上位机固件升级工具)

基于 **图莫斯（Toomoss）USB2XXX USB 转 LIN 适配器** 的车规级 **UDS 协议 OTA 升级工具**。
专为汽车电子 ECU、嵌入式 BootLoader（如 `ALIENTEK_WS_V3` 标准工程）提供安全、标准、可靠的 LIN 总线诊断刷写与测试能力。

---

## 🌟 核心特性

- **严格遵循两阶段安全刷写时序（依据赛力斯 Q/SK J02.321 车规级规范）**：
  - **阶段一（Flash Driver 注入）**：请求下载至 RAM (`0x20008000`) -> 传输驱动固件 -> 验签 (`0x31 01 DD 02` AES-128-CMAC 签名校验)。
  - **阶段二（片上 Flash 擦写与 APP 烧录）**：调用 RAM 中的 Flash Driver 擦除 APP 分区 (`0x31 01 FF 00`) -> 请求下载 APP (`0x08010000`) -> 分包烧录 -> 完整性校验 (`0x31 01 FF 01`) -> ECU 硬复位 (`0x11 01`)。
- **高等级安全访问（0x27 SecurityAccess）**：
  - 内置车规级 **AES-128-CMAC** 算法，自动获取 16 字节随机 Seed 并运算出 Key 解锁敏感服务。
- **双模操作支持**：
  - **现代桌面 GUI**：具备设备扫描、参数配置、固件选择、进度百分比、状态机实时提示与十六进制 UDS 报文监视器。
  - **CLI 命令行**：支持自动化测试、量产批处理与 CI/CD 流水线烧录。
- **高可靠性与容错**：
  - 内置 `0x3E` 测试仪在线（TesterPresent）后台异步保活线程，防止刷写过程中 ECU 发生 S3 定时器超时（5000ms）复位。
  - 完善的 NRC 负响应错误码字典与中文详细释义。
  - 单块自适应分包（默认 60 字节，完美适配下位机 64 字节缓冲区）。

---

## 🔌 硬件接线说明

Toomoss USB 转 LIN 适配器与目标板（如正点原子战舰 V3 / STM32F103）连接示意：

| Toomoss 适配器端子 | 目标开发板 / ECU | 说明 |
| :--- | :--- | :--- |
| **LIN** | **LIN 总线** (或 LIN 收发器 LIN 脚) | LIN 单线总线通信信号 |
| **VBAT / 12V** | **DC 12V 供电** | LIN 总线物理层工作电压 (9~18V，通常 12V) |
| **GND** | **GND** | 必须共地！ |

> ⚠️ **注意**：
> 1. 下位机 `ALIENTEK_WS_V3` 配置的 LIN 通信参数为：**波特率 19200 bps**，从机节点地址 **NAD = 0x01**，MRF 请求帧 ID = **0x3C**，SRF 响应帧 ID = **0x3D**。
> 2. LIN 总线需要 1kΩ 上拉电阻至 12V（Toomoss 适配器作为主机通常已内嵌主机上拉与二极管）。

---

## 🚀 快速启动

### 方式一：运行图形界面 (GUI)

直接双击运行工程根目录下的：
```bash
run_gui.bat
```
或者在命令行中执行：
```bash
python main.py
```

#### GUI 操作步骤：
1. 点击 **[扫描]**，选择检测到的 Toomoss 设备；
2. 选择 **LIN 通道**（支持 `通道 0 (LIN1)`、`通道 1 (LIN2)` 等多通道选择）；
3. 确认 LIN 波特率（默认 `19200`）与从机 NAD（默认 `0x01`），点击 **[打开连接]**；
4. 选择 **Flash Driver** 文件（`flash_drv.bin`）及目标 RAM 地址（默认 `0x20008000`）；
5. 选择 **Application 固件** 文件（`project.bin`）及目标 Flash 地址（默认 `0x08010000`）；
6. 点击 **[🚀 开始 OTA 升级]**，观察实时进度条与下方的十六进制通信日志。

---

### 方式二：运行命令行自动化模式 (CLI)

直接双击运行 `run_cli.bat` 查看帮助，或使用命令行执行自动化烧录：

```bash
python main.py --cli --app "../ALIENTEK_WS_V3/project/app/project.bin" --flash-drv "../ALIENTEK_WS_V3/project/flash_drv/flash_drv.bin" --nad 0x01 --baudrate 19200
```

#### 常用命令行参数说明：
- `--app <file>`：指定 APP 固件路径（必填，支持 `.bin` / `.hex`）。
- `--app-addr <hex>`：APP 烧录目标 Flash 地址（默认 `0x08010000`）。
- `--flash-drv <file>`：指定 Flash Driver 文件（阶段一注入，可选）。
- `--flash-drv-addr <hex>`：Flash Driver 目标 RAM 地址（默认 `0x20008000`）。
- `--nad <hex>`：从机诊断节点地址（默认 `0x01`）。
- `--baudrate <int>`：LIN 波特率（默认 `19200`）。
- `--mask <hex>`：16 字节 AES-128 安全密钥 Mask（默认 `2b7e151628aed2a6abf7158809cf4f3c`）。
- `--block-size <int>`：单块传输字节数（默认 `60`）。

---

## 📂 项目结构

```text
TOOMOSS_OTA_TOOL/
├── libs/                           # 官方动态链接库 (USB2XXX.dll, libusb-1.0.dll)
├── config/
│   └── default_config.json         # 默认通讯、UDS与刷写参数配置文件
├── src/
│   ├── toomoss/                    # Toomoss 硬件驱动层
│   │   ├── usb2xxx.py              # USB2XXX 设备枚举、打开/关闭与通道配置
│   │   └── lin_interface.py        # LIN-UDS 协议通信封装 (LIN_UDS_Request / Response)
│   ├── uds/                        # 车规级 UDS 协议层
│   │   ├── defines.py              # 服务 SID、子功能、例程 RID、NRC 中文错误码字典
│   │   ├── security.py             # AES-128-CMAC 算法计算器 (硬件安全认证)
│   │   └── client.py               # UDS 诊断客户端与后台会话保活 (0x3E)
│   ├── ota/                        # OTA 升级流程层
│   │   ├── firmware.py             # 固件读取、切片分包、CRC32 与 CMAC 签名计算
│   │   └── flasher.py              # 两阶段 OTA 刷写状态机引擎
│   └── gui/                        # 用户交互层 (Tkinter GUI)
│       └── app.py                  # 现代桌面 GUI 主窗体
├── main.py                         # 统一入口 (无参 GUI, --cli 命令行)
├── cli.py                          # 自动化命令行入口
├── run_gui.bat                     # Windows 一键启动 GUI
├── run_cli.bat                     # Windows 一键启动命令行
└── README.md                       # 说明文档
```
