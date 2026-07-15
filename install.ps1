param(
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

function Find-CompatiblePython {
    $candidates = @(
        @{ Executable = "py.exe"; Prefix = @("-3.12") },
        @{ Executable = "py.exe"; Prefix = @("-3") },
        @{ Executable = "python.exe"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Executable -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        try {
            & $command.Source @($candidate.Prefix) -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
            if ($LASTEXITCODE -eq 0) {
                return @{ Executable = $command.Source; Prefix = $candidate.Prefix }
            }
        }
        catch {
            continue
        }
    }
    return $null
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvIsCompatible = $false
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    & $venvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    $venvIsCompatible = $LASTEXITCODE -eq 0
}

Write-Host "[1/4] 检查 Python 3.10+..." -ForegroundColor Cyan
$pythonInfo = $null
if (-not $venvIsCompatible) {
    $pythonInfo = Find-CompatiblePython
    if ($null -eq $pythonInfo) {
        throw "未找到 Python 3.10 或更高版本。请从 https://www.python.org/downloads/windows/ 安装 64 位 Python，并勾选 Add Python to PATH。"
    }
}

if (-not $venvIsCompatible) {
    Write-Host "[2/4] 创建项目虚拟环境..." -ForegroundColor Cyan
    if (Test-Path -LiteralPath (Join-Path $root ".venv")) {
        throw "现有 .venv 不兼容或已损坏。请先将它改名为 .venv.broken，再重新运行安装脚本。"
    }
    & $pythonInfo.Executable @($pythonInfo.Prefix) -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "创建虚拟环境失败。"
    }
}
else {
    Write-Host "[2/4] 已存在虚拟环境，继续使用。" -ForegroundColor DarkGray
}

Write-Host "[3/4] 安装或更新项目依赖..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "升级 pip 失败，请检查网络或代理设置。"
}
& $venvPython -m pip install -e $root
if ($LASTEXITCODE -ne 0) {
    throw "安装项目依赖失败，请检查网络或代理设置。"
}

Write-Host "[4/4] 初始化通用数据目录..." -ForegroundColor Cyan
& $venvPython -c "from src.paths import prepare_data_dir; print(prepare_data_dir())"
if ($LASTEXITCODE -ne 0) {
    throw "初始化数据目录失败。"
}

Write-Host "安装完成。" -ForegroundColor Green
if (-not $SkipStart) {
    & (Join-Path $root "start-v2.ps1")
}
