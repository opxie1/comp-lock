import pytest

from complock import hosts, paths
from complock.errors import HostsError

EXISTING = "127.0.0.1 localhost\n::1 localhost\n"


def test_apply_keeps_existing_entries():
    result = hosts.apply(EXISTING, ["tiktok.com"])
    assert "127.0.0.1 localhost" in result
    assert "0.0.0.0 tiktok.com" in result
    assert result.endswith("\n")


def test_strip_restores_the_original_text():
    blocked = hosts.apply(EXISTING, ["tiktok.com", "www.tiktok.com"])
    assert hosts.strip(blocked).strip() == EXISTING.strip()


def test_apply_replaces_an_earlier_region_instead_of_stacking():
    once = hosts.apply(EXISTING, ["tiktok.com"])
    twice = hosts.apply(once, ["youtube.com"])
    assert twice.count(hosts.BEGIN) == 1
    assert "tiktok.com" not in twice
    assert "0.0.0.0 youtube.com" in twice


def test_strip_drops_an_unterminated_region():
    # A write cut short leaves the opening marker with no closing one. Anything
    # after it was ours, so it all goes.
    truncated = EXISTING + hosts.BEGIN + "\n0.0.0.0 tiktok.com\n"
    assert hosts.strip(truncated).strip() == EXISTING.strip()


def test_extract_returns_none_without_a_region():
    assert hosts.extract(EXISTING) is None


def test_extract_lists_the_blocked_hostnames():
    text = hosts.apply(EXISTING, ["a.com", "b.com"])
    assert hosts.extract(text) == ["a.com", "b.com"]


def test_extract_on_an_empty_region_returns_an_empty_list():
    assert hosts.extract(hosts.apply(EXISTING, [])) == []


def test_apply_to_an_empty_file_does_not_start_with_blank_lines():
    result = hosts.apply("", ["tiktok.com"])
    assert result.startswith(hosts.BEGIN)


class TestHostsFile:
    def test_block_then_unblock_leaves_the_file_as_it_was(self, sandbox):
        path = paths.hosts_file()
        before = path.read_bytes()
        handle = hosts.HostsFile(path, paths.hosts_backup())

        assert handle.block(["tiktok.com"]) is True
        assert b"tiktok.com" in path.read_bytes()

        assert handle.unblock() is True
        assert path.read_bytes() == before

    def test_block_is_idempotent(self, sandbox):
        handle = hosts.HostsFile(paths.hosts_file())
        assert handle.block(["tiktok.com"]) is True
        assert handle.block(["tiktok.com"]) is False

    def test_unblock_without_a_region_reports_no_change(self, sandbox):
        assert hosts.HostsFile(paths.hosts_file()).unblock() is False

    def test_writes_crlf_line_endings(self, sandbox):
        path = paths.hosts_file()
        hosts.HostsFile(path).block(["tiktok.com"])
        raw = path.read_bytes()
        assert b"\r\n" in raw
        # Every newline must be part of a CRLF pair; a bare LF would leave the
        # file inconsistent with what Windows writes.
        assert raw.replace(b"\r\n", b"").count(b"\n") == 0

    def test_backs_up_the_original_once(self, sandbox):
        path = paths.hosts_file()
        backup = paths.hosts_backup()
        original = path.read_bytes()
        handle = hosts.HostsFile(path, backup)

        handle.block(["tiktok.com"])
        assert backup.read_bytes() == original

        handle.block(["youtube.com"])
        assert backup.read_bytes() == original, "the backup must stay the pre-lock copy"

    def test_missing_file_reads_as_empty(self, sandbox, tmp_path):
        assert hosts.HostsFile(tmp_path / "absent").read() == ""

    def test_undecodable_file_is_refused_rather_than_overwritten(self, sandbox):
        path = paths.hosts_file()
        path.write_bytes(b"\xff\xfe\x00bad")
        with pytest.raises(HostsError, match="ASCII or UTF-8"):
            hosts.HostsFile(path).read()

    def test_a_region_edited_by_hand_is_detected(self, sandbox):
        path = paths.hosts_file()
        handle = hosts.HostsFile(path)
        handle.block(["a.com", "b.com"])
        raw = path.read_bytes().replace(b"0.0.0.0 b.com\r\n", b"")
        path.write_bytes(raw)
        assert handle.blocked() == ["a.com"]
