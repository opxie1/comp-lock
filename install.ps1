<#
.SYNOPSIS
Installs comp-lock.

.DESCRIPTION
Downloads the source, installs it into a private virtual environment under
C:\Program Files\comp-lock, puts a `complock` command on PATH, writes the
default configuration, and registers the scheduled task that lets a lock
survive a reboot.

The script asks for administrator rights if it does not already have them,
which is the Windows prompt about letting an app change your device. It needs
them because comp-lock rewrites the hosts file.

Run it with:

    irm https://raw.githubusercontent.com/opxie1/comp-lock/main/install.ps1 | iex

.PARAMETER InstallDir
Where to install. Defaults to C:\Program Files\comp-lock.

.PARAMETER Ref
Branch or tag to install from. Defaults to main.

.PARAMETER NoTask
Skip the scheduled task. Locks then end if the machine restarts or the
watchdog is killed.

.PARAMETER Force
Install even if a lock is currently running. The running watchdog is stopped
first.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'comp-lock'),
    [string]$Ref = 'main',
    [switch]$NoTask,
    [switch]$Force,
    [switch]$Relaunched
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Older Windows 10 builds still negotiate TLS 1.0 by default, which GitHub
# refuses.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'opxie1/comp-lock'
$InstallerUrl = "https://raw.githubusercontent.com/$Repo/$Ref/install.ps1"
$MinPython = [Version]'3.11'

function Write-Step($Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Detail($Message) {
    Write-Host "    $Message"
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PythonVersion($Exe) {
    # The `python` on PATH may be the Microsoft Store stub, which does not run
    # code, so ask the interpreter itself rather than trusting the file.
    try {
        $output = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or -not $output) { return $null }
    try {
        return [Version]($output | Select-Object -First 1).Trim()
    } catch {
        return $null
    }
}

function Find-Python {
    $seen = New-Object 'System.Collections.Generic.List[string]'

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($tag in @('-3.14', '-3.13', '-3.12', '-3.11', '-3')) {
            try {
                $found = & $launcher.Source $tag -c "import sys; print(sys.executable)" 2>$null
            } catch {
                continue
            }
            if ($LASTEXITCODE -eq 0 -and $found) { $seen.Add($found.Trim()) }
        }
    }

    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $seen.Add($command.Source) }
    }

    foreach ($exe in ($seen | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $exe)) { continue }
        $version = Get-PythonVersion $exe
        if ($version -and $version -ge $MinPython) {
            return [pscustomobject]@{ Path = $exe; Version = $version }
        }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "comp-lock needs Python $MinPython or newer, and winget is not available to install it. Install Python from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH') and run this again."
    }
    Write-Detail 'Installing Python through winget. This takes a minute.'
    & $winget.Source install --id Python.Python.3.13 --source winget `
        --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install Python (exit $LASTEXITCODE). Install it from https://www.python.org/downloads/windows/ and run this again."
    }
    # winget edits the machine PATH, which this process does not see.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Get-ActiveLock {
    $record = Join-Path $env:ProgramData 'comp-lock\session.json'
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

function Add-ToMachinePath($Directory) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $entries = $current -split ';' | Where-Object { $_ }
    foreach ($entry in $entries) {
        if ($entry.TrimEnd('\') -ieq $Directory.TrimEnd('\')) { return $false }
    }
    [Environment]::SetEnvironmentVariable('Path', ($current.TrimEnd(';') + ';' + $Directory), 'Machine')
    return $true
}

# ---------------------------------------------------------------------------

if ($env:OS -ne 'Windows_NT') {
    throw 'comp-lock only runs on Windows.'
}

if (-not (Test-Admin)) {
    Write-Step 'Asking for administrator rights'
    Write-Detail 'Windows will ask whether to let this change your device. comp-lock'
    Write-Detail 'needs it to rewrite the hosts file.'

    # Piped through `iex` there is no script file to re-launch, so fetch a copy.
    $copy = Join-Path $env:TEMP 'comp-lock-install.ps1'
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $copy -UseBasicParsing

    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$copy`"",
        '-InstallDir', "`"$InstallDir`"", '-Ref', $Ref, '-Relaunched'
    )
    if ($NoTask) { $arguments += '-NoTask' }
    if ($Force) { $arguments += '-Force' }

    $declined = $false
    try {
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs `
            -ArgumentList $arguments -PassThru -Wait
    } catch {
        $declined = $true
    }

    if ($declined) {
        Write-Host ''
        Write-Host 'Administrator rights were refused, so nothing was installed.' -ForegroundColor Red
        Write-Host 'Start a terminal with "Run as administrator" and paste the same line again.'
        Write-Host ''
    } elseif ($elevated.ExitCode -ne 0) {
        Write-Host ''
        Write-Host 'The install did not finish. The window it opened says why.' -ForegroundColor Red
        Write-Host ''
    }

    # Deliberately not `exit`: piped through iex that would close the terminal
    # the line was pasted into.
    return
}

try {
    Write-Host ''
    Write-Step "Installing comp-lock from $Repo ($Ref)"

    $venvPythonExisting = Join-Path $InstallDir 'venv\Scripts\python.exe'
    $active = Get-ActiveLock
    if ($active) {
        if (-not $Force) {
            throw "A '$($active.profile)' lock is still running. Let it finish, or run 'complock stop --force', then install again. Pass -Force to install anyway."
        }
        Write-Step 'Releasing the lock that is currently running (-Force)'
        if (Test-Path -LiteralPath $venvPythonExisting) {
            # Reuses the tool's own teardown rather than guessing at the hosts
            # file, so the browser policy and the lock record go back too.
            & $venvPythonExisting -c "from complock.session import SessionStore; from complock.watchdog import teardown; teardown(SessionStore())"
            Write-Detail 'Released.'
        } else {
            Write-Detail 'No existing installation to release it with; continuing.'
        }
    }

    Write-Step 'Looking for Python'
    $python = Find-Python
    if ($python) {
        Write-Detail "Found Python $($python.Version) at $($python.Path)"
    } else {
        Write-Detail "No Python $MinPython or newer on this machine."
        Install-Python
        $python = Find-Python
        if (-not $python) {
            throw 'Python was installed but cannot be found. Open a new terminal and run this again.'
        }
        Write-Detail "Found Python $($python.Version) at $($python.Path)"
    }

    Write-Step 'Downloading the source'
    $staging = Join-Path ([IO.Path]::GetTempPath()) ("comp-lock-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $archive = Join-Path $staging 'source.zip'
    Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/heads/$Ref.zip" `
        -OutFile $archive -UseBasicParsing
    Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
    $source = Get-ChildItem -LiteralPath $staging -Directory | Select-Object -First 1
    if (-not $source) { throw 'The downloaded archive did not contain the source.' }
    Write-Detail "Unpacked to $($source.FullName)"

    Write-Step "Installing into $InstallDir"
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    $venv = Join-Path $InstallDir 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $venvPython)) {
        # A private environment keeps comp-lock off the user's Python and
        # guarantees the scheduled task can import it as SYSTEM.
        & $python.Path -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create a virtual environment at $venv" }
    }

    & $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
    & $venvPython -m pip install --upgrade $source.FullName --quiet --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "Could not install comp-lock (pip exit $LASTEXITCODE)" }

    $version = & $venvPython -m complock --version
    if ($LASTEXITCODE -ne 0) { throw 'comp-lock was installed but will not run.' }
    Write-Detail "Installed $version"

    Write-Step 'Putting complock on PATH'
    $shim = Join-Path $InstallDir 'complock.cmd'
    @(
        '@echo off'
        'rem Entry point written by install.ps1. Calls the private environment.'
        "`"%~dp0venv\Scripts\python.exe`" -m complock %*"
    ) | Set-Content -LiteralPath $shim -Encoding ASCII
    if (Add-ToMachinePath $InstallDir) {
        Write-Detail "Added $InstallDir to the machine PATH"
    } else {
        Write-Detail "$InstallDir is already on the machine PATH"
    }
    $env:Path = $env:Path + ';' + $InstallDir

    Write-Step 'Writing the default configuration'
    $config = Join-Path $env:ProgramData 'comp-lock\config.toml'
    if (Test-Path -LiteralPath $config) {
        Write-Detail "Keeping the configuration already at $config"
    } else {
        & $venvPython -m complock init | ForEach-Object { Write-Detail $_ }
    }

    if ($NoTask) {
        Write-Step 'Skipping the scheduled task (-NoTask)'
        Write-Detail 'Locks will end if the machine restarts or the watchdog is killed.'
    } else {
        Write-Step 'Registering the reboot task'
        & (Join-Path $source.FullName 'scripts\install-task.ps1') -Python $venvPython |
            ForEach-Object { Write-Detail $_ }
    }

    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host 'comp-lock is installed.' -ForegroundColor Green
    Write-Host ''
    Write-Host '  Open a new terminal, then try:'
    Write-Host ''
    Write-Host '    complock profiles'
    Write-Host '    complock start --profile social --for 90m'
    Write-Host '    complock status'
    Write-Host '    complock stop --force'
    Write-Host ''
    Write-Host '  complock start needs an elevated terminal, for the same reason this'
    Write-Host '  installer did. Run complock doctor if something looks wrong.'
    Write-Host ''
    $exitCode = 0
} catch {
    Write-Host ''
    Write-Host "Install failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    $exitCode = 1
}

if ($Relaunched) {
    # A window of its own, opened by the elevation prompt. It has to hold open
    # long enough to be read, and its exit code is what the unelevated process
    # reports on.
    Write-Host 'Press Enter to close this window.'
    try { Read-Host | Out-Null } catch { }
    exit $exitCode
}

# Reached only when the script was already elevated, so there is no window to
# report back to and no exit code worth closing a terminal over.
if ($exitCode -ne 0) { throw 'comp-lock was not installed.' }
