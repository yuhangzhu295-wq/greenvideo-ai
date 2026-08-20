param(
  [switch]$Watch,
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ApiDir = Join-Path $RootDir "services\api"
$VenvPython = Join-Path $ApiDir ".venv\Scripts\python.exe"
$BaseUrl = "http://127.0.0.1:$Port"
$LogFile = Join-Path $RootDir "work-api.log"
$ErrFile = Join-Path $RootDir "work-api.err.log"
$PidFile = Join-Path $RootDir "work-api.pid"

function Invoke-Checked {
  param(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory = $RootDir
  )

  Push-Location $WorkingDirectory
  try {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
      throw "$FilePath failed with exit code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

function Test-ApiHealth {
  try {
    Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Get-ApiPortOwner {
  $connection = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.State -eq "Listen" } |
    Select-Object -First 1
  if ($connection) {
    return [int]$connection.OwningProcess
  }
  return $null
}

Write-Host "== GreenVideo AI local startup =="
Write-Host "Root: $RootDir"
Write-Host "API:  $BaseUrl"

if (-not (Test-Path $VenvPython)) {
  Write-Host "Creating backend virtual environment..."
  if (Get-Command py -ErrorAction SilentlyContinue) {
    Invoke-Checked -FilePath "py" -Arguments @("-3", "-m", "venv", ".venv") -WorkingDirectory $ApiDir
  } else {
    Invoke-Checked -FilePath "python" -Arguments @("-m", "venv", ".venv") -WorkingDirectory $ApiDir
  }
}

Write-Host "Installing backend dependencies..."
Invoke-Checked -FilePath $VenvPython -Arguments @("-m", "pip", "install", "-U", "pip") -WorkingDirectory $ApiDir
Invoke-Checked -FilePath $VenvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]") -WorkingDirectory $ApiDir

if (Test-ApiHealth) {
  Write-Host "FastAPI is already running."
} else {
  Write-Host "Starting FastAPI..."
  $process = Start-Process `
    -FilePath $VenvPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $ApiDir `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrFile `
    -WindowStyle Hidden `
    -PassThru
  Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII

  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-ApiHealth) {
      $ready = $true
      break
    }
  }

  if (-not $ready) {
    throw "FastAPI did not become ready. See $LogFile and $ErrFile."
  }
}

Write-Host "FastAPI health check: OK"
$ownerPid = Get-ApiPortOwner
if ($ownerPid) {
  Set-Content -LiteralPath $PidFile -Value $ownerPid -Encoding ASCII
  Write-Host "FastAPI PID: $ownerPid"
}
$env:TARO_APP_API_BASE_URL = $BaseUrl

Write-Host "Installing frontend dependencies..."
Invoke-Checked -FilePath "pnpm" -Arguments @("install", "--no-frozen-lockfile") -WorkingDirectory $RootDir

if ($Watch) {
  Write-Host "Starting Taro watch build. Keep this window open while using WeChat DevTools."
  Invoke-Checked -FilePath "pnpm" -Arguments @("--filter", "@greenvideo/miniapp", "dev:weapp") -WorkingDirectory $RootDir
} else {
  Write-Host "Building WeChat miniapp dist..."
  Invoke-Checked -FilePath "pnpm" -Arguments @("--filter", "@greenvideo/miniapp", "build:weapp") -WorkingDirectory $RootDir
  Write-Host "Done. Open apps\miniapp in WeChat DevTools. API_BASE_URL=$BaseUrl"
}
