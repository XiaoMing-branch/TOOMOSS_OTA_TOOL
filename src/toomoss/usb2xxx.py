import os
import sys
import ctypes
from dataclasses import dataclass
from typing import List, Optional, Tuple


class DEVICE_INFO_RAW(ctypes.Structure):
    _fields_ = [
        ("FirmwareName", ctypes.c_char * 32),
        ("BuildDate", ctypes.c_char * 32),
        ("HardwareVersion", ctypes.c_uint32),
        ("FirmwareVersion", ctypes.c_uint32),
        ("SerialNumber", ctypes.c_uint32 * 3),
        ("Functions", ctypes.c_uint32),
    ]


@dataclass
class DeviceInfo:
    firmware_name: str
    build_date: str
    hardware_version: str
    firmware_version: str
    serial_number: str
    functions: int


class Usb2xxxDevice:
    """
    Toomoss USB2XXX 底层动态库加载与设备管理类
    """

    _dll_instance = None

    @classmethod
    def get_dll(cls):
        if cls._dll_instance is not None:
            return cls._dll_instance

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        arch = "x86_64" if sys.maxsize > 2**32 else "x86"
        dll_dir = os.path.join(base_dir, "libs", "windows", arch)

        if not os.path.exists(dll_dir):
            raise FileNotFoundError(f"Toomoss 驱动库目录未找到: {dll_dir}")

        # 在 Windows 上添加 DLL 搜索目录
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(dll_dir)

        dll_path = os.path.join(dll_dir, "USB2XXX.dll")
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"USB2XXX.dll 未找到: {dll_path}")

        cls._dll_instance = ctypes.cdll.LoadLibrary(dll_path)

        # 声明核心函数签名
        dll = cls._dll_instance
        dll.USB_ScanDevice.argtypes = [ctypes.POINTER(ctypes.c_int)]
        dll.USB_ScanDevice.restype = ctypes.c_int

        dll.USB_OpenDevice.argtypes = [ctypes.c_int]
        dll.USB_OpenDevice.restype = ctypes.c_bool

        dll.USB_CloseDevice.argtypes = [ctypes.c_int]
        dll.USB_CloseDevice.restype = ctypes.c_bool

        dll.DEV_GetDeviceInfo.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(DEVICE_INFO_RAW),
            ctypes.c_char_p,
        ]
        dll.DEV_GetDeviceInfo.restype = ctypes.c_bool

        dll.LIN_EX_Init.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        dll.LIN_EX_Init.restype = ctypes.c_int

        # CAN 初始化函数声明
        class CAN_INIT_CONFIG(ctypes.Structure):
            _fields_ = [
                ("CAN_BaudRate", ctypes.c_uint32),
                ("CAN_Mode", ctypes.c_uint8),
                ("Reserved", ctypes.c_uint8 * 3),
            ]
        cls.CAN_INIT_CONFIG = CAN_INIT_CONFIG

        if hasattr(dll, "CAN_Init"):
            dll.CAN_Init.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(CAN_INIT_CONFIG),
            ]
            dll.CAN_Init.restype = ctypes.c_int

        return cls._dll_instance

    def __init__(self, device_index: int = 0):
        self.dll = self.get_dll()
        self.device_index = device_index
        self.device_handle: Optional[int] = None
        self.is_opened = False

    @classmethod
    def scan_devices(cls, max_devices: int = 16) -> List[int]:
        """
        扫描当前系统中已连接的 USB2XXX 设备，返回设备句柄列表
        """
        dll = cls.get_dll()
        handles = (ctypes.c_int * max_devices)()
        cnt = dll.USB_ScanDevice(handles)
        if cnt < 0:
            return []
        return [handles[i] for i in range(cnt)]

    def open(self) -> bool:
        """
        打开指定索引的设备
        """
        handles = self.scan_devices()
        if not handles:
            raise RuntimeError("未检测到任何已连接的 Toomoss USB2XXX 设备！")

        if self.device_index >= len(handles):
            raise IndexError(f"设备索引 {self.device_index} 超出可用设备数量 ({len(handles)})")

        self.device_handle = handles[self.device_index]
        ok = self.dll.USB_OpenDevice(self.device_handle)
        if not ok:
            self.device_handle = None
            self.is_opened = False
            raise RuntimeError(f"无法打开 Toomoss 设备 (Index={self.device_index})")

        self.is_opened = True
        return True

    def close(self):
        """
        关闭当前设备
        """
        if self.is_opened and self.device_handle is not None:
            self.dll.USB_CloseDevice(self.device_handle)
            self.is_opened = False
            self.device_handle = None

    def get_info(self) -> DeviceInfo:
        """
        获取设备硬件及固件详细信息
        """
        if not self.is_opened or self.device_handle is None:
            raise RuntimeError("设备尚未打开！")

        raw_info = DEVICE_INFO_RAW()
        func_str_buf = ctypes.create_string_buffer(512)
        ok = self.dll.DEV_GetDeviceInfo(self.device_handle, ctypes.byref(raw_info), func_str_buf)
        if not ok:
            raise RuntimeError("获取设备信息失败")

        def _fmt_ver(v: int) -> str:
            major = (v >> 24) & 0xFF
            minor = (v >> 16) & 0xFF
            patch = v & 0xFFFF
            return f"v{major}.{minor}.{patch}"

        sn = "-".join([f"{x:08X}" for x in raw_info.SerialNumber])

        return DeviceInfo(
            firmware_name=raw_info.FirmwareName.decode("latin1", "ignore").strip("\x00"),
            build_date=raw_info.BuildDate.decode("latin1", "ignore").strip("\x00"),
            hardware_version=_fmt_ver(raw_info.HardwareVersion),
            firmware_version=_fmt_ver(raw_info.FirmwareVersion),
            serial_number=sn,
            functions=raw_info.Functions,
        )

    def probe_channels(self) -> Tuple[List[str], List[str]]:
        """
        精确探查并返回当前硬件支持的物理通道列表:
        :return: (can_channels, lin_channels)
                 例如 (['CAN1 (通道 0)', 'CAN2 (通道 1)'], ['LIN1 (通道 0)', 'LIN2 (通道 1)'])
        """
        can_list = []
        lin_list = []

        if not self.is_opened or self.device_handle is None:
            return (["CAN1 (通道 0)", "CAN2 (通道 1)"], ["LIN1 (通道 0)", "LIN2 (通道 1)"])

        # 1. 探查 CAN 通道
        if hasattr(self.dll, "CAN_Init"):
            for ch in range(2):
                cfg = self.CAN_INIT_CONFIG()
                cfg.CAN_BaudRate = 500000
                cfg.CAN_Mode = 0
                if self.dll.CAN_Init(self.device_handle, ch, ctypes.byref(cfg)) == 0:
                    can_list.append(f"CAN{ch + 1} (通道 {ch})")

        # 2. 探查 LIN 通道
        for ch in range(2):
            if self.dll.LIN_EX_Init(self.device_handle, ch, 19200, 1) == 0:
                lin_list.append(f"LIN{ch + 1} (通道 {ch})")

        if not can_list:
            can_list = ["CAN1 (通道 0)", "CAN2 (通道 1)"]
        if not lin_list:
            lin_list = ["LIN1 (通道 0)", "LIN2 (通道 1)"]

        return can_list, lin_list

    def init_lin(self, channel: int = 0, baudrate: int = 19200, master_mode: int = 1) -> bool:
        """
        初始化 LIN 通道 (LIN1: channel=0, LIN2: channel=1)
        """
        if not self.is_opened or self.device_handle is None:
            raise RuntimeError("设备尚未打开！")

        ret = self.dll.LIN_EX_Init(self.device_handle, channel, baudrate, master_mode)
        if ret != 0:
            raise RuntimeError(f"LIN_EX_Init 初始化 LIN{channel+1} 失败，错误码: {ret}")
        return True

    def init_can(self, channel: int = 0, baudrate: int = 500000) -> bool:
        """
        初始化 CAN 通道 (CAN1: channel=0, CAN2: channel=1)
        """
        if not self.is_opened or self.device_handle is None:
            raise RuntimeError("设备尚未打开！")

        cfg = self.CAN_INIT_CONFIG()
        cfg.CAN_BaudRate = baudrate
        cfg.CAN_Mode = 0
        ret = self.dll.CAN_Init(self.device_handle, channel, ctypes.byref(cfg))
        if ret != 0:
            raise RuntimeError(f"CAN_Init 初始化 CAN{channel+1} 失败，错误码: {ret}")
        return True

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
