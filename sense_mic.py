# -*- coding: utf-8 -*-
"""麦克风感知：能量 VAD 切句 → whisper 转写 → 输出 JSON 行

给 dsh-pet-sense 插件当子进程用。事件：
    {"type":"mic","on":true}                      麦克风开关状态
    {"type":"speech","ts":...,"text":"..."}        一句转写
    {"type":"ready","model":"small"}              模型加载完成
    {"type":"error","msg":"..."}                   出错

Ctrl+Shift+Q 全局切换监听（RegisterHotKey，不需要窗口焦点）。

单独跑：
    python sense_mic.py --model small
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import time

import numpy as np

SR = 16000
MOD_WIN = 0x0008
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB001
VOICE_KEY_ID = 0xB003


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()


class HotkeyThread(threading.Thread):
    """注册全局热键并跑消息循环；按下时往 cmd 队列里塞 'toggle'"""

    def __init__(self, cmd: queue.Queue):
        super().__init__(daemon=True)
        self.cmd = cmd

    def run(self):
        u = ctypes.windll.user32
        # 目标组合可能被别的软件占了（实测 Ctrl+Shift+Q / Ctrl+Alt+Q 都注册不上），依次退让
        combos = [
            ('Ctrl+Shift+Q', MOD_CONTROL | MOD_SHIFT, ord('Q')),
            ('Alt+Shift+Q', 0x0001 | MOD_SHIFT, ord('Q')),
            ('Ctrl+Shift+F9', MOD_CONTROL | MOD_SHIFT, 0x78),
        ]
        chosen = None
        for _ in range(20):
            for name, mods, key in combos:
                if u.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, key):
                    chosen = name
                    break
            if chosen:
                break
            time.sleep(1.0)
        if not chosen:
            emit({'type': 'hotkey', 'ok': False, 'keys': ''})
            return
        emit({'type': 'hotkey', 'ok': True, 'keys': chosen})

        voice_combos = [
            ('Ctrl+Shift+P', MOD_CONTROL | MOD_SHIFT, ord('P')),
            ('Alt+Shift+P', 0x0001 | MOD_SHIFT, ord('P')),
            ('Ctrl+Shift+F8', MOD_CONTROL | MOD_SHIFT, 0x77),
        ]
        voice_key = None
        for name, mods, key in voice_combos:
            if u.RegisterHotKey(None, VOICE_KEY_ID, mods | MOD_NOREPEAT, key):
                voice_key = name
                break
        emit({'type': 'voicekey', 'ok': bool(voice_key), 'keys': voice_key or ''})
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_ID:
                    self.cmd.put('toggle')
                elif msg.wParam == VOICE_KEY_ID:
                    self.cmd.put('voice')
        u.UnregisterHotKey(None, HOTKEY_ID)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    # 单实例锁：避免热重载后两个进程抢麦克风、抢热键
    import ctypes
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, 'Local\\PetSenseMic')
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        emit({'type': 'error', 'msg': 'another sense_mic is already running'})
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='small')
    ap.add_argument('--device', type=int, default=-1)
    ap.add_argument('--threshold', type=float, default=0.012)
    ap.add_argument('--min-speech', type=float, default=0.5)
    ap.add_argument('--silence', type=float, default=0.7)
    ap.add_argument('--start-on', type=int, default=1)
    a = ap.parse_args()

    import sounddevice as sd
    import whisper

    cmd = queue.Queue()
    HotkeyThread(cmd).start()

    # 也接受 stdin 的 "toggle"（宿主插件可以远程切），方便做按钮/测试
    def stdin_reader():
        try:
            for line in sys.stdin:
                if line.strip().lower() == 'toggle':
                    cmd.put('toggle')
        except Exception:
            return

    threading.Thread(target=stdin_reader, daemon=True).start()

    download_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_models')
    os.makedirs(download_root, exist_ok=True)
    emit({'type': 'loading', 'model': a.model})
    # 跑 CPU：把显存留给 IndexTTS（打游戏时 GPU 已经很挤了）
    model = whisper.load_model(a.model, download_root=download_root, device='cpu')
    emit({'type': 'ready', 'model': a.model})

    audio_q = queue.Queue()
    state = {'on': bool(a.start_on)}

    def on_audio(indata, frames, t, status):
        if state['on']:
            audio_q.put(indata[:, 0].copy())

    def stream_factory():
        dev = None if a.device < 0 else a.device
        return sd.InputStream(samplerate=SR, channels=1, dtype='float32',
                              blocksize=int(SR * 0.1), device=dev, callback=on_audio)

    stream = None
    speech, silence, in_speech = [], 0.0, False
    last = time.time()

    emit({'type': 'mic', 'on': state['on']})

    while True:
        try:
            if state['on'] and stream is None:
                stream = stream_factory()
                stream.start()
            if (not state['on']) and stream is not None:
                stream.stop()
                stream.close()
                stream = None
                speech, silence, in_speech = [], 0.0, False
        except Exception as e:
            emit({'type': 'error', 'msg': '麦克风打开失败: %s' % str(e)[:180]})
            state['on'] = False

        # 热键
        try:
            while True:
                c = cmd.get_nowait()
                if c == 'toggle':
                    state['on'] = not state['on']
                    emit({'type': 'mic', 'on': state['on']})
                elif c == 'voice':
                    emit({'type': 'voice'})
        except queue.Empty:
            pass

        try:
            block = audio_q.get(timeout=0.2)
        except queue.Empty:
            continue

        rms = float(np.sqrt(np.mean(block ** 2)) + 1e-9)
        now = time.time()
        dt = max(1e-3, now - last)
        last = now

        if rms > a.threshold:
            in_speech = True
            speech.append(block)
            silence = 0.0
        elif in_speech:
            silence += dt
            if silence >= a.silence:
                total = sum(len(x) for x in speech) / SR
                if total >= a.min_speech:
                    audio = np.concatenate(speech).astype(np.float32)
                    try:
                        r = model.transcribe(audio, language='zh', fp16=False,
                                             condition_on_previous_text=False)
                        text = (r.get('text') or '').strip()
                        if text:
                            emit({'type': 'speech', 'ts': int(time.time() * 1000),
                                  'text': text})
                    except Exception as e:
                        emit({'type': 'error', 'msg': '转写失败: %s' % str(e)[:160]})
                speech, silence, in_speech = [], 0.0, False


if __name__ == '__main__':
    main()
