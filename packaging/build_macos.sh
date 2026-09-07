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

VERSION="${VERSION:-1.1.0}"
BUNDLE_ID="io.github.appapp777.monsterprank"
COPYRIGHT="© 2026 七也"

VIDEO="$ROOT/assets/monster_transparent_burst_shake.webm"
POSTER="$ROOT/assets/monster_transparent_burst_shake_poster.png"
METADATA="$ROOT/assets/monster_transparent_burst_shake_metadata.json"
AUDIO="$ROOT/assets/monster_transparent_burst_shake_audio.wav"
THUMB="$ROOT/assets/monster_transparent_burst_shake_thumb.png"
ICONPNG="$ROOT/assets/logo/logo-128.png"
ICONICNS="$ROOT/assets/logo/MonsterPrank.icns"   # 应用图标，没有它 .app 会顶着默认的 Python 火箭图标
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
if [[ ! -f "$ICONICNS" ]]; then
  echo "缺少应用图标：$ICONICNS" >&2
  echo "生成方法见 docs/progress.md（sips/PIL 出 iconset 后 iconutil -c icns）" >&2
  exit 1
fi

# 依赖预检必须逐个报名字：漏一个会打出一个一起手就崩的包，
# 而 PyInstaller 本身不会报错（2026-09-07 踩过，见 docs/BUGS.md）。
# ⛔ 这份名单里**故意没有 tkinter 与 customtkinter**：macOS 的界面已整条换成 AppKit，
#    一行 Tk 都不走。BUGS.md 里“漏了 customtkinter”那条说的是当时界面还在 Tk 上，
#    别照着那条把它加回来。
missing=""
for mod in PIL av PyInstaller AppKit Quartz; do
  "$PYTHON" -c "import $mod" >/dev/null 2>&1 || missing="$missing $mod"
done
if [[ -n "$missing" ]]; then
  echo "缺少构建依赖：$missing" >&2
  echo "请先运行：python3 -m pip install -r packaging/requirements-build.txt" >&2
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
  --icon "$ICONICNS"
  --name MonsterPrank
  --distpath "$ROOT/dist/macos"
  --workpath "$ROOT/build"
  --specpath "$ROOT/build"
  --collect-all av
  --collect-all objc
  --exclude-module numpy
  --exclude-module PIL.AvifImagePlugin
  # mac 版一行 Tk 都不走，排掉能省下十几兆的 Tcl/Tk，也免得 PIL 顺手把它拖进来
  --exclude-module tkinter
  --exclude-module customtkinter
  --exclude-module PIL.ImageTk
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

# PyInstaller 默认把版本写成 0.0.0、署名一栏干脆空着。Windows 那边靠
# packaging/version_info.txt 把这几项打进 exe，mac 这边只能事后改 Info.plist。
# ⚠️ 版本号要跟 Release 和 version_info.txt 对上，改一处就得改另一处。
PLIST="$APP/Contents/Info.plist"
plist_set() {   # 逐项来：Set 不成再 Add。⛔ 别把四项串成一条 PlistBuddy 命令，
                # 其中任何一项失败会让整条命令失败，剩下三项被静默跳过。
  /usr/libexec/PlistBuddy -c "Set :$1 $3" "$PLIST" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :$1 $2 $3" "$PLIST" >/dev/null
}
plist_set CFBundleShortVersionString string "$VERSION"
plist_set CFBundleVersion            string "$VERSION"
plist_set CFBundleIdentifier         string "$BUNDLE_ID"
plist_set NSHumanReadableCopyright   string "$COPYRIGHT"

# 写完回读一遍再说“已写入”——“没抛异常”不等于“写进去了”。
for key in CFBundleShortVersionString CFBundleIdentifier NSHumanReadableCopyright; do
  got="$(/usr/libexec/PlistBuddy -c "Print :$key" "$PLIST" 2>/dev/null || true)"
  if [[ -z "$got" ]]; then
    echo "Info.plist 的 $key 没写进去" >&2
    exit 1
  fi
  echo "  Info.plist $key = $got"
done

ARCHIVE="$ROOT/dist/MonsterPrank-macOS.zip"
rm -f "$ARCHIVE"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
echo "macOS 应用包已生成：$APP"
echo "macOS 压缩包已生成：$ARCHIVE"
