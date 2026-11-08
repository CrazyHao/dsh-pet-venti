# dsh-pet-venti

让 DSH 桌宠变成「温迪」：人设、皮肤、声音、眼睛和耳朵，一个插件装好。

> 需要先装好 **DSH** 和 **dsh-pet**（本插件复用 dsh-pet 的窗口、气泡和语音槽位）。

## 它做什么

| 能力 | 说明 |
|---|---|
| 人设 | 把温迪人设写进 `~/.dsh/dsh-pet/main-config.jsonc`（只写一次，备份 `.venti.bak`） |
| 皮肤 | 自带 114 个动作名的 webm（155 个文件），同步到 `~/.dsh/dsh-pet/main-animation/webm` |
| 声音 | 注册 IndexTTS 语音实现，本地零样本克隆；先合成 mp3 再和气泡同时呈现，播完才出下一条 |
| 感知 | 每 3 秒截屏（DXGI + OCR + 画面统计，跟随前台窗口所在显示器）、麦克风 VAD + whisper |
| 吐槽 | 攒最近 5 帧画面轨迹 + 上一句，调模型写一句（≤35 字），方向跟着你的屏幕走 |
| 小字条 | 桌宠脚下显示「正在观看：进程名」 |

热键：`Ctrl+Shift+Q` 开关麦克风，`Ctrl+Shift+P` 开关朗读（被占用时自动降级到 Alt 组合）。

## 安装

### 1. 装插件

```powershell
# 在 DSH 里：设置 → 插件 → 安装本地目录，选中本仓库目录
# 或者让插件管理器执行：
#   install_bundle <本仓库路径>
```

### 2. 装运行环境（一次性，约 20 GB）

```powershell
cd <本仓库>
powershell -ExecutionPolicy Bypass -File .\setup.ps1
# 可选：-DataDir D:\dsh-pet-venti   -SkipModels
```

`setup.ps1` 会：装 Python 3.11 → 感知 venv（winsdk/pillow/sounddevice）→ 拉 index-tts 源码 →
装 torch 2.8 cu128 与全部依赖 → 下 IndexTTS-2.5 模型（hf-mirror）→ 复制适配器 → 写 `paths.json`。

### 3. 放一段参考音频

零样本克隆需要一段干净的人声（5~15 秒、单人、无 BGM）：

```
%USERPROFILE%\.dsh\pet-venti\voices\我的音色.wav
```

放好后重启 DSH，或在插件页停用/启用一次。

### 4. 验证

- 桌宠会弹一句吐槽，脚下出现「正在观看：xxx」
- 想看得更清楚：双击 `watch.cmd` 开一个常驻窗口，实时显示它在看什么、说了什么

## 配置

在 DSH「设置 → 插件 → 温迪版桌宠」里改：截屏间隔、吐槽间隔、语音/麦克风默认开关等。

## 环境变量（可选）

| 变量 | 作用 |
|---|---|
| `PET_VENTI_VOICE` | 参考音色名（voices 目录里的文件名，默认 `venti_b`） |
| `PET_VENTI_LLM_PROVIDER` / `PET_VENTI_LLM_MODEL` | 走哪个模型（默认 `deepseek-account` / `deepseek-flash`） |
| `PET_VENTI_BODY_PORT` | 桌宠窗口端口（默认 7891） |

## 结构

```
index.js          宿主插件：配置同步、语音实现、感知、吐槽、投递
client.js         页面侧：拉队列、弹气泡、播音频（单飞防打断）
sense_screen.py   截屏 + OCR + 画面统计（跟随前台窗口的显示器）
sense_mic.py      VAD + whisper 转写，Ctrl+Shift+Q / Ctrl+Shift+P 热键
pet_caption.py    桌宠脚下的「正在观看：xxx」
adapter.py        IndexTTS HTTP 适配器（setup 时装到数据目录）
setup.ps1         一键装环境
assets/webm/      155 段动画素材
```

## 已知限制

- 素材来自 17 段原始动作，114 个动作名里有一部分是同一段素材的变体，视觉上会重复。
- 独占全屏的游戏可能抓不到画面，建议用无边框窗口。
- 显存吃紧（<1.2 GB 空闲）时会自动只弹气泡不朗读。
- 不附带任何参考音频；音色需要你自己提供素材。

## License

MIT（代码与文档）。IndexTTS、RVC 及其模型遵循各自许可证；素材版权归原作者。
