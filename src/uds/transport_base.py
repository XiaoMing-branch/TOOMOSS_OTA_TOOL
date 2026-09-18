"""
统一总线传输层抽象基类 (TransportInterface)
为 LIN-TP (ISO 17987-2)、CAN-TP (ISO 15765-2) 以及虚拟仿真器提供统一的收发与诊断调度契约。
"""

from abc import ABC, abstractmethod
from typing import Optional


class TransportInterface(ABC):
    """
    UDS 传输层统一接口抽象基类
    """

    @abstractmethod
    def send_request(self, req_data: bytes) -> bool:
        """
        发送 UDS 原始请求报文
        :param req_data: 应用层诊断请求数据 (SID + 参数)
        :return: 发送是否成功
        """
        pass

    @abstractmethod
    def receive_response(self, timeout_ms: int = 1000) -> bytes:
        """
        接收 UDS 原始响应报文（阻塞直至收到完整报文或超时）
        :param timeout_ms: 超时时间 (ms)
        :return: 应用层诊断响应数据 (RSID + 参数 或 0x7F + SID + NRC)
        """
        pass

    @abstractmethod
    def request_response(self, req_data: bytes, timeout_ms: int = 1000, retry_count: int = 0) -> bytes:
        """
        同步发送请求并等待响应
        :param req_data: 请求数据
        :param timeout_ms: 响应超时时间 (ms)
        :param retry_count: 重试次数
        :return: 响应报文
        """
        pass

    def close(self) -> None:
        """
        关闭传输接口，释放资源（可选实现）
        """
        pass
