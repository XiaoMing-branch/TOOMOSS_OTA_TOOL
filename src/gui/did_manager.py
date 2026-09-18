import os
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable

from ..uds.client import UdsClient, UdsNegativeResponseError


@dataclass
class DidDefinition:
    did: int
    iso_name: str
    name_cn: str
    desc: str
    selected: bool = True
    status: str = "未读取"
    parsed_val: str = ""
    ascii_val: str = ""
    hex_val: str = ""


# 国际标准 ISO 14229-1 / 赛力斯 Q/SK J02.321 核心规范 DID 列表
STANDARD_DIDS: List[DidDefinition] = [
    DidDefinition(0xF180, "bootSoftwareIdentificationDataIdentifier", "Bootloader 软件版本", "Bootloader 固件版本号代码"),
    DidDefinition(0xF186, "activeDiagnosticSessionDataIdentifier", "当前处于的诊断会话", "当前处于的 UDS 诊断会话模式"),
    DidDefinition(0xF187, "vehicleManufacturerSparePartNumber", "整车厂备件号/零件号", "主机厂汽车零部件图号/代码"),
    DidDefinition(0xF188, "vehicleManufacturerECUSoftwareNumber", "整车厂 ECU 软件号", "整车厂软件发布零件号"),
    DidDefinition(0xF189, "vehicleManufacturerECUSoftwareVersionNumber", "整车厂软件版本号", "整车厂软件系统版本号代码"),
    DidDefinition(0xF18A, "systemSupplierIdentifier", "供应商代码", "ECU 控制器一级供应商识别代码"),
    DidDefinition(0xF18C, "ECUSerialNumber", "ECU 出厂序列号", "ECU 物理硬件出厂唯一序列号"),
    DidDefinition(0xF190, "VINDataIdentifier", "车辆识别码 (VIN 码)", "17 位标准车辆底盘识别号 VIN"),
    DidDefinition(0xF194, "systemSupplierECUSoftwareNumber", "供应商软件编号", "控制器系统供应商软件代号"),
    DidDefinition(0xF195, "systemSupplierECUSoftwareVersionNumber", "供应商软件版本号", "系统供应商软件内部版本号"),
    DidDefinition(0xF197, "systemNameOrEngineType", "系统名称/安装位置", "ECU 控制器系统名称或动力总成位置"),
    # 常用工程扩展
    DidDefinition(0x0216, "applicationSoftwareVersionNumber", "应用软件版本号 (APP)", "片上运行的应用程序主版本号"),
    DidDefinition(0xF0F0, "activeRunningPartitionDataIdentifier", "当前运行分区 (A/B)", "MCU 当前运行的固件分区代码"),
]


def format_bytes_to_ascii(raw_bytes: bytes) -> str:
    """
    将原始字节数据转换为 ASCII 字符串表示:
    - 可打印字符 (0x20 - 0x7E) 直接转为字符
    - 不可打印字符 (如 0x00, 0xFF) 转为点号 '.'，避免截断与乱码
    - 若全为不可打印字符，返回空标记
    """
    if not raw_bytes:
        return ""
    ascii_chars = [chr(b) if 0x20 <= b <= 0x7E else "." for b in raw_bytes]
    return "".join(ascii_chars)


def parse_did_payload(did: int, raw_bytes: bytes) -> tuple[str, str]:
    """
    智能解析各类车规级 DID 报文。
    返回: (parsed_display_text, ascii_text)
    """
    if not raw_bytes:
        return "[空数据]", ""

    ascii_str = format_bytes_to_ascii(raw_bytes)

    # 1. 针对 0xF186 当前诊断会话
    if did == 0xF186:
        session_map = {
            1: "01 (默认会话 Default Session)",
            2: "02 (编程会话 Programming Session)",
            3: "03 (扩展会话 Extended Session)",
            4: "04 (安全系统会话 Safety Session)",
        }
        b = raw_bytes[0]
        return session_map.get(b, f"0x{b:02X} (自定义会话)"), ascii_str

    # 2. 针对 0xF190 VIN 码 (17 字节 ASCII)
    if did == 0xF190:
        try:
            vin = "".join(chr(b) for b in raw_bytes if 0x20 <= b <= 0x7E).strip()
            if len(vin) >= 5:
                return f"{vin} [VIN码]", vin
        except Exception:
            pass

    # 3. 针对纯 ASCII 字符串 (如零件号 "ALI"、系统名 "ECU"、分区 "A")
    printable = [b for b in raw_bytes if 0x20 <= b <= 0x7E]
    if len(printable) == len(raw_bytes) and len(raw_bytes) >= 1:
        try:
            pure_ascii = raw_bytes.decode("ascii").strip()
            if pure_ascii:
                return f"\"{pure_ascii}\"", pure_ascii
        except Exception:
            pass

    # 4. 针对 4 字节点分版本号 (如 01 00 00 00 -> V1.0.0.0)
    if len(raw_bytes) == 4 and raw_bytes[0] < 20:
        dot_ver = f"V{raw_bytes[0]}.{raw_bytes[1]}.{raw_bytes[2]}.{raw_bytes[3]}"
        return dot_ver, ascii_str

    # 5. 混合 ASCII (包含部分可打印字符，如至少2个有效字符)
    valid_chars = [chr(b) for b in raw_bytes if 0x20 <= b <= 0x7E]
    if len(valid_chars) >= 2:
        clean_text = "".join(valid_chars).strip()
        return f"\"{clean_text}\" (文本)", ascii_str

    # 6. 通用十六进制展示
    hex_repr = " ".join(f"{b:02X}" for b in raw_bytes)
    return hex_repr, ascii_str


class DidReaderWindow(tk.Toplevel):
    """
    国际规范 UDS DID (0x22) 交互式读取与管理窗口
    支持单选/全选勾选读取、实时进度反馈、结构化表格呈现、报告导出与剪贴板复制
    """

    def __init__(self, parent, client: UdsClient, log_cb: Optional[Callable[[str, str], None]] = None):
        super().__init__(parent)
        self.title("ECU 国际标准规范 DID 诊断读取器 (ISO 14229-1 / 0x22)")
        self.geometry("980x620")
        self.minsize(800, 500)
        self.configure(bg="#12131C")

        self.client = client
        self.log_cb = log_cb
        self.is_reading = False
        self._cancel_requested = False

        # 深复制一份 DID 列表供本次操作使用
        self.dids: List[DidDefinition] = [
            DidDefinition(
                item.did, item.iso_name, item.name_cn, item.desc, item.selected, item.status, item.parsed_val, item.ascii_val, item.hex_val
            )
            for item in STANDARD_DIDS
        ]

        self._build_ui()
        self._populate_tree()

    def _build_ui(self):
        # 顶部说明卡片
        header_frame = tk.Frame(self, bg="#1A1D2B", padx=14, pady=10)
        header_frame.pack(fill=tk.X, padx=10, pady=(10, 6))

        title_lbl = tk.Label(
            header_frame,
            text="🔍 ECU 国际标准规范 DID 批量与可选读取器",
            font=("Segoe UI", 12, "bold"),
            bg="#1A1D2B",
            fg="#F1F5F9",
        )
        title_lbl.pack(anchor=tk.W)

        desc_lbl = tk.Label(
            header_frame,
            text="涵盖 ISO 14229-1 (UDS) / 赛力斯 Q/SK J02.321-2025 规范要求的整车基础信息、版本控制、序列号及 VIN 码等核心 DID。\n勾选您想要读取的项目，点击 [🚀 一键读取选中项] 开始自动轮询收发。",
            font=("Segoe UI", 9),
            bg="#1A1D2B",
            fg="#94A3B8",
            justify=tk.LEFT,
        )
        desc_lbl.pack(anchor=tk.W, pady=(4, 0))

        # 控制操作栏
        ctrl_frame = tk.Frame(self, bg="#1A1D2B", padx=14, pady=8)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=(0, 6))

        btn_select_all = tk.Button(
            ctrl_frame,
            text="☑ 全选",
            font=("Segoe UI", 9),
            bg="#2B2F44",
            fg="#F1F5F9",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.on_select_all,
        )
        btn_select_all.pack(side=tk.LEFT, padx=(0, 6))

        btn_deselect_all = tk.Button(
            ctrl_frame,
            text="☐ 全不选",
            font=("Segoe UI", 9),
            bg="#2B2F44",
            fg="#F1F5F9",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.on_deselect_all,
        )
        btn_deselect_all.pack(side=tk.LEFT, padx=(0, 6))

        btn_toggle = tk.Button(
            ctrl_frame,
            text="🗘 反选",
            font=("Segoe UI", 9),
            bg="#2B2F44",
            fg="#F1F5F9",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.on_toggle_select,
        )
        btn_toggle.pack(side=tk.LEFT, padx=(0, 15))

        self.btn_start_read = tk.Button(
            ctrl_frame,
            text="🚀 一键读取选中项",
            font=("Segoe UI", 9, "bold"),
            bg="#10B981",
            fg="#FFFFFF",
            activebackground="#059669",
            relief="flat",
            padx=16,
            pady=4,
            cursor="hand2",
            command=self.on_start_read,
        )
        self.btn_start_read.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_copy = tk.Button(
            ctrl_frame,
            text="📋 复制全部结果",
            font=("Segoe UI", 9),
            bg="#0284C7",
            fg="#FFFFFF",
            activebackground="#0369A1",
            relief="flat",
            padx=12,
            pady=4,
            cursor="hand2",
            command=self.on_copy_results,
        )
        self.btn_copy.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_export = tk.Button(
            ctrl_frame,
            text="💾 导出报告...",
            font=("Segoe UI", 9),
            bg="#2B2F44",
            fg="#F1F5F9",
            relief="flat",
            padx=12,
            pady=4,
            cursor="hand2",
            command=self.on_export_results,
        )
        self.btn_export.pack(side=tk.LEFT)

        self.lbl_progress_status = tk.Label(
            ctrl_frame, text="待命", font=("Segoe UI", 9), bg="#1A1D2B", fg="#94A3B8"
        )
        self.lbl_progress_status.pack(side=tk.RIGHT, padx=(10, 0))

        # 中间数据表格区 - 显式适配深色高对比度主题样式
        table_frame = tk.Frame(self, bg="#12131C")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        style = ttk.Style(self)
        style.configure(
            "DidTree.Treeview",
            background="#161B22",
            foreground="#F0F6FC",
            fieldbackground="#161B22",
            font=("Segoe UI", 9),
            rowheight=26,
            borderwidth=0,
        )
        style.configure(
            "DidTree.Treeview.Heading",
            background="#21262D",
            foreground="#58A6FF",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        style.map(
            "DidTree.Treeview",
            background=[("selected", "#1F6FEB")],
            foreground=[("selected", "#FFFFFF")],
        )
        style.map(
            "DidTree.Treeview.Heading",
            background=[("active", "#30363D")],
            foreground=[("active", "#79C0FF")],
        )

        columns = ("select", "did", "iso_name", "name_cn", "ascii", "parsed", "hex", "status")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="DidTree.Treeview",
        )

        self.tree.heading("select", text="选择", anchor=tk.CENTER)
        self.tree.heading("did", text="DID 标识符", anchor=tk.CENTER)
        self.tree.heading("iso_name", text="ISO 14229 标准定义名", anchor=tk.W)
        self.tree.heading("name_cn", text="中文含义", anchor=tk.W)
        self.tree.heading("ascii", text="ASCII 转换字符", anchor=tk.W)
        self.tree.heading("parsed", text="解析值 (业务含义)", anchor=tk.W)
        self.tree.heading("hex", text="原始报文 (HEX)", anchor=tk.W)
        self.tree.heading("status", text="诊断状态", anchor=tk.CENTER)

        self.tree.column("select", width=55, anchor=tk.CENTER, stretch=False)
        self.tree.column("did", width=95, anchor=tk.CENTER, stretch=False)
        self.tree.column("iso_name", width=220, anchor=tk.W)
        self.tree.column("name_cn", width=150, anchor=tk.W)
        self.tree.column("ascii", width=160, anchor=tk.W)
        self.tree.column("parsed", width=160, anchor=tk.W)
        self.tree.column("hex", width=130, anchor=tk.W)
        self.tree.column("status", width=95, anchor=tk.CENTER, stretch=False)

        scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # 绑定单击切换选择，双击查看详情
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # 底部选中项详情卡片
        self.detail_frame = tk.Frame(self, bg="#1A1D2B", padx=14, pady=8)
        self.detail_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.lbl_detail = tk.Label(
            self.detail_frame,
            text="提示: 单击表格行开头的 [X] / [ ] 切换勾选；点击 [🚀 一键读取选中项] 开始诊断交互；双击或选择某行查看详情。",
            font=("Segoe UI", 9),
            bg="#1A1D2B",
            fg="#94A3B8",
            justify=tk.LEFT,
            wraplength=940,
        )
        self.lbl_detail.pack(anchor=tk.W)

    def _populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for i, item in enumerate(self.dids):
            sel_mark = "☑ [是]" if item.selected else "☐ [否]"
            did_hex = f"0x{item.did:04X}"
            self.tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(sel_mark, did_hex, item.iso_name, item.name_cn, item.ascii_val, item.parsed_val, item.hex_val, item.status),
            )

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            item_id = self.tree.identify_row(event.y)
            if item_id:
                idx = int(item_id)
                # 点击“选择”列时切换选择状态
                if column == "#1":
                    self.dids[idx].selected = not self.dids[idx].selected
                    sel_mark = "☑ [是]" if self.dids[idx].selected else "☐ [否]"
                    vals = list(self.tree.item(item_id, "values"))
                    vals[0] = sel_mark
                    self.tree.item(item_id, values=vals)

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        item = self.dids[idx]
        txt = (
            f"【DID 详情】: 0x{item.did:04X} ({item.iso_name})\n"
            f"中文含义: {item.name_cn} | 规范描述: {item.desc}\n"
            f"读取状态: {item.status} | ASCII: {item.ascii_val or '[无]'} | 业务解析: {item.parsed_val or '[未读取]'}\n"
            f"原始十六进制: {item.hex_val or '[无]'}"
        )
        self.lbl_detail.configure(text=txt)

    def on_select_all(self):
        for item in self.dids:
            item.selected = True
        self._populate_tree()

    def on_deselect_all(self):
        for item in self.dids:
            item.selected = False
        self._populate_tree()

    def on_toggle_select(self):
        for item in self.dids:
            item.selected = not item.selected
        self._populate_tree()

    def on_start_read(self):
        if self.is_reading:
            return
        if not self.client:
            messagebox.showwarning("警告", "诊断客户端未建立，请先连接设备！", parent=self)
            return

        selected_items = [item for item in self.dids if item.selected]
        if not selected_items:
            messagebox.showinfo("提示", "您尚未勾选任何需要读取的 DID！请勾选后重试。", parent=self)
            return

        self.is_reading = True
        self.btn_start_read.configure(state=tk.DISABLED, bg="#64748B", text="⏳ 正在读取...")
        threading.Thread(target=self._batch_read_worker, daemon=True).start()

    def _batch_read_worker(self):
        total = len([item for item in self.dids if item.selected])
        done_cnt = 0
        success_cnt = 0

        for i, item in enumerate(self.dids):
            if not item.selected:
                continue

            done_cnt += 1
            self._update_status_lbl(f"正在读取 ({done_cnt}/{total}): 0x{item.did:04X} {item.name_cn}...")
            if self.log_cb:
                self.log_cb(f"正在读取规范 DID 0x{item.did:04X} ({item.iso_name})...", "INFO")

            try:
                raw_bytes = self.client.read_data_by_id(item.did, timeout_ms=800)
                parsed, ascii_str = parse_did_payload(item.did, raw_bytes)
                hex_str = " ".join(f"{b:02X}" for b in raw_bytes)

                item.status = "✅ 成功"
                item.parsed_val = parsed
                item.ascii_val = ascii_str
                item.hex_val = hex_str
                success_cnt += 1

                if self.log_cb:
                    self.log_cb(f"DID 0x{item.did:04X} 读取成功: ASCII='{ascii_str}' | 解析='{parsed}' (Hex: {hex_str})", "INFO")

            except UdsNegativeResponseError as ne:
                nrc_hex = f"0x{ne.nrc:02X}"
                item.status = f"❌ 不支持 ({nrc_hex})"
                item.parsed_val = f"ECU 负响应: {ne.nrc_desc}"
                item.ascii_val = "-"
                item.hex_val = f"NRC {nrc_hex}"
                if self.log_cb:
                    self.log_cb(f"DID 0x{item.did:04X} 不支持: NRC={nrc_hex} ({ne.nrc_desc})", "WARN")

            except Exception as e:
                item.status = "⚠️ 超时/异常"
                item.parsed_val = f"通信失败: {e}"
                item.ascii_val = "-"
                item.hex_val = ""
                if self.log_cb:
                    self.log_cb(f"DID 0x{item.did:04X} 通信异常: {e}", "ERR")

            # 实时更新该行数据
            self.after(0, lambda idx=i: self._update_row(idx))
            time.sleep(0.05)

        self._update_status_lbl(f"读取完成！成功: {success_cnt}/{total} 项。")
        self.after(0, self._on_batch_read_done, success_cnt, total)

    def _update_status_lbl(self, text: str):
        self.after(0, lambda: self.lbl_progress_status.configure(text=text))

    def _update_row(self, idx: int):
        item = self.dids[idx]
        sel_mark = "☑ [是]" if item.selected else "☐ [否]"
        did_hex = f"0x{item.did:04X}"
        self.tree.item(
            str(idx),
            values=(sel_mark, did_hex, item.iso_name, item.name_cn, item.ascii_val, item.parsed_val, item.hex_val, item.status),
        )

    def _on_batch_read_done(self, success_cnt: int, total: int):
        self.is_reading = False
        self.btn_start_read.configure(state=tk.NORMAL, bg="#10B981", text="🚀 一键读取选中项")
        messagebox.showinfo(
            "DID 读取完成",
            f"国际标准 DID 诊断轮询已完成！\n\n共请求: {total} 项\n成功响应: {success_cnt} 项\n\n您可以在列表中双击查看每项详情，或点击 [📋 复制全部结果] 导出数据。",
            parent=self,
        )

    def on_copy_results(self):
        lines = [
            "==================================================================",
            "           ECU 国际标准规范 UDS DID 诊断信息读取报告",
            "==================================================================",
            f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "------------------------------------------------------------------",
        ]
        for item in self.dids:
            if item.status != "未读取":
                lines.append(f"• DID: 0x{item.did:04X} | {item.name_cn} ({item.iso_name})")
                lines.append(f"  状态: {item.status}")
                lines.append(f"  ASCII: {item.ascii_val or '[无]'}")
                lines.append(f"  业务解析: {item.parsed_val or '[无]'}")
                lines.append(f"  十六进制: {item.hex_val or '[无]'}")
                lines.append("------------------------------------------------------------------")

        report_text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(report_text)
        messagebox.showinfo("已复制", "已成功将所有读取到的 DID 诊断结果复制到剪贴板！", parent=self)

    def on_export_results(self):
        file_path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 DID 诊断报告",
            defaultextension=".txt",
            filetypes=[("文本文件 (*.txt)", "*.txt"), ("CSV 表格 (*.csv)", "*.csv"), ("所有文件 (*.*)", "*.*")],
        )
        if not file_path:
            return

        try:
            if file_path.endswith(".csv"):
                import csv
                with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["DID", "标准名称", "中文含义", "读取状态", "ASCII 字符", "业务解析值", "原始十六进制"])
                    for item in self.dids:
                        writer.writerow([f"0x{item.did:04X}", item.iso_name, item.name_cn, item.status, item.ascii_val, item.parsed_val, item.hex_val])
            else:
                lines = [
                    "==================================================================",
                    "           ECU 国际标准规范 UDS DID 诊断信息读取报告",
                    "==================================================================",
                    f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    "------------------------------------------------------------------\n",
                ]
                for item in self.dids:
                    lines.append(f"DID: 0x{item.did:04X} ({item.name_cn})")
                    lines.append(f"标准名称: {item.iso_name}")
                    lines.append(f"诊断状态: {item.status}")
                    lines.append(f"ASCII:    {item.ascii_val}")
                    lines.append(f"业务解析: {item.parsed_val}")
                    lines.append(f"原始HEX:  {item.hex_val}")
                    lines.append("-" * 66)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))

            messagebox.showinfo("导出成功", f"DID 诊断报告已成功保存至:\n{file_path}", parent=self)
        except Exception as e:
            messagebox.showerror("导出失败", f"无法写入文件:\n{e}", parent=self)
