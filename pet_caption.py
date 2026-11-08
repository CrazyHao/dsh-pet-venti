# -*- coding: utf-8 -*-
"""桌宠下面的一行小字：正在观看：<进程或窗口>

要点：
- 无边框、置顶、不抢焦点
- 每 0.3 秒读一次 state.json 拿当前窗口/进程
- 用 Win32 找 dsh-pet 的桌面窗口，把字条贴到它脚下；**跨显示器也跟着走**
- 位置直接用 SetWindowPos 按物理像素摆（绕开 Qt 逻辑坐标在多屏/不同 DPI 下的换算问题）
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'state.json')

u = ctypes.windll.user32
k32 = ctypes.windll.kernel32

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
MONITOR_DEFAULTTONEAREST = 2

# 64 位下必须显式声明，否则 HWND_TOPMOST(-1) 会被截成 32 位、SetWindowPos 直接失败
u.SetWindowPos.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, wt.UINT]
u.SetWindowPos.restype = wt.BOOL
u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
u.MonitorFromWindow.restype = ctypes.c_void_p


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]


u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]


def window_rect(hwnd):
    r = wt.RECT()
    if not u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def process_path(hwnd):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
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
    """dsh-pet 的桌面窗口：electron.exe、可见、尺寸像桌宠（它下半截是透明留白，所以尺寸偏大）"""
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
            found.append((w * h, int(hwnd)))
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
        self._hwnd = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)
        self.refresh()

    def showEvent(self, ev):
        self._hwnd = int(self.winId())
        super().showEvent(ev)

    def read_state(self):
        try:
            with open(STATE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def place(self, x, y, work):
        """用物理像素摆位置：只改坐标，不改尺寸"""
        w, h = (work[2] - work[0], work[3] - work[1])
        rect = window_rect(self._hwnd) if self._hwnd else None
        cw = (rect[2] - rect[0]) if rect else self.width()
        ch = (rect[3] - rect[1]) if rect else self.height()
        x = max(work[0], min(x, work[2] - cw))
        y = max(work[1], min(y, work[3] - ch))
        if self._hwnd:
            u.SetWindowPos(wt.HWND(self._hwnd), ctypes.c_void_p(-1), int(x), int(y), 0, 0,
                           SWP_NOSIZE | SWP_NOACTIVATE)
        else:
            self.move(int(x), int(y))

    def refresh(self):
        s = self.read_state()
        name = (s.get('proc') or '').strip()
        title = (s.get('title') or '').strip()
        show = name or (title[:28] if title else '…')
        new_text = '正在观看：' + show
        if new_text != self.text:
            self.text = new_text
            self.update()

        if not self._hwnd:
            self._hwnd = int(self.winId())

        pet = find_pet_window()
        if pet:
            rect = window_rect(pet)
            if rect:
                l, t, r, b = rect
                hh = b - t
                # 桌宠窗口下半截是透明留白，按高度 78% 估脚底
                feet = t + int(hh * 0.78) + 4
                mon = u.MonitorFromWindow(wt.HWND(pet), MONITOR_DEFAULTTONEAREST)
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                if u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                    work = (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
                else:
                    work = (l, t, r, b)
                cx = (l + r) // 2
                rect_cap = window_rect(self._hwnd) if self._hwnd else None
                cw = (rect_cap[2] - rect_cap[0]) if rect_cap else self.width()
                self.place(cx - cw // 2, feet, work)
                return

        # 找不到桌宠：落到光标所在屏的右下角
        pt = wt.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        mon = u.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            w = mi.rcWork
            self.place(w.right - self.width() - 40, w.bottom - self.height() - 60,
                       (w.left, w.top, w.right, w.bottom))
        else:
            self.move(40, 40)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(QFont('Microsoft YaHei', 10))
        m = p.fontMetrics()
        w = min(self.width(), m.horizontalAdvance(self.text) + 20)
        x = (self.width() - w) // 2
        path = QPainterPath()
        path.addRoundedRect(x, 1, w, self.height() - 2, 8, 8)
        p.fillPath(path, QColor(18, 24, 32, 200))
        p.setPen(QColor(180, 220, 200, 225))
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
