param(
    [Parameter(Mandatory)]
    [string]$Runtime
)

$ErrorActionPreference = 'Stop'
$runtimePath = (Resolve-Path -LiteralPath $Runtime).Path
$pythonPath = (Get-Command python.exe).Source
$roles = 'bb-bench', 'bb-design'

foreach ($role in $roles) {
    $pidFile = Join-Path $runtimePath "$role.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $oldPid = [int](Get-Content -LiteralPath $pidFile)
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid"
        if ($oldProcess.CommandLine -like "*run.py*agent $role*") {
            throw "$role is already running as process $oldPid."
        }
    }
    if (!(Test-Path -LiteralPath (Join-Path $runtimePath "$role.json"))) {
        throw "Missing config for $role. Run setup_room.py first."
    }
}

foreach ($role in $roles) {
    $process = Start-Process -FilePath $pythonPath -WindowStyle Hidden -WorkingDirectory $PSScriptRoot `
        -ArgumentList @('-X', 'utf8', 'run.py', '--runtime', ('"{0}"' -f $runtimePath), 'agent', $role) `
        -RedirectStandardOutput (Join-Path $runtimePath "$role.log") `
        -RedirectStandardError (Join-Path $runtimePath "$role.error.log") -PassThru
    Set-Content -LiteralPath (Join-Path $runtimePath "$role.pid") -Value $process.Id -Encoding ascii
    Write-Output "$role started (PID $($process.Id))"
}
