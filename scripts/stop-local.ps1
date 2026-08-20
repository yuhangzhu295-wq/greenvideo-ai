param(
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $RootDir "work-api.pid"

function Add-ProcessTree {
  param(
    [int]$ProcessId,
    [System.Collections.Generic.HashSet[int]]$TargetIds
  )

  if (-not $TargetIds.Add($ProcessId)) {
    return
  }

  Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" |
    ForEach-Object { Add-ProcessTree -ProcessId ([int]$_.ProcessId) -TargetIds $TargetIds }
}

$targetIds = [System.Collections.Generic.HashSet[int]]::new()

if (Test-Path $PidFile) {
  $rawPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
  if ($rawPid) {
    Add-ProcessTree -ProcessId ([int]$rawPid) -TargetIds $targetIds
  }
}

$apiPathPart = (Join-Path $RootDir "services\api\.venv\Scripts\python.exe")
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -and
    $_.CommandLine.Contains($apiPathPart) -and
    $_.CommandLine.Contains("uvicorn app.main:app") -and
    $_.CommandLine.Contains("--port $Port")
  } |
  ForEach-Object { Add-ProcessTree -ProcessId ([int]$_.ProcessId) -TargetIds $targetIds }

if ($targetIds.Count -eq 0) {
  Write-Host "No local FastAPI process found."
  if (Test-Path $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force
  }
  exit 0
}

$orderedIds = $targetIds | Sort-Object -Descending
foreach ($processId in $orderedIds) {
  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $processId -Force
    Write-Host "Stopped FastAPI process $processId."
  }
}

if (Test-Path $PidFile) {
  Remove-Item -LiteralPath $PidFile -Force
}
