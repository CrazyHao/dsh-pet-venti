/**
 * dsh-pet-venti 页面半边：排队 + 同步呈现（结合 dsh-pet 版）
 *
 * 宿主先合成好 mp3，这里拿到一条就：
 *   1. 让 dsh-pet 弹气泡（speak=false，避免和我们的音频打架）
 *   2. 同时播这段 mp3 —— 气泡和声音一起出现
 *   3. 播完才向宿主请求下一条，避免互相打断
 * 另外把麦克风/热键状态用短气泡提示出来。
 */
window.__ModuleLoader__.load({
  id: 'dsh-pet-venti',
  factory(require) {
    const POLL_MS = 800
    const ANIM = '点击回应-开心跃动'
    let playing = false
    let lastMic = null
    let currentAudio = null
    let currentCancel = null
    let lastVoice = null

    const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

    async function notify(text, duration = 3500) {
      try {
        await fetch('/dsh-pet-7340/broadcast?pet=main&surface=desktop', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ text, speak: false, duration }),
        })
      } catch {
        /* ignore */
      }
    }

    function playAudio(id, durMs) {
      return new Promise((resolve) => {
        let done = false
        const finish = () => {
          if (done) return
          done = true
          currentAudio = null
          currentCancel = null
          resolve()
        }
        const audio = new Audio('/dsh-pet-venti/audio/' + id + '.mp3')
        currentAudio = audio
        currentCancel = finish
        audio.onended = finish
        audio.onerror = finish
        setTimeout(finish, Math.max(4000, (durMs || 3000) + 3000))
        audio.play().catch(finish)
      })
    }

    /** 关语音时把正在播的那句立刻掐掉 */
    function stopSpeaking() {
      try {
        if (currentAudio) { currentAudio.pause(); currentAudio.currentTime = 0 }
      } catch { /* ignore */ }
      try { currentCancel?.() } catch { /* ignore */ }
      currentAudio = null
      currentCancel = null
    }

    function syncVoiceState(voice) {
      if (voice === undefined) return
      const on = voice !== false
      if (lastVoice === null) { lastVoice = on; return }
      if (on !== lastVoice) {
        lastVoice = on
        if (!on) stopSpeaking()
        void notify(on ? '诶嘿，我开口说话啦' : '先闭嘴，只弹气泡', 2500)
      }
    }

    function syncMicState(mic, hotkey) {
      const on = mic !== false
      if (lastMic === null) {
        lastMic = on
        const key = hotkey && hotkey !== 'none' ? hotkey : ''
        let hint = on ? '耳朵开着呢' : '耳朵关着'
        if (key && key !== 'Ctrl+Shift+Q') hint += '，' + key + ' 开关（Q 被占用了）'
        else if (key) hint += '，' + key + ' 可以关'
        else hint += '（热键没注册上）'
        void notify(hint, 4500)
        return
      }
      if (on !== lastMic) {
        lastMic = on
        void notify(on ? '诶嘿，麦克风打开了' : '麦克风关掉了', 2500)
      }
    }

    async function handle(line) {
      const text = String(line?.text || '').trim()
      if (!text) return
      const durMs = Number(line?.durMs || 0)
      let status = 0
      let info = ''
      try {
        const r = await fetch('/dsh-pet-7340/broadcast?pet=main&surface=desktop', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            text,
            speak: line.audio !== true,
            anim: ANIM,
            duration: Math.max(6000, durMs + 1200),
          }),
        })
        status = r.status
        if (!r.ok) info = (await r.text().catch(() => '')).slice(0, 120)
      } catch (e) {
        info = String(e).slice(0, 120)
      }
      if (line.audio === true) {
        await playAudio(line.id, durMs)
        await sleep(250)
      }
      try {
        await fetch('/dsh-pet-venti/report', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ id: line.id, status, info, text: text.slice(0, 40) }),
        })
      } catch {
        /* ignore */
      }
    }

    async function drain() {
      if (playing) return
      let data
      try {
        const r = await fetch('/dsh-pet-venti/next', {
          headers: { accept: 'application/json', 'x-pet-busy': playing ? '1' : '0' },
        })
        if (!r.ok) return
        data = await r.json()
      } catch {
        return
      }
      const lines = Array.isArray(data?.lines) ? data.lines : []
      syncMicState(data?.mic, data?.hotkey)
      syncVoiceState(data?.voice)
      if (!lines.length) return
      playing = true
      try {
        for (const line of lines) await handle(line)
      } finally {
        playing = false
      }
    }

    return {
      inject: [],
      apply(ctx) {
        let timer = null
        ctx.effect(() => {
          timer = setInterval(() => { void drain() }, POLL_MS)
          void drain()
          return () => { if (timer) clearInterval(timer) }
        })
      },
    }
  },
})
