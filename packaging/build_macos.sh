#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"   # 脚本在 packaging/，项目根在上一级
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "找不到 Python，请先安装 Python 3.10 或更高版本。" >&2
  exit 1
fi

VIDEO="$ROOT/assets/monster_transparent_burst_shake.webm"
POSTER="$ROOT/assets/monster_transparent_burst_shake_poster.png"
METADATA="$ROOT/assets/monster_transparent_burst_shake_metadata.json"
AUDIO="$ROOT/assets/monster_transparent_burst_shake_audio.wav"
THUMB="$ROOT/assets/monster_transparent_burst_shake_thumb.png"
ICONPNG="$ROOT/assets/logo/logo-128.png"
if [[ ! -f "$VIDEO" ]]; then
  echo "缺少默认透明视频：$VIDEO" >&2
  exit 1
fi
if [[ ! -f "$POSTER" ]]; then
  echo "缺少默认透明首帧：$POSTER" >&2
  exit 1
fi
if [[ ! -f "$METADATA" ]]; then
  echo "缺少默认视频元数据：$METADATA" >&2
  exit 1
fi
if [[ ! -f "$AUDIO" ]]; then
  echo "缺少默认音频：$AUDIO" >&2
  exit 1
fi
if [[ ! -f "$THUMB" ]]; then
  echo "缺少默认缩略图：$THUMB" >&2
  exit 1
fi

if ! "$PYTHON" -c 'import tkinter, PIL, PyInstaller' >/dev/null 2>&1; then
  echo "缺少构建依赖，请先运行：python3 -m pip install -r requirements-build.txt" >&2
  exit 1
fi

tool_path() {
  command -v "$1" 2>/dev/null || true
}

FFMPEG="$(tool_path ffmpeg)"
if [[ -z "$FFMPEG" ]]; then
  echo "找不到 ffmpeg，请先安装 FFmpeg，并确认它在 PATH 中。" >&2
  exit 1
fi

PYINSTALLER_ARGS=(
  -m PyInstaller
  --clean
  --noconfirm
  --onedir
  --windowed
  --name MonsterPrank
  --distpath "$ROOT/dist/macos"
  --workpath "$ROOT/build"
  --specpath "$ROOT/build"
  --exclude-module av
  --exclude-module numpy
  --exclude-module PIL.AvifImagePlugin
  --add-data "$VIDEO:assets"
  --add-data "$POSTER:assets"
  --add-data "$METADATA:assets"
  --add-data "$AUDIO:assets"
  --add-data "$THUMB:assets"
  --add-data "$ICONPNG:assets"
  --add-binary "$FFMPEG:runtime"
  "$ROOT/monster_prank.py"
)

echo "正在构建 macOS 软件包……"
"$PYTHON" "${PYINSTALLER_ARGS[@]}"

APP="$ROOT/dist/macos/MonsterPrank.app"
if [[ ! -d "$APP" ]]; then
  echo "构建完成但没有找到应用包：$APP" >&2
  exit 1
fi
cp "$ROOT/README.md" "$APP/Contents/Resources/README.md"

ARCHIVE="$ROOT/dist/MonsterPrank-macOS.zip"
rm -f "$ARCHIVE"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
echo "macOS 应用包已生成：$APP"
echo "macOS 压缩包已生成：$ARCHIVE"
