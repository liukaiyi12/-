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
echo " 数据目录：${P1_DATA:-$(pwd)/_work}"
echo " 端口：$P1_PORT"
echo "---------------------------------------------"
echo " 打开方式："
echo "   1) 在「端口」面板找到 $P1_PORT，右键 → 端口可见性 → 公开"
echo "   2) 复制那串 https://xxx-8765.app.github.dev 的地址，发给别人就能打开"
echo " 演示账号：boss / demo1234"
echo "---------------------------------------------"

exec python 07_p1_server.py
