#!/usr/bin/env bash
# Codespaces / 容器里启动应用（前台运行，容器退出就停）
set -e
cd "$(dirname "$0")/.."

export P1_ENV="${P1_ENV:-dev}"
export P1_HOST="${P1_HOST:-0.0.0.0}"
export P1_PORT="${P1_PORT:-8765}"
export PYTHONIOENCODING=utf-8

echo "---------------------------------------------"
echo " 新平食品 P1"
echo " 目录：$(pwd)"
echo " 端口：$P1_PORT"
echo "---------------------------------------------"

# postCreateCommand 有时会静默失败，这里再兜一次底。
# 没装 openpyxl 的话，07_p1_server.py 一 import 就崩，现象是"端口没人监听"。
if ! python -c "import openpyxl" >/dev/null 2>&1; then
  echo "[deps] 正在安装 openpyxl ..."
  python -m pip install -q openpyxl==3.1.5 || true
fi
python -c "import openpyxl; print('[deps] openpyxl', openpyxl.__version__)" 2>/dev/null \
  || echo "[deps] 警告：openpyxl 仍然没装上，商品目录表可能读不了"

echo "[p1] 启动中，日志同时写到 /tmp/p1.log"
echo "---------------------------------------------"
echo " 打开方式：下方「端口」面板 → 8765 → 右键 → 端口可见性 → 公开"
echo " 演示账号：boss / demo1234"
echo "---------------------------------------------"

# tee 一份日志，出错时不用翻终端历史
exec python 07_p1_server.py 2>&1 | tee /tmp/p1.log
