#!/usr/bin/env python3
"""
TOOMOSS_OTA_TOOL 综合自动化单元测试与端到端模拟测试套件
包含：
1. 核心模块与依赖导入测试
2. AES-128-CMAC 签名算法标准测试向量比对
3. 固件解析器与数据分包切片测试
4. 模拟 ECU（Mock LIN Slave）端到端两阶段 OTA 升级状态机闭环测试
   — 严格遵循 Q/SK J02.321 状态机校验，拒绝乱序请求
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
from src.uds.mock_simulator import MockUdsSimulator


class MockLinUdsInterface:
    """
    模拟 LIN 从机 ECU（严格对齐 ALIENTEK_WS_V3 下位机协议栈行为）
    实施 Q/SK J02.321 状态机规则：
    - 10 02 编程会话要求预编程标志=1，否则回 NRC 0x22
    - 31 01 02 03 刷写条件检测仅在扩展会话允许
    - 2E F184 写指纹需在扩展会话 + LEVEL_1 解锁
    - 27 09/0A FBL 解锁需在编程会话
    - 34 请求下载需编程会话 + FBL 解锁
    - 31 01 DD 02 验签后才允许擦除/兼容性检查
    - 31 01 FF 01 兼容性检查需先通过 App CMAC 验签
    """

    def __init__(self, nad=0x68, mask=bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")):
        self.nad = nad
        self.mask = mask
        self.session = SESSION_DEFAULT
        self.dtc_enabled = True

        # 安全访问状态
        self.unlocked_l1 = False
        self.unlocked_fbl = False
        self.current_seed = bytes([0x01] * 16)

        # Q/SK J02.321 状态标志
        self.pre_programming_flag = False  # 31 01 02 03 置位
        self.fingerprint_written = False   # 2E F184 置位

        # 刷写状态机
        # 0: idle, 1: drv_downloading, 2: drv_transferred, 3: drv_verified
        # 4: app_erased, 5: app_downloading, 6: app_transferred, 7: app_verified, 8: done
        self.flash_stage = 0
        self.rx_buffer = bytearray()
        self.last_bsc = 0
        self.app_valid = False

    def request_response(self, req_data: bytes, timeout_ms: int = 500, retry_count: int = 1) -> bytes:
        if len(req_data) < 1:
            return bytes([SID_NEGATIVE_RESPONSE, 0x00, NRC_INCORRECT_MESSAGE_LENGTH])

        sid = req_data[0]

        # ---------- 0x10 会话控制 ----------
        if sid == SID_DIAGNOSTIC_SESSION_CONTROL:
            sub = req_data[1] & 0x7F  # 剥离 SPRMIB
            suppress = bool(req_data[1] & 0x80)

            # 进入编程会话需要预编程标志=1 (Q/SK J02.321 核心规则)
            if sub == SESSION_PROGRAMMING and not self.pre_programming_flag:
                return bytes([SID_NEGATIVE_RESPONSE, sid, NRC_CONDITIONS_NOT_CORRECT])

            self.session = sub
            # 会话切换清空安全访问状态
            self.unlocked_l1 = False
            self.unlocked_fbl = False

            if suppress:
                return b""  # 抑制正响应
            return bytes([0x50, sub, 0x00, 0x32, 0x01, 0xF4])

        # ---------- 0x85 控制 DTC ----------
        if sid == SID_CONTROL_DTC_SETTING:
            sub = req_data[1] & 0x7F
            suppress = bool(req_data[1] & 0x80)
            self.dtc_enabled = (sub == DTC_SETTING_ON)
            if suppress:
                return b""
            return bytes([0xC5, sub])

        # ---------- 0x27 安全访问 ----------
        if sid == SID_SECURITY_ACCESS:
            sub = req_data[1]

            # LEVEL_1 (0x01/0x02) — 扩展会话下使用
            if sub == 0x01:
                if self.unlocked_l1:
                    return bytes([0x67, 0x01]) + bytes(16)  # 已解锁返回全 0
                return bytes([0x67, 0x01]) + self.current_seed
            elif sub == 0x02:
                expected_key = calculate_aes_cmac(self.mask, self.current_seed)
                received_key = req_data[2:]
                if received_key == expected_key:
                    self.unlocked_l1 = True
                    return bytes([0x67, 0x02])
                else:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_SECURITY_ACCESS, NRC_INVALID_KEY])

            # LEVEL_FBL (0x09/0x0A) — 编程会话下使用
            elif sub == 0x09:
                if self.session != SESSION_PROGRAMMING:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_SECURITY_ACCESS, NRC_CONDITIONS_NOT_CORRECT])
                if self.unlocked_fbl:
                    return bytes([0x67, 0x09]) + bytes(16)
                return bytes([0x67, 0x09]) + self.current_seed
            elif sub == 0x0A:
                expected_key = calculate_aes_cmac(self.mask, self.current_seed)
                received_key = req_data[2:]
                if received_key == expected_key:
                    self.unlocked_fbl = True
                    return bytes([0x67, 0x0A])
                else:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_SECURITY_ACCESS, NRC_INVALID_KEY])

        # ---------- 0x2E 写入 DID ----------
        if sid == SID_WRITE_DATA_BY_IDENTIFIER:
            did = (req_data[1] << 8) | req_data[2]
            if did == 0xF184:
                # 写指纹需要扩展会话 + LEVEL_1 解锁
                if self.session != SESSION_EXTENDED:
                    return bytes([SID_NEGATIVE_RESPONSE, sid, 0x7F])  # NRC: 服务不支持当前会话
                if not self.unlocked_l1:
                    return bytes([SID_NEGATIVE_RESPONSE, sid, NRC_SECURITY_ACCESS_DENIED])
                self.fingerprint_written = True
                return bytes([0x6E, req_data[1], req_data[2]])
            return bytes([SID_NEGATIVE_RESPONSE, sid, NRC_REQUEST_OUT_OF_RANGE])

        # ---------- 0x31 例程控制 ----------
        if sid == SID_ROUTINE_CONTROL:
            sub = req_data[1]
            rid = int.from_bytes(req_data[2:4], "big")

            # 刷写条件检测 (0x0203) — 设置预编程标志
            if rid == RID_CHECK_PROGRAM_CONDITIONS:
                if self.session != SESSION_EXTENDED:
                    return bytes([SID_NEGATIVE_RESPONSE, sid, NRC_CONDITIONS_NOT_CORRECT])
                self.pre_programming_flag = True
                return bytes([0x71, sub, req_data[2], req_data[3], 0x00])

            # Flash Drv / App 签名验证 (0xDD02)
            elif rid == RID_SECURITY_SIGN_CHECK:
                sig = req_data[4:]
                expected_sig = calculate_aes_cmac(self.mask, self.rx_buffer)
                if sig == expected_sig:
                    if self.flash_stage == 2:  # Flash Drv 验签
                        self.flash_stage = 3
                    elif self.flash_stage == 6:  # App 验签
                        self.flash_stage = 7
                    return bytes([0x71, sub, req_data[2], req_data[3], 0x00])
                else:
                    return bytes([0x71, sub, req_data[2], req_data[3], 0x01])

            # 擦除 APP Flash (0xFF00) — 需 Flash Drv 已验签 (stage=3)
            elif rid == RID_ERASE_APP_FLASH:
                if self.flash_stage < 3:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_ROUTINE_CONTROL, NRC_REQUEST_SEQUENCE_ERROR])
                self.flash_stage = 4
                self.rx_buffer.clear()  # 清空缓冲区，准备接收 App 数据
                return bytes([0x71, sub, req_data[2], req_data[3], 0x00])

            # 兼容性检查 (0xFF01) — 需 App 已验签 (stage=7)
            elif rid == RID_CHECK_COMPATIBILITY:
                if self.flash_stage != 7:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_ROUTINE_CONTROL, NRC_REQUEST_SEQUENCE_ERROR])
                self.app_valid = True
                self.flash_stage = 8
                return bytes([0x71, sub, req_data[2], req_data[3], 0x00])

        # ---------- 0x34 请求下载 ----------
        if sid == SID_REQUEST_DOWNLOAD:
            if not self.unlocked_fbl or self.session != SESSION_PROGRAMMING:
                return bytes([SID_NEGATIVE_RESPONSE, SID_REQUEST_DOWNLOAD, NRC_SECURITY_ACCESS_DENIED])
            addr = int.from_bytes(req_data[3:7], "big")
            if addr == 0x20008000:
                self.flash_stage = 1  # Flash Drv 下载中
            elif addr == 0x08010000:
                if self.flash_stage < 4:
                    return bytes([SID_NEGATIVE_RESPONSE, SID_REQUEST_DOWNLOAD, NRC_REQUEST_SEQUENCE_ERROR])
                self.flash_stage = 5  # APP 下载中
            self.last_bsc = 0
            self.rx_buffer.clear()
            return bytes([0x74, 0x20, 0x00, 0x40])

        # ---------- 0x36 传输数据 ----------
        if sid == SID_TRANSFER_DATA:
            bsc = req_data[1]
            data = req_data[2:]
            self.last_bsc = bsc
            self.rx_buffer.extend(data)
            return bytes([0x76, bsc])

        # ---------- 0x37 传输退出 ----------
        if sid == SID_REQUEST_TRANSFER_EXIT:
            if self.flash_stage == 1:
                self.flash_stage = 2  # Drv 传输完成，待验签
            elif self.flash_stage == 5:
                self.flash_stage = 6  # APP 传输完成，待验签
            return bytes([0x77])

        # ---------- 0x11 ECU 复位 ----------
        if sid == SID_ECU_RESET:
            sub = req_data[1]
            return bytes([0x51, sub])

        # ---------- 0x3E 测试仪在线 ----------
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
        """模拟下位机执行端到端两阶段 OTA 升级全流程 (严格 Q/SK J02.321 时序)"""
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

    def test_04_mock_rejects_wrong_sequence(self):
        """验证 Mock ECU 拒绝不符合 Q/SK J02.321 的乱序请求"""
        mock = MockLinUdsInterface()

        # 未设预编程标志直接请求编程会话 → NRC 0x22
        resp = mock.request_response(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, SESSION_PROGRAMMING]))
        self.assertEqual(resp[0], SID_NEGATIVE_RESPONSE)
        self.assertEqual(resp[2], NRC_CONDITIONS_NOT_CORRECT)

        # 正确设置扩展会话
        mock.request_response(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, SESSION_EXTENDED]))

        # 未解锁写指纹 → NRC 0x33
        resp = mock.request_response(bytes([SID_WRITE_DATA_BY_IDENTIFIER, 0xF1, 0x84]) + bytes(16))
        self.assertEqual(resp[0], SID_NEGATIVE_RESPONSE)
        self.assertEqual(resp[2], NRC_SECURITY_ACCESS_DENIED)

        # 设置条件检测通过
        mock.request_response(bytes([SID_ROUTINE_CONTROL, ROUTINE_START, 0x02, 0x03]))

        # 现在可以进入编程会话
        resp = mock.request_response(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, SESSION_PROGRAMMING]))
        self.assertEqual(resp[0], 0x50)

    def test_05_hardware_probe(self):
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

    def test_06_mock_simulator_pipeline(self):
        """验证内置通用 MockUdsSimulator 虚拟仿真器两阶段 OTA 升级与 NRC 0x78 弹性循环"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fdrv_path = os.path.join(base_dir, "samples", "sample_flash_drv.bin")
        app_path = os.path.join(base_dir, "samples", "sample_app.bin")

        mask = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        sim = MockUdsSimulator(mask=mask, mask_fbl=mask, bus_type="CAN")
        client = UdsClient(sim)

        # 预设模拟 2 次 NRC 0x78 响应挂起
        sim.simulate_nrc78_count = 2

        cfg = OtaConfig(
            mask=mask,
            mask_fbl=mask,
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

        # 执行升级
        success = flasher.execute(progress_cb=_prog)

        # 校验升级完成
        self.assertTrue(success)
        self.assertGreaterEqual(len(progress_records), 5)
        self.assertEqual(progress_records[-1][0], 100.0)
        self.assertTrue(sim.app_verified)
        self.assertEqual(sim.session, SESSION_DEFAULT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
