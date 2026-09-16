import time
import threading
from typing import Optional, Tuple
from ..toomoss.lin_interface import LinUdsInterface
from .defines import *
from .security import calculate_aes_cmac


class UdsNegativeResponseError(Exception):
    """UDS 负响应异常 (0x7F)"""

    def __init__(self, requested_sid: int, nrc: int):
        self.requested_sid = requested_sid
        self.nrc = nrc
        self.nrc_desc = get_nrc_description(nrc)
        super().__init__(
            f"UDS 请求 SID=0x{requested_sid:02X} 收到负响应: NRC=0x{nrc:02X} ({self.nrc_desc})"
        )


class UdsClient:
    """
    车规级 ISO 14229-1 (UDS) 诊断客户端
    负责诊断服务的请求打包、响应解析、NRC 校验与 TesterPresent 会话保活
    """

    def __init__(self, transport: LinUdsInterface):
        self.transport = transport
        self.active_session = SESSION_DEFAULT
        self._keepalive_active = False
        self._keepalive_thread: Optional[threading.Thread] = None

    def raw_request(self, req_data: bytes, timeout_ms: int = 500, retry: int = 2) -> bytes:
        """
        发送原始 UDS 报文并校验是否为负响应
        """
        resp = self.transport.request_response(req_data, timeout_ms=timeout_ms, retry_count=retry)
        if len(resp) < 1:
            raise TimeoutError("未收到 UDS 服务端响应")

        # 检查是否为负响应 0x7F
        if resp[0] == SID_NEGATIVE_RESPONSE:
            if len(resp) >= 3:
                req_sid = resp[1]
                nrc = resp[2]
                # 若为 0x78 (Response Pending)，等待并继续收取最终响应
                if nrc == NRC_RESPONSE_PENDING:
                    time.sleep(0.05)
                    return self.transport.receive_response(timeout_ms=3000)
                raise UdsNegativeResponseError(req_sid, nrc)
            else:
                raise ValueError(f"畸形负响应报文: {resp.hex()}")

        return resp

    # ----------------- 0x10 会话控制 -----------------
    def change_session(self, session: int, timeout_ms: int = 500) -> bytes:
        """
        0x10 诊断会话控制
        :param session: SESSION_DEFAULT (0x01) / SESSION_PROGRAMMING (0x02) / SESSION_EXTENDED (0x03)
        """
        req = bytes([SID_DIAGNOSTIC_SESSION_CONTROL, session])
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 2 or resp[0] != (SID_DIAGNOSTIC_SESSION_CONTROL + 0x40):
            raise ValueError(f"会话控制响应无效: {resp.hex()}")
        self.active_session = session
        return resp

    # ----------------- 0x85 控制 DTC 记录 -----------------
    def control_dtc_setting(self, setting_type: int = DTC_SETTING_OFF, timeout_ms: int = 500) -> bytes:
        """
        0x85 控制 DTC 存储开关
        :param setting_type: DTC_SETTING_ON (0x01) / DTC_SETTING_OFF (0x02)
        """
        req = bytes([SID_CONTROL_DTC_SETTING, setting_type])
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 2 or resp[0] != (SID_CONTROL_DTC_SETTING + 0x40):
            raise ValueError(f"控制 DTC 记录响应无效: {resp.hex()}")
        return resp

    # ----------------- 0x27 安全访问 -----------------
    def security_access(
        self,
        mask: bytes,
        level_seed: int = 0x01,
        level_key: int = 0x02,
        timeout_ms: int = 1000,
    ) -> bool:
        """
        0x27 安全访问全流程：请求 Seed -> 计算 AES-128-CMAC Key -> 发送 Key 解锁
        """
        # 1. 请求种子 (Request Seed)
        req_seed = bytes([SID_SECURITY_ACCESS, level_seed])
        resp_seed = self.raw_request(req_seed, timeout_ms=timeout_ms)

        if len(resp_seed) < 3 or resp_seed[0] != (SID_SECURITY_ACCESS + 0x40):
            raise ValueError(f"请求安全种子响应格式异常: {resp_seed.hex()}")

        seed = resp_seed[2:]
        # 如果返回种子全为 0，说明该安全级别已经处于解锁状态
        if all(b == 0 for b in seed):
            return True

        if len(seed) != 16:
            raise ValueError(f"期望 16 字节种子，实际收到 {len(seed)} 字节: {seed.hex()}")

        # 2. 计算 Key (AES-128-CMAC)
        key = calculate_aes_cmac(mask, seed)

        # 3. 发送密钥 (Send Key)
        req_key = bytes([SID_SECURITY_ACCESS, level_key]) + key
        resp_key = self.raw_request(req_key, timeout_ms=timeout_ms)

        if len(resp_key) < 2 or resp_key[0] != (SID_SECURITY_ACCESS + 0x40) or resp_key[1] != level_key:
            raise ValueError(f"安全密钥验证响应无效: {resp_key.hex()}")

        return True

    # ----------------- 0x22 读取 DID -----------------
    def read_data_by_id(self, did: int, timeout_ms: int = 500) -> bytes:
        """
        0x22 通过标识符读取数据
        """
        req = bytes([SID_READ_DATA_BY_IDENTIFIER, (did >> 8) & 0xFF, did & 0xFF])
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 3 or resp[0] != (SID_READ_DATA_BY_IDENTIFIER + 0x40):
            raise ValueError(f"读 DID 响应无效: {resp.hex()}")
        return resp[3:]

    # ----------------- 0x31 例程控制 -----------------
    def routine_control(
        self,
        subfunc: int,
        routine_id: int,
        option_record: bytes = b"",
        timeout_ms: int = 2000,
    ) -> bytes:
        """
        0x31 例程控制
        :param subfunc: ROUTINE_START (0x01) / ROUTINE_STOP (0x02) / ROUTINE_REQUEST_RESULTS (0x03)
        :param routine_id: 16位例程 ID (如 0xFF00, 0xDD02)
        :param option_record: 选项记录字节
        """
        req = (
            bytes([SID_ROUTINE_CONTROL, subfunc, (routine_id >> 8) & 0xFF, routine_id & 0xFF])
            + option_record
        )
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 4 or resp[0] != (SID_ROUTINE_CONTROL + 0x40):
            raise ValueError(f"例程控制响应无效: {resp.hex()}")
        return resp

    # ----------------- 0x34 请求下载 -----------------
    def request_download(
        self,
        memory_address: int,
        memory_size: int,
        data_format: int = 0x00,
        address_and_length_format: int = 0x44,
        timeout_ms: int = 1000,
    ) -> int:
        """
        0x34 请求下载
        :return: 服务端允许的最大数据块长度 maxNumberOfBlockLength
        """
        addr_bytes = memory_address.to_bytes(4, byteorder="big")
        size_bytes = memory_size.to_bytes(4, byteorder="big")

        req = (
            bytes([SID_REQUEST_DOWNLOAD, data_format, address_and_length_format])
            + addr_bytes
            + size_bytes
        )
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 3 or resp[0] != (SID_REQUEST_DOWNLOAD + 0x40):
            raise ValueError(f"请求下载响应格式无效: {resp.hex()}")

        # 解析 maxNumberOfBlockLength
        len_fmt = resp[1] >> 4
        if len_fmt == 0:
            len_fmt = len(resp) - 2

        max_block_len = int.from_bytes(resp[2 : 2 + len_fmt], byteorder="big")
        return max_block_len

    # ----------------- 0x36 传输数据 -----------------
    def transfer_data(
        self,
        block_sequence_counter: int,
        data_chunk: bytes,
        timeout_ms: int = 2000,
    ) -> bytes:
        """
        0x36 传输数据块
        """
        req = bytes([SID_TRANSFER_DATA, block_sequence_counter & 0xFF]) + data_chunk
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 2 or resp[0] != (SID_TRANSFER_DATA + 0x40):
            raise ValueError(f"数据传输响应无效: {resp.hex()}")
        if resp[1] != (block_sequence_counter & 0xFF):
            raise ValueError(f"块序列号不匹配: 期望 {block_sequence_counter & 0xFF}, 收到 {resp[1]}")
        return resp

    # ----------------- 0x37 请求退出传输 -----------------
    def request_transfer_exit(self, timeout_ms: int = 1000) -> bytes:
        """
        0x37 请求传输退出
        """
        req = bytes([SID_REQUEST_TRANSFER_EXIT])
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 1 or resp[0] != (SID_REQUEST_TRANSFER_EXIT + 0x40):
            raise ValueError(f"请求退出传输响应无效: {resp.hex()}")
        return resp

    # ----------------- 0x11 ECU 复位 -----------------
    def ecu_reset(self, reset_type: int = RESET_HARD, timeout_ms: int = 500) -> bytes:
        """
        0x11 ECU 复位
        """
        req = bytes([SID_ECU_RESET, reset_type])
        resp = self.raw_request(req, timeout_ms=timeout_ms)
        if len(resp) < 2 or resp[0] != (SID_ECU_RESET + 0x40):
            raise ValueError(f"ECU 复位响应无效: {resp.hex()}")
        return resp

    # ----------------- 0x3E 测试仪在线保活 -----------------
    def tester_present(self, suppress_pos_resp: bool = True, timeout_ms: int = 200) -> Optional[bytes]:
        """
        0x3E 测试仪在线（TesterPresent）
        :param suppress_pos_resp: True 则子功能为 0x80（抑制肯定响应），无正响应返回；False 则为 0x00
        """
        subfunc = 0x80 if suppress_pos_resp else 0x00
        req = bytes([SID_TESTER_PRESENT, subfunc])
        if suppress_pos_resp:
            self.transport.send_request(req)
            return None
        else:
            return self.raw_request(req, timeout_ms=timeout_ms)

    def start_keepalive(self, interval_sec: float = 2.0):
        """
        启动后台会话保活线程（维持非默认会话与 S3 定时器）
        """
        if self._keepalive_active:
            return
        self._keepalive_active = True

        def _worker():
            while self._keepalive_active:
                time.sleep(interval_sec)
                if not self._keepalive_active:
                    break
                try:
                    self.tester_present(suppress_pos_resp=True)
                except Exception:
                    pass

        self._keepalive_thread = threading.Thread(target=_worker, daemon=True)
        self._keepalive_thread.start()

    def stop_keepalive(self):
        """
        停止后台会话保活线程
        """
        self._keepalive_active = False
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=1.0)
            self._keepalive_thread = None
