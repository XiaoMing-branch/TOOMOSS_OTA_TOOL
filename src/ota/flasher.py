import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional
from ..uds.client import UdsClient, UdsNegativeResponseError
from ..uds.defines import *
from .firmware import FirmwareImage


# SPRMIB 抑制正响应标志位 (ISO 14229-1, 子功能 bit 7)
SPRMIB = 0x80

# DID 常量
DID_FINGERPRINT = 0xF184  # 刷写指纹 (16 字节)


@dataclass
class OtaConfig:
    # 安全密钥 (双级独立密钥与独立软件包签名密钥，依据 Q/SK J02.321 与下位机 uds_crypto_cfg.h)
    mask: bytes = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    mask_fbl: bytes = b""  # FBL 级独立密钥，为空则复用 mask
    sign_key: bytes = b""  # 软件包安全签名密钥（0xDD02），为空则复用 mask_fbl

    # 刷写指纹 (16 字节，标识刷写操作者身份，依据 Q/SK J02.321)
    fingerprint: bytes = bytes(16)

    # 阶段一：Flash Driver (目标为 RAM)
    flash_drv_path: Optional[str] = None
    flash_drv_addr: int = 0x20008000
    routine_verify_drv_id: int = RID_SECURITY_SIGN_CHECK  # 0xDD02

    # 阶段二：Application 固件 (目标为片上 Flash)
    app_path: str = ""
    app_addr: int = 0x08010000
    routine_erase_id: int = RID_ERASE_APP_FLASH          # 0xFF00
    routine_check_id: int = RID_CHECK_COMPATIBILITY      # 0xFF01
    routine_verify_app_id: int = RID_SECURITY_SIGN_CHECK  # 0xDD02 (App 签名也用此 RID)

    # 通讯分块与时序配置
    block_size: int = 60  # 保证不超过下位机 64 字节缓冲区


class OtaFlasher:
    """
    车规级 UDS OTA 升级执行引擎
    严格遵循 Q/SK J02.321 规范，执行标准两阶段刷写：

    ┌─────── 预编程阶段 (Extended Session) ───────┐
    │ 10 83  → 进入扩展会话 (SPRMIB)              │
    │ 31 01 02 03 → 刷写条件检测 (设预编程标志)   │
    │ 85 82  → 关闭 DTC 记录 (SPRMIB)             │
    │ 27 01/02 → LEVEL_1 安全解锁                 │
    │ 2E F184 → 写入刷写指纹                      │
    ├─────── 主编程阶段 (Programming Session) ─────┤
    │ 10 02  → 进入编程会话 (需预编程标志=1)       │
    │ 27 09/0A → LEVEL_FBL 安全解锁               │
    │ 34/36/37 → 下载 Flash Driver → RAM           │
    │ 31 01 DD 02 → Flash Driver CMAC 验签        │
    │ 31 01 FF 00 → 擦除 APP Flash 分区           │
    │ 34/36/37 → 下载 Application → Flash          │
    │ 31 01 DD 02 → Application CMAC 验签         │
    │ 31 01 FF 01 → 兼容性检查 (置 app_valid)     │
    ├─────── 后编程阶段 ──────────────────────────┤
    │ 11 01  → ECU 硬复位                         │
    └─────────────────────────────────────────────┘
    """

    # 总步骤数 (用于进度计算)
    TOTAL_STEPS = 20

    def __init__(self, client: UdsClient, config: OtaConfig):
        self.client = client
        self.config = config
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def _check_cancel(self):
        if self._cancel_requested:
            raise RuntimeError("用户取消刷写")

    def execute(
        self,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        执行完整 OTA 升级状态机 (严格遵循 Q/SK J02.321 两阶段时序)
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
            # ============================================================
            # 0. 载入固件与驱动映像
            # ============================================================
            has_flash_drv = bool(self.config.flash_drv_path)
            flash_drv_fw: Optional[FirmwareImage] = None
            if has_flash_drv:
                _log(f"载入 Flash Driver 映像: {self.config.flash_drv_path}")
                flash_drv_fw = FirmwareImage(self.config.flash_drv_path)
                _log(f"Flash Driver 大小: {flash_drv_fw.size} 字节, CRC32: 0x{flash_drv_fw.crc32:08X}")

            _log(f"载入 APP 固件映像: {self.config.app_path}")
            app_fw = FirmwareImage(self.config.app_path)
            _log(f"APP 固件大小: {app_fw.size} 字节, CRC32: 0x{app_fw.crc32:08X}")

            # 确定 FBL 密钥与软件包签名密钥 (若未独立配置则向上回退)
            mask_fbl = self.config.mask_fbl if self.config.mask_fbl else self.config.mask
            sign_key = self.config.sign_key if self.config.sign_key else mask_fbl

            # ============================================================
            # 预编程阶段 (Extended Session)
            # ============================================================

            # ---- 步骤 1: 进入扩展诊断会话 (10 83, SPRMIB 抑制正响应) ----
            _prog(2.0, "步骤 1/20: 请求进入扩展诊断会话 (0x10 03 + SPRMIB)...")
            self.client.change_session(SESSION_EXTENDED | SPRMIB)
            time.sleep(0.05)

            # ---- 步骤 2: 刷写条件检测 (31 01 02 03, 设预编程标志) ----
            _prog(5.0, "步骤 2/20: 检测 ECU 刷写前置条件 (0x31 01 02 03)...")
            self.client.routine_control(
                subfunc=ROUTINE_START,
                routine_id=RID_CHECK_PROGRAM_CONDITIONS,
            )
            _log("刷写条件检测通过，预编程标志已置位")
            time.sleep(0.05)

            # ---- 步骤 3: 关闭 DTC 存储 (85 82, SPRMIB 抑制正响应) ----
            _prog(8.0, "步骤 3/20: 停用故障码存储 (0x85 02 + SPRMIB)...")
            try:
                self.client.control_dtc_setting(DTC_SETTING_OFF | SPRMIB)
            except Exception as e:
                _log(f"停用 DTC 警告 (忽略继续): {e}")
            time.sleep(0.05)

            # ---- 步骤 4: LEVEL_1 安全访问解锁 (27 01/02, 用于写指纹) ----
            _prog(10.0, "步骤 4/20: 执行 LEVEL_1 安全访问 (0x27 01/02)...")
            unlocked_l1 = self.client.security_access(
                mask=self.config.mask,
                level_seed=0x01,
                level_key=0x02,
            )
            if not unlocked_l1:
                raise RuntimeError("LEVEL_1 安全访问未能解锁，请核对安全 Mask 密钥")
            _log("LEVEL_1 安全访问 (0x27 01/02) 解锁成功！")
            time.sleep(0.05)

            # ---- 步骤 5: 写入刷写指纹 (2E F1 84, 16 字节) ----
            _prog(13.0, "步骤 5/20: 写入刷写指纹 (0x2E F184)...")
            fingerprint = self.config.fingerprint
            if len(fingerprint) < 16:
                fingerprint = fingerprint + bytes(16 - len(fingerprint))
            self.client.write_data_by_id(DID_FINGERPRINT, fingerprint[:16])
            _log("刷写指纹写入成功")
            time.sleep(0.05)

            # ============================================================
            # 主编程阶段 (Programming Session)
            # ============================================================

            # ---- 步骤 6: 进入编程会话 (10 02, 需预编程标志=1) ----
            _prog(16.0, "步骤 6/20: 请求进入编程会话 (0x10 02)...")
            self.client.change_session(SESSION_PROGRAMMING)
            time.sleep(0.05)

            # 启动 TesterPresent 后台保活（2s 周期，维持编程会话不被 S3 超时回退）
            self.client.start_keepalive(interval_sec=2.0)

            # ---- 步骤 7: LEVEL_FBL 安全访问解锁 (27 09/0A, 刷写级) ----
            _prog(19.0, "步骤 7/20: 执行 LEVEL_FBL 安全访问 (0x27 09/0A)...")
            unlocked_fbl = self.client.security_access(
                mask=mask_fbl,
                level_seed=0x09,
                level_key=0x0A,
            )
            if not unlocked_fbl:
                raise RuntimeError("LEVEL_FBL 安全访问未能解锁，请核对安全 Mask 密钥")
            _log("LEVEL_FBL 安全访问 (0x27 09/0A) 解锁成功！")
            time.sleep(0.05)

            # ============================================================
            # 阶段一：下载 Flash Driver 到 RAM 并验签
            # ============================================================
            if has_flash_drv and flash_drv_fw:
                # ---- 步骤 8: 请求下载 Flash Driver (34) ----
                _prog(22.0, f"步骤 8/20: 请求下载 Flash Driver 到 RAM (0x{self.config.flash_drv_addr:08X})...")
                max_block = self.client.request_download(
                    memory_address=self.config.flash_drv_addr,
                    memory_size=flash_drv_fw.size,
                )
                _log(f"服务端协商单块上限: {max_block} 字节")

                effective_block = min(self.config.block_size, max_block - 2 if max_block > 2 else self.config.block_size)
                chunks = flash_drv_fw.get_chunks(effective_block)
                total_chunks = len(chunks)

                # ---- 步骤 9: 传输 Flash Driver 数据 (36) ----
                _log(f"开始传输 Flash Driver，共 {total_chunks} 块...")
                for idx, chunk in enumerate(chunks):
                    self._check_cancel()
                    bsc = (idx + 1) & 0xFF
                    self.client.transfer_data(bsc, chunk)
                    cur_pct = 25.0 + 10.0 * ((idx + 1) / total_chunks)
                    _prog(cur_pct, f"步骤 9/20: 传输 Flash Driver [{idx+1}/{total_chunks}]...")

                # ---- 步骤 10: 退出传输 (37) ----
                _prog(36.0, "步骤 10/20: 请求退出 Flash Driver 传输 (0x37)...")
                self.client.request_transfer_exit()

                # ---- 步骤 11: Flash Driver CMAC 签名验证 (31 01 DD 02) ----
                _prog(38.0, "步骤 11/20: 校验 Flash Driver 签名 (0x31 01 DD 02)...")
                drv_sig = flash_drv_fw.calculate_cmac_signature(sign_key)
                resp_verify = self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=self.config.routine_verify_drv_id,
                    option_record=drv_sig,
                )
                if len(resp_verify) >= 5 and resp_verify[4] != 0x00:
                    raise RuntimeError(f"Flash Driver 签名校验未通过，状态码: 0x{resp_verify[4]:02X}")
                _log("Flash Driver 验签成功，驱动已就绪！")
                time.sleep(0.05)
            else:
                _log("跳过阶段一 (未配置 Flash Driver)")

            # ============================================================
            # 阶段二：擦除 Flash 并下载 Application
            # ============================================================

            # ---- 步骤 12: 擦除 APP Flash 分区 (31 01 FF 00) ----
            _prog(42.0, f"步骤 12/20: 擦除 Flash APP 分区 (0x{self.config.app_addr:08X})...")
            addr_bytes = self.config.app_addr.to_bytes(4, "big")
            size_bytes = app_fw.size.to_bytes(4, "big")
            erase_option = addr_bytes + size_bytes  # Q/SK J02.321 表 42 标准格式: 31 01 FF 00 + 4B 开始地址 + 4B 长度 (共 12 字节)
            try:
                self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=self.config.routine_erase_id,
                    option_record=erase_option,
                    timeout_ms=10000,
                )
            except UdsNegativeResponseError:
                # 兼容无参数擦除 (部分 Boot 版本)
                self.client.routine_control(
                    subfunc=ROUTINE_START,
                    routine_id=self.config.routine_erase_id,
                    timeout_ms=10000,
                )
            _log("Flash APP 分区擦除成功！")
            time.sleep(0.05)

            # ---- 步骤 13: 请求下载 APP 固件 (34) ----
            _prog(46.0, f"步骤 13/20: 请求下载 APP 到 Flash (0x{self.config.app_addr:08X})...")
            max_block = self.client.request_download(
                memory_address=self.config.app_addr,
                memory_size=app_fw.size,
            )
            _log(f"服务端协商 APP 单块上限: {max_block} 字节")

            effective_block = min(self.config.block_size, max_block - 2 if max_block > 2 else self.config.block_size)
            chunks = app_fw.get_chunks(effective_block)
            total_chunks = len(chunks)

            # ---- 步骤 14-15: 传输 APP 固件数据 (36) ----
            _log(f"开始分块传输 APP 固件，共 {total_chunks} 块...")
            start_time = time.time()
            for idx, chunk in enumerate(chunks):
                self._check_cancel()
                bsc = (idx + 1) & 0xFF
                self.client.transfer_data(bsc, chunk)
                cur_pct = 48.0 + 35.0 * ((idx + 1) / total_chunks)
                _prog(cur_pct, f"步骤 14/20: 烧录 APP 固件 [{idx+1}/{total_chunks}]...")

            elapsed = time.time() - start_time
            speed_kbps = (app_fw.size / 1024.0) / elapsed if elapsed > 0 else 0
            _log(f"APP 传输完成，耗时 {elapsed:.2f}s (速率: {speed_kbps:.2f} KB/s)")

            # ---- 步骤 16: 退出 APP 传输 (37) ----
            _prog(84.0, "步骤 16/20: 请求退出 APP 传输 (0x37)...")
            self.client.request_transfer_exit()

            # ---- 步骤 17: APP 固件 CMAC 签名验证 (31 01 DD 02) ----
            _prog(87.0, "步骤 17/20: 校验 APP 固件签名 (0x31 01 DD 02)...")
            app_sig = app_fw.calculate_cmac_signature(sign_key)
            resp_app_verify = self.client.routine_control(
                subfunc=ROUTINE_START,
                routine_id=self.config.routine_verify_app_id,
                option_record=app_sig,
            )
            if len(resp_app_verify) >= 5 and resp_app_verify[4] != 0x00:
                raise RuntimeError(f"APP 固件签名校验未通过，状态码: 0x{resp_app_verify[4]:02X}")
            _log("APP 固件签名验证通过！安全签名有效位已置位")
            time.sleep(0.05)

            # ---- 步骤 18: APP 兼容性检查 (31 01 FF 01, 置 app_valid) ----
            _prog(92.0, "步骤 18/20: 检验 APP 固件兼容性/有效性 (0x31 01 FF 01)...")
            resp_chk = self.client.routine_control(
                subfunc=ROUTINE_START,
                routine_id=self.config.routine_check_id,
            )
            if len(resp_chk) >= 5 and resp_chk[4] != 0x00:
                raise RuntimeError(f"APP 兼容性校验未通过，状态码: 0x{resp_chk[4]:02X}")
            _log("APP 兼容性校验通过，app_valid 已置位！")

            # ============================================================
            # 后编程阶段
            # ============================================================

            # ---- 步骤 19: ECU 硬复位 (11 01) ----
            _prog(96.0, "步骤 19/20: 发送 ECU 硬复位指令 (0x11 01)...")
            try:
                self.client.ecu_reset(RESET_HARD)
            except Exception as e:
                _log(f"复位发送提示: {e}")

            # ---- 步骤 20: 完成 ----
            _prog(100.0, "步骤 20/20: OTA 固件升级圆满完成！ECU 已重启运行新应用程序。")
            return True

        except Exception as e:
            _log(f"❌ OTA 升级失败: {e}")
            raise
        finally:
            self.client.stop_keepalive()
