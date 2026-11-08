# -*- coding: utf-8 -*-
"""桌宠下面的一行小字：正在观看：<进程或窗口>

- 无边框、置顶、不抢焦点、点击穿透
- 每 0.8 秒读一次 state.json（插件写的）拿当前窗口/进程
- 用 Win32 找到 dsh-pet 的桌面窗口，把字条贴在它正下方；找不到就贴屏幕右下角
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'state.json')

u = ctypes.windll.user32
k32 = ctypes.windll.kernel32


def window_rect(hwnd):
    r = wt.RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def process_path(hwnd):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ''
    try:
        b = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(size)):
            return b.value
    finally:
        k32.CloseHandle(h)
    return ''


def find_pet_window():
    """dsh-pet 的桌面窗口：electron.exe、可见、尺寸像桌宠"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        rect = window_rect(hwnd)
        if not rect:
            return True
        l, t, r, b = rect
        w, h = r - l, b - t
        if not (200 <= w <= 1200 and 120 <= h <= 1000):
            return True
        p = process_path(hwnd).lower()
        if 'electron.exe' in p and '.dsh' in p:
            found.append((w * h, hwnd))
        return True

    u.EnumWindows(cb, 0)
    if not found:
        return None
    found.sort()
    return found[0][1]


class Caption(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.text = '正在观看：…'
        self.resize(340, 28)

        self._drag = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(800)
        self.refresh()

    def read_state(self):
        try:
            with open(STATE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def refresh(self):
        s = self.read_state()
        name = (s.get('proc') or '').strip()
        title = (s.get('title') or '').strip()
        if name:
            show = name
        elif title:
            show = title[:28]
        else:
            show = '…'
        self.text = '正在观看：' + show

        hwnd = find_pet_window()
        if hwnd:
            rect = window_rect(hwnd)
            if rect:
                l, t, r, b = rect
                hh = b - t
                x = (l + r) // 2 - self.width() // 2
                # 桌宠窗口底部多半在屏幕外（下半截是透明留白），按窗口高度的 66% 估脚底
                y = t + int(hh * 0.78) + 4
                g = QGuiApplication.primaryScreen().availableGeometry()
                x = max(g.left(), min(x, g.right() - self.width()))
                y = min(y, g.bottom() - self.height())
                self.move(x, y)
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.right() - self.width() - 40, g.bottom() - self.height() - 60)
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _ev):
        self._drag = None

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(QFont('Microsoft YaHei', 10))
        m = p.fontMetrics()
        w = min(self.width(), m.horizontalAdvance(self.text) + 20)
        x = (self.width() - w) // 2
        path = QPainterPath()
        path.addRoundedRect(x, 1, w, self.height() - 2, 8, 8)
        p.fillPath(path, QColor(18, 24, 32, 190))
        p.setPen(QColor(180, 220, 200, 220))
        p.drawText(x + 10, 0, w - 20, self.height(),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text)


def main():
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, True, 'Local\\PetVentiCaption')
    if kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        return
    app = QApplication(sys.argv)
    c = Caption()
    c.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
