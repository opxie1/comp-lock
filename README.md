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

## Requirements

Windows 10 or 11, Python 3.11 or newer, and administrator rights. There are no
third-party dependencies.

## Installing

From an elevated prompt, so that the scheduled task below can find the package:

```powershell
python -m pip install .
```

Then write the configuration file:

```powershell
complock init
```

That puts a commented `config.toml` in `C:\ProgramData\comp-lock\`, next to the
lock record and the log.

## Using it

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

A profile names what a lock covers. Three come with the tool: `social`,
`media`, and `deep`. Edit `config.toml` to change them or add your own.

```toml
[profiles.evening]
domains = ["@social", "@video", "news.ycombinator.com"]
processes = ["Discord", "Steam"]
```

`domains` takes hostnames, or `@name` to pull in one of the bundled lists:
`social`, `video`, `news`, `games`, and `shopping`. Every hostname also gets a
`www.` alias, so there is no need to write both. `processes` takes executable
names matched on the file name without regard to case, and a name with no
extension is read as `.exe`.

Browsers are deliberately absent from every `processes` list. Closing one takes
the rest of your tabs with it, and the hosts file already covers the sites.

## Surviving a reboot

On its own, a lock ends if the machine restarts or the watchdog is killed. The
scheduled task fixes that:

```powershell
.\scripts\install-task.ps1
```

It registers a task that runs `complock resume` at startup and once a minute
afterwards. `resume` restarts the watchdog if a lock still has time left, and
releases the block if the deadline passed while nothing was watching. Remove it
with `.\scripts\uninstall-task.ps1`.

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
