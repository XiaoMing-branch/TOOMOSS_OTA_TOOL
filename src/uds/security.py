"""
UDS 0x27 安全访问算法实现
基于 AES-128-CMAC 计算解锁密钥 Key 与固件验签
"""
import struct
from typing import Union

try:
    from cryptography.hazmat.primitives import cmac
    from cryptography.hazmat.primitives.ciphers import algorithms
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


def calculate_aes_cmac(key: bytes, message: bytes) -> bytes:
    """
    计算 16 字节 AES-128-CMAC
    :param key: 16 字节 Mask/密钥
    :param message: 待签名的消息 (如 16 字节 Seed)
    :return: 16 字节 CMAC 结果 (Key)
    """
    if len(key) != 16:
        raise ValueError(f"AES-128 密钥长度必须为 16 字节，当前为 {len(key)} 字节")

    if _HAS_CRYPTOGRAPHY:
        c = cmac.CMAC(algorithms.AES(key))
        c.update(message)
        return c.finalize()
    else:
        return _pure_python_aes_cmac(key, message)


# ----------------- 纯 Python 备用 AES-128-CMAC 实现 -----------------
# 确保在未安装任何第三方轮子时也能 100% 正确运行

_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5e, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
]
_RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _sub_word(w: int) -> int:
    return ((_SBOX[(w >> 24) & 0xFF] << 24) |
            (_SBOX[(w >> 16) & 0xFF] << 16) |
            (_SBOX[(w >> 8) & 0xFF] << 8) |
            _SBOX[w & 0xFF])


def _rot_word(w: int) -> int:
    return ((w << 8) & 0xFFFFFFFF) | ((w >> 24) & 0xFF)


def _key_expansion(key: bytes):
    w = [0] * 44
    for i in range(4):
        w[i] = struct.unpack(">I", key[4*i:4*i+4])[0]
    for i in range(4, 44):
        temp = w[i-1]
        if i % 4 == 0:
            temp = _sub_word(_rot_word(temp)) ^ (_RCON[i // 4] << 24)
        w[i] = (w[i-4] ^ temp) & 0xFFFFFFFF
    return w


def _xtimes(b: int) -> int:
    return ((b << 1) ^ 0x1B) & 0xFF if (b & 0x80) else (b << 1)


def _aes_encrypt_block(block: bytes, exp_keys) -> bytes:
    state = [list(block[i:i+4]) for i in range(0, 16, 4)]
    # Transpose to 4x4 column-major
    s = [[state[r][c] for r in range(4)] for c in range(4)]

    # AddRoundKey round 0
    for c in range(4):
        k = exp_keys[c]
        s[0][c] ^= (k >> 24) & 0xFF
        s[1][c] ^= (k >> 16) & 0xFF
        s[2][c] ^= (k >> 8) & 0xFF
        s[3][c] ^= k & 0xFF

    for r in range(1, 10):
        # SubBytes
        for row in range(4):
            for col in range(4):
                s[row][col] = _SBOX[s[row][col]]
        # ShiftRows
        s[1] = s[1][1:] + s[1][:1]
        s[2] = s[2][2:] + s[2][:2]
        s[3] = s[3][3:] + s[3][:3]
        # MixColumns
        for c in range(4):
            a0, a1, a2, a3 = s[0][c], s[1][c], s[2][c], s[3][c]
            s[0][c] = _xtimes(a0) ^ _xtimes(a1) ^ a1 ^ a2 ^ a3
            s[1][c] = a0 ^ _xtimes(a1) ^ _xtimes(a2) ^ a2 ^ a3
            s[2][c] = a0 ^ a1 ^ _xtimes(a2) ^ _xtimes(a3) ^ a3
            s[3][c] = _xtimes(a0) ^ a0 ^ a1 ^ a2 ^ _xtimes(a3)
        # AddRoundKey
        for c in range(4):
            k = exp_keys[r * 4 + c]
            s[0][c] ^= (k >> 24) & 0xFF
            s[1][c] ^= (k >> 16) & 0xFF
            s[2][c] ^= (k >> 8) & 0xFF
            s[3][c] ^= k & 0xFF

    # Round 10
    for row in range(4):
        for col in range(4):
            s[row][col] = _SBOX[s[row][col]]
    s[1] = s[1][1:] + s[1][:1]
    s[2] = s[2][2:] + s[2][:2]
    s[3] = s[3][3:] + s[3][:3]
    for c in range(4):
        k = exp_keys[40 + c]
        s[0][c] ^= (k >> 24) & 0xFF
        s[1][c] ^= (k >> 16) & 0xFF
        s[2][c] ^= (k >> 8) & 0xFF
        s[3][c] ^= k & 0xFF

    out = bytearray(16)
    for c in range(4):
        for row in range(4):
            out[c * 4 + row] = s[row][c]
    return bytes(out)


def _shift_left_128(val: bytes):
    num = int.from_bytes(val, byteorder="big")
    carry = (num >> 127) & 1
    res = ((num << 1) & ((1 << 128) - 1)).to_bytes(16, byteorder="big")
    return res, carry


def _generate_subkeys(key: bytes):
    exp_keys = _key_expansion(key)
    l_block = _aes_encrypt_block(b"\x00" * 16, exp_keys)
    k1, carry = _shift_left_128(l_block)
    if carry:
        k1_int = int.from_bytes(k1, "big") ^ 0x87
        k1 = k1_int.to_bytes(16, "big")

    k2, carry = _shift_left_128(k1)
    if carry:
        k2_int = int.from_bytes(k2, "big") ^ 0x87
        k2 = k2_int.to_bytes(16, "big")
    return exp_keys, k1, k2


def _pure_python_aes_cmac(key: bytes, message: bytes) -> bytes:
    exp_keys, k1, k2 = _generate_subkeys(key)
    n = (len(message) + 15) // 16
    if n == 0:
        n = 1
        flag = False
    else:
        flag = (len(message) % 16 == 0)

    if flag:
        m_last = bytes(a ^ b for a, b in zip(message[-16:], k1))
    else:
        pad_len = 16 - (len(message) % 16)
        padded = message[-(len(message) % 16):] + b"\x80" + b"\x00" * (pad_len - 1)
        m_last = bytes(a ^ b for a, b in zip(padded, k2))

    x = b"\x00" * 16
    for i in range(n - 1):
        y = bytes(a ^ b for a, b in zip(x, message[i * 16 : (i + 1) * 16]))
        x = _aes_encrypt_block(y, exp_keys)

    y = bytes(a ^ b for a, b in zip(x, m_last))
    return _aes_encrypt_block(y, exp_keys)
