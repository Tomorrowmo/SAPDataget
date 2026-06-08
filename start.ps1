#Requires -Version 5.1
<#
.SYNOPSIS
  SAPDataget 一键启动 —— 后端 FastAPI(:8000) + 前端 Vite(:5173)。

.DESCRIPTION
  自动完成：补 .env → 检查/安装后端与前端依赖 → 在两个独立窗口分别启动后端、前端
  → 等后端就绪 → 打开浏览器。用 admin 空密码登录。

.EXAMPLE
  右键 start.ps1 → 用 PowerShell 运行；或双击 start.bat。
  PowerShell 里：  .\start.ps1
  指定参数：       .\start.ps1 -BackendPort 8000 -FrontendPort 5173

.NOTES
  -BackendPort  须与 web/vite.config.ts 里 proxy 的目标端口一致（默认 8000）。
  装依赖会自动探测本机代理(v2ray/clash 常用 7897 等)，可用 -Proxy 覆盖、-NoInstall 跳过。
#>
param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173,
  [string]$Python = "python",
  [string]$Proxy = "",
  [switch]$NoBrowser,
  [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Info($m) { Write-Host "[启动] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[注意] $m" -ForegroundColor Yellow }
function Ok($m)   { Write-Host "[完成] $m" -ForegroundColor Green }

function Test-Port([int]$port) {
  try { return (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue).TcpTestSucceeded }
  catch { return $false }
}

function Get-Proxy {
  if ($Proxy) { return $Proxy }
  foreach ($p in 7897, 10809, 10808, 7890, 1080) {
    if (Test-Port $p) { return "http://127.0.0.1:$p" }
  }
  return ""
}

Write-Host ""
Info "SAPDataget 一键启动（后端 :$BackendPort / 前端 :$FrontendPort）"
Write-Host ""

# 0) .env：没有就从模板复制（默认 mock 模式，可离线跑）
if (-not (Test-Path "$root\.env")) {
  if (Test-Path "$root\.env.example") {
    Copy-Item "$root\.env.example" "$root\.env"
    Info ".env 不存在，已从 .env.example 复制。如需真连 SAP / 配 LLM key，请编辑 .env。"
  } else {
    Warn "缺少 .env 和 .env.example，后端可能用默认配置启动。"
  }
}

# 1) 后端依赖
if (-not $NoInstall) {
  Info "检查后端依赖..."
  $pyOk = $true
  try { & $Python -c "import fastapi, uvicorn, litellm" 2>$null; if ($LASTEXITCODE -ne 0) { $pyOk = $false } }
  catch { $pyOk = $false }
  if (-not $pyOk) {
    Warn "后端依赖缺失，安装 requirements.txt（首次较慢）..."
    $px = Get-Proxy
    if ($px) { $env:HTTPS_PROXY = $px; $env:HTTP_PROXY = $px; Info "用代理 $px 安装" }
    & $Python -m pip install -r "$root\requirements.txt"
    Remove-Item Env:HTTPS_PROXY, Env:HTTP_PROXY -ErrorAction SilentlyContinue
  } else { Info "后端依赖已就绪。" }
}

# 2) 前端依赖
if (-not $NoInstall -and -not (Test-Path "$root\web\node_modules")) {
  Warn "前端依赖缺失，npm install（首次较慢）..."
  $px = Get-Proxy
  Push-Location "$root\web"
  try {
    if ($px) { $env:HTTPS_PROXY = $px; $env:HTTP_PROXY = $px; Info "用代理 $px 安装" }
    npm install --no-audit --no-fund
  } finally {
    Remove-Item Env:HTTPS_PROXY, Env:HTTP_PROXY -ErrorAction SilentlyContinue
    Pop-Location
  }
}

# 3) 启动后端（独立窗口；端口已占用则跳过，避免重复启动）
if (Test-Port $BackendPort) {
  Warn "端口 $BackendPort 已被占用，跳过启动后端（可能已在运行）。"
} else {
  Info "启动后端 http://127.0.0.1:$BackendPort ..."
  $beCmd = "Set-Location '$root'; `$Host.UI.RawUI.WindowTitle='SAPDataget 后端 :$BackendPort'; & '$Python' -m uvicorn app.server:app --host 127.0.0.1 --port $BackendPort"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", $beCmd | Out-Null
}

# 4) 启动前端（独立窗口）
if (Test-Port $FrontendPort) {
  Warn "端口 $FrontendPort 已被占用，跳过启动前端（可能已在运行）。"
} else {
  Info "启动前端 http://localhost:$FrontendPort ..."
  $feCmd = "Set-Location '$root\web'; `$Host.UI.RawUI.WindowTitle='SAPDataget 前端 :$FrontendPort'; npm run dev"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", $feCmd | Out-Null
}

# 5) 等后端就绪 + 开浏览器
Info "等待后端就绪..."
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:$BackendPort/api/status" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch {}
  Start-Sleep -Milliseconds 500
}
if ($ready) { Ok "后端就绪 ✓" } else { Warn "后端尚未就绪（可能仍在启动），请看后端窗口日志。" }

if (-not $NoBrowser) {
  Start-Sleep -Seconds 2
  Info "打开浏览器 http://localhost:$FrontendPort"
  Start-Process "http://localhost:$FrontendPort" | Out-Null
}

Write-Host ""
Ok "启动完成。后端/前端各在独立窗口运行；关闭对应窗口即停止服务。"
Ok "浏览器打开 http://localhost:$FrontendPort ，用 admin（空密码）登录。"
Write-Host ""

