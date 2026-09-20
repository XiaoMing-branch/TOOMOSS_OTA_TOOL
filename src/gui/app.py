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


# ------------------- 现代化工业深色调色盘 (Slate Cyber Theme) -------------------
COLOR_BG_ROOT       = "#0D1117"  # 窗口根底色
COLOR_SIDEBAR_BG    = "#161B22"  # 左侧边栏底色
COLOR_PANEL_BG      = "#161B22"  # 主工作区卡片面板底色
COLOR_CARD_INNER    = "#21262D"  # 卡片内嵌子区域
COLOR_BORDER        = "#30363D"  # 描边线
COLOR_INPUT_BG      = "#0D1117"  # 输入框与下拉选择底色

COLOR_TEXT_WHITE    = "#F0F6FC"  # 核心文字
COLOR_TEXT_MUTED    = "#8B949E"  # 次要说明
COLOR_TEXT_DIM      = "#6E7681"  # 标注/暗提示

COLOR_PRIMARY       = "#1F6FEB"  # 科技蓝 (主按键)
COLOR_PRIMARY_HOVER = "#388BFD"
COLOR_SUCCESS       = "#238636"  # 翡翠绿 (连接/启动)
COLOR_SUCCESS_HOVER = "#2EA043"
COLOR_WARNING       = "#D29922"  # 琥珀橙
COLOR_DANGER        = "#DA3633"  # 珊瑚红 (中断)
COLOR_DANGER_HOVER  = "#F85149"

COLOR_LOG_BG        = "#0A0C10"  # 终端控制台黑底
COLOR_LOG_TX        = "#58A6FF"  # 发送青蓝
COLOR_LOG_RX        = "#3FB950"  # 接收明绿
COLOR_LOG_INFO      = "#C9D1D9"  # 打印白
COLOR_LOG_WARN      = "#D29922"  # 警告黄
COLOR_LOG_ERR       = "#F85149"  # 报错红


class OtaApp(tk.Tk):
    """
    Toomoss CAN / LIN UDS OTA 升级上位机 - 现代双栏工业工作台
    """

    def __init__(self):
        super().__init__()
        self.title("Toomoss Automotive Bus UDS OTA Tool - [CAN / LIN 汽车诊断刷写上位机]")
        self.geometry("1180x820")
        self.minsize(1050, 720)
        self.configure(bg=COLOR_BG_ROOT)

        # 通信句柄
        self.device: Optional[Usb2xxxDevice] = None
        self.interface = None
        self.client: Optional[UdsClient] = None
        self.flasher: Optional[OtaFlasher] = None
        self.is_flashing = False

        # 硬件探测到的物理通道
        self.available_can_channels: List[str] = ["CAN1 (通道 0)", "CAN2 (通道 1)"]
        self.available_lin_channels: List[str] = ["LIN1 (通道 0)", "LIN2 (通道 1)"]

        self._init_theme_styles()
        self._build_ui()
        self._load_default_config()

        # 启动后异步扫描硬件
        self.after(200, self.on_scan_devices)

    def _init_theme_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", background=COLOR_BG_ROOT, foreground=COLOR_TEXT_WHITE, font=("Segoe UI", 9))
        style.configure("TFrame", background=COLOR_BG_ROOT)

        # 侧边栏与卡片面板
        style.configure("Sidebar.TFrame", background=COLOR_SIDEBAR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL_BG, relief="solid", borderwidth=1)
        style.configure("Inner.TFrame", background=COLOR_CARD_INNER)
        style.configure("Transparent.TFrame", background=COLOR_PANEL_BG)

        # 标签分类
        style.configure("TLabel", background=COLOR_PANEL_BG, foreground=COLOR_TEXT_WHITE)
        style.configure("Sidebar.TLabel", background=COLOR_SIDEBAR_BG, foreground=COLOR_TEXT_WHITE)
        style.configure("SidebarMuted.TLabel", background=COLOR_SIDEBAR_BG, foreground=COLOR_TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("SidebarTitle.TLabel", background=COLOR_SIDEBAR_BG, foreground="#58A6FF", font=("Segoe UI", 10, "bold"))
        style.configure("PanelTitle.TLabel", background=COLOR_PANEL_BG, foreground="#58A6FF", font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", background=COLOR_PANEL_BG, foreground=COLOR_TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("StatusBadge.TLabel", background=COLOR_CARD_INNER, foreground="#58A6FF", font=("Segoe UI", 8, "bold"), padding=(6, 2))

        # 按钮样式定义
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 9, "bold"),
            background=COLOR_PRIMARY,
            foreground="#FFFFFF",
            padding=(10, 4),
            relief="flat",
        )
        style.map("Primary.TButton", background=[("active", COLOR_PRIMARY_HOVER), ("disabled", "#21262D")])

        style.configure(
            "Success.TButton",
            font=("Segoe UI", 10, "bold"),
            background=COLOR_SUCCESS,
            foreground="#FFFFFF",
            padding=(16, 7),
            relief="flat",
        )
        style.map("Success.TButton", background=[("active", COLOR_SUCCESS_HOVER), ("disabled", "#21262D")])

        style.configure(
            "Danger.TButton",
            font=("Segoe UI", 9, "bold"),
            background=COLOR_DANGER,
            foreground="#FFFFFF",
            padding=(12, 5),
            relief="flat",
        )
        style.map("Danger.TButton", background=[("active", COLOR_DANGER_HOVER), ("disabled", "#21262D")])

        style.configure(
            "Secondary.TButton",
            font=("Segoe UI", 9),
            background="#30363D",
            foreground=COLOR_TEXT_WHITE,
            padding=(8, 4),
            relief="flat",
        )
        style.map("Secondary.TButton", background=[("active", "#3C444D"), ("disabled", "#21262D")])

        # 输入与选择控件
        style.configure("TCombobox", fieldbackground=COLOR_INPUT_BG, background="#30363D", foreground=COLOR_TEXT_WHITE)
        style.map("TCombobox", fieldbackground=[("readonly", COLOR_INPUT_BG)], foreground=[("readonly", COLOR_TEXT_WHITE)])

        style.configure("TEntry", fieldbackground=COLOR_INPUT_BG, foreground=COLOR_TEXT_WHITE, insertcolor="#FFFFFF")
        style.configure("TCheckbutton", background=COLOR_PANEL_BG, foreground=COLOR_TEXT_WHITE)
        style.map("TCheckbutton", background=[("active", COLOR_PANEL_BG)])

        style.configure("Sidebar.TRadiobutton", background=COLOR_SIDEBAR_BG, foreground=COLOR_TEXT_WHITE)
        style.map("Sidebar.TRadiobutton", background=[("active", COLOR_SIDEBAR_BG)])

        # 进度条
        style.configure("Horizontal.TProgressbar", background="#1F6FEB", troughcolor=COLOR_INPUT_BG, bordercolor=COLOR_BORDER)

    def _build_ui(self):
        # 整体采用左右双栏布局
        root_container = ttk.Frame(self, padding=8)
        root_container.pack(fill=tk.BOTH, expand=True)

        # =========================================================================
        # 1. 左侧边栏 (Sidebar): 硬件设备、总线通道与节点连接管理
        # =========================================================================
        sidebar = ttk.Frame(root_container, style="Sidebar.TFrame", width=290, padding=12)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        sidebar.pack_propagate(False)

        # 品牌与系统标题
        brand_box = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand_box.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(brand_box, text="⚡ TOOMOSS OTA", style="SidebarTitle.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(brand_box, text="UDS on CAN/LIN 汽车刷写工具", style="SidebarMuted.TLabel").pack(anchor=tk.W)

        ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # 通信总线协议类型
        ttk.Label(sidebar, text="总线协议类型", style="SidebarTitle.TLabel").pack(anchor=tk.W, pady=(4, 4))
        proto_box = ttk.Frame(sidebar, style="Sidebar.TFrame")
        proto_box.pack(fill=tk.X, pady=(0, 8))
        self.var_bus_type = tk.StringVar(value="LIN")
        ttk.Radiobutton(proto_box, text="LIN", value="LIN", variable=self.var_bus_type, style="Sidebar.TRadiobutton", command=self._on_bus_type_changed).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(proto_box, text="CAN", value="CAN", variable=self.var_bus_type, style="Sidebar.TRadiobutton", command=self._on_bus_type_changed).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(proto_box, text="仿真 (Mock)", value="MOCK", variable=self.var_bus_type, style="Sidebar.TRadiobutton", command=self._on_bus_type_changed).pack(side=tk.LEFT)

        # 适配器设备探测
        ttk.Label(sidebar, text="USB 适配器设备", style="SidebarMuted.TLabel").pack(anchor=tk.W, pady=(4, 2))
        dev_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        dev_row.pack(fill=tk.X, pady=(0, 6))
        self.cb_devices = ttk.Combobox(dev_row, state="readonly", width=18)
        self.cb_devices.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        btn_scan = ttk.Button(dev_row, text="扫描", style="Secondary.TButton", width=5, command=self.on_scan_devices)
        btn_scan.pack(side=tk.LEFT)

        # 物理通道选择
        self.lbl_channel_title = ttk.Label(sidebar, text="物理通道", style="SidebarMuted.TLabel")
        self.lbl_channel_title.pack(anchor=tk.W, pady=(4, 2))
        self.cb_channel = ttk.Combobox(sidebar, state="readonly")
        self.cb_channel.pack(fill=tk.X, pady=(0, 6))

        # 波特率设置
        ttk.Label(sidebar, text="通讯波特率 (Baudrate)", style="SidebarMuted.TLabel").pack(anchor=tk.W, pady=(4, 2))
        self.cb_baudrate = ttk.Combobox(sidebar, state="readonly")
        self.cb_baudrate.pack(fill=tk.X, pady=(0, 6))

        # 节点寻址
        self.lbl_addr_title = ttk.Label(sidebar, text="从机 NAD 诊断地址", style="SidebarMuted.TLabel")
        self.lbl_addr_title.pack(anchor=tk.W, pady=(4, 2))
        self.entry_addr = ttk.Entry(sidebar)
        self.entry_addr.insert(0, "0x68")
        self.entry_addr.pack(fill=tk.X, pady=(0, 10))

        # 连接开关与状态
        self.btn_connect = ttk.Button(sidebar, text="打开设备连接", style="Primary.TButton", command=self.on_toggle_connect)
        self.btn_connect.pack(fill=tk.X, pady=(4, 6))

        self.lbl_conn_indicator = ttk.Label(sidebar, text="● 未连接", foreground="#6E7681", style="Sidebar.TLabel", font=("Segoe UI", 9, "bold"))
        self.lbl_conn_indicator.pack(anchor=tk.CENTER, pady=(2, 10))

        ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # 状态概览看板
        ttk.Label(sidebar, text="适配器与通道状态", style="SidebarTitle.TLabel").pack(anchor=tk.W, pady=(4, 2))
        self.lbl_hw_badge = ttk.Label(sidebar, text="设备: 尚未连接", style="SidebarMuted.TLabel")
        self.lbl_hw_badge.pack(anchor=tk.W, pady=1)
        self.lbl_device_detail = ttk.Label(sidebar, text="请先在上方点击 [扫描] 并连接", style="SidebarMuted.TLabel", wraplength=260)
        self.lbl_device_detail.pack(anchor=tk.W, pady=2)

        # 快捷测试操作区 (底部固定)
        quick_box = ttk.Frame(sidebar, style="Sidebar.TFrame")
        quick_box.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        ttk.Label(quick_box, text="诊断快捷工具", style="SidebarTitle.TLabel").pack(anchor=tk.W, pady=(0, 6))
        ttk.Button(quick_box, text="🔍 规范 DID 诊断读取 (0x22)", style="Secondary.TButton", command=self.on_open_did_reader).pack(fill=tk.X, pady=2)
        ttk.Button(quick_box, text="🔄 ECU 硬复位 (0x11 01)", style="Secondary.TButton", command=self.on_ecu_reset).pack(fill=tk.X, pady=2)

        # =========================================================================
        # 2. 右侧主工作区 (Main Content Area): 固件配置、刷写状态机、报文监视
        # =========================================================================
        main_workspace = ttk.Frame(root_container, style="TFrame")
        main_workspace.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # ------------------ 卡片 A: 固件与安全密钥参数 ------------------
        card_fw = ttk.Frame(main_workspace, style="Panel.TFrame", padding=12)
        card_fw.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(card_fw, text="1. 固件两阶段烧录配置 (严谨顺序：Flash Driver 驱动 -> APP 应用程序)", style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 6))

        # 阶段一：Flash Driver
        fdrv_row = ttk.Frame(card_fw, style="Transparent.TFrame")
        fdrv_row.pack(fill=tk.X, pady=2)

        self.var_use_flash_drv = tk.BooleanVar(value=True)
        self.chk_flash_drv = ttk.Checkbutton(fdrv_row, text="【阶段一】下载 Flash Driver (写入 RAM 并验签)", variable=self.var_use_flash_drv, command=self._on_flash_drv_toggle)
        self.chk_flash_drv.pack(side=tk.LEFT)

        ttk.Label(fdrv_row, text="RAM 基址:").pack(side=tk.LEFT, padx=(12, 4))
        self.entry_fdrv_addr = ttk.Entry(fdrv_row, width=12)
        self.entry_fdrv_addr.insert(0, "0x20008000")
        self.entry_fdrv_addr.pack(side=tk.LEFT, padx=(0, 8))

        self.lbl_fdrv_info = ttk.Label(fdrv_row, text="大小: 0 B | 签名: 待加载", style="Muted.TLabel")
        self.lbl_fdrv_info.pack(side=tk.LEFT)

        fdrv_file_row = ttk.Frame(card_fw, style="Transparent.TFrame")
        fdrv_file_row.pack(fill=tk.X, pady=(2, 6))
        ttk.Label(fdrv_file_row, text="Driver 路径:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_fdrv_path = ttk.Entry(fdrv_file_row)
        self.entry_fdrv_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_fdrv_browse = ttk.Button(fdrv_file_row, text="选择 Driver...", style="Secondary.TButton", command=self.on_browse_fdrv)
        self.btn_fdrv_browse.pack(side=tk.LEFT)

        ttk.Separator(card_fw, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=4)

        # 阶段二：App 固件
        app_row = ttk.Frame(card_fw, style="Transparent.TFrame")
        app_row.pack(fill=tk.X, pady=2)
        ttk.Label(app_row, text="【阶段二】下载 Application 固件 (擦除片上 Flash 并烧录)", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        ttk.Label(app_row, text="Flash 基址:").pack(side=tk.LEFT, padx=(12, 4))
        self.entry_app_addr = ttk.Entry(app_row, width=12)
        self.entry_app_addr.insert(0, "0x08010000")
        self.entry_app_addr.pack(side=tk.LEFT, padx=(0, 8))

        self.lbl_app_info = ttk.Label(app_row, text="大小: 0 B | CRC32: 0x00000000", style="Muted.TLabel")
        self.lbl_app_info.pack(side=tk.LEFT)

        app_file_row = ttk.Frame(card_fw, style="Transparent.TFrame")
        app_file_row.pack(fill=tk.X, pady=(2, 6))
        ttk.Label(app_file_row, text="APP 路径:").pack(side=tk.LEFT, padx=(0, 16))
        self.entry_app_path = ttk.Entry(app_file_row)
        self.entry_app_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_app_browse = ttk.Button(app_file_row, text="选择 APP...", style="Secondary.TButton", command=self.on_browse_app)
        self.btn_app_browse.pack(side=tk.LEFT)

        # 协议高级参数：AES 密钥与 BlockSize
        sec_row = ttk.Frame(card_fw, style="Transparent.TFrame")
        sec_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(sec_row, text="AES-128 Mask:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_mask = ttk.Entry(sec_row, width=32)
        self.entry_mask.insert(0, "2b7e151628aed2a6abf7158809cf4f3c")
        self.entry_mask.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(sec_row, text="单块 BlockSize:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_block_size = ttk.Entry(sec_row, width=6)
        self.entry_block_size.insert(0, "60")
        self.entry_block_size.pack(side=tk.LEFT)

        # ------------------ 卡片 B: OTA 升级控制与仪表 ------------------
        card_ctrl = ttk.Frame(main_workspace, style="Panel.TFrame", padding=12)
        card_ctrl.pack(fill=tk.X, pady=(0, 8))

        ctrl_title_row = ttk.Frame(card_ctrl, style="Transparent.TFrame")
        ctrl_title_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(ctrl_title_row, text="2. 升级流程执行与状态仪表", style="PanelTitle.TLabel").pack(side=tk.LEFT)
        self.lbl_progress_badge = ttk.Label(ctrl_title_row, text="0.0 %", style="StatusBadge.TLabel")
        self.lbl_progress_badge.pack(side=tk.RIGHT)

        self.progress_bar = ttk.Progressbar(card_ctrl, orient=tk.HORIZONTAL, mode="determinate", style="Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(2, 6))

        action_row = ttk.Frame(card_ctrl, style="Transparent.TFrame")
        action_row.pack(fill=tk.X)
        self.lbl_status_text = ttk.Label(action_row, text="系统已就绪。确认参数后点击 [🚀 开始 OTA 刷写]", font=("Segoe UI", 9, "bold"))
        self.lbl_status_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_cancel_ota = ttk.Button(action_row, text="⏹ 中止", style="Danger.TButton", command=self.on_cancel_ota, state=tk.DISABLED)
        self.btn_cancel_ota.pack(side=tk.RIGHT, padx=(6, 0))

        self.btn_start_ota = ttk.Button(action_row, text="🚀 开始 OTA 刷写", style="Success.TButton", command=self.on_start_ota)
        self.btn_start_ota.pack(side=tk.RIGHT)

        # ------------------ 卡片 C: 诊断总线实时监视器 (终端级) ------------------
        card_log = ttk.Frame(main_workspace, style="Panel.TFrame", padding=10)
        card_log.pack(fill=tk.BOTH, expand=True)

        log_head = ttk.Frame(card_log, style="Transparent.TFrame")
        log_head.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(log_head, text="3. 诊断总线实时监视器 (UDS 报文抓包追踪)", style="PanelTitle.TLabel").pack(side=tk.LEFT)

        self.var_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_head, text="自动滚屏", variable=self.var_autoscroll).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(log_head, text="清空日志", style="Secondary.TButton", command=self.on_clear_log).pack(side=tk.RIGHT)

        # 文本框与双向滚动
        text_container = tk.Frame(card_log, bg=COLOR_LOG_BG)
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

        # 终端底部：手动快速发送调试条
        manual_bar = ttk.Frame(card_log, style="Transparent.TFrame")
        manual_bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(manual_bar, text="手动发送十六进制 UDS 指令:").pack(side=tk.LEFT, padx=(0, 6))
        self.entry_raw_uds = ttk.Entry(manual_bar, width=28)
        self.entry_raw_uds.insert(0, "10 03")
        self.entry_raw_uds.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(manual_bar, text="发送", style="Secondary.TButton", command=self.on_send_raw_uds).pack(side=tk.LEFT)

        # 初始化协议总线选择
        self._on_bus_type_changed()

    def _on_bus_type_changed(self):
        bus = self.var_bus_type.get()
        if bus == "LIN":
            self.lbl_channel_title.configure(text="LIN 物理通道")
            self.cb_channel["values"] = self.available_lin_channels
            self.cb_channel.current(0)

            self.cb_baudrate["values"] = ["9600", "19200", "20000"]
            self.cb_baudrate.set("19200")

            self.lbl_addr_title.configure(text="从机 NAD 诊断地址")
            self.entry_addr.delete(0, tk.END)
            self.entry_addr.insert(0, "0x68")
            if hasattr(self, "entry_block_size"):
                self.entry_block_size.delete(0, tk.END)
                self.entry_block_size.insert(0, "60")
        elif bus == "CAN":
            self.lbl_channel_title.configure(text="CAN 物理通道")
            self.cb_channel["values"] = self.available_can_channels
            self.cb_channel.current(0)

            self.cb_baudrate["values"] = ["250000", "500000", "1000000"]
            self.cb_baudrate.set("500000")

            self.lbl_addr_title.configure(text="CAN 请求 ID")
            self.entry_addr.delete(0, tk.END)
            self.entry_addr.insert(0, "0x7E0")
            if hasattr(self, "entry_block_size"):
                self.entry_block_size.delete(0, tk.END)
                self.entry_block_size.insert(0, "256")
        else:  # MOCK 虚拟仿真模式
            self.lbl_channel_title.configure(text="虚拟 ECU 仿真模式")
            self.cb_channel["values"] = ["内置虚拟 ECU (免硬件)"]
            self.cb_channel.current(0)

            self.cb_baudrate["values"] = ["内存直通"]
            self.cb_baudrate.set("内存直通")

            self.lbl_addr_title.configure(text="虚拟节点地址")
            self.entry_addr.delete(0, tk.END)
            self.entry_addr.insert(0, "0x01 / 0x7E0")
            if hasattr(self, "entry_block_size"):
                self.entry_block_size.delete(0, tk.END)
                self.entry_block_size.insert(0, "256")

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
            content = f"[{tag_id}] {note}" if note else f"[{tag_id}] Header (轮询应答)"
            self.log(f"TX -> {content}", tag="TX")
        else:
            if len(data) < 8:
                data = data.ljust(8, b'\xFF')
            self.log(f"TX -> [{tag_id}] {data.hex(' ').upper()}", tag="TX")

    def _log_rx(self, data: bytes, pid: Optional[int] = None):
        bus = self.var_bus_type.get()
        if pid is not None:
            tag_id = f"0x{pid:02X}" if bus == "LIN" else f"0x{pid:03X}"
        else:
            tag_id = "0x3D" if bus == "LIN" else "0x7E8"
        if len(data) < 8:
            data = data.ljust(8, b'\xFF')
        self.log(f"RX <- [{tag_id}] {data.hex(' ').upper()}", tag="RX")

    def on_clear_log(self):
        self.txt_log.delete("1.0", tk.END)

    def on_scan_devices(self):
        try:
            handles = Usb2xxxDevice.scan_devices()
            if handles:
                items = [f"设备 #{i} (句柄: {h})" for i, h in enumerate(handles)]
                self.cb_devices["values"] = items
                self.cb_devices.current(0)

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
                    self.log(f"硬件扫描成功: {info.firmware_name} (SN: {info.serial_number})")
                except Exception as ex:
                    self.log(f"读取设备功能提示: {ex}")
            else:
                self.cb_devices["values"] = []
                self.cb_devices.set("未发现设备")
                self.lbl_hw_badge.configure(text="设备: 未检测到适配器")
                self.log("未检测到已连接的 Toomoss 适配器，请检查 USB 连接", tag="WARN")
        except Exception as e:
            self.log(f"扫描设备异常: {e}", tag="ERR")

    def on_toggle_connect(self):
        if self.client is not None:
            if self.device is not None and self.device.is_opened:
                try:
                    self.device.close()
                except Exception:
                    pass
            self.device = None
            self.interface = None
            self.client = None
            self.btn_connect.configure(text="打开设备连接", style="Primary.TButton")
            self.cb_channel.configure(state="readonly")
            self.cb_devices.configure(state="readonly")
            self.cb_baudrate.configure(state="readonly")
            self.lbl_conn_indicator.configure(text="● 未连接", foreground="#6E7681")
            self.lbl_device_detail.configure(text="适配器状态: 已安全断开")
            self.log("已断开通信连接")
        else:
            try:
                bus_type = self.var_bus_type.get()
                if bus_type == "MOCK":
                    from ..uds.mock_simulator import MockUdsSimulator
                    mask_str = self.entry_mask.get().strip()
                    mask = bytes.fromhex(mask_str) if mask_str else bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
                    iface = MockUdsSimulator(
                        mask=mask,
                        mask_fbl=mask,
                        tx_logger=self._log_tx,
                        rx_logger=self._log_rx,
                        bus_type="CAN",
                    )
                    client = UdsClient(iface)
                    self.device = None
                    self.interface = iface
                    self.client = client

                    self.btn_connect.configure(text="断开连接", style="Danger.TButton")
                    self.cb_channel.configure(state=tk.DISABLED)
                    self.cb_devices.configure(state=tk.DISABLED)
                    self.cb_baudrate.configure(state=tk.DISABLED)
                    self.lbl_conn_indicator.configure(text="● 虚拟 ECU 就绪 (免硬件)", foreground=COLOR_SUCCESS)
                    self.lbl_device_detail.configure(text="运行模式: 虚拟 ECU 内存仿真\n支持完整两阶段 OTA 升级与抓包")
                    self.log("成功激活【虚拟 ECU 仿真模式】！无需物理硬件即可进行全流程诊断与 OTA 验证。", tag="INFO")
                    return

                sel_idx = self.cb_devices.current()
                if sel_idx < 0:
                    sel_idx = 0
                dev = Usb2xxxDevice(device_index=sel_idx)
                dev.open()

                baudrate = int(self.cb_baudrate.get())
                ch_idx = self.cb_channel.current()
                if ch_idx < 0:
                    ch_idx = 0

                info = dev.get_info()

                if bus_type == "LIN":
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
                    desc = f"LIN {baudrate}bps 主机 | 通道: {ch_label} | NAD: 0x{nad:02X}"
                else:
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
                    desc = f"CAN {baudrate}bps | 通道: {ch_label} | ID: 0x{req_id:03X}/0x{res_id:03X}"

                client = UdsClient(iface)

                self.device = dev
                self.interface = iface
                self.client = client

                self.btn_connect.configure(text="断开连接", style="Danger.TButton")
                self.cb_channel.configure(state=tk.DISABLED)
                self.cb_devices.configure(state=tk.DISABLED)
                self.cb_baudrate.configure(state=tk.DISABLED)
                self.lbl_conn_indicator.configure(text=f"● {ch_label} 在线就绪", foreground=COLOR_SUCCESS)
                self.lbl_device_detail.configure(text=f"{info.firmware_name}\nSN: {info.serial_number}\n{desc}")
                self.log(f"成功打开通信通道: {desc}")
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
        if not self.client or (self.var_bus_type.get() != "MOCK" and (not self.device or not self.device.is_opened)):
            messagebox.showwarning("提示", "请先打开设备通道连接！")
            return

        app_path = self.entry_app_path.get().strip()
        if not app_path or not os.path.exists(app_path):
            messagebox.showwarning("提示", "请先选择有效的 APP 固件文件！")
            return

        use_fdrv = self.var_use_flash_drv.get()
        fdrv_path = self.entry_fdrv_path.get().strip() if use_fdrv else None
        if use_fdrv and (not fdrv_path or not os.path.exists(fdrv_path)):
            messagebox.showwarning("提示", "已启用 Flash Driver，但未指定有效的驱动文件！")
            return

        try:
            mask = bytes.fromhex(self.entry_mask.get().strip())
            fdrv_addr = int(self.entry_fdrv_addr.get().strip(), 16)
            app_addr = int(self.entry_app_addr.get().strip(), 16)
            block_size = int(self.entry_block_size.get().strip())
        except Exception as e:
            messagebox.showerror("参数错误", f"基地址或参数格式不合法 (例如 0x08010000):\n{e}")
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
        """
        后台异步刷写工作线程。
        
        【线程安全设计说明】：
        Tkinter 基于单线程事件循环模型，跨线程直接操作 UI 控件存在竞态甚至导致解释器崩溃。
        此处严格通过 self.after(0, ...) 将进度刷新、日志记录与弹窗调度回派发到主线程消息队列安全执行。
        """
        def _prog_cb(pct: float, txt: str):
            self.after(0, lambda: self._update_progress(pct, txt))

        def _log_cb(msg: str):
            self.after(0, lambda: self.log(msg))

        try:
            self.flasher.execute(progress_cb=_prog_cb, log_cb=_log_cb)
            self.after(0, lambda: messagebox.showinfo("升级成功", "🎉 OTA 固件升级圆满完成！ECU 已重启运行新应用程序。"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("升级失败", f"刷写中断或出错:\n{e}"))
        finally:
            self.after(0, self._on_flash_finished)

    def _update_progress(self, pct: float, txt: str):
        self.progress_bar["value"] = pct
        self.lbl_status_text.configure(text=txt)
        self.lbl_progress_badge.configure(text=f"{pct:5.1f} %")

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
            self.log(f"手动指令完成，应答: {resp.hex(' ').upper()}")
        except Exception as e:
            self.log(f"手动发送异常: {e}", tag="ERR")
