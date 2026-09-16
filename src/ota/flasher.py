import time
import threading
from dataclasses import dataclass
from typing import Callable, Optional
from ..uds.client import UdsClient, UdsNegativeResponseError
from ..uds.defines import *
from .firmware import FirmwareImage


@dataclass
class OtaConfig:
    # 安全密钥
    mask: bytes = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    level_seed: int = 0x01
    level_key: int = 0x02

    # 阶段一：Flash Driver (目标为 RAM)
    flash_drv_path: Optional[str] = None
    flash_drv_addr: int = 0x20008000
    routine_verify_drv_id: int = RID_SECURITY_SIGN_CHECK  # 0xDD02

    # 阶段二：Application 固件 (目标为片上 Flash)
    app_path: str = ""
    app_addr: int = 0x08010000
    routine_erase_id: int = RID_ERASE_APP_FLASH          # 0xFF00
    routine_check_id: int = RID_CHECK_COMPATIBILITY      # 0xFF01

    # 通讯分块与时序配置
    block_size: int = 60  # 保证不超过下位机 64 字节缓冲区


class OtaFlasher:
    """
    车规级 UDS OTA 升级执行引擎
    遵循 Q/SK J02.321 规范，执行严格两阶段刷写：
    【阶段一：Flash Driver 注入与验签】 -> 【阶段二：Flash 擦除与 APP 编程】
    """

    def __init__(self, client: UdsClient, config: OtaConfig):
        self.client = client
        self.config = config
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def execute(
        self,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        执行完整 OTA 升级状态机
        :param progress_cb: 进度更新回调 (percent: 0.0~100.0, step_text: str)
        :param log_cb: 文本日志回调
        """
        self._cancel_requested = False

        def _log(msg: str):
            if log_cb:
                log_cb(msg)

        def _prog(percent: float, text: str):
            _log(f"[{percent:5.1f}%] {text}")
            if progress_cb:
                progress_cb(percent, text)

        try:
            # 0. 载入固件与驱动映像
            has_flash_drv = bool(self.config.flash_drv_path)
            flash_drv_fw: Optional[FirmwareImage] = None
            if has_flash_drv:
                _log(f"载入 Flash Driver 映像: {self.config.flash_drv_path}")
                flash_drv_fw = FirmwareImage(self.config.flash_drv_path)
                _log(f"Flash Driver 大小: {flash_drv_fw.size} 字节, CRC32: 0x{flash_drv_fw.crc32:08X}")

            _log(f"载入 APP 固件映像: {self.config.app_path}")
            app_fw = FirmwareImage(self.config.app_path)
            _log(f"APP 固件大小: {app_fw.size} 字节, CRC32: 0x{app_fw.crc32:08X}")

            # ---------------- 步骤 1：切换到扩展会话 (10 03) ----------------
            _prog(5.0, "步骤 1/12: 请求进入扩展诊断会话 (0x10 03)...")
            self.client.change_session(SESSION_EXTENDED)
            time.sleep(0.05)

            # ---------------- 步骤 2：关闭 DTC 存储 (85 02) ----------------
            _prog(10.0, "步骤 2/12: 停用故障码存储 (0x85 02)...")
            try:
                self.client.control_dtc_setting(DTC_SETTING_OFF)
            except Exception as e:
                _log(f"停用 DTC 警告 (忽略继续): {e}")
            time.sleep(0.05)

            # ---------------- 步骤 3：切换到编程会话 (10 02) ----------------
            _prog(15.0, "步骤 3/12: 请求进入编程会话 (0x10 02)...")
            self.client.change_session(SESSION_PROGRAMMING)
            time.sleep(0.05)

            # 启动 TesterPresent 后台保活（2s 周期）
            self.client.start_keepalive(interval_sec=2.0)

            # ---------------- 步骤 4：安全访问解锁 (支持 FBL 0x09/0x0A 与 Level 1 0x01/0x02) ----------------
            _prog(20.0, "步骤 4/12: 执行 0x27 安全访问解锁 (AES-128-CMAC)...")
            unlocked = False
            # 优先尝试 FBL 刷写级安全访问 (0x09/0x0A，Q/SK J02.321 车规标准)
            try:
                _log(f"尝试 FBL 级安全访问 (0x27 09/0A)...")
                unlocked = self.client.security_access(
                    mask=self.config.mask,
                    level_seed=0x09,
                    level_key=0x0A,
                )
                if unlocked:
                    _log("FBL 级安全访问 (0x27 09/0A) 解锁成功！")
            except Exception as e:
                _log(f"FBL 级安全访问提示: {e}，尝试切换 Level 1 (0x27 01/02)...")

            # 若未解锁或用户显式配置了 Level 1，尝试 Level 1 (0x01/0x02)
            if not unlocked:
                unlocked = self.client.security_access(
                    mask=self.config.mask,
                    level_seed=self.config.level_seed,
                    level_key=self.config.level_key,
                )
                if unlocked:
                    _log(f"Level 1 安全访问 (0x27 {self.config.level_seed:02X}/{self.config.level_key:02X}) 解锁成功！")

            if not unlocked:
                raise RuntimeError("安全访问未能解锁，请核对安全 Mask 密钥")
            time.sleep(0.05)

            # ---------------- 步骤 5-7：下载 Flash Driver (若配置) ----------------
            if has_flash_drv and flash_drv_fw:
                _prog(25.0, f"步骤 5/12: 请求下载 Flash Driver 到 RAM (0x{self.config.flash_drv_addr:08X})...")
                max_block = self.client.request_download(
                    memory_address=self.config.flash_drv_addr,
                    memory_size=flash_drv_fw.size,
                )
                _log(f"服务端协商单块上限: {max_block} 字节")

                effective_block = min(self.config.block_size, max_block - 2 if max_block > 2 else self.config.block_size)
                chunks = flash_drv_fw.get_chunks(effective_block)
                total_chunks = len(chunks)

                _log(f"开始传输 Flash Driver，共 {total_chunks} 块...")
                for idx, chunk in enumerate(chunks):
                    if self._cancel_requested:
                        raise RuntimeError("用户取消刷写")
                    bsc = (idx + 1) & 0xFF
                    self.client.transfer_data(bsc, chunk)
                    cur_pct = 25.0 + 15.0 * ((idx + 1) / total_chunks)
                    _prog(cur_pct, f"步骤 5/12: 传输 Flash Driver [{idx+1}/{total_chunks}]...")

                _prog(40.0, "步骤 6/12: 请求退出 Flash Driver 传输 (0x37)...")
                self.client.request_transfer_exit()

                _prog(42.0, "步骤 7/12: 校验 Flash Driver 签名 (0x31 01 DD 02)...")
                sig = flash_drv_fw.calculate_cmac_signature(self.config.mask)
                resp_verify = self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=self.config.routine_verify_drv_id,
                    option_record=sig,
                )
                if len(resp_verify) >= 5 and resp_verify[4] != 0x00:
                    raise RuntimeError(f"Flash Driver 签名校验未通过，状态码: 0x{resp_verify[4]:02X}")
                _log("Flash Driver 验签成功，驱动已就绪！")
                time.sleep(0.05)

            # ---------------- 步骤 8：检查刷写条件 (31 01 02 03) ----------------
            _prog(45.0, "步骤 8/12: 检查 ECU 刷写前置条件 (0x31 01 02 03)...")
            try:
                self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=RID_CHECK_PROGRAM_CONDITIONS,
                )
            except Exception as e:
                _log(f"刷写条件检测警告 (忽略继续): {e}")

            # ---------------- 步骤 9：擦除 APP 分区 (31 01 FF 00) ----------------
            _prog(48.0, f"步骤 9/12: 擦除 Flash APP 分区 (0x{self.config.app_addr:08X})...")
            # 依据下位机 uds_download.c，FF00 参数为 0x44 + 4字节起始地址 + 4字节长度
            addr_bytes = self.config.app_addr.to_bytes(4, "big")
            size_bytes = app_fw.size.to_bytes(4, "big")
            erase_option = b"\x44" + addr_bytes + size_bytes
            try:
                self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=self.config.routine_erase_id,
                    option_record=erase_option,
                    timeout_ms=5000,
                )
            except Exception:
                # 兼容无参数擦除
                self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=self.config.routine_erase_id,
                    timeout_ms=5000,
                )
            _log("Flash APP 分区擦除成功！")
            time.sleep(0.05)

            # ---------------- 步骤 10：下载 APP 固件 (34 -> 36 -> 37) ----------------
            _prog(50.0, f"步骤 10/12: 请求下载 APP 到 Flash (0x{self.config.app_addr:08X})...")
            max_block = self.client.request_download(
                memory_address=self.config.app_addr,
                memory_size=app_fw.size,
            )
            _log(f"服务端协商 APP 单块上限: {max_block} 字节")

            effective_block = min(self.config.block_size, max_block - 2 if max_block > 2 else self.config.block_size)
            chunks = app_fw.get_chunks(effective_block)
            total_chunks = len(chunks)

            _log(f"开始分块传输 APP 固件，共 {total_chunks} 块...")
            start_time = time.time()
            for idx, chunk in enumerate(chunks):
                if self._cancel_requested:
                    raise RuntimeError("用户取消刷写")
                bsc = (idx + 1) & 0xFF
                self.client.transfer_data(bsc, chunk)
                cur_pct = 50.0 + 40.0 * ((idx + 1) / total_chunks)
                _prog(cur_pct, f"步骤 10/12: 烧录 APP 固件 [{idx+1}/{total_chunks}]...")

            elapsed = time.time() - start_time
            speed_kbps = (app_fw.size / 1024.0) / elapsed if elapsed > 0 else 0
            _log(f"APP 传输完成，耗时 {elapsed:.2f}s (速率: {speed_kbps:.2f} KB/s)")

            _prog(92.0, "步骤 10/12: 请求退出 APP 传输 (0x37)...")
            self.client.request_transfer_exit()

            # ---------------- 步骤 11：APP 完整性与兼容性校验 (31 01 FF 01) ----------------
            _prog(95.0, "步骤 11/12: 检验 APP 固件完整性/兼容性 (0x31 01 FF 01)...")
            resp_chk = self.client.routine_control(
                subfunc=ROUTINE_START,
                routine_id=self.config.routine_check_id,
            )
            if len(resp_chk) >= 5 and resp_chk[4] != 0x00:
                raise RuntimeError(f"APP 完整性校验未通过，状态码: 0x{resp_chk[4]:02X}")
            _log("APP 固件校验通过，app_valid 已置位！")

            # ---------------- 步骤 12：ECU 复位重启 (11 01) ----------------
            _prog(98.0, "步骤 12/12: 发送 ECU 硬复位指令 (0x11 01)...")
            try:
                self.client.ecu_reset(RESET_HARD)
            except Exception as e:
                _log(f"复位发送提示: {e}")

            _prog(100.0, "OTA 固件升级圆满完成！ECU 已重启运行新应用程序。")
            return True

        except Exception as e:
            _log(f"❌ OTA 升级失败: {e}")
            raise
        finally:
            self.client.stop_keepalive()
