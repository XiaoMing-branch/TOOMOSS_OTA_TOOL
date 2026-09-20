# Toomoss UDS OTA Tool (车规级 CAN / LIN / 虚拟仿真多模态刷写工具)

基于 **图莫斯（Toomoss）USB2XXX 适配器** 与 **内存虚拟仿真器** 的全栈车规级 **UDS 协议 OTA 刷写工具**。
专为汽车电子 ECU、嵌入式 BootLoader（如 `ALIENTEK_WS_V3` 标准工程）提供安全、标准、可靠的 CAN / LIN 总线诊断刷写、DID 读取与自动化回归测试能力。

---

## 🌟 核心特性

- **三模态通信架构支持 (LIN / CAN / MOCK)**：
  - **LIN-TP (ISO 17987-2 / ISO 14229-7)**：支持主节点调度、MRF (0x3C) / SRF (0x3D) 时隙轮询与经典校验。
  - **CAN-TP (ISO 15765-2)**：支持标准单帧 (SF)、首帧 (FF)、连续帧 (CF) 与流控帧 (FC) 拆包分包重组。
  - **虚拟 ECU 仿真 (Mock Mode)**：内置免硬件虚拟 ECU，无需任何适配器或开发板即可 100% 跑通两阶段 OTA 升级与报文抓包。
- **严格遵循两阶段安全刷写时序（依据赛力斯 Q/SK J02.321 车规级规范）**：
  - **阶段一（Flash Driver 注入）**：请求下载至 RAM (`0x20008000`) -> 传输驱动固件 -> 验签 (`0x31 01 DD 02` AES-128-CMAC 签名校验)。
  - **阶段二（片上 Flash 擦写与 APP 烧录）**：调用 RAM 中的 Flash Driver 擦除 APP 分区 (`0x31 01 FF 00`) -> 请求下载 APP (`0x08010000`) -> 分包烧录 -> 完整性校验 (`0x31 01 FF 01`) -> ECU 硬复位 (`0x11 01`)。
- **高等级安全访问（0x27 SecurityAccess）**：
  - 内置车规级 **AES-128-CMAC** 算法，自动获取 16 字节随机 Seed 并运算出 Key 解锁敏感服务。
- **动态 P2* 弹性循环机制**：
  - 核心客户端具备针对 `NRC 0x78 (Response Pending)` 的自适应长等待状态机（默认 5000ms P2* 循环监控），从容应对大容量 Flash 擦除或长耗时验签。
- **丰富易用的交互形态**：
  - **现代桌面 GUI**：总线单选切换、设备扫描、参数自适应、百分比进度条与十六进制 UDS 实时抓包控制台。
  - **规范 DID 诊断读取工具 (`0x22`)**：可视化解析并展示车规零件号、版本号、VIN 码与运行状态。
  - **工业级 CLI 命令行**：支持自动化测试、量产批处理与 CI/CD 流水线烧录（`--bus {lin,can,mock}`）。

---

## 🔌 硬件接线说明

Toomoss USB 适配器与目标板（如正点原子战舰 V3 / STM32F103）连接示意：

### 1. LIN 模式接线
| Toomoss 适配器端子 | 目标开发板 / ECU | 说明 |
| :--- | :--- | :--- |
| **LIN** | **LIN 总线** (或板载 LIN 收发器) | LIN 单线总线通信信号 |
| **VBAT / 12V** | **DC 12V 供电** | LIN 总线物理层工作电压 (9~18V，通常 12V) |
| **GND** | **GND** | 必须共地！ |

> ⚠️ **LIN 参数**：下位机 `ALIENTEK_WS_V3` 标准配置：**波特率 19200 bps**，从机节点地址 **NAD = 0x68**，MRF 请求帧 ID = **0x3C**，SRF 响应帧 ID = **0x3D**。

### 2. CAN 模式接线
| Toomoss 适配器端子 | 目标开发板 / ECU | 说明 |
| :--- | :--- | :--- |
| **CAN_H** | **CAN_H** | 差分高信号线 |
| **CAN_L** | **CAN_L** | 差分低信号线 |
| **GND** | **GND** | 必须共地（总线两端需并接 120Ω 终端电阻） |

> ⚠️ **CAN 参数**：下位机 `ALIENTEK_WS_V3` 标准配置：**波特率 500 kbps**，诊断请求 ID = **0x7E0**，诊断响应 ID = **0x7E8**，功能寻址 ID = **0x7DF**。

---

## 🚀 快速启动

### 方式一：运行图形界面 (GUI)

直接双击运行工程根目录下的 `run_gui.bat`，或在命令行中执行：
```bash
python main.py
```

#### GUI 操作步骤：
1. 在左侧选择 **总线协议类型**：`[ LIN | CAN | 仿真 (Mock) ]`；
2. 若为物理硬件：点击 **[扫描]** 选择检测到的设备，确认通道与波特率，点击 **[打开设备连接]**；
3. 若为免硬件仿真：直接选中 `仿真 (Mock)`，点击 **[打开设备连接]** 即可激活内存直通虚拟 ECU；
4. 选择 **Flash Driver** 文件（`samples/sample_flash_drv.bin`）及目标 RAM 地址（默认 `0x20008000`）；
5. 选择 **Application 固件** 文件（`samples/sample_app.bin`）及目标 Flash 地址（默认 `0x08010000`）；
6. 点击 **[🚀 开始 OTA 刷写]**，观察实时进度条与下方的十六进制通信日志。

---

### 方式二：运行命令行自动化模式 (CLI)

直接双击运行 `run_cli.bat` 查看帮助，或使用命令行执行自动化烧录：

```bash
# 1. 免硬件 MOCK 仿真模式快速回归测试（耗时约 1.5 秒）：
python cli.py --bus mock --app samples/sample_app.bin --flash-drv samples/sample_flash_drv.bin

# 2. 真实 LIN 总线刷写模式：
python cli.py --bus lin --channel 0 --baudrate 19200 --nad 0x68 --app samples/sample_app.bin --flash-drv samples/sample_flash_drv.bin

# 3. 真实 CAN 总线刷写模式：
python cli.py --bus can --channel 0 --baudrate 500000 --can-req-id 0x7E0 --can-res-id 0x7E8 --app samples/sample_app.bin --flash-drv samples/sample_flash_drv.bin
```

#### 常用命令行参数说明：
- `--bus {lin,can,mock}`：协议总线类型（默认 `lin`）。
- `--app <file>`：指定 APP 固件路径（必填，支持 `.bin` / `.hex`）。
- `--app-addr <hex>`：APP 烧录目标 Flash 地址（默认 `0x08010000`）。
- `--flash-drv <file>`：指定 Flash Driver 文件（阶段一注入，可选）。
- `--flash-drv-addr <hex>`：Flash Driver 目标 RAM 地址（默认 `0x20008000`）。
- `--nad <hex>`：LIN 从机诊断节点地址（默认 `0x68`）。
- `--can-req-id <hex>`：CAN 请求 ID（默认 `0x7E0`）。
- `--can-res-id <hex>`：CAN 响应 ID（默认 `0x7E8`）。
- `--baudrate <int>`：总线波特率（LIN 默认 `19200`，CAN 默认 `500000`）。
- `--mask <hex>`：16 字节 AES-128 安全密钥 Mask（默认 `2b7e151628aed2a6abf7158809cf4f3c`）。
- `--block-size <int>`：单块传输字节数（LIN 默认 `60`，CAN/MOCK 默认 `256`）。

---

## 📂 项目结构

```text
TOOMOSS_OTA_TOOL/
├── libs/                           # 官方动态链接库 (USB2XXX.dll, libusb-1.0.dll)
├── config/
│   └── default_config.json         # 默认通讯、UDS 与刷写参数配置文件
├── samples/                        # 示例固件 (sample_app.bin, sample_flash_drv.bin)
├── src/
│   ├── toomoss/                    # Toomoss 硬件适配层
│   │   ├── usb2xxx.py              # USB2XXX 设备枚举、打开/关闭与通道探测
│   │   ├── lin_interface.py        # LIN-TP 协议通信封装 (TransportInterface 实现)
│   │   └── can_interface.py        # CAN-TP 协议通信封装 (TransportInterface 实现)
│   ├── uds/                        # 车规级 UDS 协议核心层
│   │   ├── transport_base.py       # 传输接口抽象基类 (TransportInterface)
│   │   ├── mock_simulator.py       # 内置零硬件虚拟 ECU 仿真器 (Mock ECU)
│   │   ├── defines.py              # 服务 SID、子功能、例程 RID、NRC 中文错误码字典
│   │   ├── security.py             # AES-128-CMAC 算法计算器 (硬件安全认证)
│   │   └── client.py               # UDS 诊断客户端、NRC 0x78 弹性循环与后台保活
│   ├── ota/                        # OTA 升级流程层
│   │   ├── firmware.py             # 固件读取、切片分包、CRC32 与 CMAC 签名计算
│   │   └── flasher.py              # 两阶段 OTA 刷写状态机引擎
│   └── gui/                        # 用户交互层 (Tkinter GUI)
│       ├── app.py                  # 现代桌面 GUI 主窗体与报文监视器
│       └── did_manager.py          # 规范 DID 诊断读取工具窗体 (0x22)
├── tests/
│   └── test_all.py                 # 全套自动化单元与集成测试套件
├── main.py                         # 统一入口 (无参 GUI, --cli 命令行)
├── cli.py                          # 自动化命令行参数化入口
├── run_gui.bat                     # Windows 一键启动 GUI
├── run_cli.bat                     # Windows 一键启动命令行
└── README.md                       # 说明文档
```
