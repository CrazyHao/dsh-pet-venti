# -*- coding: utf-8 -*-
"""常备窗口：实时显示桌宠正在看哪个窗口/显示器，以及刚说了什么

数据来自同目录的 state.json（插件每帧/每句都会更新）。
双击 watch.cmd 打开，关掉窗口就停。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'state.json')


def clear():
    os.system('cls')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass
    last_sig = None
    print('温迪在看什么（Ctrl+C 退出）')
    print('-' * 78)
    while True:
        try:
            with open(STATE, 'r', encoding='utf-8') as f:
                s = json.load(f)
        except Exception:
            time.sleep(0.8)
            continue
        sig = (s.get('proc'), s.get('title'), s.get('monitor'), s.get('say'), s.get('mic'))
        if sig != last_sig:
            last_sig = sig
            t = time.strftime('%H:%M:%S', time.localtime((s.get('at') or 0) / 1000))
            print('%s  %-9s  %-18s  %s' % (
                t, s.get('monitor') or '?', (s.get('proc') or '?')[:18],
                (s.get('title') or '')[:44]))
            if s.get('say'):
                print('           ↳ 「%s」' % s['say'][:60])
            print('           麦克风: %s' % ('开' if s.get('mic', True) else '关'))
            print('-' * 78, flush=True)
        time.sleep(1.0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
