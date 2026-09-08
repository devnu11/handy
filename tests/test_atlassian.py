import pytest

from handy.tools import _atlassian


def test_client_from_env_reads_environment(monkeypatch):
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com/")
    monkeypatch.setenv("JIRA_TOKEN", "secret")
    client = _atlassian.client_from_env("jira")
    assert client.base_url == "https://jira.example.com"  # trailing slash trimmed
    assert client.token == "secret"


def test_explicit_arguments_beat_the_environment(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_URL", "https://from-env")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "env-token")
    client = _atlassian.client_from_env("confluence", "https://flag", "flag-token")
    assert (client.base_url, client.token) == ("https://flag", "flag-token")


@pytest.mark.parametrize("missing", ["JIRA_URL", "JIRA_TOKEN"])
def test_missing_credentials_are_reported(monkeypatch, missing):
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "secret")
    monkeypatch.delenv(missing)
    with pytest.raises(_atlassian.AtlassianError, match="JIRA"):
        _atlassian.client_from_env("jira")


def test_encode_multipart_carries_fields_and_file():
    body, content_type = _atlassian.encode_multipart(
        {"minorEdit": "true"}, "chart.png", b"\x89PNG", "image/png"
    )
    boundary = content_type.split("boundary=")[1]
    assert body.count(f"--{boundary}".encode()) == 3  # two parts plus the terminator
    assert b'name="minorEdit"' in body
    assert b'filename="chart.png"' in body
    assert b"\x89PNG" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())
