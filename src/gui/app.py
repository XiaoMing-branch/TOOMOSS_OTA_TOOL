import os
import sys
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, List, Tuple

from ..toomoss.usb2xxx import Usb2xxxDevice, DeviceInfo
from ..toomoss.lin_interface import LinUdsInterface, LinUdsAddrConfig
from ..toomoss.can_interface import CanUdsInterface, CanUdsAddrConfig
from ..uds.client import UdsClient, UdsNegativeResponseError
from ..uds.defines import *
from ..ota.firmware import FirmwareImage
from ..ota.flasher import OtaFlasher, OtaConfig
from .did_manager import DidReaderWindow


# ------------------- 现代化深色主题调色盘 (Modern Cyber Dark) -------------------
COLOR_BG_DARK       = "#12131C"  # 窗口主底色
COLOR_CARD_BG       = "#1A1D2B"  # 模块卡片底色
COLOR_CARD_BORDER   = "#2B2F44"  # 边框线
COLOR_INPUT_BG      = "#0E1017"  # 输入框与下拉框底色
COLOR_TEXT_PRIMARY  = "#F1F5F9"  # 主文字 (亮白灰)
COLOR_TEXT_MUTED    = "#94A3B8"  # 辅助提示文字 (柔和蓝灰)
COLOR_TEXT_DIM      = "#64748B"  # 较暗标注文字

COLOR_PRIMARY       = "#0284C7"  # 科技蓝 (主按键)
COLOR_PRIMARY_HOVER = "#0369A1"
COLOR_SUCCESS       = "#10B981"  # 翡翠绿 (连接/成功)
COLOR_SUCCESS_HOVER = "#059669"
COLOR_WARNING       = "#F59E0B"  # 琥珀橙
COLOR_DANGER        = "#EF4444"  # 珊瑚红 (中止)
COLOR_DANGER_HOVER  = "#DC2626"

COLOR_LOG_BG        = "#0A0C10"  # 终端抓包黑底
COLOR_LOG_TX        = "#38BDF8"  # 发送报文青蓝
COLOR_LOG_RX        = "#4ADE80"  # 接收报文亮绿
COLOR_LOG_INFO      = "#E2E8F0"  # 普通信息白
COLOR_LOG_WARN      = "#FBBF24"  # 警告黄
COLOR_LOG_ERR       = "#F87171"  # 错误红


class OtaApp(tk.Tk):
    """
    Toomoss CAN / LIN UDS OTA 升级上位机 - 现代专业工程界面
    """

    def __init__(self):
        super().__init__()
        self.title("Toomoss Automotive Bus UDS OTA Tool - [CAN / LIN 汽车诊断刷写上位机]")
        self.geometry("1020x840")
        self.minsize(920, 720)
        self.configure(bg=COLOR_BG_DARK)

        # 核心通信对象
        self.device: Optional[Usb2xxxDevice] = None
        self.interface = None
        self.client: Optional[UdsClient] = None
        self.flasher: Optional[OtaFlasher] = None
        self.is_flashing = False

        # 硬件探测到的真实物理通道
        self.available_can_channels: List[str] = ["CAN1 (通道 0)", "CAN2 (通道 1)"]
        self.available_lin_channels: List[str] = ["LIN1 (通道 0)", "LIN2 (通道 1)"]

        self._init_theme_styles()
        self._build_ui()
        self._load_default_config()

        # 启动后自动扫描硬件
        self.after(200, self.on_scan_devices)

    def _init_theme_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # 基础样式
        style.configure(".", background=COLOR_BG_DARK, foreground=COLOR_TEXT_PRIMARY, font=("Segoe UI", 9))
        style.configure("TFrame", background=COLOR_BG_DARK)
        style.configure("Card.TFrame", background=COLOR_CARD_BG, relief="solid", borderwidth=1)
        style.configure("CardInner.TFrame", background=COLOR_CARD_BG)

        # 标签
        style.configure("TLabel", background=COLOR_CARD_BG, foreground=COLOR_TEXT_PRIMARY)
        style.configure("Muted.TLabel", background=COLOR_CARD_BG, foreground=COLOR_TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("SectionTitle.TLabel", background=COLOR_CARD_BG, foreground="#38BDF8", font=("Segoe UI", 10, "bold"))
        style.configure("Badge.TLabel", background="#1E293B", foreground="#38BDF8", font=("Segoe UI", 8, "bold"), padding=(6, 2))

        # 按钮样式
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 9, "bold"),
            background=COLOR_PRIMARY,
            foreground="#FFFFFF",
            padding=(10, 4),
            relief="flat",
        )
        style.map("Primary.TButton", background=[("active", COLOR_PRIMARY_HOVER), ("disabled", "#334155")])

        style.configure(
            "Success.TButton",
            font=("Segoe UI", 10, "bold"),
            background=COLOR_SUCCESS,
            foreground="#FFFFFF",
            padding=(16, 6),
            relief="flat",
        )
        style.map("Success.TButton", background=[("active", COLOR_SUCCESS_HOVER), ("disabled", "#334155")])

        style.configure(
            "Danger.TButton",
            font=("Segoe UI", 9, "bold"),
            background=COLOR_DANGER,
            foreground="#FFFFFF",
            padding=(10, 4),
            relief="flat",
        )
        style.map("Danger.TButton", background=[("active", COLOR_DANGER_HOVER), ("disabled", "#334155")])

        style.configure(
            "Secondary.TButton",
            font=("Segoe UI", 9),
            background="#2D3249",
            foreground=COLOR_TEXT_PRIMARY,
            padding=(8, 4),
            relief="flat",
        )
        style.map("Secondary.TButton", background=[("active", "#3E4464"), ("disabled", "#1E2233")])

        # 下拉框与输入框
        style.configure("TCombobox", fieldbackground=COLOR_INPUT_BG, background="#2D3249", foreground=COLOR_TEXT_PRIMARY)
        style.map("TCombobox", fieldbackground=[("readonly", COLOR_INPUT_BG)], foreground=[("readonly", COLOR_TEXT_PRIMARY)])

        style.configure("TEntry", fieldbackground=COLOR_INPUT_BG, foreground=COLOR_TEXT_PRIMARY, insertcolor="#FFFFFF")
        style.configure("TCheckbutton", background=COLOR_CARD_BG, foreground=COLOR_TEXT_PRIMARY)
        style.map("TCheckbutton", background=[("active", COLOR_CARD_BG)])

        style.configure("TRadiobutton", background=COLOR_CARD_BG, foreground=COLOR_TEXT_PRIMARY)
        style.map("TRadiobutton", background=[("active", COLOR_CARD_BG)])

        # 进度条
        style.configure("Horizontal.TProgressbar", background="#0284C7", troughcolor="#0E1017", bordercolor=COLOR_CARD_BORDER)

    def _build_ui(self):
        main_box = ttk.Frame(self, padding=12)
        main_box.pack(fill=tk.BOTH, expand=True)

        # ---------------- 卡片 1: 硬件与总线通道选择 (明确区分 CAN1/CAN2 与 LIN1/LIN2) ----------------
        card1 = ttk.Frame(main_box, style="Card.TFrame", padding=12)
        card1.pack(fill=tk.X, pady=(0, 10))

        # 标题行
        title_row1 = ttk.Frame(card1, style="CardInner.TFrame")
        title_row1.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(title_row1, text="1. Toomoss 硬件通道与总线协议配置", style="SectionTitle.TLabel").pack(side=tk.LEFT)
        self.lbl_hw_badge = ttk.Label(title_row1, text="硬件未连接", style="Badge.TLabel")
        self.lbl_hw_badge.pack(side=tk.RIGHT)

        # 通信总线协议类型选择 (单选)
        proto_row = ttk.Frame(card1, style="CardInner.TFrame")
        proto_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(proto_row, text="总线协议类型:").pack(side=tk.LEFT, padx=(0, 10))
        self.var_bus_type = tk.StringVar(value="LIN")
        rb_lin = ttk.Radiobutton(proto_row, text="LIN 总线 (UDS on LIN)", value="LIN", variable=self.var_bus_type, command=self._on_bus_type_changed)
        rb_lin.pack(side=tk.LEFT, padx=(0, 15))
        rb_can = ttk.Radiobutton(proto_row, text="CAN 总线 (ISO-TP / UDS on CAN)", value="CAN", variable=self.var_bus_type, command=self._on_bus_type_changed)
        rb_can.pack(side=tk.LEFT, padx=(0, 20))

        self.lbl_bus_hint = ttk.Label(proto_row, text="提示: LIN 模式下可选用 LIN1/LIN2，CAN 模式下可选用 CAN1/CAN2", style="Muted.TLabel")
        self.lbl_bus_hint.pack(side=tk.LEFT)

        # 详细参数选择行
        row_params = ttk.Frame(card1, style="CardInner.TFrame")
        row_params.pack(fill=tk.X, pady=2)

        ttk.Label(row_params, text="设备:").pack(side=tk.LEFT, padx=(0, 4))
        self.cb_devices = ttk.Combobox(row_params, state="readonly", width=22)
        self.cb_devices.pack(side=tk.LEFT, padx=(0, 6))

        btn_scan = ttk.Button(row_params, text="扫描", style="Secondary.TButton", width=5, command=self.on_scan_devices)
        btn_scan.pack(side=tk.LEFT, padx=(0, 12))

        # 物理通道下拉框 (核心：明确展示 CAN1/CAN2 或 LIN1/LIN2)
        self.lbl_channel_title = ttk.Label(row_params, text="物理通道:")
        self.lbl_channel_title.pack(side=tk.LEFT, padx=(0, 4))
        self.cb_channel = ttk.Combobox(row_params, state="readonly", width=15)
        self.cb_channel.pack(side=tk.LEFT, padx=(0, 12))

        # 波特率
        ttk.Label(row_params, text="波特率:").pack(side=tk.LEFT, padx=(0, 4))
        self.cb_baudrate = ttk.Combobox(row_params, state="readonly", width=9)
        self.cb_baudrate.pack(side=tk.LEFT, padx=(0, 12))

        # 节点寻址
        self.lbl_addr_title = ttk.Label(row_params, text="从机 NAD:")
        self.lbl_addr_title.pack(side=tk.LEFT, padx=(0, 4))
        self.entry_addr = ttk.Entry(row_params, width=9)
        self.entry_addr.insert(0, "0x01")
        self.entry_addr.pack(side=tk.LEFT, padx=(0, 15))

        # 连接按钮
        self.btn_connect = ttk.Button(row_params, text="打开连接", style="Primary.TButton", command=self.on_toggle_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 8))

        self.lbl_conn_indicator = ttk.Label(row_params, text="● 未连接", foreground="#64748B", font=("Segoe UI", 9, "bold"))
        self.lbl_conn_indicator.pack(side=tk.LEFT)

        # 底部状态信息
        self.lbl_device_detail = ttk.Label(card1, text="适配器状态: 请先点击 [扫描] 并打开设备连接", style="Muted.TLabel")
        self.lbl_device_detail.pack(fill=tk.X, pady=(6, 0))

        # ---------------- 卡片 2: 刷写固件与两阶段配置 ----------------
        card2 = ttk.Frame(main_box, style="Card.TFrame", padding=12)
        card2.pack(fill=tk.X, pady=(0, 10))

        title_row2 = ttk.Frame(card2, style="CardInner.TFrame")
        title_row2.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(title_row2, text="2. 刷写流程与固件配置 (严谨两阶段：先 Flash Drv 后 APP 固件)", style="SectionTitle.TLabel").pack(side=tk.LEFT)

        # 阶段一：Flash Driver
        fdrv_box = ttk.Frame(card2, style="CardInner.TFrame")
        fdrv_box.pack(fill=tk.X, pady=3)

        self.var_use_flash_drv = tk.BooleanVar(value=True)
        self.chk_flash_drv = ttk.Checkbutton(fdrv_box, text="【阶段一】下载 Flash Driver (写入 RAM 并验签)", variable=self.var_use_flash_drv, command=self._on_flash_drv_toggle)
        self.chk_flash_drv.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(fdrv_box, text="目标 RAM 地址:").pack(side=tk.LEFT, padx=(8, 4))
        self.entry_fdrv_addr = ttk.Entry(fdrv_box, width=12)
        self.entry_fdrv_addr.insert(0, "0x20008000")
        self.entry_fdrv_addr.pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_fdrv_info = ttk.Label(fdrv_box, text="大小: 0 B | 校验: 待加载", style="Muted.TLabel")
        self.lbl_fdrv_info.pack(side=tk.LEFT)

        fdrv_path_box = ttk.Frame(card2, style="CardInner.TFrame")
        fdrv_path_box.pack(fill=tk.X, pady=2)
        ttk.Label(fdrv_path_box, text="Flash Drv 文件:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_fdrv_path = ttk.Entry(fdrv_path_box)
        self.entry_fdrv_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_fdrv_browse = ttk.Button(fdrv_path_box, text="选择文件...", style="Secondary.TButton", command=self.on_browse_fdrv)
        self.btn_fdrv_browse.pack(side=tk.LEFT)

        ttk.Separator(card2, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # 阶段二：APP 固件
        app_box = ttk.Frame(card2, style="CardInner.TFrame")
        app_box.pack(fill=tk.X, pady=3)

        ttk.Label(app_box, text="【阶段二】下载 Application 固件 (擦除并烧录片上 Flash)", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(app_box, text="目标 Flash 地址:").pack(side=tk.LEFT, padx=(8, 4))
        self.entry_app_addr = ttk.Entry(app_box, width=12)
        self.entry_app_addr.insert(0, "0x08010000")
        self.entry_app_addr.pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_app_info = ttk.Label(app_box, text="大小: 0 B | CRC32: 0x00000000", style="Muted.TLabel")
        self.lbl_app_info.pack(side=tk.LEFT)

        app_path_box = ttk.Frame(card2, style="CardInner.TFrame")
        app_path_box.pack(fill=tk.X, pady=2)
        ttk.Label(app_path_box, text="APP 固件文件:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_app_path = ttk.Entry(app_path_box)
        self.entry_app_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_app_browse = ttk.Button(app_path_box, text="选择文件...", style="Secondary.TButton", command=self.on_browse_app)
        self.btn_app_browse.pack(side=tk.LEFT)

        # 选项行
        sec_box = ttk.Frame(card2, style="CardInner.TFrame")
        sec_box.pack(fill=tk.X, pady=(6, 2))

        ttk.Label(sec_box, text="安全 Mask (AES-128 Key 16字节):").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_mask = ttk.Entry(sec_box, width=34)
        self.entry_mask.insert(0, "2b7e151628aed2a6abf7158809cf4f3c")
        self.entry_mask.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(sec_box, text="分块大小 (BlockSize):").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_block_size = ttk.Entry(sec_box, width=6)
        self.entry_block_size.insert(0, "60")
        self.entry_block_size.pack(side=tk.LEFT)

        # ---------------- 卡片 3: OTA 执行与控制 ----------------
        card3 = ttk.Frame(main_box, style="Card.TFrame", padding=12)
        card3.pack(fill=tk.X, pady=(0, 10))

        self.progress_bar = ttk.Progressbar(card3, orient=tk.HORIZONTAL, mode="determinate", style="Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 6))

        ctrl_row = ttk.Frame(card3, style="CardInner.TFrame")
        ctrl_row.pack(fill=tk.X)

        self.lbl_status_text = ttk.Label(ctrl_row, text="系统就绪。请确认接线与文件后，点击 [🚀 开始 OTA 升级]", font=("Segoe UI", 9, "bold"))
        self.lbl_status_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_start_ota = ttk.Button(ctrl_row, text="🚀 开始 OTA 升级", style="Success.TButton", command=self.on_start_ota)
        self.btn_start_ota.pack(side=tk.RIGHT, padx=(6, 0))

        self.btn_cancel_ota = ttk.Button(ctrl_row, text="⏹ 中止", style="Danger.TButton", command=self.on_cancel_ota, state=tk.DISABLED)
        self.btn_cancel_ota.pack(side=tk.RIGHT, padx=(6, 0))

        ttk.Button(ctrl_row, text="🔍 规范 DID 诊断读取 (0x22)", style="Primary.TButton", command=self.on_open_did_reader).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(ctrl_row, text="ECU 复位 (0x11)", style="Secondary.TButton", command=self.on_ecu_reset).pack(side=tk.RIGHT, padx=(6, 0))

        # ---------------- 卡片 4: 终端级实时抓包监视器 ----------------
        card4 = ttk.Frame(main_box, style="Card.TFrame", padding=10)
        card4.pack(fill=tk.BOTH, expand=True)

        header_row4 = ttk.Frame(card4, style="CardInner.TFrame")
        header_row4.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header_row4, text="4. 诊断总线实时监视器 (UDS 报文追踪日志)", style="SectionTitle.TLabel").pack(side=tk.LEFT)

        self.var_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(header_row4, text="自动滚动", variable=self.var_autoscroll).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(header_row4, text="清空日志", style="Secondary.TButton", command=self.on_clear_log).pack(side=tk.RIGHT)

        # 文本框与滚动条
        text_container = tk.Frame(card4, bg=COLOR_LOG_BG)
        text_container.pack(fill=tk.BOTH, expand=True)

        self.txt_log = tk.Text(
            text_container,
            wrap=tk.NONE,
            font=("Consolas", 9),
            bg=COLOR_LOG_BG,
            fg=COLOR_LOG_INFO,
            insertbackground="#FFFFFF",
            relief="flat",
            padx=8,
            pady=6,
        )
        scroll_y = ttk.Scrollbar(text_container, orient=tk.VERTICAL, command=self.txt_log.yview)
        scroll_x = ttk.Scrollbar(text_container, orient=tk.HORIZONTAL, command=self.txt_log.xview)
        self.txt_log.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        # 配置文本颜色标签
        self.txt_log.tag_config("TX", foreground=COLOR_LOG_TX)
        self.txt_log.tag_config("RX", foreground=COLOR_LOG_RX)
        self.txt_log.tag_config("TIME", foreground=COLOR_TEXT_DIM)
        self.txt_log.tag_config("WARN", foreground=COLOR_LOG_WARN)
        self.txt_log.tag_config("ERR", foreground=COLOR_LOG_ERR)
        self.txt_log.tag_config("INFO", foreground=COLOR_LOG_INFO)

        # 快捷测试栏
        manual_box = ttk.Frame(card4, style="CardInner.TFrame")
        manual_box.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(manual_box, text="手动发送 UDS (十六进制):").pack(side=tk.LEFT, padx=(0, 6))
        self.entry_raw_uds = ttk.Entry(manual_box, width=28)
        self.entry_raw_uds.insert(0, "10 03")
        self.entry_raw_uds.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(manual_box, text="发送", style="Secondary.TButton", command=self.on_send_raw_uds).pack(side=tk.LEFT)

        # 初始化通道模式
        self._on_bus_type_changed()

    def _on_bus_type_changed(self):
        bus = self.var_bus_type.get()
        if bus == "LIN":
            # 精确切换为 LIN 通道列表
            self.lbl_channel_title.configure(text="LIN 通道:")
            self.cb_channel["values"] = self.available_lin_channels
            self.cb_channel.current(0)

            self.cb_baudrate["values"] = ["9600", "19200", "20000"]
            self.cb_baudrate.set("19200")

            self.lbl_addr_title.configure(text="从机 NAD:")
            self.entry_addr.delete(0, tk.END)
            self.entry_addr.insert(0, "0x01")

            self.lbl_bus_hint.configure(text="当前为 LIN 总线模式: 请选择 LIN1 (通道0) 或 LIN2 (通道1)，下位机 NAD 默认 0x01")
        else:
            # 精确切换为 CAN 通道列表
            self.lbl_channel_title.configure(text="CAN 通道:")
            self.cb_channel["values"] = self.available_can_channels
            self.cb_channel.current(0)

            self.cb_baudrate["values"] = ["250000", "500000", "1000000"]
            self.cb_baudrate.set("500000")

            self.lbl_addr_title.configure(text="CAN 请求ID:")
            self.entry_addr.delete(0, tk.END)
            self.entry_addr.insert(0, "0x7E0")

            self.lbl_bus_hint.configure(text="当前为 CAN 总线模式: 请选择 CAN1 (通道0) 或 CAN2 (通道1)，下位机标准 ID 0x7E0/0x7E8")

    def _load_default_config(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg_file = os.path.join(base_dir, "config", "default_config.json")
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    sec_cfg = cfg.get("security", {})
                    self.entry_mask.delete(0, tk.END)
                    self.entry_mask.insert(0, sec_cfg.get("mask_hex", "2b7e151628aed2a6abf7158809cf4f3c"))

                    fdrv_cfg = cfg.get("flash_driver", {})
                    self.entry_fdrv_addr.delete(0, tk.END)
                    self.entry_fdrv_addr.insert(0, fdrv_cfg.get("target_ram_addr", "0x20008000"))

                    app_cfg = cfg.get("app_firmware", {})
                    self.entry_app_addr.delete(0, tk.END)
                    self.entry_app_addr.insert(0, app_cfg.get("target_flash_addr", "0x08010000"))
            except Exception:
                pass

        # 优先载入 samples 或 ALIENTEK_WS_V3 下的固件
        sample_fdrv = os.path.join(base_dir, "samples", "sample_flash_drv.bin")
        if os.path.exists(sample_fdrv):
            self._set_fdrv_file(sample_fdrv)

        sample_app = os.path.join(base_dir, "samples", "sample_app.bin")
        if os.path.exists(sample_app):
            self._set_app_file(sample_app)

    def log(self, text: str, tag: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d} "
        self.txt_log.insert(tk.END, timestamp, "TIME")
        self.txt_log.insert(tk.END, f"[{tag}] {text}\n", tag)
        if self.var_autoscroll.get():
            self.txt_log.see(tk.END)

    def _log_tx(self, data: bytes, pid: Optional[int] = None, note: str = ""):
        bus = self.var_bus_type.get()
        if pid is not None:
            tag_id = f"0x{pid:02X}" if bus == "LIN" else f"0x{pid:03X}"
        else:
            tag_id = "0x3C" if bus == "LIN" else "0x7E0"
        
        if len(data) == 0:
            content = f"[{tag_id}] {note}" if note else f"[{tag_id}] Header (读头轮询)"
            self.log(f"TX -> {content}", tag="TX")
        else:
            # 严格确保报文为完整 8 字节，空余位用 0xFF 补足
            if len(data) < 8:
                data = data.ljust(8, b'\xFF')
            self.log(f"TX -> [{tag_id}] {data.hex(' ').upper()}", tag="TX")

    def _log_rx(self, data: bytes, pid: Optional[int] = None):
        bus = self.var_bus_type.get()
        if pid is not None:
            tag_id = f"0x{pid:02X}" if bus == "LIN" else f"0x{pid:03X}"
        else:
            tag_id = "0x3D" if bus == "LIN" else "0x7E8"
        # 严格确保报文为完整 8 字节，空余位用 0xFF 补足
        if len(data) < 8:
            data = data.ljust(8, b'\xFF')
        self.log(f"RX <- [{tag_id}] {data.hex(' ').upper()}", tag="RX")

    def on_clear_log(self):
        self.txt_log.delete("1.0", tk.END)

    def on_scan_devices(self):
        try:
            handles = Usb2xxxDevice.scan_devices()
            if handles:
                items = [f"设备 #{i} (Handle: {h})" for i, h in enumerate(handles)]
                self.cb_devices["values"] = items
                self.cb_devices.current(0)

                # 探测硬件实际能力与通道
                try:
                    probe_dev = Usb2xxxDevice(0)
                    probe_dev.open()
                    can_chs, lin_chs = probe_dev.probe_channels()
                    self.available_can_channels = can_chs
                    self.available_lin_channels = lin_chs
                    info = probe_dev.get_info()
                    probe_dev.close()

                    self.lbl_hw_badge.configure(text=f"{info.firmware_name} (在线)")
                    self._on_bus_type_changed()
                    self.log(f"扫描成功！识别到设备: {info.firmware_name} (SN: {info.serial_number})")
                    self.log(f"硬件物理通道分布: CAN 通道: {can_chs} | LIN 通道: {lin_chs}")
                except Exception as ex:
                    self.log(f"读取设备功能提示: {ex}")
            else:
                self.cb_devices["values"] = []
                self.cb_devices.set("未发现设备")
                self.lbl_hw_badge.configure(text="未发现设备")
                self.log("未检测到已连接的 Toomoss 适配器，请检查 USB 插线", tag="WARN")
        except Exception as e:
            self.log(f"扫描设备异常: {e}", tag="ERR")

    def on_toggle_connect(self):
        if self.device is not None and self.device.is_opened:
            # 断开
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
            self.interface = None
            self.client = None
            self.btn_connect.configure(text="打开连接", style="Primary.TButton")
            self.cb_channel.configure(state="readonly")
            self.cb_devices.configure(state="readonly")
            self.cb_baudrate.configure(state="readonly")
            self.lbl_conn_indicator.configure(text="● 未连接", foreground="#64748B")
            self.lbl_device_detail.configure(text="适配器状态: 已断开连接")
            self.log("已断开 Toomoss 适配器连接")
        else:
            # 打开
            try:
                sel_idx = self.cb_devices.current()
                if sel_idx < 0:
                    sel_idx = 0
                dev = Usb2xxxDevice(device_index=sel_idx)
                dev.open()

                baudrate = int(self.cb_baudrate.get())
                ch_idx = self.cb_channel.current()
                if ch_idx < 0:
                    ch_idx = 0

                bus_type = self.var_bus_type.get()
                info = dev.get_info()

                if bus_type == "LIN":
                    # 初始化指定的 LIN 通道 (LIN1 或 LIN2)
                    dev.init_lin(channel=ch_idx, baudrate=baudrate, master_mode=1)
                    nad = int(self.entry_addr.get().strip(), 16)
                    addr_cfg = LinUdsAddrConfig(nad=nad)
                    iface = LinUdsInterface(
                        device=dev,
                        channel=ch_idx,
                        addr_cfg=addr_cfg,
                        tx_logger=self._log_tx,
                        rx_logger=self._log_rx,
                    )
                    ch_label = f"LIN{ch_idx + 1}"
                    desc = f"协议: LIN {baudrate}bps 主机模式 | 物理通道: {ch_label} (通道 {ch_idx}) | 从机 NAD: 0x{nad:02X}"
                else:
                    # 初始化指定的 CAN 通道 (CAN1 或 CAN2)
                    dev.init_can(channel=ch_idx, baudrate=baudrate)
                    req_id = int(self.entry_addr.get().strip(), 16)
                    res_id = req_id + 8
                    addr_cfg = CanUdsAddrConfig(req_id=req_id, res_id=res_id)
                    iface = CanUdsInterface(
                        device=dev,
                        channel=ch_idx,
                        addr_cfg=addr_cfg,
                        tx_logger=self._log_tx,
                        rx_logger=self._log_rx,
                    )
                    ch_label = f"CAN{ch_idx + 1}"
                    desc = f"协议: CAN {baudrate}bps 物理寻址 | 物理通道: {ch_label} (通道 {ch_idx}) | ID: 0x{req_id:03X}/0x{res_id:03X}"

                client = UdsClient(iface)

                self.device = dev
                self.interface = iface
                self.client = client

                self.btn_connect.configure(text="断开连接", style="Danger.TButton")
                self.cb_channel.configure(state=tk.DISABLED)
                self.cb_devices.configure(state=tk.DISABLED)
                self.cb_baudrate.configure(state=tk.DISABLED)
                self.lbl_conn_indicator.configure(text=f"● {ch_label} 已就绪", foreground=COLOR_SUCCESS)
                self.lbl_device_detail.configure(text=f"{info.firmware_name} | SN: {info.serial_number} | {desc}")
                self.log(f"成功开启通信: {desc}")
            except Exception as e:
                messagebox.showerror("连接错误", f"无法打开硬件通道:\n{e}")
                self.log(f"连接失败: {e}", tag="ERR")

    def _on_flash_drv_toggle(self):
        state = tk.NORMAL if self.var_use_flash_drv.get() else tk.DISABLED
        self.entry_fdrv_path.configure(state=state)
        self.btn_fdrv_browse.configure(state=state)
        self.entry_fdrv_addr.configure(state=state)

    def on_browse_fdrv(self):
        f = filedialog.askopenfilename(
            title="选择 Flash Driver 驱动文件 (.bin)",
            filetypes=[("Binary Files", "*.bin"), ("All Files", "*.*")],
        )
        if f:
            self._set_fdrv_file(f)

    def _set_fdrv_file(self, path: str):
        self.entry_fdrv_path.delete(0, tk.END)
        self.entry_fdrv_path.insert(0, path)
        try:
            fw = FirmwareImage(path)
            mask = bytes.fromhex(self.entry_mask.get().strip())
            sig = fw.calculate_cmac_signature(mask)
            self.lbl_fdrv_info.configure(text=f"大小: {fw.size} B | CMAC: {sig[:4].hex().upper()}...")
        except Exception as e:
            self.lbl_fdrv_info.configure(text=f"错误: {e}")

    def on_browse_app(self):
        f = filedialog.askopenfilename(
            title="选择 Application 应用程序固件 (.bin / .hex)",
            filetypes=[("Firmware Files", "*.bin;*.hex"), ("Binary Files", "*.bin"), ("Hex Files", "*.hex"), ("All Files", "*.*")],
        )
        if f:
            self._set_app_file(f)

    def _set_app_file(self, path: str):
        self.entry_app_path.delete(0, tk.END)
        self.entry_app_path.insert(0, path)
        try:
            fw = FirmwareImage(path)
            self.lbl_app_info.configure(text=f"大小: {fw.size} B | CRC32: 0x{fw.crc32:08X}")
        except Exception as e:
            self.lbl_app_info.configure(text=f"错误: {e}")

    def on_start_ota(self):
        if not self.client or not self.device or not self.device.is_opened:
            messagebox.showwarning("提示", "请先连接设备通道！")
            return

        app_path = self.entry_app_path.get().strip()
        if not app_path or not os.path.exists(app_path):
            messagebox.showwarning("提示", "请先选择有效的 APP 固件文件！")
            return

        use_fdrv = self.var_use_flash_drv.get()
        fdrv_path = self.entry_fdrv_path.get().strip() if use_fdrv else None
        if use_fdrv and (not fdrv_path or not os.path.exists(fdrv_path)):
            messagebox.showwarning("提示", "已启用 Flash Driver，但未指定有效的 Flash Driver 文件！")
            return

        try:
            mask = bytes.fromhex(self.entry_mask.get().strip())
            fdrv_addr = int(self.entry_fdrv_addr.get().strip(), 16)
            app_addr = int(self.entry_app_addr.get().strip(), 16)
            block_size = int(self.entry_block_size.get().strip())
        except Exception as e:
            messagebox.showerror("参数错误", f"地址或参数格式不合法 (例如 0x08010000):\n{e}")
            return

        cfg = OtaConfig(
            mask=mask,
            flash_drv_path=fdrv_path,
            flash_drv_addr=fdrv_addr,
            app_path=app_path,
            app_addr=app_addr,
            block_size=block_size,
        )

        self.flasher = OtaFlasher(self.client, cfg)
        self.is_flashing = True
        self.btn_start_ota.configure(state=tk.DISABLED)
        self.btn_cancel_ota.configure(state=tk.NORMAL)
        self.progress_bar["value"] = 0

        threading.Thread(target=self._flasher_worker, daemon=True).start()

    def _flasher_worker(self):
        def _prog_cb(pct: float, txt: str):
            self.after(0, lambda: self._update_progress(pct, txt))

        def _log_cb(msg: str):
            self.after(0, lambda: self.log(msg))

        try:
            self.flasher.execute(progress_cb=_prog_cb, log_cb=_log_cb)
            self.after(0, lambda: messagebox.showinfo("升级成功", "OTA 固件升级圆满完成！ECU 已重启运行新应用程序。"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("升级失败", f"刷写中断或出错:\n{e}"))
        finally:
            self.after(0, self._on_flash_finished)

    def _update_progress(self, pct: float, txt: str):
        self.progress_bar["value"] = pct
        self.lbl_status_text.configure(text=txt)

    def _on_flash_finished(self):
        self.is_flashing = False
        self.btn_start_ota.configure(state=tk.NORMAL)
        self.btn_cancel_ota.configure(state=tk.DISABLED)

    def on_cancel_ota(self):
        if self.flasher and self.is_flashing:
            self.flasher.request_cancel()
            self.log("正在请求中止升级...", tag="WARN")

    def on_open_did_reader(self):
        if not self.client:
            messagebox.showwarning("提示", "请先连接设备并开启通信！")
            return
        DidReaderWindow(self, self.client, log_cb=self.log)

    def on_read_sw_ver(self):
        if not self.client:
            messagebox.showwarning("提示", "请先连接设备！")
            return
        threading.Thread(target=self._read_sw_ver_worker, daemon=True).start()

    @staticmethod
    def _clean_did_string(val: bytes) -> str:
        if not val:
            return "[空]"
        # 将字节转为 ASCII 字符，不可打印字符用 '.' 代替
        ascii_chars = [chr(b) if 0x20 <= b <= 0x7E else "." for b in val]
        ascii_str = "".join(ascii_chars)
        hex_str = " ".join(f"{b:02X}" for b in val)

        # 若是 4 字节版本号
        if len(val) == 4 and val[0] < 20:
            ver_str = f"V{val[0]}.{val[1]}.{val[2]}.{val[3]}"
            return f"ASCII: '{ascii_str}' | 版本: {ver_str} (Hex: {hex_str})"

        return f"ASCII: '{ascii_str}' (Hex: {hex_str})"

    def _read_sw_ver_worker(self):
        try:
            self.log("正在读取 ECU 诊断识别信息 (DID 0x0216 / 0xF189 / 0xF187)...")
            results = []

            # 1. 尝试读取 0x0216 (标准软件版本)
            try:
                val_0216 = self.client.read_data_by_id(0x0216)
                disp_0216 = self._clean_did_string(val_0216)
                results.append(f"• 软件版本 (0x0216): {disp_0216}")
                self.log(f"ECU 软件版本 (0x0216): {disp_0216}")
            except Exception as e:
                self.log(f"读取 DID 0x0216 响应: {e}", tag="WARN")

            # 2. 尝试读取 0xF189 (序列号 / 节点序号代码)
            try:
                val_f189 = self.client.read_data_by_id(0xF189)
                disp_f189 = self._clean_did_string(val_f189)
                results.append(f"• 序列编号 (0xF189): {disp_f189}")
                self.log(f"ECU 序列编号 (0xF189): {disp_f189}")
            except Exception as e:
                self.log(f"读取 DID 0xF189 响应: {e}", tag="WARN")

            # 3. 尝试读取 0xF187 (零件号)
            try:
                val_f187 = self.client.read_data_by_id(0xF187)
                disp_f187 = self._clean_did_string(val_f187)
                results.append(f"• 零件编号 (0xF187): {disp_f187}")
                self.log(f"ECU 零件编号 (0xF187): {disp_f187}")
            except Exception:
                pass

            if results:
                info_text = "\n".join(results)
                self.after(0, lambda: messagebox.showinfo("ECU 版本与识别信息", f"成功读取 ECU 识别信息:\n\n{info_text}"))
            else:
                raise RuntimeError("未能从 ECU 读取到有效 DID 响应")

        except Exception as e:
            self.log(f"读取版本异常: {e}", tag="ERR")
            self.after(0, lambda: messagebox.showwarning("读取失败", f"无法获取软件版本:\n{e}"))

    def on_ecu_reset(self):
        if not self.client:
            messagebox.showwarning("提示", "请先连接设备！")
            return
        try:
            self.client.ecu_reset(RESET_HARD)
            self.log("已发送 ECU 硬复位指令 (0x11 01)")
            messagebox.showinfo("提示", "已成功发送 ECU 硬复位指令！")
        except Exception as e:
            self.log(f"发送复位失败: {e}", tag="ERR")

    def on_send_raw_uds(self):
        if not self.interface:
            messagebox.showwarning("提示", "请先连接设备！")
            return
        raw_str = self.entry_raw_uds.get().strip().replace(" ", "")
        try:
            data = bytes.fromhex(raw_str)
        except ValueError:
            messagebox.showerror("格式错误", "请输入有效的十六进制字符串，例如 '10 03' 或 '22 F1 89'")
            return

        threading.Thread(target=lambda: self._send_raw_worker(data), daemon=True).start()

    def _send_raw_worker(self, data: bytes):
        try:
            self.interface.send_request(data)
            resp = self.interface.receive_response(timeout_ms=1000)
            self.log(f"手动测试完成，应答: {resp.hex(' ').upper()}")
        except Exception as e:
            self.log(f"手动发送异常: {e}", tag="ERR")
