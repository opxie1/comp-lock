import time

import pytest

from complock import paths
from complock.errors import SessionError
from complock.session import (
    Session,
    SessionStore,
    format_duration,
    parse_duration,
)


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("30", 1800),
        ("90m", 5400),
        ("2h", 7200),
        ("1h30m", 5400),
        ("45s", 45),
        ("1h30m15s", 5415),
        ("  90m  ", 5400),
        ("90M", 5400),
        ("1h 30m", 5400),
    ],
)
def test_durations_parse(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "0", "0m", "-5m", "25h", "m", "1x"])
def test_unusable_durations_are_rejected(text):
    with pytest.raises(SessionError):
        parse_duration(text)


@pytest.mark.parametrize(
    "seconds,text",
    [(45, "45s"), (90, "1m 30s"), (3600, "1h 00m"), (5415, "1h 30m"), (-10, "0s")],
)
def test_durations_format_for_reading(seconds, text):
    assert format_duration(seconds) == text


def test_remaining_counts_down_from_the_deadline():
    now = time.time()
    session = Session("social", now, now + 600, hard=False, pid=1)
    assert 598 < session.remaining <= 600
    assert session.is_active()


def test_an_expired_session_is_not_active():
    now = time.time()
    assert not Session("social", now - 100, now - 1, hard=False, pid=1).is_active()


class TestSessionStore:
    def session(self, offset=600):
        now = time.time()
        return Session("social", now, now + offset, hard=True, pid=4242)

    def test_round_trip(self, sandbox):
        store = SessionStore()
        original = self.session()
        store.save(original)
        assert store.load() == original

    def test_no_file_means_no_session(self, sandbox):
        assert SessionStore().load() is None

    def test_clear_removes_the_record(self, sandbox):
        store = SessionStore()
        store.save(self.session())
        store.clear()
        assert store.load() is None

    def test_clear_on_a_missing_record_is_quiet(self, sandbox):
        SessionStore().clear()

    def test_a_corrupt_record_is_reported_rather_than_ignored(self, sandbox):
        # Treating an unreadable record as "no lock" would turn a damaged file
        # into a way out of a lock.
        paths.session_file().parent.mkdir(parents=True, exist_ok=True)
        paths.session_file().write_text("{not json", encoding="utf-8")
        with pytest.raises(SessionError, match="readable lock record"):
            SessionStore().load()

    def test_a_record_missing_a_field_is_reported(self, sandbox):
        paths.session_file().parent.mkdir(parents=True, exist_ok=True)
        paths.session_file().write_text('{"profile": "social"}', encoding="utf-8")
        with pytest.raises(SessionError, match="readable lock record"):
            SessionStore().load()

    def test_saving_leaves_no_scratch_file_behind(self, sandbox):
        store = SessionStore()
        store.save(self.session())
        leftovers = list(paths.data_dir().glob("*.new"))
        assert leftovers == []

    def test_replace_pid_keeps_the_deadline(self, sandbox):
        original = self.session()
        moved = original.replace_pid(99)
        assert moved.pid == 99
        assert moved.ends_at == original.ends_at
        assert moved.started_at == original.started_at
