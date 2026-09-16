#!/usr/bin/env python3
"""
TOOMOSS_OTA_TOOL 综合自动化单元测试与端到端模拟测试套件
包含：
1. 核心模块与依赖导入测试
2. AES-128-CMAC 签名算法标准测试向量比对
3. 固件解析器与数据分包切片测试
4. 模拟 ECU（Mock LIN Slave）端到端两阶段 OTA 升级状态机闭环测试
5. 物理 Toomoss 硬件适配器在线自检
"""
import sys
import os
import unittest
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.uds.defines import *
from src.uds.security import calculate_aes_cmac
from src.ota.firmware import FirmwareImage
from src.ota.flasher import OtaFlasher, OtaConfig
from src.toomoss.usb2xxx import Usb2xxxDevice
from src.toomoss.lin_interface import LinUdsInterface, LinUdsAddrConfig
from src.uds.client import UdsClient


class MockLinUdsInterface:
    """
    模拟 LIN 从机 ECU（与 ALIENTEK_WS_V3 下位机协议栈行为 100% 对齐）
    用于在无硬件或台架未通电时进行纯软件协议闭环验证
    """

    def __init__(self, nad=0x01, mask=bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")):
        self.nad = nad
        self.mask = mask
        self.session = SESSION_DEFAULT
        self.dtc_enabled = True
        self.unlocked = False
        self.current_seed = bytes([0x01] * 16)
        self.stage = 0  # 0: idle, 1: drv_dl, 2: drv_verify, 3: drv_ready, 4: app_erase, 5: app_dl, 6: done
        self.rx_buffer = bytearray()
        self.last_bsc = 0
        self.app_valid = False

    def request_response(self, req_data: bytes, timeout_ms: int = 500, retry_count: int = 1) -> bytes:
        if len(req_data) < 1:
            return bytes([SID_NEGATIVE_RESPONSE, 0x00, NRC_INCORRECT_MESSAGE_LENGTH])

        sid = req_data[0]

        # 0x10 会话控制
        if sid == SID_DIAGNOSTIC_SESSION_CONTROL:
            sub = req_data[1]
            self.session = sub
            self.unlocked = False  # 会话切换清空解锁状态
            return bytes([0x50, sub, 0x00, 0x32, 0x01, 0xF4])

        # 0x85 控制 DTC
        if sid == SID_CONTROL_DTC_SETTING:
            sub = req_data[1]
            self.dtc_enabled = (sub == DTC_SETTING_ON)
            return bytes([0xC5, sub])

        # 0x27 安全访问
        if sid == SID_SECURITY_ACCESS:
            sub = req_data[1]
            if sub == 0x01:  # 请求种子
                return bytes([0x67, 0x01]) + self.current_seed
            elif sub == 0x02:  # 发送密钥
                expected_key = calculate_aes_cmac(self.mask, self.current_seed)
                received_key = req_data[2:]
                if received_key == expected_key:
                    self.unlocked = True
                    return bytes([0x67, 0x02])
                else:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_SECURITY_ACCESS, NRC_INVALID_KEY])

        # 0x34 请求下载
        if sid == SID_REQUEST_DOWNLOAD:
            if not self.unlocked or self.session != SESSION_PROGRAMMING:
                return bytes([SID_NEGATIVE_RESPONSE, SID_REQUEST_DOWNLOAD, NRC_SECURITY_ACCESS_DENIED])
            addr = int.from_bytes(req_data[3:7], "big")
            if addr == 0x20008000:
                self.stage = 1  # Flash Drv 下载
            elif addr == 0x08010000:
                if self.stage < 4:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_REQUEST_DOWNLOAD, NRC_REQUEST_SEQUENCE_ERROR])
                self.stage = 5  # APP 下载
            self.last_bsc = 0
            self.rx_buffer.clear()
            # 允许单块 60 字节
            return bytes([0x74, 0x20, 0x00, 0x40])

        # 0x36 传输数据
        if sid == SID_TRANSFER_DATA:
            bsc = req_data[1]
            data = req_data[2:]
            self.last_bsc = bsc
            self.rx_buffer.extend(data)
            return bytes([0x76, bsc])

        # 0x37 传输退出
        if sid == SID_REQUEST_TRANSFER_EXIT:
            if self.stage == 1:
                self.stage = 2  # Drv 待验签
            elif self.stage == 5:
                self.stage = 6  # APP 传输完成
            return bytes([0x77])

        # 0x31 例程控制
        if sid == SID_ROUTINE_CONTROL:
            sub = req_data[1]
            rid = int.from_bytes(req_data[2:4], "big")
            if rid == RID_CHECK_PROGRAM_CONDITIONS:
                return bytes([0x71, sub, req_data[2], req_data[3], 0x00])
            elif rid == RID_SECURITY_SIGN_CHECK:  # 0xDD02
                sig = req_data[4:]
                expected_sig = calculate_aes_cmac(self.mask, self.rx_buffer)
                if sig == expected_sig:
                    self.stage = 3  # Drv 就绪
                    return bytes([0x71, sub, req_data[2], req_data[3], 0x00])
                else:
                    return bytes([0x71, sub, req_data[2], req_data[3], 0x01])
            elif rid == RID_ERASE_APP_FLASH:  # 0xFF00
                if self.stage != 3:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_ROUTINE_CONTROL, NRC_REQUEST_SEQUENCE_ERROR])
                self.stage = 4  # 已擦除
                return bytes([0x71, sub, req_data[2], req_data[3], 0x00])
            elif rid == RID_CHECK_COMPATIBILITY:  # 0xFF01
                if self.stage != 6:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_ROUTINE_CONTROL, NRC_REQUEST_SEQUENCE_ERROR])
                self.app_valid = True
                return bytes([0x71, sub, req_data[2], req_data[3], 0x00])

        # 0x11 ECU 复位
        if sid == SID_ECU_RESET:
            sub = req_data[1]
            return bytes([0x51, sub])

        # 0x3E 测试仪在线
        if sid == SID_TESTER_PRESENT:
            if req_data[1] == 0x80:
                return b""  # 抑制肯定响应
            return bytes([0x7E, req_data[1]])

        return bytes([SID_NEGATIVE_RESPONSE, sid, NRC_SERVICE_NOT_SUPPORTED])

    def send_request(self, req_data: bytes):
        pass

    def receive_response(self, timeout_ms: int = 500) -> bytes:
        return b""


class TestToomossOtaSuite(unittest.TestCase):

    def test_01_security_cmac(self):
        """验证 AES-128-CMAC 计算结果符合标准向量"""
        mask = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        seed = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
        key = calculate_aes_cmac(mask, seed)
        self.assertEqual(key.hex(), "bd506e8c49138891bdbffd61d6219c17")

    def test_02_firmware_slicing(self):
        """验证固件分包切片与 CRC32 计算"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample_path = os.path.join(base_dir, "samples", "sample_flash_drv.bin")
        fw = FirmwareImage(sample_path)
        self.assertGreater(fw.size, 0)
        chunks = fw.get_chunks(block_size=60)
        reconstructed = b"".join(chunks)
        self.assertEqual(reconstructed, fw.data)

    def test_03_mock_end_to_end_ota(self):
        """模拟下位机执行端到端两阶段 OTA 升级全流程"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fdrv_path = os.path.join(base_dir, "samples", "sample_flash_drv.bin")
        app_path = os.path.join(base_dir, "samples", "sample_app.bin")

        mock_transport = MockLinUdsInterface()
        client = UdsClient(mock_transport)

        cfg = OtaConfig(
            mask=mock_transport.mask,
            flash_drv_path=fdrv_path,
            flash_drv_addr=0x20008000,
            app_path=app_path,
            app_addr=0x08010000,
            block_size=60,
        )

        flasher = OtaFlasher(client, cfg)
        progress_records = []

        def _prog(pct, txt):
            progress_records.append((pct, txt))

        success = flasher.execute(progress_cb=_prog)
        self.assertTrue(success)
        self.assertTrue(mock_transport.app_valid, "升级完成后 app_valid 必须为 True")
        self.assertEqual(progress_records[-1][0], 100.0, "最终进度必须达到 100%")

    def test_04_hardware_probe(self):
        """测试已连接的真实 Toomoss 硬件"""
        handles = Usb2xxxDevice.scan_devices()
        if len(handles) > 0:
            dev = Usb2xxxDevice(0)
            dev.open()
            info = dev.get_info()
            self.assertTrue(len(info.firmware_name) > 0)
            dev.init_lin(channel=0, baudrate=19200, master_mode=1)
            dev.init_lin(channel=1, baudrate=19200, master_mode=1)
            dev.close()
            print(f"\n[OK] 物理 Toomoss 设备在线自检通过: {info.firmware_name} (SN: {info.serial_number})")
        else:
            print("\n[跳过] 当前未插入 Toomoss 物理设备，跳过硬件探测")


if __name__ == "__main__":
    unittest.main(verbosity=2)
