import argparse

import pytest

from handy.tools import confluence_block as block

BODY = "<p>Intro</p><!-- handy:perf --><p>old</p><!-- /handy:perf --><p>Outro</p>"


class FakeConfluence:
    def __init__(self, body=BODY):
        self.page = {
            "id": "123",
            "type": "page",
            "title": "Weekly",
            "version": {"number": 7},
            "body": {"storage": {"value": body}},
        }
        self.puts = []

    def get(self, path, params=None):
        return self.page

    def put(self, path, payload):
        self.puts.append(payload)
        return {}


def test_replace_swaps_only_the_block():
    updated = block.replace_block(BODY, "perf", "<p>new</p>")
    assert "<p>new</p>" in updated
    assert "<p>old</p>" not in updated
    assert updated.startswith("<p>Intro</p>")
    assert updated.endswith("<p>Outro</p>")


def test_replace_keeps_other_blocks_intact():
    body = BODY + "<!-- handy:conformance --><p>keep</p><!-- /handy:conformance -->"
    updated = block.replace_block(body, "perf", "<p>new</p>")
    assert "<!-- handy:conformance --><p>keep</p>" in updated


def test_missing_markers_are_an_error_not_a_silent_append():
    with pytest.raises(block.BlockMissingError, match="--init"):
        block.replace_block("<p>Just prose</p>", "perf", "<p>new</p>")


def test_init_appends_a_fresh_block():
    updated = block.replace_block("<p>Just prose</p>", "perf", "<p>new</p>", init=True)
    assert updated.startswith("<p>Just prose</p>")
    assert "<!-- handy:perf -->" in updated
    assert "<!-- /handy:perf -->" in updated


def test_a_second_run_reuses_the_block_it_created():
    once = block.replace_block("<p>Prose</p>", "perf", "<p>one</p>", init=True)
    twice = block.replace_block(once, "perf", "<p>two</p>")
    assert twice.count("<!-- handy:perf -->") == 1
    assert "<p>one</p>" not in twice


def test_image_markup_references_the_attachment():
    markup = block.image_markup("perf.png", "Run 4821")
    assert 'ri:filename="perf.png"' in markup
    assert "Run 4821" in markup


def test_image_markup_without_a_caption_has_no_empty_paragraph():
    assert "<em>" not in block.image_markup("perf.png")


def test_update_block_bumps_the_page_version():
    client = FakeConfluence()
    assert block.update_block(client, "123", "perf", "<p>new</p>") == 8
    payload = client.puts[0]
    assert payload["version"]["number"] == 8
    assert "<p>new</p>" in payload["body"]["storage"]["value"]


def test_dry_run_writes_nothing(tmp_path, capsys):
    image = tmp_path / "perf.png"
    image.write_bytes(b"png")
    args = argparse.Namespace(
        page_id="123",
        name="perf",
        image=image,
        markup=None,
        caption="",
        init=False,
        url=None,
        token=None,
        dry_run=True,
        timeout=5.0,
    )
    assert block.run(args) == 0
    assert "would write into <!-- handy:perf -->" in capsys.readouterr().out
