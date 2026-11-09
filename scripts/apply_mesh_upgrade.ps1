param(
    [string]$TargetRoot = 'D:\Enfection\furniture_planner\furniture_manufacturing_starter',
    [ValidateSet('mesh-upgrade-manifest.json','global-upgrade-manifest.json','editable-cad-upgrade-manifest.json')]
    [string]$ManifestName = 'mesh-upgrade-manifest.json'
)
$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$resolvedTarget = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\')
if ($resolvedTarget -ne 'D:\Enfection\furniture_planner\furniture_manufacturing_starter') {
    throw 'This reviewed update is restricted to the original project path.'
}
$manifestPath = Join-Path (Join-Path $workspaceRoot 'docs') $ManifestName
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
# Verify the entire update before changing any original project file.
foreach ($item in $manifest.files) {
    $sourcePath = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $item.path))
    $targetPath = [IO.Path]::GetFullPath((Join-Path $resolvedTarget $item.path))
    if (-not $sourcePath.StartsWith($workspaceRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not $targetPath.StartsWith($resolvedTarget + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Update manifest contains a path outside the reviewed roots.'
    }
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLower() -ne $item.source_sha256) {
        throw "Staged content changed: $($item.path)"
    }
    if (Test-Path -LiteralPath $targetPath) {
        $actual = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $item.previous_sha256) { throw "Original project changed since review: $($item.path)" }
    } elseif ($null -ne $item.previous_sha256) {
        throw "Original project file disappeared since review: $($item.path)"
    }
}
$backupRoot = Join-Path $workspaceRoot ('outputs\original-source-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
foreach ($item in $manifest.files) {
    $sourcePath = Join-Path $workspaceRoot $item.path
    $targetPath = Join-Path $resolvedTarget $item.path
    if (Test-Path -LiteralPath $targetPath) {
        $backupPath = Join-Path $backupRoot $item.path
        New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($backupPath)) | Out-Null
        Copy-Item -LiteralPath $targetPath -Destination $backupPath
    }
    New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($targetPath)) | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
}
Write-Output "Applied $($manifest.files.Count) reviewed files to $resolvedTarget"
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path (Join-Path $resolvedTarget 'docs') $ManifestName) -Force
Write-Output "Original files backed up under $backupRoot"
