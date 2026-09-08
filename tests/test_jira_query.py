from handy.tools import jira_query


class FakeJira:
    """Serves canned search pages and records the params it was asked for."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return self.pages.pop(0) if self.pages else {"issues": [], "total": 0}


def issue(key, **fields):
    return {"key": key, "fields": fields}


def test_normalize_flattens_jiras_nesting():
    normalized = jira_query.normalize_issue(
        issue(
            "FOO-1",
            summary="Broken thing",
            status={"name": "Open"},
            issuetype={"name": "Bug"},
            priority={"name": "High"},
            assignee={"displayName": "Ada"},
            components=[{"name": "api"}, {"name": "web"}],
            created="2024-01-15T10:23:45.000+0000",
            resolutiondate=None,
        )
    )
    assert normalized == {
        "key": "FOO-1",
        "summary": "Broken thing",
        "status": "Open",
        "type": "Bug",
        "priority": "High",
        "assignee": "Ada",
        "components": ["api", "web"],
        "created": "2024-01-15T10:23:45.000+0000",
        "resolved": None,
    }


def test_normalize_tolerates_missing_fields():
    normalized = jira_query.normalize_issue({"key": "FOO-2", "fields": {}})
    assert normalized["status"] == "Unknown"
    assert normalized["priority"] is None
    assert normalized["components"] == []


def test_search_pages_until_the_total_is_reached():
    client = FakeJira(
        [
            {"issues": [issue("FOO-1"), issue("FOO-2")], "total": 3},
            {"issues": [issue("FOO-3")], "total": 3},
        ]
    )
    issues = jira_query.search_issues(client, "project = FOO", page_size=2)
    assert [item["key"] for item in issues] == ["FOO-1", "FOO-2", "FOO-3"]
    assert [call[1]["startAt"] for call in client.calls] == [0, 2]


def test_search_respects_the_limit():
    client = FakeJira([{"issues": [issue("FOO-1"), issue("FOO-2")], "total": 99}])
    issues = jira_query.search_issues(client, "project = FOO", limit=2, page_size=50)
    assert len(issues) == 2
    assert client.calls[0][1]["maxResults"] == 2


def test_search_stops_on_an_empty_page():
    client = FakeJira([{"issues": [], "total": 99}])
    assert jira_query.search_issues(client, "project = FOO") == []


def test_document_wraps_the_issues():
    document = jira_query.build_document("project = FOO", [{"key": "FOO-1"}])
    assert document["jql"] == "project = FOO"
    assert document["count"] == 1
    assert document["fetched_at"]
