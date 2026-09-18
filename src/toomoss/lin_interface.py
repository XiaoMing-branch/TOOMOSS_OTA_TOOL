import ctypes
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple
from .usb2xxx import Usb2xxxDevice


class LIN_UDS_ADDR(ctypes.Structure):
    """
    Toomoss 官方 LIN_UDS_ADDR 结构体定义 (5 字节)
    """
    _pack_ = 1
    _fields_ = [
        ("ReqID", ctypes.c_uint8),       # 诊断请求帧 ID，通常 0x3C
        ("ResID", ctypes.c_uint8),       # 诊断响应帧 ID，通常 0x3D
        ("NAD", ctypes.c_uint8),         # 从机节点地址 (NAD)，默认 0x01
        ("CheckType", ctypes.c_uint8),   # 0: 经典校验 (Classic, 诊断帧标准), 1: 增强校验
        ("STmin", ctypes.c_uint8),       # 最小帧间隔 (ms)，默认 10
    ]


class LIN_EX_MSG(ctypes.Structure):
    """
    Toomoss 官方 LIN_EX_MSG 物理帧结构体定义
    """
    _pack_ = 1
    _fields_ = [
        ("Timestamp", ctypes.c_uint32),  # 时间戳 (100us)
        ("MsgType", ctypes.c_uint8),     # 帧类型
        ("CheckType", ctypes.c_uint8),   # 校验类型 (0: 经典, 1: 增强)
        ("DataLen", ctypes.c_uint8),     # 数据长度 (1~8)
        ("Sync", ctypes.c_uint8),        # 同步字节 (固定 0x55)
        ("PID", ctypes.c_uint8),         # 带校验位的 PID (如 0x3C 或 0x3D)
        ("Data", ctypes.c_uint8 * 8),    # 8 字节数据场
        ("Check", ctypes.c_uint8),       # 校验字节
        ("BreakBits", ctypes.c_uint8),   # 间隔段位数
        ("Reserve1", ctypes.c_uint8),    # 保留
    ]


@dataclass
class LinUdsAddrConfig:
    req_id: int = 0x3C
    res_id: int = 0x3D
    nad: int = 0x01
    check_type: int = 0                  # 0: 经典校验 (0x3C/0x3D 诊断帧必须为 0)
    st_min: int = 10                     # 连续帧最小间隔 (ms)
    max_data_len: int = 4096


from ..uds.transport_base import TransportInterface


class LinUdsInterface(TransportInterface):
    """
    Toomoss LIN UDS 通信接口类
    实现 ISO 14229-7 (UDS on LIN) / ISO 17987-2 的报文请求与响应收发
    收发报文严格按 8 字节格式呈现，空余位填充 0xFF
    """

    def __init__(
        self,
        device: Usb2xxxDevice,
        channel: int = 0,
        addr_cfg: Optional[LinUdsAddrConfig] = None,
        tx_logger: Optional[Callable[..., None]] = None,
        rx_logger: Optional[Callable[..., None]] = None,
    ):
        self.device = device
        self.channel = channel
        self.addr_cfg = addr_cfg or LinUdsAddrConfig()
        self.tx_logger = tx_logger
        self.rx_logger = rx_logger

        dll = self.device.dll
        # LIN_UDS_Request(int DevHandle, int LINIndex, LIN_UDS_ADDR *pUDSAddr, unsigned char *pReqData, int DataLen)
        dll.LIN_UDS_Request.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(LIN_UDS_ADDR),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
        ]
        dll.LIN_UDS_Request.restype = ctypes.c_int

        # LIN_UDS_Response(int DevHandle, int LINIndex, LIN_UDS_ADDR *pUDSAddr, unsigned char *pResData, int TimeOutMs)
        dll.LIN_UDS_Response.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(LIN_UDS_ADDR),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
        ]
        dll.LIN_UDS_Response.restype = ctypes.c_int

        # LIN_EX_MasterRead(int DevHandle, int LINIndex, unsigned char PID, unsigned char *pData)
        dll.LIN_EX_MasterRead.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint8,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        dll.LIN_EX_MasterRead.restype = ctypes.c_int

        # LIN_UDS_GetMsgFromUDSBuffer(int DevHandle, int LINIndex, LIN_EX_MSG *pLINMsg, int BufferSize)
        dll.LIN_UDS_GetMsgFromUDSBuffer.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        dll.LIN_UDS_GetMsgFromUDSBuffer.restype = ctypes.c_int

    def _get_raw_addr(self) -> LIN_UDS_ADDR:
        raw = LIN_UDS_ADDR()
        raw.ReqID = self.addr_cfg.req_id
        raw.ResID = self.addr_cfg.res_id
        raw.NAD = self.addr_cfg.nad
        raw.CheckType = self.addr_cfg.check_type
        raw.STmin = self.addr_cfg.st_min
        return raw

    def send_request(self, req_data: bytes) -> bool:
        """
        发送 UDS 诊断请求，并将发送出的完整 8 字节（空余位用 0xFF 补足）LIN 物理报文送入 tx_logger
        """
        if not self.device.is_opened or self.device.device_handle is None:
            raise RuntimeError("设备未打开")

        raw_addr = self._get_raw_addr()
        data_len = len(req_data)
        buf = (ctypes.c_uint8 * data_len).from_buffer_copy(req_data)

        ret = self.device.dll.LIN_UDS_Request(
            self.device.device_handle,
            self.channel,
            ctypes.byref(raw_addr),
            buf,
            data_len,
        )

        if ret != 0:
            raise RuntimeError(f"LIN_UDS_Request 失败，返回码: {ret}")

        # 从硬件底层抓取实际发出的完整 8 字节 LIN 报文
        if self.tx_logger:
            msgs = (LIN_EX_MSG * 64)()
            msg_cnt = self.device.dll.LIN_UDS_GetMsgFromUDSBuffer(
                self.device.device_handle, self.channel, msgs, 64
            )
            if msg_cnt > 0:
                for i in range(msg_cnt):
                    m = msgs[i]
                    frame_data = bytes(m.Data[:m.DataLen])
                    if len(frame_data) < 8:
                        frame_data = frame_data.ljust(8, b'\xFF')
                    try:
                        self.tx_logger(frame_data, pid=m.PID)
                    except TypeError:
                        self.tx_logger(frame_data)
            else:
                # 备用组装：标准 8 字节单帧 (NAD + (0x00|LEN) + Data + 0xFF)
                if len(req_data) <= 5:
                    sf = bytes([self.addr_cfg.nad, len(req_data)]) + req_data
                    sf = sf.ljust(8, b'\xFF')
                    try:
                        self.tx_logger(sf, pid=self.addr_cfg.req_id)
                    except TypeError:
                        self.tx_logger(sf)
                else:
                    pad = req_data.ljust(8, b'\xFF')
                    try:
                        self.tx_logger(pad, pid=self.addr_cfg.req_id)
                    except TypeError:
                        self.tx_logger(pad)

        return True

    def receive_response(self, timeout_ms: int = 500) -> bytes:
        """
        接收 UDS 诊断响应（阻塞直至收到或超时）
        主机主动发送 0x3D 读头轮询从机，并将收到的完整 8 字节响应报文送入 rx_logger
        """
        if not self.device.is_opened or self.device.device_handle is None:
            raise RuntimeError("设备未打开")

        # 记录主机正在发送 0x3D 读头轮询从机
        if self.tx_logger:
            try:
                self.tx_logger(b"", pid=self.addr_cfg.res_id, note="Header (发送 0x3D 读头，轮询从机响应)")
            except TypeError:
                try:
                    self.tx_logger(b"", pid=self.addr_cfg.res_id)
                except Exception:
                    pass

        raw_addr = self._get_raw_addr()
        res_buf = (ctypes.c_uint8 * 4096)()

        ret = self.device.dll.LIN_UDS_Response(
            self.device.device_handle,
            self.channel,
            ctypes.byref(raw_addr),
            res_buf,
            timeout_ms,
        )

        # 抓取硬件底层实际交互的 LIN 物理报文
        msgs = (LIN_EX_MSG * 64)()
        msg_cnt = self.device.dll.LIN_UDS_GetMsgFromUDSBuffer(
            self.device.device_handle, self.channel, msgs, 64
        )

        if ret > 0:
            resp_bytes = bytes(res_buf[:ret])
            if self.rx_logger:
                logged = False
                if msg_cnt > 0:
                    for i in range(msg_cnt):
                        m = msgs[i]
                        # 0x7D / 0x3D 响应帧且带有从机数据
                        if (m.PID in (self.addr_cfg.res_id, 0x7D, 0x3D)) and m.DataLen > 0:
                            frame_data = bytes(m.Data[:m.DataLen])
                            if len(frame_data) < 8:
                                frame_data = frame_data.ljust(8, b'\xFF')
                            try:
                                self.rx_logger(frame_data, pid=self.addr_cfg.res_id)
                                logged = True
                            except TypeError:
                                self.rx_logger(frame_data)
                                logged = True
                if not logged and resp_bytes:
                    if len(resp_bytes) <= 5:
                        sf = (bytes([self.addr_cfg.nad, len(resp_bytes)]) + resp_bytes).ljust(8, b'\xFF')
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

        # 若 LIN_UDS_Response 固件接口未取得数据，使用 LIN_EX_MasterRead 显式发送 0x3D 读头尝试读取
        mr_buf = (ctypes.c_uint8 * 8)()
        mr_ret = self.device.dll.LIN_EX_MasterRead(
            self.device.device_handle,
            self.channel,
            self.addr_cfg.res_id,
            mr_buf,
        )
        if mr_ret > 0:
            raw_frame = bytes(mr_buf[:mr_ret]).ljust(8, b'\xFF')
            if self.rx_logger:
                try:
                    self.rx_logger(raw_frame, pid=self.addr_cfg.res_id)
                except TypeError:
                    self.rx_logger(raw_frame)
            if raw_frame[0] == self.addr_cfg.nad:
                pci = raw_frame[1]
                if (pci & 0xF0) == 0x00:
                    n = pci & 0x0F
                    if n > 0 and n <= 5:
                        return raw_frame[2:2+n]
                    elif n == 0 and raw_frame[2] <= 5:
                        return raw_frame[3:3+raw_frame[2]]

        raise TimeoutError(f"LIN 从机无响应（主机已发送 0x3D 读头轮询，但总线超时未收到从机应答，返回码: {ret}）")

    def request_response(
        self,
        req_data: bytes,
        timeout_ms: int = 500,
        retry_count: int = 3,
    ) -> bytes:
        """
        执行一次请求-响应交互，支持超时自动重发
        """
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
        raise TimeoutError("LIN UDS 通信在指定重试次数内无响应")
