/**
 * dsh-pet-venti —— 温迪版桌宠（结合 dsh-pet 版）
 *
 * 依赖 dsh-pet 的窗口和交互，本插件负责四件事：
 *   1. 人设 + 动画配置写进 dsh-pet（114 个动作名；首次写，备份 .venti.bak）
 *   2. 自带 webm 素材同步到 dsh-pet 的 main-animation/webm
 *   3. 向 dsh-pet 注册 IndexTTS 语音实现，并自动拉起本地 TTS 服务
 *   4. 屏幕/麦克风感知 → 模型写一句 → 先合成 mp3 → 页面侧同时弹气泡+播音（播完才下一条）
 */
import { execFileSync, spawn } from 'node:child_process'
import {
  copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createInterface } from 'node:readline'

export const name = 'dsh-pet-venti'
export const inject = ['petApi7340', 'webServer', 'llm']

const PLUGIN_DIR = fileURLToPath(new URL('.', import.meta.url))
// 默认按「插件目录的上一级」找运行环境（我本机就是这样）；
// 别的机器由 setup.ps1 生成 paths.json 指向它自己装的那份。
const DEFAULT_ROOT = fileURLToPath(new URL('..', import.meta.url))
const PATHS = {
  envRoot: DEFAULT_ROOT,
  toolsVenv: join(DEFAULT_ROOT, 'tools'),
  ttsVenv: join(DEFAULT_ROOT, 'src', 'index-tts-main', '.venv'),
  adapter: join(DEFAULT_ROOT, 'indextts', 'adapter.py'),
}
// 安装环境的位置：优先用户目录（跨插件升级稳定），其次插件目录（本地开发）
for (const p of [
  join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'pet-venti', 'paths.json'),
  join(PLUGIN_DIR, 'paths.json'),
]) {
  try { Object.assign(PATHS, JSON.parse(readFileSync(p, 'utf8'))) } catch { /* 下一个 */ }
}
const ENV_ROOT = PATHS.envRoot
const PY_TOOLS = join(PATHS.toolsVenv, 'Scripts', 'python.exe')
const PY_TOOLS_W = join(PATHS.toolsVenv, 'Scripts', 'pythonw.exe')
const PY_LLM = join(PATHS.ttsVenv, 'Scripts', 'python.exe')
const PY_LLM_W = join(PATHS.ttsVenv, 'Scripts', 'pythonw.exe')
const ADAPTER = PATHS.adapter
const AUDIO_DIR = join(PLUGIN_DIR, 'audio')
const CAPTION = join(PLUGIN_DIR, 'pet_caption.py')

const TTS_URL = 'http://127.0.0.1:7880/api/v1/tts/tasks'
const TTS_HEALTH = 'http://127.0.0.1:7880/health'
const TTS_VOICE = process.env.PET_VENTI_VOICE || 'venti_b'
const PROVIDER = process.env.PET_VENTI_LLM_PROVIDER || 'deepseek-account'
const MODEL = process.env.PET_VENTI_LLM_MODEL || 'deepseek-flash'

const SCREEN_INTERVAL = 3
const ROAST_MIN_GAP_MS = 12000
const HISTORY_SIZE = 6

const PERSONA = `你是蒙德城的吟游诗人温迪，自由的风神。爱喝酒（尤其是苹果酒和蒲公英酒）、爱弹琴、爱开玩笑，说话轻快又有点懒散，被抓包就用「诶嘿」糊弄过去，偶尔随口念一句诗。碎碎念只说一句、20 字以内，像随口嘟囔；聊天也保持口语、别长篇大论。不要解释自己是 AI，也不要提「助手」「模型」「服务」这类词。`

const PET_HOME = join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'dsh-pet')
const PET_CONFIG = join(PET_HOME, 'main-config.jsonc')
const PET_ANIM_DIR = join(PET_HOME, 'main-animation', 'webm')
const MARKER = join(PET_HOME, '.dsh-pet-venti.json')
const CONFIG_VERSION = 3

export function apply(ctx) {
  try {
    if (typeof globalThis.__petVentiDispose === 'function') globalThis.__petVentiDispose()
  } catch { /* ignore */ }

  const log = (m) => {
    try { ctx.logger?.info?.('[pet-venti] ' + m) } catch { /* ignore */ }
  }

  try { mkdirSync(AUDIO_DIR, { recursive: true }) } catch { /* ignore */ }
  try {
    const cutoff = Date.now() - 2 * 3600 * 1000
    for (const f of readdirSync(AUDIO_DIR)) {
      const p = join(AUDIO_DIR, f)
      try { if (statSync(p).mtimeMs < cutoff) unlinkSync(p) } catch { /* ignore */ }
    }
  } catch { /* ignore */ }

  // 桌宠配置是 JSONC（带注释），读之前先把注释去掉
  function stripJsonc(text) {
    let out = ''
    let i = 0
    let inStr = false
    while (i < text.length) {
      const c = text[i]
      if (inStr) {
        out += c
        if (c === '\\' && i + 1 < text.length) { out += text[i + 1]; i += 2; continue }
        if (c === '"') inStr = false
        i++
        continue
      }
      if (c === '"') { inStr = true; out += c; i++; continue }
      if (c === '/' && text[i + 1] === '/') { while (i < text.length && text[i] !== '\n') i++; continue }
      if (c === '/' && text[i + 1] === '*') {
        i += 2
        while (i + 1 < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++
        i += 2
        continue
      }
      out += c
      i++
    }
    return out
  }

  // ---------- 配置 + 素材同步 ----------
  function syncPet() {
    try {
      if (!existsSync(PET_CONFIG)) { log('dsh-pet config missing: ' + PET_CONFIG); return }
      let applied = 0
      try { applied = JSON.parse(readFileSync(MARKER, 'utf8')).version || 0 } catch { /* first */ }
      const anims = JSON.parse(readFileSync(join(PLUGIN_DIR, 'animations.json'), 'utf8'))
      // 桌宠设置页保存时会把配置写回内置模板（人设、display 都会被打回默认），
      // 用「名字还是默认的蓝毛小女仆」当作被重置的信号，重新套一遍温迪配置。
      const rawCfg = readFileSync(PET_CONFIG, 'utf8')
      const factoryDefault = rawCfg.includes('蓝毛小女仆')
      if (applied < CONFIG_VERSION || factoryDefault) {
        try { copyFileSync(PET_CONFIG, PET_CONFIG + '.venti.bak') } catch { /* ignore */ }
        const c = JSON.parse(stripJsonc(rawCfg))
        c.whisperPrompt = PERSONA
        if (Array.isArray(c.pets) && c.pets[0]) {
          c.pets[0].whisperPrompt = PERSONA
          c.pets[0].name = '温迪'
          c.pets[0].display = 'desktop'   // 只留桌面那只，避免桌面 + 网页两只同时出现
        }
        if (anims.animations) c.animations = anims.animations
        writeFileSync(PET_CONFIG, JSON.stringify(c, null, 2) + '\n', 'utf8')
        writeFileSync(MARKER, JSON.stringify({ version: CONFIG_VERSION }), 'utf8')
        log('dsh-pet persona + ' + (anims.animations?.categories?.length || 0) + ' categories written')
      }
      const src = join(PLUGIN_DIR, 'assets', 'webm')
      if (existsSync(src)) {
        mkdirSync(PET_ANIM_DIR, { recursive: true })
        let n = 0
        for (const f of readdirSync(src)) {
          if (!f.endsWith('.webm')) continue
          const s = join(src, f)
          const d = join(PET_ANIM_DIR, f)
          try {
            if (!existsSync(d) || statSync(d).size !== statSync(s).size) { copyFileSync(s, d); n++ }
          } catch { /* ignore */ }
        }
        if (n) log('synced ' + n + ' animations')
      }
    } catch (e) {
      log('sync failed: ' + String(e).slice(0, 140))
    }
  }
  let voiceCache = { at: 0, on: true }
  function petVoiceEnabled() {
    const now = Date.now()
    if (now - voiceCache.at < 5000) return voiceCache.on
    let on = true
    try {
      const cfg = JSON.parse(stripJsonc(readFileSync(PET_CONFIG, 'utf8')))
      on = cfg.voiceEnabled !== false
    } catch { /* 读不到就当开着 */ }
    voiceCache = { at: now, on }
    return on
  }

  let initialVoiceOn = true
  try { initialVoiceOn = JSON.parse(stripJsonc(readFileSync(PET_CONFIG, 'utf8'))).voiceEnabled !== false } catch { /* 默认开 */ }

  syncPet()

  // 设置页保存会把配置打回模板，隔一分钟看一眼，被重置就再套一遍
  const cfgWatch = setInterval(() => {
    try {
      const raw = readFileSync(PET_CONFIG, 'utf8')
      if (raw.includes('蓝毛小女仆')) { log('config reset detected, reapplying'); syncPet() }
    } catch { /* ignore */ }
  }, 60000)
  // ---------- TTS ----------
  async function ensureTts() {
    try {
      const r = await fetch(TTS_HEALTH, { signal: AbortSignal.timeout(1500) })
      if (r.ok) return
    } catch { /* down */ }
    if (!existsSync(PY_LLM_W) || !existsSync(ADAPTER)) return
    try {
      spawn(PY_LLM_W, [ADAPTER], { cwd: ENV_ROOT, detached: true, stdio: 'ignore', windowsHide: true }).unref()
      log('starting TTS server')
    } catch { /* ignore */ }
  }
  void ensureTts()

  // 桌宠下面那行「正在观看：xxx」
  let captionChild = null
  function ensureCaption() {
    try {
      if (captionChild && captionChild.exitCode === null) return
      if (!existsSync(PY_TOOLS_W) || !existsSync(CAPTION)) return

      captionChild = spawn(PY_TOOLS_W, [CAPTION], {
        cwd: PLUGIN_DIR, detached: true, stdio: 'ignore', windowsHide: true,
      })
      captionChild.unref()
      log('caption bar started pid=' + captionChild.pid)
    } catch (e) {
      log('caption failed: ' + String(e).slice(0, 100))
    }
  }
  ensureCaption()
  const captionWatch = setInterval(ensureCaption, 30000)

  async function synthMp3(text) {
    const res = await fetch(TTS_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ text, prompt_audio: TTS_VOICE, format: 'mp3' }),
    })
    if (!res.ok) throw new Error('tts http ' + res.status)
    const buf = Buffer.from(await res.arrayBuffer())
    if (!buf.length) throw new Error('tts empty')
    return buf
  }

  // ---------- 语音实现（dsh-pet 用它来念自己的碎碎念） ----------
  if (ctx.petApi7340?.voice?.register) {
    ctx.effect(() => ctx.petApi7340.voice.register({
      id: 'indextts',
      name: 'IndexTTS (Venti)',
      async synthesize(input) {
        // 语音开关关掉时，连 dsh-pet 自己的碎碎念也不许出声
        if (!state.voiceOn) throw new Error('voice disabled by hotkey')
        const text = String(input?.text ?? '').trim()
        if (!text) return new Uint8Array(0)
        return new Uint8Array(await synthMp3(text))
      },
    }))
  }

  // ---------- 感知 ----------
  const state = {
    screen: null, history: [], speech: [], mic: true, hotkey: '',
    voiceOn: true, voiceKey: '',
    queue: [], seq: 0, lastRoastAt: 0, lastContextHash: '', lastText: '', busy: false,
  }
  state.voiceOn = initialVoiceOn
  const workers = { screen: null, mic: null }

  function runWorker(label, python, script, args, opts = {}) {
    if (!existsSync(python) || !existsSync(script)) {
      log('worker missing: ' + label)
      return () => {}
    }
    let child = null
    let stopped = false
    let restartTimer = null
    const start = () => {
      if (stopped) return
      child = spawn(python, [script, ...args], {
        cwd: PLUGIN_DIR, windowsHide: true,
        stdio: [opts.stdin ? 'pipe' : 'ignore', 'pipe', 'pipe'],
      })
      workers[label] = child
      const rl = createInterface({ input: child.stdout })
      rl.on('line', (line) => {
        let evt
        try { evt = JSON.parse(line) } catch { return }
        onEvent(label, evt)
      })
      child.stderr.on('data', (d) => {
        const s = String(d).trim()
        if (s && !s.includes('%|')) log(label + ' stderr: ' + s.slice(0, 140))
      })
      child.on('exit', (code) => {
        if (stopped) return
        log(label + ' exited ' + code + ', restart in 5s')
        restartTimer = setTimeout(start, 5000)
      })
      log(label + ' started pid=' + child.pid)
    }
    start()
    return () => {
      stopped = true
      if (restartTimer) clearTimeout(restartTimer)
      try { child?.kill() } catch { /* ignore */ }
    }
  }

  function onEvent(label, evt) {
    if (evt.type === 'screen') {
      state.screen = {
        title: evt.title || '', proc: evt.proc || '', monitor: evt.monitor || '',
        text: evt.text || '', stats: evt.stats || null, ts: evt.ts || Date.now(),
      }
      state.history.push({
        title: state.screen.title, proc: state.screen.proc,
        text: (state.screen.text || '').slice(0, 60),
        hue: evt.stats?.hue || '', diff: evt.stats?.diff ?? null,
      })
      while (state.history.length > HISTORY_SIZE) state.history.shift()
      writeState()
      scheduleRoast()
    } else if (evt.type === 'speech') {
      state.speech.push(evt.text)
      while (state.speech.length > 3) state.speech.shift()
      scheduleRoast()
    } else if (evt.type === 'mic') {
      state.mic = !!evt.on
      log('mic ' + (evt.on ? 'on' : 'off'))
      writeState()
    } else if (evt.type === 'voice') {
      state.voiceOn = !state.voiceOn
      log('voice ' + (state.voiceOn ? 'on' : 'off'))
      if (!state.voiceOn) {
        state.queue.length = 0          // 丢掉还没播的那句
        log('cleared pending audio queue')
      }
    } else if (evt.type === 'voicekey') {
      state.voiceKey = evt.ok ? (evt.keys || '') : 'none'
      log('voice hotkey ' + (evt.keys || '') + ' ' + (evt.ok ? 'ok' : 'failed'))
    } else if (evt.type === 'hotkey') {
      state.hotkey = evt.ok ? (evt.keys || '') : 'none'
      log('hotkey ' + (evt.keys || '') + ' ' + (evt.ok ? 'ok' : 'failed'))
    } else if (evt.type === 'ready') {
      log('mic model ready')
    } else if (evt.type === 'error') {
      log(label + ' error: ' + (evt.msg || ''))
    }
  }

  function freeVramMb() {
    try {
      return Number(execFileSync('nvidia-smi',
        ['--query-gpu=memory.free', '--format=csv,noheader,nounits'],
        { timeout: 1500, encoding: 'utf8', windowsHide: true }).trim().split('\n')[0])
    } catch {
      return null
    }
  }

  async function push(text) {
    let audio = null
    const voiceOn = state.voiceOn   // 热键说了算；配置只在启动时决定初始值
    const free = voiceOn ? freeVramMb() : null
    if (!voiceOn) {
      log('语音关闭，只弹气泡（Ctrl+Shift+P 打开）')
    } else if (free === null || free >= 1200) {
      try {
        const buf = await synthMp3(text)
        const id = ++state.seq
        writeFileSync(join(AUDIO_DIR, id + '.mp3'), buf)
        audio = { id, durMs: Math.round(buf.length * 8 / 64) }
      } catch (e) {
        log('synth failed (bubble only): ' + String(e).slice(0, 120))
      }
    }
    state.queue.push({
      id: audio?.id ?? ++state.seq, text, at: Date.now(),
      audio: !!audio, durMs: audio?.durMs ?? 0,
    })
    while (state.queue.length > 1) state.queue.shift()
    const who = (state.screen?.proc || '?') + ' | ' +
      (state.screen?.title || '').slice(0, 18) + ' | ' + (state.screen?.monitor || '?')
    log('ready' + (audio ? ' (' + audio.durMs + 'ms)' : ' (bubble only)') + ' [' + who + '] ' + text)
    writeState({ say: text })
  }

  function scheduleRoast() {
    if (state.busy) return
    const now = Date.now()
    if (now - state.lastRoastAt < ROAST_MIN_GAP_MS) return
    const s = state.screen
    const hash = [
      s?.title || '', s?.proc || '', (s?.text || '').slice(0, 120),
      s?.stats?.hue || '', s?.stats ? Math.round(s.stats.diff / 10) : '',
      state.speech.join('/'),
    ].join('|')
    if (hash === state.lastContextHash) return
    state.lastContextHash = hash
    state.lastRoastAt = now
    state.busy = true
    const t0 = Date.now()
    roast()
      .then(() => log('roast done in ' + (Date.now() - t0) + 'ms'))
      .catch((e) => log('roast failed: ' + String(e).slice(0, 160)))
      .finally(() => { state.busy = false })
  }

  async function roast() {
    if (!ctx.llm) return
    const s = state.screen
    if (!s && !state.speech.length) return
    const parts = []
    if (s) {
      parts.push('【当前画面】' + (s.title || '（无标题）') +
        (s.proc ? '（进程 ' + s.proc + '）' : '') +
        (s.monitor ? '（显示器 ' + s.monitor + '）' : ''))
      if (s.stats) {
        parts.push('亮度 ' + s.stats.bright + '，对比 ' + s.stats.std +
          '，与上一帧差异 ' + s.stats.diff + '，主色偏' + s.stats.hue)
      }
      const t = (s.text || '').replace(/\s+/g, ' ').slice(0, 300)
      parts.push(t ? '屏幕文字（OCR）：' + t : '屏幕上没有可识别文字（多半是游戏或视频）。')
      if (state.history.length > 1) {
        const track = state.history.slice(-5).map((h, i) => {
          const bits = [h.proc || h.title || '画面']
          if (h.hue) bits.push(h.hue + (h.diff !== null ? '/Δ' + Math.round(h.diff) : ''))
          if (h.text) bits.push('「' + h.text.slice(0, 24) + '」')
          return (i + 1) + '. ' + bits.join(' ')
        })
        parts.push('【最近十几秒的画面轨迹】\n' + track.join('\n'))
      }
    }
    if (state.speech.length) parts.push('【用户刚说的话】' + state.speech.join(' / '))
    if (state.lastText) parts.push('【你上一句吐槽】' + state.lastText)

    const prompt = PERSONA + '\n\n' + parts.join('\n') + `

现在说一句中文吐槽（不超过 35 字，口语、俏皮，可带「诶嘿」）。
规则：只输出这句话本身，不要引号、不要解释、不要提「我看到/听到」；尽量接着上一句说，同一场景就吐槽进展别重复；游戏就按游戏内容说，看不清就含糊点；画面很暗或几乎没动就吐槽发呆。`

    let text = ''
    for await (const chunk of ctx.llm.stream({
      provider: PROVIDER, model: MODEL,
      messages: [{ role: 'user', content: [{ type: 'text', text: prompt }] }],
    })) {
      if (!chunk) continue
      if (chunk.type === 'text-delta') text += chunk.text || chunk.delta?.text || ''
      else if (chunk.text && !chunk.type) text += chunk.text
    }
    text = String(text).replace(/^["'「『]+|["'」』]+$/g, '').replace(/\s+/g, ' ').trim()
    if (!text) return
    if (text.length > 35) text = text.slice(0, 35)
    if (text === state.lastText) return
    state.lastText = text
    await push(text)
  }

  // ---------- 页面接口 ----------
  const sentAt = new Map()
  let pollHits = 0

  const offNext = ctx.webServer.register({
    kind: 'prefix',
    path: '/dsh-pet-venti/next',
    handler: async (req, res) => {
      pollHits++
      if (pollHits % 60 === 1) log('client poll #' + pollHits + ' queued=' + state.queue.length)
      const busy = String(req.headers['x-pet-busy'] || '') === '1'
      const lines = busy ? [] : state.queue.splice(0, 1)
      for (const l of lines) sentAt.set(l.id, l.at)
      res.setHeader('content-type', 'application/json; charset=utf-8')
      res.end(JSON.stringify({ lines, mic: state.mic, hotkey: state.hotkey,
        voice: state.voiceOn, voiceKey: state.voiceKey }))
    },
  })

  const offAudio = ctx.webServer.register({
    kind: 'prefix',
    path: '/dsh-pet-venti/audio',
    handler: async (req, res) => {
      const m = /\/dsh-pet-venti\/audio\/(\d+)\.mp3/.exec(req.url || '')
      if (!m) { res.statusCode = 404; return res.end() }
      try {
        const buf = readFileSync(join(AUDIO_DIR, m[1] + '.mp3'))
        res.setHeader('content-type', 'audio/mpeg')
        res.setHeader('content-length', String(buf.length))
        res.end(buf)
      } catch {
        res.statusCode = 404
        res.end()
      }
    },
  })

  const offToggle = ctx.webServer.register({
    kind: 'prefix',
    path: '/dsh-pet-venti/toggle',
    handler: async (req, res) => {
      try {
        workers.mic?.stdin?.write('toggle\n')
        res.statusCode = 202
        res.end('toggled')
      } catch {
        res.statusCode = 500
        res.end('no mic worker')
      }
    },
  })

  const offReport = ctx.webServer.register({
    kind: 'prefix',
    path: '/dsh-pet-venti/report',
    handler: async (req, res) => {
      let body = ''
      try { for await (const chunk of req) body += chunk } catch { /* ignore */ }
      let info = null
      try { info = JSON.parse(body) } catch { /* ignore */ }
      const at = info && sentAt.get(info.id)
      if (at) sentAt.delete(info.id)
      log('played: age=' + (at ? (Date.now() - at) + 'ms' : '?') +
        ' status=' + (info?.status ?? '?') + ' text=' + String(info?.text ?? '').slice(0, 32))
      res.statusCode = 204
      res.end()
    },
  })

  const STATE_FILE = join(PLUGIN_DIR, 'state.json')
  function writeState(extra = {}) {
    try {
      writeFileSync(STATE_FILE, JSON.stringify({
        at: Date.now(),
        proc: state.screen?.proc || '',
        title: state.screen?.title || '',
        monitor: state.screen?.monitor || '',
        text: state.screen?.text ? state.screen.text.slice(0, 80) : '',
        last: state.lastText || '',
        mic: state.mic,
        ...extra,
      }, null, 2), 'utf8')
    } catch { /* ignore */ }
  }

  const stopScreen = runWorker('screen', PY_TOOLS, join(PLUGIN_DIR, 'sense_screen.py'),
    ['--interval', String(SCREEN_INTERVAL)])
  const stopMic = runWorker('mic', PY_LLM, join(PLUGIN_DIR, 'sense_mic.py'), ['--model', 'small'],
    { stdin: true })

  const dispose = () => {
    for (const off of [offNext, offAudio, offToggle, offReport]) {
      try { off?.() } catch { /* ignore */ }
    }
    stopScreen()
    stopMic()
    clearInterval(cfgWatch)
    clearInterval(captionWatch)
    try { if (captionChild?.pid) execFileSync('taskkill', ['/PID', String(captionChild.pid), '/F'], { windowsHide: true, stdio: 'ignore' }) } catch { /* ignore */ }
    if (globalThis.__petVentiDispose === dispose) globalThis.__petVentiDispose = null
  }
  globalThis.__petVentiDispose = dispose
  ctx.effect(() => dispose)

  log('ready (dsh-pet mode: persona + anims + voice + sensing)')
}
