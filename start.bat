@echo off
REM One-click startup script for InvestPro (Windows)
setlocal enabledelayedexpansion

echo === InvestPro 可转债数据查看器 ===
echo.

set SCRIPT_DIR=%~dp0

REM ── 后端 ──────────────────────────────────────────────────
echo [1/2] 启动后端 (FastAPI)...
cd /d "%SCRIPT_DIR%backend"

REM 创建虚拟环境（如果不存在）
if not exist ".venv\Scripts\activate.bat" (
    echo   创建 Python 虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo   [错误] 无法创建虚拟环境，请确认已安装 Python 3.11+
        pause
        exit /b 1
    )
)

REM 激活虚拟环境并安装依赖
call .venv\Scripts\activate.bat
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo   安装后端依赖...
    pip install -r requirements.txt -q
)

REM 在新窗口中启动后端
start "InvestPro Backend" cmd /k "cd /d %SCRIPT_DIR%backend && call .venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000"
echo   后端已在新窗口启动，监听 http://localhost:8000

REM 等待后端就绪
timeout /t 3 /nobreak >nul

REM ── 前端 ──────────────────────────────────────────────────
echo [2/2] 启动前端 (Vite)...
cd /d "%SCRIPT_DIR%frontend"

if not exist "node_modules" (
    echo   安装前端依赖...
    npm install -q
)

REM 在新窗口中启动前端
start "InvestPro Frontend" cmd /k "cd /d %SCRIPT_DIR%frontend && npm run dev"
echo   前端已在新窗口启动，监听 http://localhost:5173

echo.
echo ✅ 全部启动完成！
echo    请在浏览器打开 http://localhost:5173
echo    关闭对应的命令行窗口可停止相应服务
echo.
pause
