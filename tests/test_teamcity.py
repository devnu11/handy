from datetime import UTC, datetime, timedelta, timezone

import pytest

from handy.tools import _teamcity


def test_client_from_env_reads_environment(monkeypatch):
    monkeypatch.setenv("TEAMCITY_URL", "https://tc.example.com/")
    monkeypatch.setenv("TEAMCITY_TOKEN", "secret")
    client = _teamcity.client_from_env()
    assert client.base_url == "https://tc.example.com"  # trailing slash trimmed
    assert client.token == "secret"


def test_explicit_arguments_beat_the_environment(monkeypatch):
    monkeypatch.setenv("TEAMCITY_URL", "https://from-env")
    monkeypatch.setenv("TEAMCITY_TOKEN", "env-token")
    client = _teamcity.client_from_env("https://flag", "flag-token")
    assert (client.base_url, client.token) == ("https://flag", "flag-token")


@pytest.mark.parametrize("missing", ["TEAMCITY_URL", "TEAMCITY_TOKEN"])
def test_missing_credentials_are_reported(monkeypatch, missing):
    monkeypatch.setenv("TEAMCITY_URL", "https://tc.example.com")
    monkeypatch.setenv("TEAMCITY_TOKEN", "secret")
    monkeypatch.delenv(missing)
    with pytest.raises(_teamcity.TeamCityError, match=missing):
        _teamcity.client_from_env()


def test_parse_timestamp_reads_teamcitys_format():
    parsed = _teamcity.parse_timestamp("20130305T170030+0400")
    assert parsed == datetime(2013, 3, 5, 17, 0, 30, tzinfo=timezone(timedelta(hours=4)))


@pytest.mark.parametrize("value", [None, "", "2013-03-05T17:00:30Z", "junk"])
def test_parse_timestamp_returns_none_for_unusable_values(value):
    assert _teamcity.parse_timestamp(value) is None


def test_format_timestamp_round_trips():
    moment = datetime(2026, 11, 10, 8, 30, 0, tzinfo=UTC)
    text = _teamcity.format_timestamp(moment)
    assert text == "20261110T083000+0000"
    assert _teamcity.parse_timestamp(text) == moment


def test_format_timestamp_treats_naive_as_utc():
    assert _teamcity.format_timestamp(datetime(2026, 1, 2, 3, 4, 5)) == "20260102T030405+0000"


def test_decode_handles_empty_and_rejects_non_json():
    assert _teamcity._decode("u", b"") == {}
    assert _teamcity._decode("u", b'{"a": 1}') == {"a": 1}
    with pytest.raises(_teamcity.TeamCityError, match="non-JSON"):
        _teamcity._decode("u", b"<html>login</html>")
