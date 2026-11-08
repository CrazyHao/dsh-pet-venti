# 温迪桌宠（dsh-pet-venti）一键装环境
#
# 干的事：Python 3.11 → 感知 venv → index-tts 源码 + torch(cu128) → IndexTTS 模型 → 适配器 → 写 paths.json
# 用法（在插件目录里）：
#     powershell -ExecutionPolicy Bypass -File .\setup.ps1
# 可选：-DataDir D:\dsh-pet-venti  -SkipModels
param(
  [string]$DataDir = "$env:USERPROFILE\.dsh\pet-venti",
  [switch]$SkipModels
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$PluginDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TUNA = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$HF = 'https://hf-mirror.com'

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

function Download($url, $dst) {
  if (Test-Path $dst) { Ok "已有 $(Split-Path -Leaf $dst)"; return }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
  Write-Host "    下载 $url"
  Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# ---------- 1. Python 3.11 ----------
Step "Python 3.11"
$PyDir = Join-Path $DataDir 'py311'
$Py = Join-Path $PyDir 'python\python.exe'
if (-not (Test-Path $Py)) {
  $tar = Join-Path $DataDir 'py311.tar.gz'
  Download 'https://registry.npmmirror.com/-/binary/python-build-standalone/20261026/cpython-3.11.18+20261026-x86_64-pc-windows-msvc-install_only.tar.gz' $tar
  New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
  tar -xzf $tar -C $PyDir
}
if (-not (Test-Path $Py)) { throw "Python 解压失败：$Py" }
Ok (& $Py -c "import sys;print(sys.version.split()[0])")

# ---------- 2. 感知 venv（截图 OCR / 麦克风） ----------
Step "感知环境（winsdk / pillow / sounddevice）"
$ToolsVenv = Join-Path $DataDir 'tools'
if (-not (Test-Path "$ToolsVenv\Scripts\python.exe")) {
  & $Py -m venv $ToolsVenv
}
& "$ToolsVenv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -i $TUNA numpy pillow scipy winsdk sounddevice
Ok "感知依赖装好"

# ---------- 3. index-tts 源码 ----------
Step "index-tts 源码"
$SrcRoot = Join-Path $DataDir 'src'
$Proj = Join-Path $SrcRoot 'index-tts-main'
if (-not (Test-Path (Join-Path $Proj 'indextts'))) {
  $tgz = Join-Path $DataDir 'index-tts.tar.gz'
  Download 'https://codeload.github.com/index-tts/index-tts/tar.gz/refs/heads/main' $tgz
  New-Item -ItemType Directory -Force -Path $SrcRoot | Out-Null
  tar -xzf $tgz -C $SrcRoot
}
if (-not (Test-Path (Join-Path $Proj 'indextts'))) { throw "源码解压失败：$Proj" }
Ok $Proj

# ---------- 4. TTS venv：torch + 依赖 ----------
Step "TTS 环境（torch cu128 + 依赖）"
$TtsVenv = Join-Path $Proj '.venv'
$TtsPy = Join-Path $TtsVenv 'Scripts\python.exe'
if (-not (Test-Path $TtsPy)) { & $Py -m venv $TtsVenv }

& $TtsPy -m pip install -q --disable-pip-version-check --upgrade pip
& $TtsPy -m pip install -q --disable-pip-version-check --index-url https://download.pytorch.org/whl/cu128 "torch==2.8.0+cu128" "torchaudio==2.8.0+cu128"
if ($LASTEXITCODE -ne 0) { Warn "torch 安装失败（可能没有 NVIDIA 卡）；后面 TTS 会不可用" }

$deps = @(
  'accelerate==1.8.1','cn2an==0.5.22','cython==3.0.7','einops>=0.8.1','ffmpeg-python==0.2.0',
  'fugashi>=1.2.0','json5==0.10.0','keras==2.9.0','librosa==0.10.2.post1','matplotlib==3.10.0',
  'modelscope==1.27.0','munch==4.0.0','numba==0.63.0','numpy==2.2.6','omegaconf>=2.3.0',
  'opencv-python==4.9.0.80','pandas==2.3.2','safetensors==0.5.2','sentencepiece>=0.2.1',
  'tensorboard==2.20.0','textstat>=0.7.10','tokenizers==0.21.0','requests>=2.28','tqdm>=4.67.1',
  'transformers==4.52.1','wetext>=0.0.9','flatten-dict','pyloudnorm','julius','rich','markdown',
  'mediapy','importlib-resources','protobuf','ffmpy','randomname','docstring-parser','imageio-ffmpeg',
  'nltk','inflect','more-itertools','tiktoken','regex'
)
& $TtsPy -m pip install -q --disable-pip-version-check --only-binary :all: -i $TUNA $deps
& $TtsPy -m pip install -q --disable-pip-version-check --no-deps -i $TUNA "g2p-en==2.1.0"

# 只出 sdist 的纯 Python 包：直接解压复制进 site-packages（Windows 上编译 distance 很麻烦）
$SP = Join-Path $TtsVenv 'Lib\site-packages'
$sdists = @(
  @{ n='jieba';          d='jieba' },
  @{ n='unidic-lite';    d='unidic_lite' },
  @{ n='openai-whisper'; d='whisper' },
  @{ n='argbind';        d='argbind' },
  @{ n='descript-audiotools'; d='audiotools' },
  @{ n='randomname';     d='randomname' }
)
foreach ($s in $sdists) {
  if (Test-Path (Join-Path $SP $s.d)) { continue }
  Warn "补装 $($s.n)"
  & $TtsPy -m pip install -q --disable-pip-version-check --no-deps -i $TUNA $s.n 2>$null
}
if (-not (Test-Path (Join-Path $SP 'distance'))) {
  New-Item -ItemType Directory -Force -Path (Join-Path $SP 'distance') | Out-Null
  @'
def levenshtein(s1, s2):
    if s1 == s2: return 0
    if not s1: return len(s2)
    if not s2: return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1, 1):
        cur = [i]
        for j, c2 in enumerate(s2, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (c1 != c2)))
        prev = cur
    return prev[-1]
def hamming(a, b): return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
def jaccard(a, b):
    A, B = set(a), set(b); return len(A & B) / len(A | B) if (A | B) else 1.0
def sorensen(a, b):
    A, B = set(a), set(b); return 2 * len(A & B) / (len(A) + len(B)) if (A or B) else 1.0
'@ | Set-Content (Join-Path $SP 'distance\__init__.py') -Encoding UTF8
}

& $TtsPy -m pip install -q --disable-pip-version-check --no-build-isolation --no-deps -e $Proj 2>$null
& $TtsPy -c "from indextts.infer_v2_5 import IndexTTS2; print('indextts ok')"
if ($LASTEXITCODE -ne 0) { Warn "indextts 导入失败，稍后看日志" }

# ---------- 5. 模型 ----------
$Ckpt = Join-Path $DataDir 'checkpoints'
if (-not $SkipModels) {
  Step "IndexTTS-2.5 模型（约 5 GB）"
  New-Item -ItemType Directory -Force -Path $Ckpt | Out-Null
  $files = @('config.yaml','gpt.pth','s2mel.pth','codec.pth','bpe.model','feat1.pt','feat2.pt',
             'wav2vec2bert_stats.pt','multilingual_zh_ja_yue_char_del.tiktoken','qwen0.6bemo4-merge/model.safetensors',
             'qwen0.6bemo4-merge/config.json','qwen0.6bemo4-merge/tokenizer.json','qwen0.6bemo4-merge/vocab.json')
  foreach ($f in $files) {
    $dst = Join-Path $Ckpt $f
    if (Test-Path $dst) { continue }
    Download "$HF/IndexTeam/IndexTTS-2.5/resolve/main/$f" $dst
  }
}

# ---------- 6. 适配器 ----------
Step "TTS 适配器"
$AdapterDir = Join-Path $DataDir 'indextts'
New-Item -ItemType Directory -Force -Path $AdapterDir | Out-Null
Copy-Item (Join-Path $PluginDir 'adapter.py') (Join-Path $AdapterDir 'adapter.py') -Force
Ok (Join-Path $AdapterDir 'adapter.py')

# ---------- 7. paths.json ----------
Step "写入 paths.json"
$paths = @{
  envRoot   = $DataDir
  toolsVenv = $ToolsVenv
  ttsVenv   = $TtsVenv
  adapter   = (Join-Path $AdapterDir 'adapter.py')
}
$paths | ConvertTo-Json | Set-Content (Join-Path $PluginDir 'paths.json') -Encoding UTF8
Ok (Join-Path $PluginDir 'paths.json')

Write-Host ""
Write-Host "完成。重启 DSH（或在插件页停用再启用 dsh-pet-venti）即可。" -ForegroundColor Cyan
