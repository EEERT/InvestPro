#!/usr/bin/env bash
# One-click startup script for InvestPro
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== InvestPro 可转债数据查看器 ==="
echo ""

# Backend
echo "[1/2] 启动后端 (FastAPI)..."
cd "$SCRIPT_DIR/backend"
if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "  安装后端依赖..."
  pip3 install -r requirements.txt -q
fi
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID，监听 http://localhost:8000"

# Wait for backend to be ready
sleep 2

# Frontend
echo "[2/2] 启动前端 (Vite)..."
cd "$SCRIPT_DIR/frontend"
if [ ! -d "node_modules" ]; then
  echo "  安装前端依赖..."
  npm install -q
fi
npm run dev &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID，监听 http://localhost:5173"
echo ""
echo "✅ 全部启动完成！请在浏览器打开 http://localhost:5173"
echo "   按 Ctrl+C 停止所有服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '服务已停止'" EXIT INT TERM
wait
