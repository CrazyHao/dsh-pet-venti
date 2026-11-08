# -*- coding: utf-8 -*-
"""屏幕感知：截当前前台窗口所在的显示器 → OCR + 画面统计 → JSON 行

- 捕获用 DXGI Desktop Duplication（ddagrab），并**跟随前台窗口所在的显示器**
- 所有子进程都带 CREATE_NO_WINDOW：不再每 3 秒闪一个 cmd 黑框
- 事件里带上 monitor / prob / title，插件会把它写进日志和气泡上下文

事件：
    {"type":"screen","ts":...,"title":...,"proc":"game.exe","monitor":"DISPLAY2",
     "text":"OCR 文本","stats":{...},"cap":"dda"}
"""
import argparse
import asyncio
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

FFMPEG = r'C:\Users\23681\Documents\deepseek-harness\default-workspace\voice\tools\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v9.0.2.exe'
SCALE_W = 1720
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _startupinfo():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def run_quiet(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=NO_WINDOW, startupinfo=_startupinfo())
    except Exception:
        return None


def monitors():
    rects = []

    def cb(hmon, hdc, lprc, lparam):
        r = ctypes.cast(lprc, ctypes.POINTER(wt.RECT)).contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return 1

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p)
    ctypes.windll.user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
    return rects


def foreground():
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    hwnd = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(hwnd)
    title = ''
    if n > 0:
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
    proc_name = ''
    try:
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(0x1000, False, pid.value)
        if h:
            b = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if k.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(size)):
                proc_name = os.path.basename(b.value)
            k.CloseHandle(h)
    except Exception:
        pass
    rect = wt.RECT()
    try:
        u.GetWindowRect(hwnd, ctypes.byref(rect))
    except Exception:
        pass
    return title, proc_name, rect


def pick_monitor(rect, mons):
    """前台窗口中心落在哪块屏上 → (索引, 名字)"""
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    for i, (l, t, r, b) in enumerate(mons):
        if l <= cx < r and t <= cy < b:
            return i, 'DISPLAY%d' % (i + 1)
    return 0, 'DISPLAY1'


def grab(path, mon_idx):
    attempts = [
        ('dda', [FFMPEG, '-y', '-loglevel', 'error', '-filter_complex',
                 'ddagrab=%d,hwdownload,format=bgra,scale=%d:-2' % (mon_idx, SCALE_W),
                 '-frames:v', '1', path]),
        ('dda0', [FFMPEG, '-y', '-loglevel', 'error', '-filter_complex',
                  'ddagrab=0,hwdownload,format=bgra,scale=%d:-2' % SCALE_W,
                  '-frames:v', '1', path]),
        ('gdi', [FFMPEG, '-y', '-loglevel', 'error', '-f', 'gdigrab', '-i', 'desktop',
                 '-vf', 'scale=%d:-2' % SCALE_W, '-frames:v', '1', path]),
    ]
    for name, cmd in attempts:
        r = run_quiet(cmd)
        if r and r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 4096:
            return name
    return None


async def ocr_png(path):
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    with open(path, 'rb') as f:
        writer.write_bytes(f.read())
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    eng = None
    for tag in ('zh-Hans-CN', 'zh-CN', 'en-US'):
        try:
            eng = OcrEngine.try_create_from_language(Language(tag))
        except Exception:
            eng = None
        if eng:
            break
    if eng is None:
        eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        return ''
    res = await eng.recognize_async(bitmap)
    return res.text or ''


def image_stats(path, prev):
    try:
        from PIL import Image
        import numpy as np
    except Exception:
        return None, prev
    im = Image.open(path).convert('RGB')
    im.thumbnail((480, 480))
    a = np.asarray(im, dtype=np.float32)
    gray = a.mean(axis=2)
    diff = 0.0
    if prev is not None and prev.shape == gray.shape:
        diff = float(np.abs(gray - prev).mean())
    r, g, b = a[:, :, 0].mean(), a[:, :, 1].mean(), a[:, :, 2].mean()
    if max(r, g, b) - min(r, g, b) < 12:
        hue = '灰'
    elif g >= r and g >= b:
        hue = '绿'
    elif b >= r and b >= g:
        hue = '蓝'
    elif r >= g and r >= b:
        hue = '红'
    else:
        hue = '杂'
    return {'bright': round(float(gray.mean()), 1), 'std': round(float(gray.std()), 1),
            'diff': round(diff, 1), 'hue': hue}, gray


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, 'Local\\PetSenseScreen')
    if ctypes.windll.kernel32.GetLastError() == 183:
        emit({'type': 'error', 'msg': 'another sense_screen is already running'})
        return

    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=float, default=3.0)
    ap.add_argument('--max-chars', type=int, default=900)
    a = ap.parse_args()

    tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_tmp')
    os.makedirs(tmpdir, exist_ok=True)
    png = os.path.join(tmpdir, 'screen.png')
    prev_gray = None
    last_sig = None

    while True:
        t0 = time.time()
        try:
            mons = monitors() or [(0, 0, 1920, 1080)]
            title, proc, wrect = foreground()
            idx, mname = pick_monitor(wrect, mons)
            cap = grab(png, idx)
            if not cap:
                emit({'type': 'error', 'msg': 'grab failed'})
                time.sleep(a.interval)
                continue
            text = ' '.join((asyncio.run(ocr_png(png)) or '').split())[:a.max_chars]
            st, prev_gray = image_stats(png, prev_gray)
            sig = (title, proc, mname, text[:120], st and st['hue'], st and int(st['bright'] // 15))
            if sig == last_sig:
                time.sleep(max(0.3, a.interval - (time.time() - t0)))
                continue
            last_sig = sig
            emit({'type': 'screen', 'ts': int(time.time() * 1000), 'title': title,
                  'proc': proc, 'monitor': mname, 'text': text, 'stats': st, 'cap': cap})
        except Exception as e:
            emit({'type': 'error', 'msg': str(e)[:200]})
        time.sleep(max(0.3, a.interval - (time.time() - t0)))


if __name__ == '__main__':
    main()
