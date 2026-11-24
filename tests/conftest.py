import pytest


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point comp-lock at a temporary data directory and hosts file.

    Without this every test that touches state would need administrator rights
    and would edit the real hosts file.
    """
    data = tmp_path / "data"
    data.mkdir()
    hosts = tmp_path / "hosts"
    # Written as bytes: text mode would translate the newline again and leave
    # a CR CR LF that no real hosts file has.
    hosts.write_bytes(b"127.0.0.1 localhost\r\n")
    monkeypatch.setenv("COMPLOCK_DATA_DIR", str(data))
    monkeypatch.setenv("COMPLOCK_HOSTS", str(hosts))
    return tmp_path
