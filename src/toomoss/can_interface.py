import ctypes
import time
from dataclasses import dataclass
from typing import Callable, Optional
from .usb2xxx import Usb2xxxDevice


class CAN_UDS_CONFIG(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("STmin", ctypes.c_uint8),       # 最小连续帧间隔时间 (ms)
        ("BlockSize", ctypes.c_uint8),   # 连续帧块大小
        ("FillByte", ctypes.c_uint8),    # 空余填充字节 (0xFF)
        ("_Res0", ctypes.c_uint8),       # 保留
        ("N_Bs", ctypes.c_uint32),       # N_Bs 超时
    ]


class CAN_UDS_ADDR_RAW(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ReqID", ctypes.c_uint32),      # 诊断请求 CAN ID (如 0x7E0)
        ("ResID", ctypes.c_uint32),      # 诊断响应 CAN ID (如 0x7E8)
        ("Flag", ctypes.c_uint8),        # 0: 标准帧 (11-bit), 1: 扩展帧 (29-bit)
        ("AddrType", ctypes.c_uint8),    # 0: 物理寻址, 1: 功能寻址
        ("CheckType", ctypes.c_uint8),   # 保留/校验类型
        ("STmin", ctypes.c_uint8),       # 连续帧最小间隔时间 (ms)
        ("MaxDataLen", ctypes.c_uint16), # 单次请求最大数据长度
    ]


@dataclass
class CanUdsAddrConfig:
    req_id: int = 0x7E0
    res_id: int = 0x7E8
    flag: int = 0         # 标准帧
    addr_type: int = 0    # 物理寻址
    check_type: int = 0
    st_min: int = 5
    max_data_len: int = 4096


from ..uds.transport_base import TransportInterface


class CanUdsInterface(TransportInterface):
    """
    Toomoss CAN UDS (ISO 15765-2 / ISO 14229) 诊断通信接口类
    收发报文严格按 8 字节格式呈现，空余位填充 0xFF
    """

    def __init__(
        self,
        device: Usb2xxxDevice,
        channel: int = 0,
        addr_cfg: Optional[CanUdsAddrConfig] = None,
        tx_logger: Optional[Callable[..., None]] = None,
        rx_logger: Optional[Callable[..., None]] = None,
    ):
        self.device = device
        self.channel = channel
        self.addr_cfg = addr_cfg or CanUdsAddrConfig()
        self.tx_logger = tx_logger
        self.rx_logger = rx_logger

        dll = self.device.dll
        dll.CAN_UDS_Request.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(CAN_UDS_ADDR_RAW),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
        ]
        dll.CAN_UDS_Request.restype = ctypes.c_int

        dll.CAN_UDS_Response.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(CAN_UDS_ADDR_RAW),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
        ]
        dll.CAN_UDS_Response.restype = ctypes.c_int

        # 配置硬件底层 CAN-UDS 填充字节为 0xFF
        if hasattr(dll, "CAN_UDS_Config"):
            try:
                cfg = CAN_UDS_CONFIG(
                    STmin=self.addr_cfg.st_min,
                    BlockSize=0,
                    FillByte=0xFF,
                    _Res0=0,
                    N_Bs=1000,
                )
                dll.CAN_UDS_Config.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(CAN_UDS_CONFIG)]
                dll.CAN_UDS_Config.restype = ctypes.c_int
                dll.CAN_UDS_Config(self.device.device_handle, self.channel, ctypes.byref(cfg))
            except Exception:
                pass

    def _get_raw_addr(self) -> CAN_UDS_ADDR_RAW:
        raw = CAN_UDS_ADDR_RAW()
        raw.ReqID = self.addr_cfg.req_id
        raw.ResID = self.addr_cfg.res_id
        raw.Flag = self.addr_cfg.flag
        raw.AddrType = self.addr_cfg.addr_type
        raw.CheckType = self.addr_cfg.check_type
        raw.STmin = self.addr_cfg.st_min
        raw.MaxDataLen = self.addr_cfg.max_data_len
        return raw

    def send_request(self, req_data: bytes) -> bool:
        if not self.device.is_opened or self.device.device_handle is None:
            raise RuntimeError("设备未打开")

        # 记录发送报文：组装完整 8 字节 CAN ISO-TP 单帧或多帧，空余位填充 0xFF
        if self.tx_logger:
            if len(req_data) <= 7:
                sf = (bytes([len(req_data)]) + req_data).ljust(8, b'\xFF')
                try:
                    self.tx_logger(sf, pid=self.addr_cfg.req_id)
                except TypeError:
                    self.tx_logger(sf)
            else:
                # 首帧
                ff = (bytes([0x10 | ((len(req_data) >> 8) & 0x0F), len(req_data) & 0xFF]) + req_data[:6]).ljust(8, b'\xFF')
                try:
                    self.tx_logger(ff, pid=self.addr_cfg.req_id)
                except TypeError:
                    self.tx_logger(ff)
                # 连续帧
                offset = 6
                sn = 1
                while offset < len(req_data):
                    chunk = req_data[offset:offset+7]
                    cf = (bytes([0x20 | (sn & 0x0F)]) + chunk).ljust(8, b'\xFF')
                    try:
                        self.tx_logger(cf, pid=self.addr_cfg.req_id)
                    except TypeError:
                        self.tx_logger(cf)
                    offset += 7
                    sn = (sn + 1) & 0x0F

        raw_addr = self._get_raw_addr()
        data_len = len(req_data)
        buf = (ctypes.c_uint8 * data_len).from_buffer_copy(req_data)

        ret = self.device.dll.CAN_UDS_Request(
            self.device.device_handle,
            self.channel,
            ctypes.byref(raw_addr),
            buf,
            data_len,
        )
        if ret != 0:
            raise RuntimeError(f"CAN_UDS_Request 失败，返回码: {ret}")
        return True

    def receive_response(self, timeout_ms: int = 500) -> bytes:
        if not self.device.is_opened or self.device.device_handle is None:
            raise RuntimeError("设备未打开")

        raw_addr = self._get_raw_addr()
        res_buf = (ctypes.c_uint8 * 4096)()

        ret = self.device.dll.CAN_UDS_Response(
            self.device.device_handle,
            self.channel,
            ctypes.byref(raw_addr),
            res_buf,
            timeout_ms,
        )
        if ret < 0:
            raise TimeoutError(f"CAN_UDS_Response 超时或失败，返回码: {ret}")

        resp_bytes = bytes(res_buf[:ret])
        if self.rx_logger and resp_bytes:
            if len(resp_bytes) <= 7:
                sf = (bytes([len(resp_bytes)]) + resp_bytes).ljust(8, b'\xFF')
                try:
                    self.rx_logger(sf, pid=self.addr_cfg.res_id)
                except TypeError:
                    self.rx_logger(sf)
            else:
                pad = resp_bytes.ljust(8, b'\xFF')
                try:
                    self.rx_logger(pad, pid=self.addr_cfg.res_id)
                except TypeError:
                    self.rx_logger(pad)
        return resp_bytes

    def request_response(
        self,
        req_data: bytes,
        timeout_ms: int = 500,
        retry_count: int = 3,
    ) -> bytes:
        last_err = None
        for attempt in range(retry_count):
            try:
                self.send_request(req_data)
                resp = self.receive_response(timeout_ms)
                if len(resp) > 0:
                    return resp
            except Exception as e:
                last_err = e
                time.sleep(0.02 * (attempt + 1))

        if last_err:
            raise last_err
        raise TimeoutError("CAN UDS 通信在指定重试次数内无响应")
