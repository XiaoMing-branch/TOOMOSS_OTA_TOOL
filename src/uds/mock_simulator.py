"""
零硬件虚拟 ECU 仿真器 (Mock ECU Simulator)
完整模拟下位机 UDS 协议栈、两阶段安全刷写状态机与 AES-128-CMAC 认证。
支持学生和初学者在没有任何物理硬件和适配器的情况下，完整演练 OTA 升级与报文抓包。
"""

import time
import os
from typing import Callable, Optional
from .transport_base import TransportInterface
from .defines import *
from .security import calculate_aes_cmac


class MockUdsSimulator(TransportInterface):
    """
    虚拟 ECU 诊断模拟器
    实现 ISO 14229-1 核心服务与赛力斯 Q/SK J02.321 两阶段刷写流程
    """

    def __init__(
        self,
        mask: bytes = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c"),
        mask_fbl: bytes = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c"),
        tx_logger: Optional[Callable[..., None]] = None,
        rx_logger: Optional[Callable[..., None]] = None,
        bus_type: str = "CAN",  # "CAN" 或 "LIN"
    ):
        self.mask = mask
        self.mask_fbl = mask_fbl or mask
        self.tx_logger = tx_logger
        self.rx_logger = rx_logger
        self.bus_type = bus_type

        # 仿真状态
        self.session = SESSION_DEFAULT
        self.preprogram_flag = False
        self.security_level1_unlocked = False
        self.security_fbl_unlocked = False
        self.last_seed = b""
        self.last_seed_level = 0
        self.failed_attempts = 0
        self.delay_timer_end = 0.0

        # 下载与内存镜像
        self.download_active = False
        self.download_target = 0
        self.download_total_len = 0
        self.download_received_len = 0
        self.expected_bsc = 1
        self.buffer = bytearray()
        self.flash_drv_loaded = False
        self.flash_drv_verified = False
        self.app_erased = False
        self.app_verified = False
        self.fingerprint = bytes(16)

        # 挂起响应 (0x78) 模拟计数
        self.simulate_nrc78_count = 0
        self._pending_response = b""

    def _log_frame(self, data: bytes, is_rx: bool = False):
        """模拟将数据以 CAN (8B) 或 LIN (8B) 格式输出到监视器 (单帧或首帧)"""
        logger = self.rx_logger if is_rx else self.tx_logger
        if not logger:
            return

        if self.bus_type == "CAN":
            arb_id = 0x7E8 if is_rx else 0x7E0
            if len(data) <= 7:
                # CAN ISO-TP 单帧 (SF: 0x00 | LEN)
                frame = (bytes([len(data)]) + data).ljust(8, b'\xFF')
            else:
                # CAN ISO-TP 首帧 (FF: 0x10 | DL_MSB, DL_LSB)
                ff_pci = bytes([0x10 | ((len(data) >> 8) & 0x0F), len(data) & 0xFF])
                frame = (ff_pci + data[:6]).ljust(8, b'\xFF')
            try:
                logger(frame, pid=arb_id)
            except TypeError:
                logger(frame)
        else:
            pid = 0x3D if is_rx else 0x3C
            nad = 0x01
            if len(data) <= 5:
                # LIN-TP 单帧 (NAD, LEN)
                frame = (bytes([nad, len(data)]) + data).ljust(8, b'\xFF')
            else:
                # LIN-TP 首帧 (NAD, 0x10 | DL_MSB, DL_LSB)
                ff_pci = bytes([nad, 0x10 | ((len(data) >> 8) & 0x0F), len(data) & 0xFF])
                frame = (ff_pci + data[:5]).ljust(8, b'\xFF')
            try:
                logger(frame, pid=pid)
            except TypeError:
                logger(frame)

    def send_request(self, req_data: bytes) -> bool:
        self._log_frame(req_data, is_rx=False)
        self._pending_response = self._handle_uds_request(req_data)
        return True

    def receive_response(self, timeout_ms: int = 1000) -> bytes:
        if self.simulate_nrc78_count > 0:
            self.simulate_nrc78_count -= 1
            # 模拟 NRC 0x78
            nrc78_resp = bytes([SID_NEGATIVE_RESPONSE, self._pending_request_sid, NRC_RESPONSE_PENDING])
            self._log_frame(nrc78_resp, is_rx=True)
            return nrc78_resp

        resp = self._pending_response
        self._pending_response = b""
        if resp:
            self._log_frame(resp, is_rx=True)
        return resp

    def request_response(self, req_data: bytes, timeout_ms: int = 1000, retry_count: int = 0) -> bytes:
        self.send_request(req_data)
        return self.receive_response(timeout_ms)

    def _handle_uds_request(self, req: bytes) -> bytes:
        if not req:
            return b""

        sid = req[0]
        self._pending_request_sid = sid

        # 0x10 会话控制
        if sid == SID_DIAGNOSTIC_SESSION_CONTROL:
            if len(req) < 2:
                return bytes([0x7F, sid, NRC_INCORRECT_MESSAGE_LENGTH])
            sub = req[1] & 0x7F
            suppress = bool(req[1] & 0x80)

            if sub == SESSION_DEFAULT:
                self.session = SESSION_DEFAULT
                self.security_level1_unlocked = False
                self.security_fbl_unlocked = False
            elif sub == SESSION_EXTENDED:
                self.session = SESSION_EXTENDED
            elif sub == SESSION_PROGRAMMING:
                if not self.preprogram_flag:
                    return bytes([0x7F, sid, NRC_CONDITIONS_NOT_CORRECT])
                self.session = SESSION_PROGRAMMING
            else:
                return bytes([0x7F, sid, NRC_SUBFUNCTION_NOT_SUPPORTED])

            if suppress:
                return b""
            # P2 = 50ms (0x0032), P2* = 5000ms (0x01F4)
            return bytes([0x50, sub, 0x00, 0x32, 0x01, 0xF4])

        # 0x3E 测试仪在线
        elif sid == SID_TESTER_PRESENT:
            if len(req) >= 2 and (req[1] & 0x80):
                return b""
            return bytes([0x7E, 0x00])

        # 0x27 安全访问
        elif sid == SID_SECURITY_ACCESS:
            if len(req) < 2:
                return bytes([0x7F, sid, NRC_INCORRECT_MESSAGE_LENGTH])
            sub = req[1]

            # 延时检测
            if time.time() < self.delay_timer_end:
                return bytes([0x7F, sid, NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED])

            # 请求种子 (Seed)
            if sub in (0x01, 0x09):
                if (sub == 0x01 and self.security_level1_unlocked) or (sub == 0x09 and self.security_fbl_unlocked):
                    return bytes([0x67, sub]) + bytes(16)  # 已解锁回全 0 种子
                seed = os.urandom(16)
                self.last_seed = seed
                self.last_seed_level = sub
                return bytes([0x67, sub]) + seed

            # 发送密钥 (Key)
            elif sub in (0x02, 0x0A):
                if not self.last_seed or self.last_seed_level != (sub - 1):
                    return bytes([0x7F, sid, NRC_REQUEST_SEQUENCE_ERROR])
                key_received = req[2:]
                active_mask = self.mask if sub == 0x02 else self.mask_fbl
                expected_key = calculate_aes_cmac(active_mask, self.last_seed)

                if key_received == expected_key:
                    if sub == 0x02:
                        self.security_level1_unlocked = True
                    else:
                        self.security_fbl_unlocked = True
                    self.last_seed = b""
                    self.failed_attempts = 0
                    return bytes([0x67, sub])
                else:
                    self.failed_attempts += 1
                    if self.failed_attempts >= 3:
                        self.delay_timer_end = time.time() + 10.0  # 锁定 10 秒
                        return bytes([0x7F, sid, NRC_EXCEEDED_NUMBER_OF_ATTEMPTS])
                    return bytes([0x7F, sid, NRC_INVALID_KEY])

        # 0x22 读 DID
        elif sid == SID_READ_DATA_BY_IDENTIFIER:
            if len(req) < 3:
                return bytes([0x7F, sid, NRC_INCORRECT_MESSAGE_LENGTH])
            did = (req[1] << 8) | req[2]
            if did == 0xF184:
                return bytes([0x62, req[1], req[2]]) + self.fingerprint
            if did == 0xF186:
                return bytes([0x62, req[1], req[2], self.session])
            if did == 0xF180:
                return bytes([0x62, req[1], req[2]]) + b"BOOT_V1.0.0"
            if did == 0xF187:
                return bytes([0x62, req[1], req[2]]) + b"ALIENTEK-STM32"
            if did == 0xF188:
                return bytes([0x62, req[1], req[2]]) + b"SW_NUM_001"
            if did == 0xF189:
                return bytes([0x62, req[1], req[2]]) + b"SW_VER_01.00"
            if did == 0xF18A:
                return bytes([0x62, req[1], req[2]]) + b"SUPPLIER_ALI"
            if did == 0xF18C:
                return bytes([0x62, req[1], req[2]]) + b"SN2026090001"
            if did == 0xF190:
                return bytes([0x62, req[1], req[2]]) + b"LSGPC52U0N0123456"
            if did == 0xF194:
                return bytes([0x62, req[1], req[2]]) + b"SYS_SW_001"
            if did == 0xF195:
                return bytes([0x62, req[1], req[2]]) + b"SYS_VER_1.0"
            if did == 0xF197:
                return bytes([0x62, req[1], req[2]]) + b"STM32F103_ECU"
            if did == 0x0216:
                return bytes([0x62, req[1], req[2]]) + b"APP_V1.0.0"
            if did == 0xF0F0:
                return bytes([0x62, req[1], req[2]]) + b"A"
            return bytes([0x62, req[1], req[2], 0x01, 0x00, 0x01])

        # 0x2E 写 DID
        elif sid == SID_WRITE_DATA_BY_IDENTIFIER:
            if len(req) < 3:
                return bytes([0x7F, sid, NRC_INCORRECT_MESSAGE_LENGTH])
            did = (req[1] << 8) | req[2]
            if did == 0xF184:
                self.fingerprint = req[3:19].ljust(16, b'\x00')
                return bytes([0x6E, req[1], req[2]])
            return bytes([0x6E, req[1], req[2]])

        # 0x85 DTC 设置
        elif sid == SID_CONTROL_DTC_SETTING:
            if len(req) >= 2 and (req[1] & 0x80):
                return b""
            return bytes([0xC5, req[1] & 0x7F])

        # 0x31 例程控制
        elif sid == SID_ROUTINE_CONTROL:
            if len(req) < 4:
                return bytes([0x7F, sid, NRC_INCORRECT_MESSAGE_LENGTH])
            ctrl_type = req[1]
            rid = (req[2] << 8) | req[3]

            if rid == RID_CHECK_PROGRAM_CONDITIONS:  # 0x0203
                self.preprogram_flag = True
                return bytes([0x71, ctrl_type, req[2], req[3], 0x00])

            elif rid == RID_ERASE_APP_FLASH:  # 0xFF00
                if self.session != SESSION_PROGRAMMING or not self.security_fbl_unlocked:
                    return bytes([0x7F, sid, NRC_SECURITY_ACCESS_DENIED])
                self.app_erased = True
                return bytes([0x71, ctrl_type, req[2], req[3], 0x00])

            elif rid == RID_SECURITY_SIGN_CHECK:  # 0xDD02
                # 校验固件 CMAC 签名
                if len(req) >= 20:
                    sig_received = req[4:20]
                    # 计算当前缓冲的 CMAC
                    active_mask = self.mask_fbl if self.session == SESSION_PROGRAMMING else self.mask
                    expected_sig = calculate_aes_cmac(active_mask, bytes(self.buffer))
                    if sig_received == expected_sig:
                        if self.download_target == 0x20008000:
                            self.flash_drv_verified = True
                        else:
                            self.app_verified = True
                        return bytes([0x71, ctrl_type, req[2], req[3], 0x00])  # 00: 成功
                    else:
                        return bytes([0x71, ctrl_type, req[2], req[3], 0x01])  # 01: 验签失败
                return bytes([0x71, ctrl_type, req[2], req[3], 0x00])

            elif rid == RID_CHECK_COMPATIBILITY:  # 0xFF01
                if not self.app_verified:
                    return bytes([0x7F, sid, NRC_CONDITIONS_NOT_CORRECT])
                return bytes([0x71, ctrl_type, req[2], req[3], 0x00])

            return bytes([0x71, ctrl_type, req[2], req[3], 0x00])

        # 0x34 请求下载
        elif sid == SID_REQUEST_DOWNLOAD:
            if self.session != SESSION_PROGRAMMING or not self.security_fbl_unlocked:
                return bytes([0x7F, sid, NRC_SECURITY_ACCESS_DENIED])
            addr = int.from_bytes(req[3:7], 'big')
            length = int.from_bytes(req[7:11], 'big')
            self.download_target = addr
            self.download_total_len = length
            self.download_received_len = 0
            self.expected_bsc = 1
            self.buffer = bytearray()
            self.download_active = True
            # MaxNumberOfBlockLength = 0x0FFF (4095)
            return bytes([0x74, 0x20, 0x0F, 0xFF])

        # 0x36 传输数据
        elif sid == SID_TRANSFER_DATA:
            if not self.download_active:
                return bytes([0x7F, sid, NRC_REQUEST_SEQUENCE_ERROR])
            bsc = req[1]
            if bsc != self.expected_bsc:
                if bsc == ((self.expected_bsc - 1) & 0xFF):
                    return bytes([0x76, bsc])  # 重复包应答
                return bytes([0x7F, sid, NRC_WRONG_BLOCK_SEQUENCE_COUNTER])

            data = req[2:]
            self.buffer.extend(data)
            self.download_received_len += len(data)
            self.expected_bsc = (self.expected_bsc + 1) & 0xFF
            return bytes([0x76, bsc])

        # 0x37 传输退出
        elif sid == SID_REQUEST_TRANSFER_EXIT:
            self.download_active = False
            return bytes([0x77, 0x00])

        # 0x11 ECU 复位
        elif sid == SID_ECU_RESET:
            self.session = SESSION_DEFAULT
            self.preprogram_flag = False
            self.security_level1_unlocked = False
            self.security_fbl_unlocked = False
            return bytes([0x51, req[1]])

        return bytes([0x7F, sid, NRC_SERVICE_NOT_SUPPORTED])
