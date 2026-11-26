import pytest

from complock import profiles
from complock.config import Profile
from complock.errors import ProfileError


def make(domains=(), processes=()):
    return Profile(name="test", domains=tuple(domains), processes=tuple(processes))


def test_every_bundled_list_loads_and_is_not_empty():
    for name in profiles.available_blocklists():
        assert profiles.load_blocklist(name), name


def test_bundled_lists_contain_no_comments_or_blanks():
    for name in profiles.available_blocklists():
        entries = profiles.load_blocklist(name)
        assert all(entry and not entry.startswith("#") for entry in entries), name


def test_unknown_blocklist_names_the_available_ones():
    with pytest.raises(ProfileError, match="bundled lists are"):
        profiles.load_blocklist("nonsense")


def test_list_reference_is_expanded():
    resolved = profiles.resolve(make(domains=["@social"]))
    assert "tiktok.com" in resolved.hostnames
    assert "instagram.com" in resolved.hostnames


def test_named_services_are_covered_by_the_social_list():
    resolved = profiles.resolve(make(domains=["@social"]))
    for host in ("tiktok.com", "instagram.com", "snapchat.com"):
        assert host in resolved.hostnames
        assert f"www.{host}" in resolved.hostnames


def test_youtube_streams_are_covered_by_the_video_list():
    resolved = profiles.resolve(make(domains=["@video"]))
    # Blocking the page without googlevideo.com leaves playback working.
    assert "youtube.com" in resolved.hostnames
    assert "googlevideo.com" in resolved.hostnames


def test_each_hostname_gains_a_www_alias():
    resolved = profiles.resolve(make(domains=["example.com"]))
    assert resolved.hostnames == ("example.com", "www.example.com")


def test_a_www_hostname_is_not_doubled():
    resolved = profiles.resolve(make(domains=["www.example.com"]))
    assert resolved.hostnames == ("www.example.com",)


def test_hostnames_are_lowercased_and_deduplicated():
    resolved = profiles.resolve(make(domains=["Example.COM", "example.com."]))
    assert resolved.hostnames == ("example.com", "www.example.com")


def test_hostnames_are_sorted_so_the_hosts_region_is_stable():
    resolved = profiles.resolve(make(domains=["b.com", "a.com"]))
    assert list(resolved.hostnames) == sorted(resolved.hostnames)


@pytest.mark.parametrize("bad", ["localhost", "not a host", "example..com", "-example.com", ""])
def test_entries_that_are_not_hostnames_are_rejected(bad):
    with pytest.raises(ProfileError, match="not a hostname"):
        profiles.resolve(make(domains=[bad]))


def test_the_error_says_which_blocklist_or_profile_is_at_fault():
    with pytest.raises(ProfileError, match="profile 'test'"):
        profiles.resolve(make(domains=["bogus"]))


def test_process_names_gain_an_exe_extension():
    resolved = profiles.resolve(make(processes=["Steam"]))
    assert resolved.processes == ("steam.exe",)


def test_process_names_with_an_extension_are_left_alone():
    resolved = profiles.resolve(make(processes=["RobloxPlayerBeta.exe"]))
    assert resolved.processes == ("robloxplayerbeta.exe",)


def test_a_dotted_name_still_gains_exe():
    # Bedrock Edition is Minecraft.Windows.exe. Treating any dot as "already
    # has an extension" produced minecraft.windows, which matches nothing.
    resolved = profiles.resolve(make(processes=["Minecraft.Windows"]))
    assert resolved.processes == ("minecraft.windows.exe",)


@pytest.mark.parametrize("name", ["Steam.exe", "thing.cmd", "thing.bat", "thing.com"])
def test_known_executable_suffixes_are_not_doubled(name):
    resolved = profiles.resolve(make(processes=[name]))
    assert resolved.processes == (name.casefold(),)


def test_the_two_spellings_of_a_process_collapse_to_one():
    resolved = profiles.resolve(make(processes=["Steam", "steam.exe"]))
    assert resolved.processes == ("steam.exe",)


@pytest.mark.parametrize("bad", [r"C:\Program Files\Steam\steam.exe", "steam/steam.exe"])
def test_a_path_instead_of_a_process_name_is_rejected(bad):
    with pytest.raises(ProfileError, match="executable name only"):
        profiles.resolve(make(processes=[bad]))
