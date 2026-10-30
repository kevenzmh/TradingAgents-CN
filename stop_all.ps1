# TradingAgents-CN 一键停止脚本
# 用法: powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
# 功能: 停止前端(:3000) -> 后端(:8000)，可选 -StopDb 同时停止数据库容器
# 说明: 数据库容器默认保留运行（数据持久化在 volume，停止不影响数据）

param(
    [switch]$StopDb  # 加上此参数同时停止 mongo/redis 容器
)

$ErrorActionPreference = "Continue"

function Write-Step($msg) { Write-Host "`n[INFO] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

function Test-Port($port) {
    return (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue).TcpTestSucceeded
}

# 按命令行内容找进程并停止（uvicorn 后端 / vite 前端）
function Stop-ByPort($port, $name) {
    if (-not (Test-Port $port)) {
        Write-Warn "$name 端口 $port 未监听，跳过"
        return
    }
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -match "python|node") {
            # 先停主进程；uvicorn/vite 可能有子进程（conda run 包装、esbuild 等）
            taskkill /PID $procId /T /F 2>$null | Out-Null
            Write-Ok "$name 进程已停止 ($($proc.ProcessName), PID=$procId)"
        } else {
            Write-Warn "端口 $port 被 PID=$procId ($($proc.ProcessName)) 占用且非本项目进程，请手动处理"
        }
    }
}

# ============ 1. 前端 ============
Write-Step "停止前端 (Vite, 端口 3000)..."
Stop-ByPort -port 3000 -name "前端"

# ============ 2. 后端 ============
Write-Step "停止后端 (FastAPI, 端口 8000)..."
Stop-ByPort -port 8000 -name "后端"

# ============ 3. 数据库（可选） ============
if ($StopDb) {
    Write-Step "停止数据库容器 (WSL Docker)..."
    wsl --% docker stop mybrain-mongo mybrain-redis 2>&1 | Out-Null
    if (-not (Test-Port 27017)) { Write-Ok "MongoDB 容器已停止" } else { Write-Fail "MongoDB 容器仍在运行" }
    if (-not (Test-Port 6379))  { Write-Ok "Redis 容器已停止" }    else { Write-Fail "Redis 容器仍在运行" }
    Write-Host "  提示: 数据保留在 docker volume 中，下次 start_all.ps1 会自动拉起" -ForegroundColor DarkGray
} else {
    Write-Step "保留数据库容器运行（如需停止: .\stop_all.ps1 -StopDb）"
}

# ============ 4. 汇总 ============
Write-Host "`n================================================" -ForegroundColor Cyan
Write-Host "  停止完成！" -ForegroundColor Green
Write-Host "  前端 :3000 -> $($(if (Test-Port 3000) {'仍在运行'} else {'已停止'}))"
Write-Host "  后端 :8000 -> $($(if (Test-Port 8000) {'仍在运行'} else {'已停止'}))"
Write-Host "  MongoDB :27017 -> $($(if (Test-Port 27017) {'仍在运行'} else {'已停止'}))"
Write-Host "  Redis :6379 -> $($(if (Test-Port 6379) {'仍在运行'} else {'已停止'}))"
Write-Host "================================================`n" -ForegroundColor Cyan
