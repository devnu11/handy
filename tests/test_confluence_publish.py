import pytest

from handy.tools import confluence_publish as publish


class FakeConfluence:
    def __init__(self, attachments=(), page=None):
        self.attachments = list(attachments)
        self.page = page or {
            "id": "123",
            "type": "page",
            "title": "Weekly report",
            "version": {"number": 4},
            "body": {"storage": {"value": "<p>Intro</p>"}},
        }
        self.uploads = []
        self.puts = []

    def get(self, path, params=None):
        if path.endswith("/child/attachment"):
            wanted = (params or {}).get("filename")
            return {"results": [a for a in self.attachments if a["title"] == wanted]}
        return self.page

    def upload(self, path, filename, content, content_type=None, fields=None):
        self.uploads.append((path, filename, content, content_type, fields))
        return {"results": [{"id": "att-new"}]}

    def put(self, path, payload):
        self.puts.append((path, payload))
        return {}


def test_first_upload_creates_a_new_attachment():
    client = FakeConfluence()
    attachment_id, replaced = publish.upload_attachment(client, "123", "chart.png", b"png")
    assert (attachment_id, replaced) == ("att-new", False)
    path, filename, content, content_type, fields = client.uploads[0]
    assert path == "/rest/api/content/123/child/attachment"
    assert (filename, content, content_type) == ("chart.png", b"png", "image/png")
    assert fields["minorEdit"] == "true"


def test_reupload_replaces_the_existing_attachment_data():
    client = FakeConfluence(attachments=[{"id": "att-7", "title": "chart.png"}])
    attachment_id, replaced = publish.upload_attachment(client, "123", "chart.png", b"png")
    assert replaced is True
    assert attachment_id == "att-new"
    assert client.uploads[0][0] == "/rest/api/content/123/child/attachment/att-7/data"


def test_embed_is_appended_once_and_bumps_the_page_version():
    client = FakeConfluence()
    assert publish.ensure_embedded(client, "123", "chart.png") is True
    _, payload = client.puts[0]
    assert payload["version"]["number"] == 5
    assert payload["body"]["storage"]["value"].startswith("<p>Intro</p>")
    assert 'ri:filename="chart.png"' in payload["body"]["storage"]["value"]


def test_embed_is_a_noop_when_the_page_already_shows_the_image():
    client = FakeConfluence()
    client.page["body"]["storage"]["value"] = publish.embed_markup("chart.png")
    assert publish.ensure_embedded(client, "123", "chart.png") is False
    assert client.puts == []


def test_dry_run_touches_nothing(tmp_path, capsys):
    image = tmp_path / "chart.png"
    image.write_bytes(b"png")
    args = _args(image=image, dry_run=True)
    assert publish.run(args) == 0
    assert "would upload" in capsys.readouterr().out


def test_missing_file_is_an_error(tmp_path, capsys):
    assert publish.run(_args(image=tmp_path / "gone.png")) == 1
    assert "no such file" in capsys.readouterr().out


def _args(**overrides):
    import argparse

    defaults = {
        "image": None,
        "page_id": "123",
        "name": None,
        "url": None,
        "token": None,
        "comment": "c",
        "embed": True,
        "dry_run": False,
        "timeout": 5.0,
    }
    return argparse.Namespace(**{**defaults, **overrides})


@pytest.mark.parametrize("filename", ["chart.png", "weekly report.png"])
def test_embed_markup_references_the_attachment(filename):
    assert f'ri:filename="{filename}"' in publish.embed_markup(filename)
