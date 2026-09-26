# Flow Kit — bật máy chủ và mở giao diện. Gọi từ start.bat (bấm đúp).
#
# 1. Chuẩn bị giao diện khi dashboard/dist chưa có hoặc cũ hơn mã nguồn dashboard
#    (npm ci khi chưa có node_modules, rồi npm run build).
# 2. Bật máy chủ (.venv\Scripts\python.exe -m agent.main) nếu nó chưa chạy.
# 3. Chờ máy chủ trả lời rồi mở http://127.0.0.1:8100.
#
# -NoBrowser: không mở trình duyệt (để kiểm tra).
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root = Split-Path -Parent $PSScriptRoot
$Dashboard = Join-Path $Root 'dashboard'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Url = 'http://127.0.0.1:8100'

function Fail([string]$Message) {
    Write-Host ''
    Write-Host "LỖI: $Message" -ForegroundColor Red
    Write-Host ''
    if (-not $NoBrowser) { Read-Host 'Nhấn Enter để đóng cửa sổ này' | Out-Null }
    exit 1
}

function Test-Server {
    try {
        $r = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Find-Npm {
    $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # Node vừa cài nhưng PATH của phiên này chưa cập nhật
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
    $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $env:ProgramFiles 'nodejs\npm.cmd'
    if (Test-Path $default) { return $default }
    return $null
}

function Get-NewestSourceTime {
    $files = @(Get-ChildItem -Path (Join-Path $Dashboard 'src') -Recurse -File)
    foreach ($name in 'package.json', 'package-lock.json', 'index.html', 'vite.config.ts') {
        $f = Join-Path $Dashboard $name
        if (Test-Path $f) { $files += Get-Item $f }
    }
    return ($files | Measure-Object -Property LastWriteTime -Maximum).Maximum
}

Write-Host 'Flow Kit' -ForegroundColor Cyan
if (-not (Test-Path $Python)) {
    Fail "Không thấy $Python. Thư mục .venv của Flow Kit chưa được cài đặt."
}

# ── 1. Giao diện ────────────────────────────────────────────
$DistIndex = Join-Path $Dashboard 'dist\index.html'
$needBuild = -not (Test-Path $DistIndex)
if (-not $needBuild) {
    $needBuild = (Get-NewestSourceTime) -gt (Get-Item $DistIndex).LastWriteTime
}
if ($needBuild) {
    Write-Host 'Đang chuẩn bị giao diện… (lần đầu có thể mất vài phút)' -ForegroundColor Yellow
    $npm = Find-Npm
    if (-not $npm) {
        Fail ("Chưa cài Node.js, cần để chuẩn bị giao diện.`n" +
              "  1. Tải bản LTS tại https://nodejs.org và cài đặt (giữ các lựa chọn mặc định).`n" +
              "  2. Bấm đúp start.bat lại.")
    }
    Push-Location $Dashboard
    try {
        if (-not (Test-Path (Join-Path $Dashboard 'node_modules'))) {
            & $npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { Fail 'Cài thư viện cho giao diện (npm ci) không thành công. Kiểm tra kết nối mạng rồi thử lại.' }
        }
        & $npm run build
        if ($LASTEXITCODE -ne 0) { Fail 'Chuẩn bị giao diện (npm run build) không thành công. Xem thông báo phía trên.' }
    } finally {
        Pop-Location
    }
    Write-Host 'Giao diện đã sẵn sàng.' -ForegroundColor Green
}

# ── 2. Máy chủ ──────────────────────────────────────────────
if (Test-Server) {
    Write-Host 'Máy chủ đang chạy.'
} else {
    Write-Host 'Đang bật máy chủ…'
    Start-Process -FilePath $Python -ArgumentList '-m', 'agent.main' -WorkingDirectory $Root -WindowStyle Minimized
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Server)) {
        if ((Get-Date) -gt $deadline) {
            Fail "Máy chủ không trả lời sau 60 giây. Xem nhật ký tại output\logs\server.log."
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host 'Máy chủ đã bật (cửa sổ thu nhỏ trên thanh tác vụ; đóng cửa sổ đó là tắt máy chủ).' -ForegroundColor Green
}

# ── 3. Trình duyệt ──────────────────────────────────────────
if (-not $NoBrowser) { Start-Process $Url }
Write-Host "Giao diện: $Url"
