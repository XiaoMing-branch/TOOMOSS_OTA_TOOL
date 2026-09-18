#!/usr/bin/env python3
"""
Toomoss LIN UDS OTA 工具 - 命令行接口 (CLI)
适用于自动化脚本、工厂量产烧录与回归测试
"""
import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.toomoss.usb2xxx import Usb2xxxDevice
from src.toomoss.lin_interface import LinUdsInterface, LinUdsAddrConfig
from src.uds.client import UdsClient
from src.ota.flasher import OtaFlasher, OtaConfig


def main():
    parser = argparse.ArgumentParser(description="Toomoss LIN UDS OTA Flashing Tool (CLI Mode)")
    parser.add_argument("--device-idx", type=int, default=0, help="Toomoss 设备索引 (默认: 0)")
    parser.add_argument("--channel", type=int, default=0, help="LIN 通道索引 (默认: 0)")
    parser.add_argument("--baudrate", type=int, default=19200, help="LIN 波特率 (默认: 19200)")
    parser.add_argument("--nad", type=lambda x: int(x, 0), default=0x68, help="ECU 诊断节点地址 NAD (默认: 0x68)")
    parser.add_argument("--app", type=str, required=True, help="APP 固件文件路径 (.bin / .hex)")
    parser.add_argument("--app-addr", type=lambda x: int(x, 0), default=0x08010000, help="APP 目标 Flash 基址 (默认: 0x08010000)")
    parser.add_argument("--flash-drv", type=str, default=None, help="Flash Driver 擦写驱动文件路径 (.bin)")
    parser.add_argument("--flash-drv-addr", type=lambda x: int(x, 0), default=0x20008000, help="Flash Driver 目标 RAM 基址 (默认: 0x20008000)")
    parser.add_argument("--mask", type=str, default="2b7e151628aed2a6abf7158809cf4f3c", help="AES-128 安全密钥 Mask (16 字节十六进制)")
    parser.add_argument("--block-size", type=int, default=60, help="单次 0x36 传输块大小 (默认: 60)")

    args = parser.parse_args()

    print("==================================================================")
    print("      Toomoss LIN UDS OTA CLI Flasher (车规级两阶段刷写)")
    print("==================================================================")
    print(f"[*] APP 固件路径   : {args.app}")
    print(f"[*] APP Flash 地址 : 0x{args.app_addr:08X}")
    print(f"[*] Flash Driver   : {args.flash_drv if args.flash_drv else '[已禁用]'}")
    if args.flash_drv:
        print(f"[*] Flash Drv RAM  : 0x{args.flash_drv_addr:08X}")
    print(f"[*] LIN 波特率     : {args.baudrate} bps")
    print(f"[*] 从机 NAD       : 0x{args.nad:02X}")
    print("------------------------------------------------------------------")

    # 1. 扫描与打开设备
    handles = Usb2xxxDevice.scan_devices()
    if not handles:
        print("[-] 错误: 未检测到任何已连接的 Toomoss USB2XXX 设备！")
        return 1

    print(f"[+] 发现 {len(handles)} 个设备，正在连接设备 #{args.device_idx}...")
    dev = Usb2xxxDevice(device_index=args.device_idx)
    dev.open()

    try:
        # 2. 初始化 LIN
        dev.init_lin(channel=args.channel, baudrate=args.baudrate, master_mode=1)
        info = dev.get_info()
        print(f"[+] 硬件连接成功: {info.firmware_name} (SN: {info.serial_number})")

        # 3. 构造 UDS 客户端
        addr_cfg = LinUdsAddrConfig(nad=args.nad)
        iface = LinUdsInterface(device=dev, channel=args.channel, addr_cfg=addr_cfg)
        client = UdsClient(iface)

        # 4. 配置并执行刷写
        ota_cfg = OtaConfig(
            mask=bytes.fromhex(args.mask),
            flash_drv_path=args.flash_drv,
            flash_drv_addr=args.flash_drv_addr,
            app_path=args.app,
            app_addr=args.app_addr,
            block_size=args.block_size,
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
        dev.close()
        print("[*] 已断开设备连接。")


if __name__ == "__main__":
    sys.exit(main())
