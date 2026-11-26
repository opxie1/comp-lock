# comp-lock

A focus lock for Windows. It blocks a named set of sites and desktop
applications for a fixed stretch of time, then puts everything back when the
time is up.

There are two mechanisms, and both need an elevated prompt:

- Sites are pointed at `0.0.0.0` in `%SystemRoot%\System32\drivers\etc\hosts`.
  comp-lock owns one fenced region of that file and leaves every other line
  alone, so a machine that already redirects hosts for development keeps those
  entries.
- Applications are closed as they appear. A watchdog checks every couple of
  seconds, so launching a blocked app during a lock gets you a window that
  disappears.

Closing an application on sight is cruder than stopping it from starting, which
you can do with an Image File Execution Options redirect. The redirect was left
out on purpose: a bad value there can leave a program unlaunchable long after
the lock ends, and a sweep that runs every two seconds repairs itself.

## Installing

Open a terminal and paste one line:

```powershell
irm https://raw.githubusercontent.com/opxie1/comp-lock/main/install.ps1 | iex
```

Windows asks whether to let it change your device. Say yes. comp-lock rewrites
the hosts file, and that needs administrator rights, so the installer asks for
them up front rather than failing later.

It finds Python 3.11 or newer, installing it from winget or python.org if the
machine has none, then puts comp-lock in its own virtual environment under
`C:\Program Files\comp-lock`, adds a `complock` command to PATH, writes the
default configuration to `C:\ProgramData\comp-lock\config.toml`, and registers
the scheduled task described below. Open a new terminal afterwards so the PATH
change takes effect.

Python is located before the installer elevates, and the path is handed to the
elevated half. That is not fussiness: `python` on PATH is usually an execution
alias under `WindowsApps` that only resolves for the user who owns it, a
per-user Python is registered in that user's registry hive and nowhere else,
and `winget` is an alias too. An elevated process that looks for any of them
the ordinary way can easily find nothing on a machine that plainly has Python.

If the only Python on the machine is installed for one user, the scheduled task
runs as SYSTEM and may not be able to reach it. The installer runs the task once
to check, and says so if that is the case.

Removing it works the same way. Any lock still running is released first:

```powershell
irm https://raw.githubusercontent.com/opxie1/comp-lock/main/uninstall.ps1 | iex
```

To pass options, download the script first instead of piping it:

```powershell
irm https://raw.githubusercontent.com/opxie1/comp-lock/main/install.ps1 -OutFile install.ps1; .\install.ps1 -NoTask
```

`install.ps1` takes `-InstallDir`, `-Ref`, `-NoTask`, and `-Force`.
`uninstall.ps1` takes `-Purge` to delete the configuration and logs as well,
and `-Force` to end a hard lock that would otherwise block the uninstall.

Re-running the installer upgrades the code but leaves `config.toml` alone, so
that it never discards settings you have edited. That also means new bundled
profiles do not appear on their own. To take the current defaults, overwriting
whatever is there:

```powershell
complock init --force
```

### From source

```powershell
python -m pip install .
complock init
.\scripts\install-task.ps1
```

Run that from an elevated prompt, and install machine-wide rather than with
`--user`, or the scheduled task will not be able to import the package.

## Requirements

Windows 10 or 11. Python 3.11 or newer, which the installer will fetch if you
do not have it. There are no third-party dependencies.

## Using it

`start` and `stop` change the hosts file, so run them from a terminal started
with "Run as administrator". `status`, `profiles` and `doctor` do not need it.

Start a lock and get your prompt back:

```powershell
complock start --profile social --for 90m
```

`--for` takes `90m`, `2h`, `1h30m`, `45s`, or a plain number of minutes. The
longest lock is 24 hours.

Check on it:

```powershell
complock status
```

End it early:

```powershell
complock stop --force
```

`stop` refuses without `--force` while a lock is still running, and refuses
outright on a hard lock when `allow_early_stop` is off.

Other commands:

| Command | What it does |
| --- | --- |
| `complock profiles` | Lists the profiles and how much each one covers |
| `complock run` | Runs a lock in the foreground instead of detaching, which is the easiest way to watch what it is doing |
| `complock doctor` | Checks privileges, the hosts file, browser policy, the lock record, and the reboot task |
| `complock resume` | Re-engages an unfinished lock. The scheduled task calls this; you should not need to |

Exit codes are 0 for success, 1 when there was nothing to do (no lock running,
a file already present), and 2 for an error.

## Profiles

A profile names what a lock covers. Four come with the tool: `minecraft`,
`social`, `media`, and `deep`. Edit `config.toml` to change them or add your
own.

```toml
[profiles.evening]
domains = ["@social", "@video", "news.ycombinator.com"]
processes = ["Discord", "Steam"]
```

`domains` takes hostnames, or `@name` to pull in one of the bundled lists:
`social`, `video`, `news`, `games`, `shopping`, and `minecraft`. Every hostname
also gets a `www.` alias, so there is no need to write both. `processes` takes
executable names matched on the file name without regard to case, and a name
that does not already end in an executable extension gets `.exe` added.

Browsers are deliberately absent from every `processes` list. Closing one takes
the rest of your tabs with it, and the hosts file already covers the sites.

## Executables that host more than one program

Some executables are not one application. `javaw.exe` is every Java program on
the machine, so closing it on sight would take unrelated work down alongside
the game you meant to block. An executable listed in the `command_line` table
is closed only when its command line, or its own path, contains one of the
fragments given for it:

```toml
[command_line]
"javaw.exe" = ["minecraft", "prismlauncher", "multimc", "modrinth", "atlauncher"]
```

A process whose command line cannot be read is left running rather than guessed
at, so the failure goes in the harmless direction. `complock profiles` lists
these separately from the rest, since "22 applications" would otherwise imply
that every Java program is fair game.

This is what makes blocking Minecraft Java Edition actually work. The launcher
is one process, but the game itself is `javaw.exe`, and a third-party launcher
such as Prism or MultiMC never starts the official launcher at all. The
`minecraft` blocklist also covers `minecraftservices.com` and
`sessionserver.mojang.com`, which carry authentication: with those unreachable
the game cannot sign in, whichever launcher started it.

Minecraft is in every bundled profile, including the lightest one, rather than
only in `deep`. Use the `minecraft` profile on its own to block just the game.

## Surviving a reboot

On its own, a lock would end if the machine restarted or the watchdog were
killed. A scheduled task, which the installer registers for you, runs
`complock resume` at startup and once a minute afterwards. `resume` starts a
new watchdog if a lock still has time left, and releases the block if the
deadline passed while nothing was watching.

Add or remove it by hand with `.\scripts\install-task.ps1` and
`.\scripts\uninstall-task.ps1`, or skip it at install time with `-NoTask`.
`complock doctor` reports whether it is registered.

## Hard locks

`--hard` changes one thing: if the watchdog is interrupted, it leaves the block
in place instead of tidying up, and the scheduled task starts a new watchdog
within the minute. Combined with `allow_early_stop = false` in the
configuration, `complock stop` will not end the lock before its deadline.

This is a speed bump, not a security boundary. You have administrator rights,
which means you can always edit the hosts file, delete the lock record in
`C:\ProgramData\comp-lock\`, or unregister the task. The point is to make
quitting take enough deliberate steps that you notice you are doing it.

## Limitations

Worth knowing before you rely on it:

- Chrome, Edge, and Firefox can resolve names themselves over HTTPS, which
  skips the hosts file completely. comp-lock turns that off by group policy
  while a lock runs, but policy is read at browser startup, so a browser that
  is already open keeps resolving the old way until you restart it. `complock
  start` says so when it sees one running.
- A VPN or proxy that does its own name resolution also skips the hosts file.
- Reaching a site by IP address skips it too.
- Someone editing the hosts file by hand gets through for a second or two
  before the watchdog puts the region back.
- Closing an application is abrupt. Unsaved work in a blocked app is lost, so
  do not put anything you actually work in on a `processes` list.
- The hosts file is machine-wide. A lock applies to every account on the
  computer, not just yours.
- None of this touches your phone.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

The tests redirect the data directory and the hosts file through the
`COMPLOCK_DATA_DIR` and `COMPLOCK_HOSTS` environment variables, so they need
neither administrator rights nor a real hosts file. Those variables are also
handy for trying a profile out against a scratch file before you commit to it.

## Licence

MIT. See [LICENSE](LICENSE).
