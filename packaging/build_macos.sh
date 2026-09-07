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

# ⛔⛔ 改完 Info.plist **必须重新签名**，这一段不许删也不许挪到 ditto 后面。
#     PyInstaller 在 BUNDLE 那一步已经给 .app 盖了一个 ad-hoc 签名，而 Info.plist
#     是被那个签名**封在里面**的。上面 PlistBuddy 一动它，封印当场作废：
#         codesign --verify → invalid Info.plist (plist or signature have been modified)
#     后果不是“少了个签名”，是**签名坏了**——这两件事在 macOS 眼里差别极大：
#       · 没签名／ad-hoc 签名 → “无法验证开发者”，右键“打开”能绕过去
#       · 签名坏了           → “已损坏，请移到废纸篓”，右键“打开”**也救不回来**
#     2026-09-07 的 v1.1.0 就是这么发出去的，见 docs/BUGS.md。

ENTITLEMENTS="$HERE/MonsterPrank.entitlements"
if [[ ! -f "$ENTITLEMENTS" ]]; then
  echo "缺少权限清单：$ENTITLEMENTS" >&2
  exit 1
fi

# 有 Developer ID Application 证书就用它（可发给别人 ＋ 能公证），没有就退回 ad-hoc。
# ⚠️ 免费的“个人团队”只签得出 Apple Development，那种证书**发给别人一样打不开**，
#    所以这里只认 Developer ID，绝不拿 Apple Development 顶上。
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
if [[ -z "$SIGN_IDENTITY" ]]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
fi
if [[ -n "$SIGN_IDENTITY" ]]; then
  echo "签名身份：$SIGN_IDENTITY"
  SIGN_ARGS=(--sign "$SIGN_IDENTITY" --options runtime --timestamp
             --entitlements "$ENTITLEMENTS")
else
  echo "没有 Developer ID Application 证书，退回 ad-hoc 签名（可本机运行，不适合分发）。"
  SIGN_ARGS=(--sign - )
fi

# ⛔ 顺序是硬要求：**先内后外**。签外壳会把里面所有东西的哈希封进去，
#    所以任何一个内嵌的东西在外壳之后再签，外壳的封印当场作废。
#    三步走：① 所有 Mach-O 文件 → ② 嵌套的 bundle（本包里是 Python.framework，
#    框架必须**整体**签，只签里面那个 dylib 不算数）→ ③ 外壳。
echo "正在签名……"
signed=0
while IFS= read -r f; do
  if file -b "$f" 2>/dev/null | grep -q "Mach-O"; then
    codesign --force "${SIGN_ARGS[@]}" "$f" >/dev/null 2>&1 || {
      echo "签名失败：$f" >&2; exit 1; }
    signed=$((signed + 1))
  fi
done < <(find "$APP" -type f)

bundles=0
while IFS= read -r b; do
  [[ -z "$b" ]] && continue
  codesign --force "${SIGN_ARGS[@]}" "$b" >/dev/null 2>&1 || {
    echo "签名失败：$b" >&2; exit 1; }
  bundles=$((bundles + 1))
done < <(find "$APP" -type d \( -name "*.framework" -o -name "*.bundle" \) | awk '{print length, $0}' | sort -rn | cut -d' ' -f2-)

codesign --force "${SIGN_ARGS[@]}" "$APP"
echo "  已签 $signed 个二进制 ＋ $bundles 个嵌套框架 ＋ 外壳"

# ⭐ 这道闸是这次事故的直接产物：光看 codesign 有没有报错是不够的，
#    上一版就是“没抛异常但签名是坏的”。这里真去验一遍封印。
#    ⛔ 别写成 `codesign ... | tail`：管道的退出码是 tail 的，codesign 红了也看不见
#       （这里只是恰好有 pipefail 兜着，但闸不该指望远处的一行设置）。
if ! verify_out="$(codesign --verify --deep --strict --verbose=2 "$APP" 2>&1)"; then
  echo "签名自检没过：" >&2
  echo "$verify_out" | tail -5 >&2
  exit 1
fi
echo "  签名自检通过：$(echo "$verify_out" | tail -1)"
codesign -dvv "$APP" 2>&1 | grep -E "^(Identifier|Authority|TeamIdentifier|Signature)" | sed 's/^/  /'

ARCHIVE="$ROOT/dist/MonsterPrank-macOS.zip"
rm -f "$ARCHIVE"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"

# 公证：要 Developer ID 签名 ＋ 一份存好的 App Store Connect 凭据。
# 先存一次（只需一次，之后一直在钥匙串里）：
#   xcrun notarytool store-credentials MonsterPrank \
#     --apple-id <你的 Apple ID> --team-id <团队 ID> --password <专用密码>
# 专用密码在 https://account.apple.com 的“登录与安全 → App 专用密码”里生成。
NOTARY_PROFILE="${NOTARY_PROFILE:-MonsterPrank}"
if [[ -n "$SIGN_IDENTITY" ]] \
   && security find-generic-password -s "com.apple.gke.notary.tool" -a "$NOTARY_PROFILE" >/dev/null 2>&1; then
  echo "正在公证（要等 Apple 那边跑完，通常几分钟）……"
  xcrun notarytool submit "$ARCHIVE" --keychain-profile "$NOTARY_PROFILE" --wait
  # 装订：把公证票据钉进 .app，之后**断网也能打开**。⛔ 钉的是 .app 不是 zip，
  #       所以钉完必须重新打包，否则发出去的还是没票据的那一份。
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  rm -f "$ARCHIVE"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
  echo "已公证并装订。"
else
  echo "跳过公证（没有 Developer ID 证书，或钥匙串里没有名为 $NOTARY_PROFILE 的凭据）。"
fi

# 最后按 macOS 自己的分发标准评一次，把结论如实打出来。
# ⛔ 这一段只报告不拦：没公证时它必然报 Fatal，那是已知状态不是构建失败。
#    唯一不许出现的是 Info.plist 那一条——上面已经拦死了。
#    ⚠️ 它没通过时**退出码是非零的**（未公证时实测 70），而本脚本开头是
#       `set -euo pipefail`——所以必须 `|| true` 兜住，否则这行会把整个脚本掐死在
#       最后一步，前面全部做完了却报失败（2026-09-07 踩过一次）。
if command -v syspolicy_check >/dev/null 2>&1; then
  echo "分发评估（syspolicy_check）："
  syspolicy_check distribution "$APP" 2>&1 | sed 's/^/  /' | head -30 || true
fi

echo "macOS 应用包已生成：$APP"
echo "macOS 压缩包已生成：$ARCHIVE"
