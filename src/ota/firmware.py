import os
import zlib
from typing import List, Optional
from ..uds.security import calculate_aes_cmac


class FirmwareImage:
    """
    固件映像文件加载器与解析器（支持 .bin 与 Intel .hex）
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.filename = os.path.basename(file_path)
        self.data = b""
        self.load()

    def load(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"固件文件不存在: {self.file_path}")

        ext = os.path.splitext(self.file_path)[1].lower()
        if ext == ".bin":
            with open(self.file_path, "rb") as f:
                self.data = f.read()
        elif ext == ".hex":
            self.data = self._parse_intel_hex(self.file_path)
        else:
            # 默认作为二进制读取
            with open(self.file_path, "rb") as f:
                self.data = f.read()

        if len(self.data) == 0:
            raise ValueError(f"固件文件内容为空: {self.file_path}")

        # STM32 Flash 按半字 (2 字节) 编程，确保长度为偶数 (不足补 0xFF)
        if len(self.data) % 2 != 0:
            self.data += b"\xFF"

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def crc32(self) -> int:
        return zlib.crc32(self.data) & 0xFFFFFFFF

    def calculate_cmac_signature(self, mask: bytes) -> bytes:
        """
        计算固件的 AES-128-CMAC 签名（用于 0x31 01 DD 02 验签）
        """
        return calculate_aes_cmac(mask, self.data)

    def get_chunks(self, block_size: int = 60) -> List[bytes]:
        """
        按指定块大小切片分包
        """
        if block_size <= 0:
            raise ValueError("block_size 必须大于 0")
        return [self.data[i : i + block_size] for i in range(0, len(self.data), block_size)]

    @staticmethod
    def _parse_intel_hex(filepath: str) -> bytes:
        """
        简易 Intel HEX 格式解析器，提取连续固件数据
        """
        data_dict = {}
        extended_addr = 0
        min_addr = 0xFFFFFFFF
        max_addr = 0

        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line.startswith(":"):
                    continue
                byte_count = int(line[1:3], 16)
                address = int(line[3:7], 16)
                record_type = int(line[7:9], 16)
                record_data = bytes.fromhex(line[9 : 9 + byte_count * 2])

                if record_type == 0x00:  # 数据记录
                    abs_addr = extended_addr + address
                    for i, b in enumerate(record_data):
                        cur_addr = abs_addr + i
                        data_dict[cur_addr] = b
                        if cur_addr < min_addr:
                            min_addr = cur_addr
                        if cur_addr > max_addr:
                            max_addr = cur_addr
                elif record_type == 0x01:  # 文件结束
                    break
                elif record_type == 0x02:  # 扩展段地址
                    extended_addr = int(line[9:13], 16) << 4
                elif record_type == 0x04:  # 扩展线性地址
                    extended_addr = int(line[9:13], 16) << 16

        if not data_dict:
            raise ValueError("HEX 文件中未发现有效数据记录")

        total_len = max_addr - min_addr + 1
        firmware_bytes = bytearray(b"\xFF" * total_len)
        for addr, b in data_dict.items():
            firmware_bytes[addr - min_addr] = b

        return bytes(firmware_bytes)
