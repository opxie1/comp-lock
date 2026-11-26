import subprocess

import pytest

from complock import cli


def fake_run(returncode, stdout="", stderr=""):
    def run(*args, **kwargs):
        assert kwargs.get("text") is True, "output has to be decoded to be inspected"
        return subprocess.CompletedProcess(args[0], returncode, stdout, stderr)

    return run


class TestScheduledTaskFinding:
    """schtasks exits 1 for a missing task and for an unreadable one alike.

    Treating both as missing told anyone running doctor without administrator
    rights that reboot persistence was broken while it was working.
    """

    def test_registered(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", fake_run(0, "TaskName: comp-lock resume"))
        level, label, detail = cli._scheduled_task_finding()
        assert (level, label) == ("ok", "reboot task")
        assert detail == "registered"

    def test_access_denied_is_not_reported_as_missing(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", fake_run(1, stderr="ERROR: Access is denied."))
        level, _, detail = cli._scheduled_task_finding()
        assert level == "warn"
        assert "administrator rights" in detail
        assert "not registered" not in detail

    def test_access_denied_on_stdout_is_also_caught(self, monkeypatch):
        # schtasks is inconsistent about which stream it uses.
        monkeypatch.setattr(subprocess, "run", fake_run(1, stdout="ERROR: Access is Denied."))
        level, _, detail = cli._scheduled_task_finding()
        assert "administrator rights" in detail

    def test_genuinely_missing(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            fake_run(1, stderr="ERROR: The system cannot find the file specified."),
        )
        level, _, detail = cli._scheduled_task_finding()
        assert level == "warn"
        assert "not registered" in detail

    def test_schtasks_missing_entirely(self, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("schtasks not found")

        monkeypatch.setattr(subprocess, "run", explode)
        level, _, detail = cli._scheduled_task_finding()
        assert level == "warn"
        assert "task scheduler" in detail

    def test_queries_the_name_the_installer_registers(self, monkeypatch):
        seen = {}

        def run(*args, **kwargs):
            seen["argv"] = args[0]
            return subprocess.CompletedProcess(args[0], 0, "", "")

        monkeypatch.setattr(subprocess, "run", run)
        cli._scheduled_task_finding()
        assert cli.TASK_NAME in seen["argv"]
        assert cli.TASK_NAME == "comp-lock resume"


class TestDoctor:
    def test_reports_every_section_and_exits_nonzero_on_failure(self, sandbox, capsys):
        # Not elevated under test, so the privilege check fails and doctor
        # should exit 1 while still printing the rest.
        code = cli.main(["doctor"])
        out = capsys.readouterr().out
        for label in ("administrator rights", "hosts file", "configuration", "lock state", "reboot task"):
            assert label in out, label
        assert code in (0, 1)

    def test_every_line_is_a_known_level(self, sandbox, capsys):
        cli.main(["doctor"])
        out = capsys.readouterr().out
        for line in [l for l in out.splitlines() if l.strip()]:
            assert line.split()[0] in {"ok", "warn", "fail"}, line
