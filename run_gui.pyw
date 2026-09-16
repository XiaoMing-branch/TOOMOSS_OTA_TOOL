#!/usr/bin/env python3
"""
双击直接启动 GUI (无黑框模式)
"""
import sys
import os

# 切换工作目录到当前文件所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, script_dir)

if __name__ == "__main__":
    from src.gui.app import OtaApp
    app = OtaApp()
    app.mainloop()
