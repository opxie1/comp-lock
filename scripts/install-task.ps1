#Requires -RunAsAdministrator
<#
.SYNOPSIS
Registers the scheduled task that keeps a lock running across a reboot.

.DESCRIPTION
`complock resume` re-engages a lock that still has time left on it and starts a
new worker if the previous one died. The task runs it at startup and once a
minute afterwards. Without the task, closing the worker or restarting the
machine ends a lock early.

The task runs as SYSTEM, so comp-lock has to be installed for the interpreter
given by -Python rather than in a per-user site-packages directory. This script
checks that before registering anything.

.PARAMETER Python
Interpreter to run comp-lock with. Defaults to the python on PATH.

.PARAMETER TaskName
Name to register the task under.

.EXAMPLE
.\install-task.ps1

.EXAMPLE
.\install-task.ps1 -Python 'C:\Program Files\Python313\python.exe'
#>
[CmdletBinding()]
param(
    [string]$Python,
    [string]$TaskName = 'comp-lock resume'
)

$ErrorActionPreference = 'Stop'

if (-not $Python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) {
        throw 'No python found on PATH. Pass -Python with the full path to the interpreter.'
    }
    $Python = $found.Source
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "No interpreter at $Python"
}

Write-Host "Checking that comp-lock is importable by $Python"
$probe = & $Python -m complock --version
if ($LASTEXITCODE -ne 0) {
    throw "$Python cannot import complock. Install it machine-wide from an elevated prompt: `"$Python`" -m pip install ."
}
Write-Host "  $probe"

$action = New-ScheduledTaskAction -Execute $Python -Argument '-m complock resume'

$atStartup = New-ScheduledTaskTrigger -AtStartup
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger @($atStartup, $everyMinute) `
    -Principal $principal `
    -Settings $settings `
    -Description 'Re-engages an unfinished comp-lock lock after a reboot or a stopped worker.' `
    -Force | Out-Null

Write-Host "Registered the task '$TaskName'."

# The probe above ran as the installing user. The task runs as SYSTEM, which is
# a different thing entirely: a virtual environment built on a per-user Python
# lives under that user's profile, and `resume` is no use if SYSTEM cannot
# reach it. `complock resume` does nothing when no lock is stored, so running
# it once here is safe and settles the question.
Write-Host 'Checking that SYSTEM can run it'
Start-ScheduledTask -TaskName $TaskName

$result = $null
foreach ($attempt in 1..20) {
    Start-Sleep -Milliseconds 500
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $state = (Get-ScheduledTask -TaskName $TaskName).State
    if ($state -ne 'Running' -and $info.LastRunTime) {
        $result = $info.LastTaskResult
        break
    }
}

if ($null -eq $result) {
    Write-Warning "The task did not finish in time to be checked. Inspect it with: schtasks /query /tn `"$TaskName`" /v /fo list"
} elseif ($result -eq 0) {
    Write-Host '  SYSTEM ran it successfully.'
} else {
    Write-Warning "SYSTEM could not run the task (result 0x$('{0:X}' -f $result))."
    Write-Warning "A lock will still work, but it will not survive a reboot. $Python is most likely somewhere SYSTEM cannot read, which happens when Python is installed for one user only. Installing Python for all users and reinstalling comp-lock fixes it."
}

Write-Host "Check it any time with: schtasks /query /tn `"$TaskName`""
