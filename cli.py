#!/usr/bin/env python3
"""
Toomoss UDS OTA 工具 - 命令行接口 (CLI)
支持 LIN-TP / CAN-TP 物理总线与免硬件 MOCK 仿真模式
适用于自动化脚本、工厂量产烧录与 CI/CD 回归测试
"""
import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.toomoss.usb2xxx import Usb2xxxDevice
from src.toomoss.lin_interface import LinUdsInterface, LinUdsAddrConfig
from src.toomoss.can_interface import CanUdsInterface, CanUdsAddrConfig
from src.uds.mock_simulator import MockUdsSimulator
from src.uds.client import UdsClient
from src.ota.flasher import OtaFlasher, OtaConfig


def main():
    parser = argparse.ArgumentParser(description="Toomoss UDS OTA Flashing Tool (CLI Mode - CAN/LIN/MOCK)")
    parser.add_argument("--bus", choices=["lin", "can", "mock"], default="lin", help="总线类型: lin, can, mock (默认: lin)")
    parser.add_argument("--device-idx", type=int, default=0, help="Toomoss 设备索引 (默认: 0)")
    parser.add_argument("--channel", type=int, default=0, help="总线通道索引 (默认: 0)")
    parser.add_argument("--baudrate", type=int, default=None, help="波特率 (LIN 默认 19200, CAN 默认 500000)")
    parser.add_argument("--nad", type=lambda x: int(x, 0), default=0x68, help="LIN 从机 NAD (默认: 0x68)")
    parser.add_argument("--can-req-id", type=lambda x: int(x, 0), default=0x7E0, help="CAN 请求 ID (默认: 0x7E0)")
    parser.add_argument("--can-res-id", type=lambda x: int(x, 0), default=0x7E8, help="CAN 应答 ID (默认: 0x7E8)")
    parser.add_argument("--app", type=str, required=True, help="APP 固件文件路径 (.bin / .hex)")
    parser.add_argument("--app-addr", type=lambda x: int(x, 0), default=0x08010000, help="APP 目标 Flash 基址 (默认: 0x08010000)")
    parser.add_argument("--flash-drv", type=str, default=None, help="Flash Driver 擦写驱动文件路径 (.bin)")
    parser.add_argument("--flash-drv-addr", type=lambda x: int(x, 0), default=0x20008000, help="Flash Driver 目标 RAM 基址 (默认: 0x20008000)")
    parser.add_argument("--mask", type=str, default="2b7e151628aed2a6abf7158809cf4f3c", help="AES-128 安全密钥 Mask (16 字节十六进制)")
    parser.add_argument("--block-size", type=int, default=None, help="单次 0x36 传输块大小 (LIN 默认 60, CAN/MOCK 默认 256)")

    args = parser.parse_args()

    bus_type = args.bus.lower()
    if args.baudrate is None:
        baudrate = 19200 if bus_type == "lin" else 500000
    else:
        baudrate = args.baudrate

    if args.block_size is None:
        block_size = 60 if bus_type == "lin" else 256
    else:
        block_size = args.block_size

    print("==================================================================")
    print("      Toomoss UDS OTA CLI Flasher (车规级 CAN/LIN/MOCK 两阶段刷写)")
    print("==================================================================")
    print(f"[*] 协议总线类型   : {bus_type.upper()}")
    print(f"[*] APP 固件路径   : {args.app}")
    print(f"[*] APP Flash 地址 : 0x{args.app_addr:08X}")
    print(f"[*] Flash Driver   : {args.flash_drv if args.flash_drv else '[已禁用]'}")
    if args.flash_drv:
        print(f"[*] Flash Drv RAM  : 0x{args.flash_drv_addr:08X}")
    if bus_type == "lin":
        print(f"[*] LIN 通道/波特率: 通道 {args.channel} / {baudrate} bps")
        print(f"[*] 从机 NAD       : 0x{args.nad:02X}")
    elif bus_type == "can":
        print(f"[*] CAN 通道/波特率: 通道 {args.channel} / {baudrate} bps")
        print(f"[*] CAN 请求/响应ID: 0x{args.can_req_id:03X} / 0x{args.can_res_id:03X}")
    else:
        print("[*] 仿真环境       : 虚拟 ECU 内存直通仿真 (免物理适配器)")
    print(f"[*] 单块 BlockSize : {block_size} 字节")
    print("------------------------------------------------------------------")

    dev = None
    try:
        mask_bytes = bytes.fromhex(args.mask)

        # 1. 建立传输接口
        if bus_type == "mock":
            iface = MockUdsSimulator(mask=mask_bytes, mask_fbl=mask_bytes, bus_type="CAN")
        else:
            handles = Usb2xxxDevice.scan_devices()
            if not handles:
                print("[-] 错误: 未检测到任何已连接的 Toomoss USB2XXX 设备！")
                return 1

            print(f"[+] 发现 {len(handles)} 个设备，正在连接设备 #{args.device_idx}...")
            dev = Usb2xxxDevice(device_index=args.device_idx)
            dev.open()

            info = dev.get_info()
            print(f"[+] 硬件连接成功: {info.firmware_name} (SN: {info.serial_number})")

            if bus_type == "lin":
                dev.init_lin(channel=args.channel, baudrate=baudrate, master_mode=1)
                addr_cfg = LinUdsAddrConfig(nad=args.nad)
                iface = LinUdsInterface(device=dev, channel=args.channel, addr_cfg=addr_cfg)
            else:
                dev.init_can(channel=args.channel, baudrate=baudrate)
                addr_cfg = CanUdsAddrConfig(req_id=args.can_req_id, res_id=args.can_res_id)
                iface = CanUdsInterface(device=dev, channel=args.channel, addr_cfg=addr_cfg)

        # 2. 构造 UDS 客户端与 Flasher
        client = UdsClient(iface)
        ota_cfg = OtaConfig(
            mask=mask_bytes,
            flash_drv_path=args.flash_drv,
            flash_drv_addr=args.flash_drv_addr,
            app_path=args.app,
            app_addr=args.app_addr,
            block_size=block_size,
        )

        flasher = OtaFlasher(client, ota_cfg)

        def _cli_progress(pct: float, txt: str):
            bar_len = 30
            filled = int(bar_len * pct / 100.0)
            bar = "=" * filled + "-" * (bar_len - filled)
            print(f"\r[{bar}] {pct:5.1f}% | {txt}", end="", flush=True)

        def _cli_log(msg: str):
            pass

        print("[*] 开始执行 OTA 刷写流程...")
        start_t = time.time()
        flasher.execute(progress_cb=_cli_progress, log_cb=_cli_log)
        total_time = time.time() - start_t

        print(f"\n[+] 刷写圆满成功！总耗时: {total_time:.2f} 秒。")
        return 0

    except Exception as e:
        print(f"\n[-] 刷写失败: {e}")
        return 2
    finally:
        if dev is not None and dev.is_opened:
            dev.close()
            print("[*] 已断开物理设备连接。")


if __name__ == "__main__":
    sys.exit(main())
