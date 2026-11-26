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
    [string]$PythonPath,
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

# Used only when the machine has no usable Python. Pinned rather than resolved
# at run time because python.org publishes no stable "latest" endpoint.
$PythonFallbackVersion = '3.13.7'

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

function Update-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Resolve-Interpreter($Candidate) {
    <#
    Asks a candidate interpreter where it really lives and what version it is.

    Going through sys.executable matters: `python` on PATH is usually an
    execution alias in WindowsApps, and that alias only resolves for the user
    who owns it. Recording the real path instead means the interpreter is still
    reachable after the installer elevates.
    #>
    if (-not $Candidate) { return $null }

    # Scoped to this function. A native command writing to stderr is not a
    # reason to abandon the install, which is what 'Stop' would make it.
    $ErrorActionPreference = 'Continue'

    # Deliberately free of quote characters. Windows PowerShell does not escape
    # embedded quotes when it builds the command line for a native program, so
    # a probe containing them reaches Python already mangled. One value per
    # line also keeps a path with spaces in it unambiguous.
    $probe = 'import sys; print(sys.executable); print(sys.version_info[0]); print(sys.version_info[1])'
    try {
        $raw = & $Candidate '-c' $probe
    } catch {
        return $null
    }

    $lines = @($raw | Where-Object { $_ -ne $null } | ForEach-Object { [string]$_ })
    if ($lines.Count -lt 3) { return $null }

    $exe = $lines[0].Trim()
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
    try {
        $version = [Version]("$($lines[1].Trim()).$($lines[2].Trim())")
    } catch {
        return $null
    }

    # An execution alias is a zero-length reparse point, so it is no use to an
    # elevated process or to the scheduled task.
    if ((Get-Item -LiteralPath $exe).Length -eq 0) { return $null }

    return [pscustomobject]@{ Path = $exe; Version = $version }
}

function Get-PythonCandidates {
    $found = New-Object 'System.Collections.Generic.List[string]'

    # The registry first. Unlike PATH it is readable once elevated, and a
    # per-user install records itself under HKCU and nowhere else.
    foreach ($root in @(
        'HKCU:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore'
    )) {
        if (-not (Test-Path $root)) { continue }
        foreach ($key in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            $installKey = Join-Path $key.PSPath 'InstallPath'
            if (-not (Test-Path $installKey)) { continue }
            $values = Get-ItemProperty $installKey -ErrorAction SilentlyContinue
            if ($values.ExecutablePath) {
                $found.Add([string]$values.ExecutablePath)
            } elseif ($values.'(default)') {
                $found.Add((Join-Path ([string]$values.'(default)') 'python.exe'))
            }
        }
    }

    foreach ($pattern in @(
        "$env:LocalAppData\Programs\Python\Python3*\python.exe",
        "$env:LocalAppData\Python\pythoncore-3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe",
        'C:\Python3*\python.exe'
    )) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            ForEach-Object { $found.Add($_.FullName) }
    }

    foreach ($name in @('py', 'python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $found.Add($command.Source) }
    }

    return ($found | Select-Object -Unique)
}

function Find-Python {
    $best = $null
    foreach ($candidate in (Get-PythonCandidates)) {
        $resolved = Resolve-Interpreter $candidate
        if (-not $resolved) { continue }
        if ($resolved.Version -lt $MinPython) { continue }
        if (-not $best -or $resolved.Version -gt $best.Version) { $best = $resolved }
    }
    return $best
}

function Install-Python {
    <#
    Installs Python for all users.

    winget is tried first because it keeps itself current, but it is itself an
    execution alias on most machines and so is often missing from an elevated
    session. The python.org installer is the dependable path and the one that
    actually runs on a machine with only a per-user Python.
    #>
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Detail 'Installing Python through winget. This takes a minute.'
        $ErrorActionPreference = 'Continue'
        & $winget.Source install --id Python.Python.3.13 --source winget --scope machine `
            --accept-source-agreements --accept-package-agreements --silent
        $wingetExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($wingetExit -eq 0) {
            Update-ProcessPath
            if (Find-Python) { return }
        }
        Write-Detail 'winget did not manage it. Falling back to the python.org installer.'
    } else {
        Write-Detail 'winget is not usable here, so using the python.org installer.'
    }

    $architecture = 'amd64'
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { $architecture = 'arm64' }
    $file = "python-$PythonFallbackVersion-$architecture.exe"
    $url = "https://www.python.org/ftp/python/$PythonFallbackVersion/$file"
    $target = Join-Path $env:TEMP $file

    Write-Detail "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing

    Write-Detail 'Installing Python for all users. This takes a couple of minutes.'
    # InstallAllUsers so the scheduled task can reach it as SYSTEM, and
    # Include_launcher so `py` works afterwards.
    $installer = Start-Process -FilePath $target -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0'
    )
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    if ($installer.ExitCode -ne 0) {
        throw "The Python installer stopped with exit code $($installer.ExitCode). Install Python $MinPython or newer from https://www.python.org/downloads/windows/ and run this again."
    }
    Update-ProcessPath
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

    # Resolved here rather than after elevating. A per-user Python is recorded
    # only in this user's registry hive, and `python` on PATH is usually an
    # execution alias that no elevated process can follow, so the elevated half
    # can easily find nothing at all on a machine that plainly has Python.
    if (-not $PythonPath) {
        $alreadyHave = Find-Python
        if ($alreadyHave) {
            Write-Detail "Found Python $($alreadyHave.Version) at $($alreadyHave.Path)"
            $PythonPath = $alreadyHave.Path
        }
    }
    if ($PythonPath) { $arguments += @('-PythonPath', "`"$PythonPath`"") }

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
    $python = $null
    if ($PythonPath) {
        # Handed over by the unelevated half, which could still see the user's
        # own installs.
        $python = Resolve-Interpreter $PythonPath
        if ($python -and $python.Version -lt $MinPython) { $python = $null }
        if (-not $python) {
            Write-Detail "The interpreter at $PythonPath is not usable; looking for another."
        }
    }
    if (-not $python) { $python = Find-Python }
    if ($python) {
        Write-Detail "Found Python $($python.Version) at $($python.Path)"
    } else {
        Write-Detail "No Python $MinPython or newer is installed for all users."
        Install-Python
        $python = Find-Python
        if (-not $python) {
            throw 'Python was installed but still cannot be found. Open a new terminal and run this again.'
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
