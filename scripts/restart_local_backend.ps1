param([ValidatePattern('^[a-fA-F0-9-]{36}$')][string]$RetryWorkflow = '', [switch]$OfflineRecovery)
$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$backendMetadataPath = Join-Path $workspacePath 'outputs/engineering-live/backend-process.json'
if (-not (Test-Path -LiteralPath $backendMetadataPath)) {
    $backendReceipts = @(Get-ChildItem -LiteralPath (Join-Path $workspacePath 'outputs') -Filter 'backend-process.json' -File -Recurse | Where-Object {
        $receiptRecord = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        $launcherRecord = Get-Process -Id $receiptRecord.launcher_pid -ErrorAction SilentlyContinue
        $launcherRecord -and $launcherRecord.ProcessName -eq 'uv'
    })
    if ($backendReceipts.Count -ne 1) { throw 'Specify a unique backend process receipt before restarting.' }
    $backendMetadataPath = $backendReceipts[0].FullName
}
$baseUri = 'http://127.0.0.1:8000'
if ($OfflineRecovery) {
    Push-Location -LiteralPath $workspacePath
    try {
        & uv run --no-sync --env-file .env python scripts/check_backend_idle.py
        if ($LASTEXITCODE -ne 0) { throw 'The database idle check refused the restart.' }
    } finally { Pop-Location }
} else {
    $projectList = Invoke-RestMethod -Uri ($baseUri + '/api/projects') -TimeoutSec 20
    foreach ($item in $projectList) {
        $allWorkflows = Invoke-RestMethod -Uri ($baseUri + '/api/v1/projects/' + $item.id + '/workflows') -TimeoutSec 20
        if (@($allWorkflows | Where-Object { $_.status -in @('QUEUED','RUNNING','CAD_QUEUED','ESTIMATED_READY') }).Count -gt 0) {
            throw 'A furniture workflow is active. Leave the backend running.'
        }
    }
}
$backendMetadata = Get-Content -LiteralPath $backendMetadataPath | ConvertFrom-Json
$backendProcesses = @(Get-CimInstance Win32_Process)
$backendLauncher = $backendProcesses | Where-Object { $_.ProcessId -eq $backendMetadata.launcher_pid -and $_.Name -eq 'uv.exe' }
$backendPython = $backendProcesses | Where-Object {
    $_.ParentProcessId -eq $backendMetadata.launcher_pid -and $_.Name -eq 'python.exe' -and
    $_.CommandLine.Contains($workspacePath + '\.venv\Scripts\python.exe') -and $_.CommandLine.Contains('uvicorn app.main:app')
}
if (-not $backendLauncher -or -not $backendPython) { throw 'The workspace backend process identity could not be verified.' }
$backendPython | ForEach-Object { Stop-Process -Id $_.ProcessId }
Stop-Process -Id $backendLauncher.ProcessId -ErrorAction SilentlyContinue
$env:OPENAI_REASONING_MODEL = 'gpt-6.1-sol'
$env:AI_PHOTO_MAX_OUTPUT_TOKENS = '32000'
$env:AI_PHOTO_REASONING_EFFORT = 'medium'
$logDirectory = Join-Path $workspacePath 'outputs/engineering-live'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$backendMetadataPath = Join-Path $logDirectory 'backend-process.json'
$logPrefix = Join-Path $logDirectory ('backend-delivery-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
$uvPath = (Get-Command uv.exe).Source
$newBackendLauncher = Start-Process -FilePath $uvPath -ArgumentList @('run','--no-sync','--env-file','.env','python','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $workspacePath -WindowStyle Hidden -RedirectStandardOutput ($logPrefix + '-stdout.log') -RedirectStandardError ($logPrefix + '-stderr.log') -PassThru
@{launcher_pid=$newBackendLauncher.Id;port=8000;provider='cadquery';workflow='IMAGE_TO_CAD';log_prefix=$logPrefix} | ConvertTo-Json | Set-Content -LiteralPath $backendMetadataPath
$ready = $false
for ($attempt=0; $attempt -lt 40; $attempt++) {
    try { $health = Invoke-RestMethod -Uri ($baseUri + '/health/live'); if ($health.status -eq 'ok') { $ready=$true; break } } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw ('Backend did not become ready. Inspect ' + $logPrefix + '-stderr.log') }
Write-Output ('Backend ready: launcher ' + $newBackendLauncher.Id)
if ($RetryWorkflow) {
    Invoke-RestMethod -Method Post -Uri ($baseUri + '/api/v1/agents/workflows/' + $RetryWorkflow + '/retry') -ContentType 'application/json' -Body '{}' | ConvertTo-Json
}
