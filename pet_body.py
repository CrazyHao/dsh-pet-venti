# -*- coding: utf-8 -*-
"""温迪桌宠本体（独立版）—— 透明窗口 + 动画 + 气泡 + 音频 + 重力拖拽

配置来自插件（POST /config）：
    gravity, restitution, ground_friction, throw_power, confine_to_screen

动画名来自同目录 animations.json（原版 dsh-pet 的 114 个动作名，已配好素材）。

接口：
    GET  /ping
    POST /config {gravity,restitution,ground_friction,throw_power,confine_to_screen}
    POST /say    {text,audio,dur_ms,anim}
    POST /bubble {text,dur_ms}
    POST /anim   {name}
    POST /quit
"""
import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication, QMenu, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
WEBM_DIR = os.path.join(HERE, 'assets', 'webm')
ANIMS_FILE = os.path.join(HERE, 'animations.json')
FFMPEG = os.path.join(
    HERE, '..', 'tools', 'Lib', 'site-packages', 'imageio_ffmpeg', 'binaries',
    'ffmpeg-win-x86_64-v9.0.2.exe')

WIN_W, WIN_H = 420, 236
BUBBLE_H = 74

DEFAULT_CFG = {
    'gravity': 1400.0,
    'restitution': 0.78,
    'ground_friction': 2.5,
    'throw_power': 1.0,
    'confine_to_screen': True,
}


def log(msg):
    try:
        print('[pet-body]', msg, flush=True)
    except Exception:
        pass


def load_anims():
    try:
        with open(ANIMS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('animations') or {}
    except Exception:
        return {}


def load_frames(name, w, h):
    path = os.path.join(WEBM_DIR, name + '.webm')
    if not os.path.isfile(path) or not os.path.isfile(FFMPEG):
        return []
    cmd = [FFMPEG, '-v', 'error', '-c:v', 'libvpx-vp9', '-i', path,
           '-vf', 'scale=%d:%d' % (w, h), '-f', 'rawvideo', '-pix_fmt', 'rgba', '-']
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        raw = subprocess.run(cmd, capture_output=True, timeout=20,
                             creationflags=0x08000000, startupinfo=si).stdout
    except Exception as e:
        log('decode failed %s: %s' % (name, e))
        return []
    size = w * h * 4
    return [QImage(raw[i:i + size], w, h, QImage.Format.Format_RGBA8888).copy()
            for i in range(0, len(raw) - size + 1, size)]


class PetWindow(QWidget):
    def __init__(self, port):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(WIN_W, WIN_H + BUBBLE_H)

        self.port = port
        self.ctrl_port = port + 1
        self.cfg = dict(DEFAULT_CFG)
        self.anims = load_anims()
        self.idle_name = (self.anims.get('idle') or ['待机-温迪'])[0]
        self.click_names = self.anims.get('clicks') or ['点击-开心']
        self.random_names = []
        for cat in self.anims.get('categories') or []:
            self.random_names.extend(cat.get('actions') or [])
        if not self.random_names:
            self.random_names = [self.idle_name]

        self.frames = {}
        self.current = []
        self.frame_idx = 0
        self.anim_until = 0.0
        self.bubble_text = ''
        self.bubble_until = 0.0

        # 物理
        self.vx = 0.0
        self.vy = 0.0
        self.dragging = False
        self.last_drag = None
        self.last_t = time.time()

        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self)
        self.player.setAudioOutput(self.out)
        self.out.setVolume(0.9)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.on_anim_tick)
        self.anim_timer.start(33)

        self.phys_timer = QTimer(self)
        self.phys_timer.timeout.connect(self.on_phys_tick)
        self.phys_timer.start(16)

        # 偶尔自己来段小动作
        self.idle_action_timer = QTimer(self)
        self.idle_action_timer.timeout.connect(self.maybe_idle_action)
        self.idle_action_timer.start(18000)

        self.to_default()
        self.play(self.idle_name)

    # ---------- 位置 / 物理 ----------
    def screen_rect(self):
        g = QGuiApplication.primaryScreen().availableGeometry()
        return g

    def to_default(self):
        g = self.screen_rect()
        self.move(g.right() - self.width() - 40, g.top() + 80)
        self.vx = self.vy = 0.0

    def on_phys_tick(self):
        if self.dragging:
            self.last_t = time.time()
            return
        now = time.time()
        dt = min(0.05, max(0.001, now - self.last_t))
        self.last_t = now

        g = self.screen_rect()
        floor = g.bottom() - self.height() + 1
        self.vy += self.cfg['gravity'] * dt
        x = self.x() + self.vx * dt
        y = self.y() + self.vy * dt

        # 地面
        if y > floor:
            y = floor
            if abs(self.vy) > 40:
                self.vy = -self.vy * self.cfg['restitution']
            else:
                self.vy = 0
            self.vx *= max(0.0, 1.0 - self.cfg['ground_friction'] * dt)
            if abs(self.vx) < 6:
                self.vx = 0

        # 左右墙
        if x < g.left():
            x = g.left()
            self.vx = -self.vx * self.cfg['restitution']
        elif x > g.right() - self.width():
            x = g.right() - self.width()
            self.vx = -self.vx * self.cfg['restitution']

        if self.cfg['confine_to_screen']:
            y = min(max(y, g.top()), floor)
        else:
            y = min(y, floor)

        self.move(int(x), int(y))

    # ---------- 动画 ----------
    def frames_of(self, name):
        if name not in self.frames:
            self.frames[name] = load_frames(name, WIN_W, WIN_H)
            if len(self.frames) > 5:
                for k in list(self.frames)[:-5]:
                    self.frames.pop(k, None)
        return self.frames[name]

    def play(self, name, hold_ms=0):
        fs = self.frames_of(name) or self.frames_of(self.idle_name)
        if not fs:
            return
        self.current = fs
        self.current_name = name
        self.frame_idx = 0
        self.anim_until = time.time() + hold_ms / 1000.0 if hold_ms else 0.0

    def on_anim_tick(self):
        if not self.current:
            return
        idle = self.frames_of(self.idle_name)
        if self.frame_idx < len(self.current) - 1:
            self.frame_idx += 1
        elif self.anim_until and time.time() < self.anim_until:
            pass
        elif self.current is not idle:
            self.play(self.idle_name)
        else:
            self.frame_idx = 0
        self.update()

    def maybe_idle_action(self):
        if self.dragging or not self.random_names:
            return
        if time.time() < self.bubble_until:
            return
        name = random.choice(self.random_names)
        self.play(name)

    # ---------- 气泡 ----------
    def say(self, text, ms):
        self.bubble_text = text or ''
        self.bubble_until = time.time() + max(1.0, ms / 1000.0)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self.current:
            p.drawImage(0, BUBBLE_H, self.current[self.frame_idx])
        if self.bubble_text and time.time() < self.bubble_until:
            self.paint_bubble(p)

    def paint_bubble(self, p):
        text = self.bubble_text
        p.setFont(QFont('Microsoft YaHei', 11))
        m = p.fontMetrics()
        max_w = self.width() - 32
        rect = m.boundingRect(0, 0, max_w, 400, int(Qt.TextFlag.TextWordWrap), text)
        w = min(max_w, rect.width() + 28)
        h = rect.height() + 22
        x = (self.width() - w) // 2
        y = max(4, BUBBLE_H - h - 8)
        path = QPainterPath()
        path.addRoundedRect(x, y, w, h, 12, 12)
        p.fillPath(path, QColor(22, 30, 40, 232))
        p.setPen(QColor(127, 211, 165, 180))
        p.drawPath(path)
        p.setPen(QColor(240, 244, 248))
        p.drawText(x + 14, y + 10, w - 28, h - 20,
                   int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   text)

    # ---------- 鼠标 ----------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_from = ev.globalPosition().toPoint()
            self.win_from = self.pos()
            self.last_drag = (time.time(), self.drag_from)
            self.vx = self.vy = 0.0
            drag_name = (self.anims.get('drag') or ['拖拽-温迪'])[0]
            self.play(drag_name)
        elif ev.button() == Qt.MouseButton.RightButton:
            self.menu(ev.globalPosition().toPoint())

    def mouseMoveEvent(self, ev):
        if not self.dragging:
            return
        now = time.time()
        pos = ev.globalPosition().toPoint()
        delta = pos - self.drag_from
        self.move(self.win_from + delta)
        # 用最近一次位移估速度，松手时甩出去
        if self.last_drag:
            t0, p0 = self.last_drag
            dt = now - t0
            if dt > 0.004:
                self.vx = (pos.x() - p0.x()) / dt
                self.vy = (pos.y() - p0.y()) / dt
            self.last_drag = (now, pos)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton or not self.dragging:
            return
        self.dragging = False
        moved = (ev.globalPosition().toPoint() - self.drag_from).manhattanLength()
        if moved < 6:
            self.vx = self.vy = 0.0
            self.play(random.choice(self.click_names), hold_ms=1200)
        else:
            power = float(self.cfg.get('throw_power') or 1.0)
            self.vx = max(-4000, min(4000, self.vx * power))
            self.vy = max(-4000, min(4000, self.vy * power))
            self.last_t = time.time()

    def menu(self, pos):
        menu = QMenu(self)
        a1 = QAction('说句话', self)
        a1.triggered.connect(self.ask_plugin)
        a2 = QAction('回原位', self)
        a2.triggered.connect(self.to_default)
        a3 = QAction('退出', self)
        a3.triggered.connect(QApplication.instance().quit)
        for a in (a1, a2, a3):
            menu.addAction(a)
        menu.exec(pos)

    def ask_plugin(self):
        def run():
            import urllib.request
            try:
                req = urllib.request.Request('http://127.0.0.1:%d/ask' % self.ctrl_port, data=b'{}',
                                             headers={'content-type': 'application/json'})
                urllib.request.urlopen(req, timeout=3).read()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()


class Api(BaseHTTPRequestHandler):
    pet = None

    def log_message(self, *_a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/ping'):
            return self._json({'ok': True})
        self._json({'ok': False}, 404)

    def do_POST(self):
        n = int(self.headers.get('content-length') or 0)
        try:
            p = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            p = {}
        pet = Api.pet
        if self.path.startswith('/config'):
            for k in ('gravity', 'restitution', 'ground_friction', 'throw_power'):
                if k in p:
                    try:
                        pet.cfg[k] = float(p[k])
                    except (TypeError, ValueError):
                        pass
            pet.cfg['confine_to_screen'] = bool(p.get('confine_to_screen', pet.cfg['confine_to_screen']))
            return self._json({'ok': True})
        if self.path.startswith('/say'):
            text = p.get('text') or ''
            dur = int(p.get('dur_ms') or 4000)
            anim = p.get('anim') or random.choice(pet.click_names)
            audio = p.get('audio') or ''
            pet.say(text, dur)
            pet.play(anim, hold_ms=dur)
            if audio and os.path.isfile(audio):
                pet.player.stop()
                pet.player.setSource(QUrl.fromLocalFile(audio))
                pet.player.play()
            return self._json({'ok': True})
        if self.path.startswith('/bubble'):
            pet.say(p.get('text') or '', int(p.get('dur_ms') or 3500))
            return self._json({'ok': True})
        if self.path.startswith('/anim'):
            pet.play(p.get('name') or pet.idle_name)
            return self._json({'ok': True})
        if self.path.startswith('/quit'):
            QApplication.instance().quit()
            return self._json({'ok': True})
        self._json({'ok': False}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=7891)
    a = ap.parse_args()
    app = QApplication(sys.argv)
    win = PetWindow(a.port)
    Api.pet = win
    server = ThreadingHTTPServer(('127.0.0.1', a.port), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log('listening on %d' % a.port)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
