import pytest

from complock import apps, win32


class FakeProcesses:
    """Stands in for the Win32 process calls.

    Each entry is pid -> (name, command line or None, image path or None). A
    None models a process comp-lock cannot inspect, which is the case the
    guarded matching has to get right.
    """

    def __init__(self, entries):
        self.entries = entries
        self.terminated = []

    def install(self, monkeypatch):
        monkeypatch.setattr(
            win32, "iter_processes", lambda: [(pid, v[0]) for pid, v in self.entries.items()]
        )
        monkeypatch.setattr(
            win32, "process_command_line", lambda pid: self.entries.get(pid, (None,) * 3)[1]
        )
        monkeypatch.setattr(
            win32, "process_image_path", lambda pid: self.entries.get(pid, (None,) * 3)[2]
        )
        monkeypatch.setattr(win32, "terminate", lambda pid: self.terminated.append(pid))
        return self


MINECRAFT_JAVA = (
    "javaw.exe",
    r'"C:\Program Files\Java\jre1.8.0\bin\javaw.exe" -Xmx2G '
    r'-Djava.library.path=C:\Users\me\AppData\Roaming\.minecraft\versions\1.20\natives '
    r"net.minecraft.client.main.Main --username me",
    r"C:\Program Files\Java\jre1.8.0\bin\javaw.exe",
)
UNRELATED_JAVA = (
    "javaw.exe",
    r'"C:\Program Files\Java\jre1.8.0\bin\javaw.exe" -jar C:\work\invoices\report.jar',
    r"C:\Program Files\Java\jre1.8.0\bin\javaw.exe",
)
GUARD = {"javaw.exe": ["minecraft", "prismlauncher", "multimc"]}


class TestSweep:
    def test_closes_a_plain_name_match(self, monkeypatch):
        fake = FakeProcesses({100: ("tiktok.exe", None, None)}).install(monkeypatch)
        closed = apps.sweep(["tiktok.exe"])
        assert fake.terminated == [100]
        assert closed == [(100, "tiktok.exe")]

    def test_leaves_everything_else_alone(self, monkeypatch):
        fake = FakeProcesses({100: ("notepad.exe", None, None)}).install(monkeypatch)
        assert apps.sweep(["tiktok.exe"]) == []
        assert fake.terminated == []

    def test_matching_is_case_insensitive(self, monkeypatch):
        fake = FakeProcesses({100: ("TikTok.EXE", None, None)}).install(monkeypatch)
        apps.sweep(["tiktok.exe"])
        assert fake.terminated == [100]

    def test_never_closes_a_protected_process(self, monkeypatch):
        fake = FakeProcesses({100: ("explorer.exe", None, None)}).install(monkeypatch)
        assert apps.sweep(["explorer.exe"]) == []
        assert fake.terminated == []

    def test_an_empty_profile_closes_nothing(self, monkeypatch):
        fake = FakeProcesses({100: ("tiktok.exe", None, None)}).install(monkeypatch)
        assert apps.sweep([]) == []
        assert fake.terminated == []

    def test_a_refused_kill_does_not_stop_the_sweep(self, monkeypatch):
        fake = FakeProcesses(
            {100: ("tiktok.exe", None, None), 200: ("tiktok.exe", None, None)}
        ).install(monkeypatch)

        def refuse_first(pid):
            if pid == 100:
                raise OSError("access denied")
            fake.terminated.append(pid)

        monkeypatch.setattr(win32, "terminate", refuse_first)
        closed = apps.sweep(["tiktok.exe"])
        assert fake.terminated == [200]
        assert closed == [(200, "tiktok.exe")]


class TestGuardedSweep:
    """javaw.exe is every Java program, so it is only closed on a match."""

    def test_closes_minecraft_java(self, monkeypatch):
        fake = FakeProcesses({500: MINECRAFT_JAVA}).install(monkeypatch)
        closed = apps.sweep(["javaw.exe"], GUARD)
        assert fake.terminated == [500]
        assert closed == [(500, "javaw.exe")]

    def test_spares_an_unrelated_java_program(self, monkeypatch):
        fake = FakeProcesses({500: UNRELATED_JAVA}).install(monkeypatch)
        assert apps.sweep(["javaw.exe"], GUARD) == []
        assert fake.terminated == [], "an unrelated Java program must survive"

    def test_tells_the_two_apart_in_one_sweep(self, monkeypatch):
        fake = FakeProcesses({500: MINECRAFT_JAVA, 501: UNRELATED_JAVA}).install(monkeypatch)
        apps.sweep(["javaw.exe"], GUARD)
        assert fake.terminated == [500]

    def test_an_unreadable_process_is_left_running(self, monkeypatch):
        # Neither the command line nor the path can be read, so there is no
        # evidence either way. Killing on a guess is the worse mistake.
        fake = FakeProcesses({500: ("javaw.exe", None, None)}).install(monkeypatch)
        assert apps.sweep(["javaw.exe"], GUARD) == []
        assert fake.terminated == []

    def test_the_image_path_alone_can_match(self, monkeypatch):
        fake = FakeProcesses(
            {500: ("javaw.exe", None, r"C:\Users\me\AppData\Roaming\PrismLauncher\java\javaw.exe")}
        ).install(monkeypatch)
        apps.sweep(["javaw.exe"], GUARD)
        assert fake.terminated == [500]

    def test_the_command_line_alone_can_match(self, monkeypatch):
        fake = FakeProcesses({500: ("javaw.exe", "javaw -cp x org.multimc.EntryPoint", None)}).install(
            monkeypatch
        )
        apps.sweep(["javaw.exe"], GUARD)
        assert fake.terminated == [500]

    def test_fragments_match_regardless_of_case(self, monkeypatch):
        fake = FakeProcesses({500: ("javaw.exe", "-Dfoo NET.MINECRAFT.Client", None)}).install(
            monkeypatch
        )
        apps.sweep(["javaw.exe"], {"javaw.exe": ["MineCraft"]})
        assert fake.terminated == [500]

    def test_a_guard_for_an_unlisted_process_changes_nothing(self, monkeypatch):
        # The guard only narrows; it never adds a process to the profile.
        fake = FakeProcesses({500: MINECRAFT_JAVA}).install(monkeypatch)
        assert apps.sweep(["tiktok.exe"], GUARD) == []
        assert fake.terminated == []

    def test_an_unguarded_process_is_not_inspected(self, monkeypatch):
        fake = FakeProcesses({500: ("tiktok.exe", None, None)}).install(monkeypatch)

        def explode(pid):
            raise AssertionError("an unguarded process should not be inspected")

        monkeypatch.setattr(win32, "process_command_line", explode)
        apps.sweep(["tiktok.exe"], GUARD)
        assert fake.terminated == [500]
