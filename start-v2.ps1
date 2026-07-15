param(
    [int]$Port = 0,
    [string]$ChromePath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "虚拟环境不存在。请先双击 install-and-start.cmd，或运行 .\install.ps1。"
}

$dataRoot = $env:TAOBAO_ASSISTANT_DATA_DIR
if ([string]::IsNullOrWhiteSpace($dataRoot)) {
    $dataRoot = Join-Path $env:LOCALAPPDATA "TaobaoAssistant"
}
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$configPath = Join-Path $dataRoot "config.json"
$config = $null
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try {
        $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
    }
    catch {
        Write-Warning "配置文件无法读取，将使用默认设置：$($_.Exception.Message)"
    }
}

if ($Port -le 0 -and -not [string]::IsNullOrWhiteSpace($env:TAOBAO_ASSISTANT_PORT)) {
    $Port = [int]$env:TAOBAO_ASSISTANT_PORT
}
if ($Port -le 0 -and $null -ne $config -and $null -ne $config.port) {
    $Port = [int]$config.port
}
if ($Port -le 0) {
    $Port = 8550
}
if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "本地服务端口必须在 1024-65535 之间。"
}

if ([string]::IsNullOrWhiteSpace($ChromePath)) {
    $ChromePath = $env:TAOBAO_ASSISTANT_CHROME
}
if ([string]::IsNullOrWhiteSpace($ChromePath) -and $null -ne $config) {
    $ChromePath = [string]$config.chrome_path
}

$chromeCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($ChromePath)) {
    $chromeCandidates += $ChromePath
}
$chromeCommand = Get-Command chrome.exe -ErrorAction SilentlyContinue
if ($null -ne $chromeCommand) {
    $chromeCandidates += $chromeCommand.Source
}
$chromeCandidates += @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_ -PathType Leaf)
} | Select-Object -First 1
if (-not $chrome) {
    throw "未找到 Google Chrome。请先安装 Chrome，或在设置页填写 chrome.exe 的完整路径。"
}

function Get-FreeLocalPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

$effectivePort = $Port
$existing = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
$alreadyRunning = $false
if ($null -ne $existing) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($existing.OwningProcess)" -ErrorAction SilentlyContinue
    if ($null -ne $owner -and [string]$owner.CommandLine -match "main\.py" -and [string]$owner.CommandLine -match "--port") {
        $alreadyRunning = $true
    }
    else {
        $effectivePort = Get-FreeLocalPort
        Write-Warning "端口 $Port 已被其他程序占用，本次临时使用空闲端口 $effectivePort。"
    }
}

if (-not $alreadyRunning) {
    $stdoutLog = Join-Path $dataRoot "service.out.log"
    $stderrLog = Join-Path $dataRoot "service.err.log"
    Write-Host "正在启动服务。旧版数据首次迁移时可能需要 1-3 分钟，请勿关闭此窗口。" -ForegroundColor Cyan
    $serviceProcess = Start-Process `
        -FilePath $python `
        -ArgumentList @("main.py", "--no-browser", "--port", "$effectivePort") `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    $ready = $false
    for ($i = 0; $i -lt 900; $i++) {
        Start-Sleep -Milliseconds 200
        if ($serviceProcess.HasExited) {
            $details = Get-Content -Raw -Encoding UTF8 -LiteralPath $stderrLog -ErrorAction SilentlyContinue
            throw "V2 服务进程提前退出（退出码 $($serviceProcess.ExitCode)）。$details"
        }
        if (Get-NetTCPConnection -State Listen -LocalPort $effectivePort -ErrorAction SilentlyContinue) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw "V2 服务启动超过 3 分钟。请查看日志：$stderrLog"
    }
}

$url = "http://127.0.0.1:$effectivePort"
Start-Process -FilePath $chrome -ArgumentList $url
Write-Host "淘宝助手已启动：$url" -ForegroundColor Green
Write-Host "通用数据目录：$dataRoot"
