#!/bin/bash
# 双击停止 Instagram 运营工具
cd "$(dirname "$0")" || exit 1
if [ -f server.pid ] && kill "$(cat server.pid)" 2>/dev/null; then
  rm -f server.pid; echo "已停止。"
else
  echo "没有找到正在运行的服务（server.pid）。"
fi
