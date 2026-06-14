"""模拟 main.py 的 Qt 环境来测试弹幕"""
import sys
import os
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')

# 先导入 Qt（跟 main.py 一样）
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

import time
import threading
from core.danmaku_recorder import DanmakuRecorder

d = DanmakuRecorder("3044248", "./_test_dm_qt", "qt_test")
d.start("xml")

# 等 15 秒
time.sleep(15)
d.stop()

# 检查文件
fpath = "./_test_dm_qt/qt_test.xml"
if os.path.exists(fpath):
    with open(fpath, encoding="utf-8") as f:
        lines = [l for l in f if l.strip().startswith("<d ")]
    print(f"danmaku count from file: {len(lines)}")
else:
    print("NO FILE CREATED")
