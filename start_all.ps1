# TradingAgents-CN 一键启动脚本
# 用法: powershell -ExecutionPolicy Bypass -File .\start_all.ps1
# 功能: 启动 WSL Docker 数据库(mybrain-mongo/mybrain-redis) -> 后端(:8000) -> 前端(:3000)
# 说明: 后端/前端在独立窗口运行，关闭对应窗口即停止服务

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step($msg) { Write-Host "`n[INFO] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

function Test-Port($port) {
    return (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue).TcpTestSucceeded
}

# ============ 1. 数据库容器（WSL Docker） ============
Write-Step "检查数据库容器 (WSL Docker)..."

$mongoUp = Test-Port 27017
$redisUp = Test-Port 6379

if (-not $mongoUp -or -not $redisUp) {
    # WSL 或 Docker 未就绪时重启 docker 服务并拉起容器
    wsl -u root -e sh -c "systemctl restart docker 2>/dev/null; sleep 3; docker start mybrain-mongo mybrain-redis" | Out-Null

    # 等待端口就绪（最多 30 秒）
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $mongoUp = Test-Port 27017
        $redisUp = Test-Port 6379
        if ($mongoUp -and $redisUp) { break }
    }
}

# 关键修复：确保 WSL 宿主到 docker 网桥的路由存在（WSL/docker 重启后会丢失）
wsl -u root -e sh -c "ip route show | grep -q '172.19.0.0/16 dev br-f1e55055fcc7' || ip route add 172.19.0.0/16 dev br-f1e55055fcc7" | Out-Null

if (Test-Port 27017) { Write-Ok "MongoDB :27017" } else { Write-Fail "MongoDB :27017 不可用（请手动检查: wsl docker start mybrain-mongo）" }
if (Test-Port 6379)  { Write-Ok "Redis :6379" }    else { Write-Fail "Redis :6379 不可用（请手动检查: wsl docker start mybrain-redis）" }

# ============ 2. 后端 ============
Write-Step "启动后端 (FastAPI, 端口 8000)..."

if (Test-Port 8000) {
    Write-Warn "端口 8000 已被占用，跳过后端启动（可能已在运行）"
} else {
    # 用独立窗口启动，方便看日志；conda env: tradingagents (Python 3.11)
    Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
        cd '$ProjectRoot'
        Write-Host '=== TradingAgents-CN 后端 ===' -ForegroundColor Cyan
        conda run --no-capture-output -n tradingagents uvicorn app.main:app --host 0.0.0.0 --port 8000
"@
    # 等待后端就绪（首次启动要初始化，最多 120 秒）
    $deadline = (Get-Date).AddSeconds(120)
    $backendOk = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try {
            $r = Invoke-WebRequest -Uri 'http://localhost:8000/api/health' -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $backendOk = $true; break }
        } catch { }
    }
    if ($backendOk) { Write-Ok "后端 http://localhost:8000 已就绪" }
    else { Write-Fail "后端 120 秒内未就绪，请查看后端窗口日志" }
}

# ============ 3. 前端 ============
Write-Step "启动前端 (Vite, 端口 3000)..."

if (Test-Port 3000) {
    Write-Warn "端口 3000 已被占用，跳过前端启动（可能已在运行）"
} else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
        cd '$ProjectRoot\frontend'
        Write-Host '=== TradingAgents-CN 前端 ===' -ForegroundColor Cyan
        npm run dev
"@
    $deadline = (Get-Date).AddSeconds(60)
    $frontendOk = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            $r = Invoke-WebRequest -Uri 'http://localhost:3000/' -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $frontendOk = $true; break }
        } catch { }
    }
    if ($frontendOk) { Write-Ok "前端 http://localhost:3000 已就绪" }
    else { Write-Fail "前端 60 秒内未就绪，请查看前端窗口日志" }
}

# ============ 4. 完成 ============
Write-Host "`n================================================" -ForegroundColor Cyan
Write-Host "  启动完成！" -ForegroundColor Green
Write-Host "  前端:    http://localhost:3000"
Write-Host "  后端API: http://localhost:8000/api/health"
Write-Host "  登录账号: admin / .env 中的 ADMIN_DEFAULT_PASSWORD"
Write-Host "  停止服务: 关闭后端/前端两个 PowerShell 窗口"
Write-Host "================================================`n" -ForegroundColor Cyan

Start-Process "http://localhost:3000/"
