<#
.SYNOPSIS
Removes comp-lock.

.DESCRIPTION
Releases any lock in progress, unregisters the scheduled task, takes the
install directory off PATH, and deletes it. Configuration and logs under
C:\ProgramData\comp-lock are left alone unless -Purge is given.

Asks for administrator rights if it does not already have them.

Run it with:

    irm https://raw.githubusercontent.com/opxie1/comp-lock/main/uninstall.ps1 | iex

.PARAMETER Purge
Also delete the configuration, logs, and the saved copy of the hosts file.

.PARAMETER Force
Release the lock even when it is a hard lock that `complock stop` refuses to
end. Without this, a running hard lock stops the uninstall.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'comp-lock'),
    [string]$Ref = 'main',
    [switch]$Purge,
    [switch]$Force,
    [switch]$Relaunched
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'opxie1/comp-lock'
$UninstallerUrl = "https://raw.githubusercontent.com/$Repo/$Ref/uninstall.ps1"
$DataDir = Join-Path $env:ProgramData 'comp-lock'
$TaskName = 'comp-lock resume'

function Write-Step($Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Detail($Message) { Write-Host "    $Message" }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ActiveLock {
    $record = Join-Path $DataDir 'session.json'
    if (-not (Test-Path -LiteralPath $record)) { return $null }
    try {
        $session = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    if ($session.ends_at -gt $now) { return $session }
    return $null
}

function Remove-FromMachinePath($Directory) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $kept = $current -split ';' |
        Where-Object { $_ } |
        Where-Object { $_.TrimEnd('\') -ine $Directory.TrimEnd('\') }
    $updated = ($kept -join ';')
    if ($updated -eq $current) { return $false }
    [Environment]::SetEnvironmentVariable('Path', $updated, 'Machine')
    return $true
}

# ---------------------------------------------------------------------------

if (-not (Test-Admin)) {
    Write-Step 'Asking for administrator rights'
    $copy = Join-Path $env:TEMP 'comp-lock-uninstall.ps1'
    Invoke-WebRequest -Uri $UninstallerUrl -OutFile $copy -UseBasicParsing

    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$copy`"",
        '-InstallDir', "`"$InstallDir`"", '-Ref', $Ref, '-Relaunched'
    )
    if ($Purge) { $arguments += '-Purge' }
    if ($Force) { $arguments += '-Force' }

    try {
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs `
            -ArgumentList $arguments -PassThru -Wait
    } catch {
        throw 'Administrator rights were refused. Re-run this in a terminal started with "Run as administrator".'
    }
    exit $elevated.ExitCode
}

try {
    Write-Host ''
    Write-Step 'Removing comp-lock'

    $venvPython = Join-Path $InstallDir 'venv\Scripts\python.exe'
    $haveTool = Test-Path -LiteralPath $venvPython

    $active = Get-ActiveLock
    if ($active) {
        Write-Step "Releasing the '$($active.profile)' lock"
        $released = $false
        if ($haveTool) {
            & $venvPython -m complock stop --force
            if ($LASTEXITCODE -eq 0) {
                $released = $true
            } elseif ($Force) {
                # A hard lock with allow_early_stop off refuses `stop`. Call the
                # teardown directly so the hosts file does not keep a block that
                # nothing is left to remove.
                & $venvPython -c "from complock.session import SessionStore; from complock.watchdog import teardown; teardown(SessionStore())"
                $released = $true
                Write-Detail 'Released a hard lock because -Force was given.'
            }
        }
        if (-not $released) {
            throw "The '$($active.profile)' lock would not release. Wait for it to finish, or pass -Force to end it and uninstall anyway."
        }
    } else {
        # There may still be a stale block from an expired lock nobody released.
        if ($haveTool) {
            & $venvPython -c "from complock.session import SessionStore; from complock.watchdog import teardown; teardown(SessionStore())" 2>$null
        }
        Write-Detail 'No lock was running.'
    }

    Write-Step 'Unregistering the scheduled task'
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Detail "Removed '$TaskName'."
    } else {
        Write-Detail "No task named '$TaskName' was registered."
    }

    Write-Step 'Taking complock off PATH'
    if (Remove-FromMachinePath $InstallDir) {
        Write-Detail "Removed $InstallDir from the machine PATH."
    } else {
        Write-Detail "$InstallDir was not on the machine PATH."
    }

    Write-Step 'Deleting the install directory'
    if (Test-Path -LiteralPath $InstallDir) {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
        Write-Detail "Deleted $InstallDir."
    } else {
        Write-Detail "Nothing at $InstallDir."
    }

    if ($Purge) {
        Write-Step 'Deleting configuration and logs (-Purge)'
        if (Test-Path -LiteralPath $DataDir) {
            Remove-Item -LiteralPath $DataDir -Recurse -Force
            Write-Detail "Deleted $DataDir."
        } else {
            Write-Detail "Nothing at $DataDir."
        }
    } else {
        Write-Step 'Keeping configuration and logs'
        Write-Detail "$DataDir was left in place. Pass -Purge to delete it."
    }

    Write-Host ''
    Write-Host 'comp-lock is removed.' -ForegroundColor Green
    Write-Host ''
    $exitCode = 0
} catch {
    Write-Host ''
    Write-Host "Uninstall failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    $exitCode = 1
}

if ($Relaunched) {
    Write-Host 'Press Enter to close this window.'
    try { Read-Host | Out-Null } catch { }
}

exit $exitCode
