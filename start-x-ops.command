#!/bin/bash
# 双击启动 @LucaAiAi X 运营工具 v1.4（macOS），直接打开「对标账号分析」。再次双击：已在运行就直接打开网页，否则重新启动。
cd "$(dirname "$0")" || exit 1
PORT=$(grep -E '^PORT=' .env.local 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' "'\''')
PORT=${PORT:-8788}
URL="http://127.0.0.1:$PORT"

if curl -s -m 3 "$URL/api/health" | grep -q '"ok": true'; then
  echo "工具已经在运行：$URL"; open "$URL/#bench"; exit 0
fi
if [ -f server.pid ] && kill -0 "$(cat server.pid)" 2>/dev/null; then
  kill "$(cat server.pid)" 2>/dev/null; sleep 1
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 被其他程序占用。请在 .env.local 里把 PORT= 改成别的端口（例如 8789）后再双击。"
  read -n 1 -s -r -p "按任意键关闭…"; exit 1
fi
if ! command -v python3 >/dev/null; then
  echo "没有找到 python3。请先安装 Python 3（https://www.python.org/downloads/）。"
  read -n 1 -s -r -p "按任意键关闭…"; exit 1
fi
PORT=$PORT nohup python3 server.py >> server.log 2>&1 &
echo $! > server.pid
for i in $(seq 1 20); do
  sleep 0.5
  if curl -s -m 2 "$URL/api/health" | grep -q '"ok": true'; then
    echo "已启动：$URL（日志：$(pwd)/server.log）"; open "$URL/#bench"; exit 0
  fi
done
echo "启动失败，最后几行日志："; tail -20 server.log
read -n 1 -s -r -p "按任意键关闭…"
