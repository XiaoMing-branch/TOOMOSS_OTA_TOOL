#!/usr/bin/env python3
"""
Toomoss LIN UDS OTA 工具统一主入口
- 默认启动图形用户界面 (GUI)
- 传入 --cli 启动命令行自动化模式
"""
import sys
import os

# 确保项目根目录在导入路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if "--cli" in sys.argv:
        from cli import main as cli_main
        sys.argv.remove("--cli")
        sys.exit(cli_main())
    else:
        from src.gui.app import OtaApp
        app = OtaApp()
        app.mainloop()


if __name__ == "__main__":
    main()
