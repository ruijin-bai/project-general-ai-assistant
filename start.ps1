param(
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$DataDir = (Join-Path $PSScriptRoot '.runtime'),
    [string]$ModelEnv = (Join-Path $PSScriptRoot '.env'),
    [switch]$MultiUser,
    [string]$SetupAdmin,
    [switch]$Backup
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $assistantArguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'run.py'), '--port', "$Port", '--data-dir', $DataDir, '--model-env', $ModelEnv)
    if ($MultiUser) { $assistantArguments += '--multi-user' }
    if ($SetupAdmin) { $assistantArguments += @('--setup-admin', $SetupAdmin) }
    if ($Backup) { $assistantArguments += '--backup' }
    & python @assistantArguments
    if ($LASTEXITCODE -ne 0) { throw "服务退出：$LASTEXITCODE" }
} finally {
    Pop-Location
}
