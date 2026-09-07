#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/dist/macos/MonsterPrank.app"

if [[ -d "$APP" ]]; then
  open "$APP"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 python3。请先运行 build_macos.sh 构建软件包。"
  read -r -p "按回车键关闭……"
  exit 1
fi

exec python3 "$ROOT/monster_prank.py"
