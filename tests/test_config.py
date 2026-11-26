import pytest

from complock import config
from complock.errors import ConfigError

MINIMAL = """
[profiles.focus]
domains = ["example.com"]
"""


def write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


DEFAULT_PROFILES = {"minecraft", "social", "media", "deep"}


def test_packaged_default_loads(sandbox):
    loaded = config.load()
    assert DEFAULT_PROFILES <= set(loaded.profiles)
    assert config.MIN_POLL_INTERVAL <= loaded.poll_interval <= config.MAX_POLL_INTERVAL


def test_default_text_is_what_init_writes(sandbox):
    # `complock init` copies this text verbatim, so it has to parse as the
    # configuration it claims to be.
    text = config.default_text()
    path = sandbox / "copied.toml"
    path.write_text(text, encoding="utf-8")
    assert set(config.load(path).profiles) == DEFAULT_PROFILES


def test_default_guards_java_so_it_is_not_killed_blindly(sandbox):
    # Every default profile lists javaw, so the guard has to be there or a lock
    # would close unrelated Java programs.
    loaded = config.load()
    for profile in loaded.profiles.values():
        if "javaw" in profile.processes:
            assert "javaw.exe" in loaded.command_line, profile.name
    assert "minecraft" in loaded.command_line["javaw.exe"]


class TestCommandLine:
    def test_absent_table_is_empty(self, tmp_path):
        assert config.load(write(tmp_path, MINIMAL)).command_line == {}

    def test_names_gain_an_exe_extension(self, tmp_path):
        body = MINIMAL + '\n[command_line]\njavaw = ["minecraft"]\n'
        assert config.load(write(tmp_path, body)).command_line == {"javaw.exe": ("minecraft",)}

    def test_names_are_lowercased(self, tmp_path):
        body = MINIMAL + '\n[command_line]\n"JavaW.EXE" = ["Minecraft"]\n'
        parsed = config.load(write(tmp_path, body)).command_line
        assert set(parsed) == {"javaw.exe"}

    def test_an_entry_with_no_fragments_is_rejected(self, tmp_path):
        body = MINIMAL + "\n[command_line]\njavaw = []\n"
        with pytest.raises(ConfigError, match="never match"):
            config.load(write(tmp_path, body))

    def test_fragments_must_be_strings(self, tmp_path):
        body = MINIMAL + "\n[command_line]\njavaw = [3]\n"
        with pytest.raises(ConfigError, match="not a name"):
            config.load(write(tmp_path, body))

    def test_table_must_be_a_table(self, tmp_path):
        # Ahead of the profile table: a bare key after one would belong to it.
        body = 'command_line = "javaw"\n' + MINIMAL
        with pytest.raises(ConfigError, match="table of executable names"):
            config.load(write(tmp_path, body))


def test_missing_file_falls_back_to_the_default(tmp_path):
    loaded = config.load(tmp_path / "nothing.toml")
    assert loaded.source is None
    assert loaded.profiles


def test_file_is_used_when_present(tmp_path):
    loaded = config.load(write(tmp_path, MINIMAL))
    assert set(loaded.profiles) == {"focus"}
    assert loaded.source is not None


def test_unknown_top_level_key_is_rejected(tmp_path):
    body = "poll_intervl = 2.0\n" + MINIMAL
    with pytest.raises(ConfigError, match="poll_intervl"):
        config.load(write(tmp_path, body))


def test_unknown_profile_key_is_rejected(tmp_path):
    body = '[profiles.focus]\ndomians = ["example.com"]\n'
    with pytest.raises(ConfigError, match="domians"):
        config.load(write(tmp_path, body))


@pytest.mark.parametrize("value", ["0.1", "120"])
def test_poll_interval_outside_the_allowed_range_is_rejected(tmp_path, value):
    body = f"poll_interval = {value}\n" + MINIMAL
    with pytest.raises(ConfigError, match="poll_interval"):
        config.load(write(tmp_path, body))


def test_poll_interval_must_be_a_number(tmp_path):
    body = 'poll_interval = "fast"\n' + MINIMAL
    with pytest.raises(ConfigError, match="must be a number"):
        config.load(write(tmp_path, body))


def test_flag_must_be_boolean(tmp_path):
    body = 'allow_early_stop = "no"\n' + MINIMAL
    with pytest.raises(ConfigError, match="true or false"):
        config.load(write(tmp_path, body))


def test_a_profile_that_blocks_nothing_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="blocks nothing"):
        config.load(write(tmp_path, "[profiles.empty]\n"))


def test_at_least_one_profile_is_required(tmp_path):
    with pytest.raises(ConfigError, match="no profiles"):
        config.load(write(tmp_path, "poll_interval = 2.0\n"))


def test_domains_must_be_a_list(tmp_path):
    body = '[profiles.focus]\ndomains = "example.com"\n'
    with pytest.raises(ConfigError, match="list of strings"):
        config.load(write(tmp_path, body))


def test_invalid_toml_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match="not valid TOML"):
        config.load(write(tmp_path, "profiles = [\n"))


def test_profile_lookup_lists_the_alternatives(tmp_path):
    loaded = config.load(write(tmp_path, MINIMAL))
    with pytest.raises(ConfigError, match="defined profiles: focus"):
        loaded.profile("missing")
