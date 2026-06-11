"""Tests for rossum_get_automation_insights / rossum_get_automation_projections.

Same harness idea as test_server_contract.py: drive the real handlers with the
HTTP boundary monkeypatched, capture what they emit via write_message. The
projections handler additionally pins the never-raise degradation contract
(non-200, malformed-200, empty-projections, network failure).
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error

import pytest
import repo_lib as R

sys.path.insert(0, str(R.SERVER_PY.parent))
import server  # noqa: E402  (path must be set up before import)

INSIGHTS_FIXTURE = {
    "document_automation_rate": 0.0,
    "document_touchless_rate": 0.438,
    "is_aurora_queue": True,
    "document_automation_timeseries": [
        {
            "date": "2026-03-12",
            "automated_count": 0,
            "non_automated_count": 40,
            "touchless_count": 16,
            "touched_count": 24,
        },
        {
            "date": "2026-03-13",
            "automated_count": 0,
            "non_automated_count": 33,
            "touchless_count": 16,
            "touched_count": 17,
        },
    ],
    "document_blockers": [
        {
            "blocker": "low_score",
            "granularity": "datapoint",
            "document_count": 72,
            "example_annotation_ids": list(range(1000, 1050)),
        },
        {
            "blocker": "automation_disabled",
            "granularity": "annotation",
            "document_count": 3,
            "example_annotation_ids": [2001, 2002, 2003],
        },
    ],
    "datapoint_statistics": [
        {
            "schema_id": "account_num",
            "blocked_document_counts": {"low_score": 50},
            "estimated_error_rate": None,
            "confidence_threshold": 0.95,
            "is_quality_estimate": False,
            "blockers": [
                {
                    "blocker": "low_score",
                    "granularity": "datapoint",
                    "document_count": 50,
                    "example_annotation_ids": list(range(3000, 3010)),
                }
            ],
        },
        {
            "schema_id": "iban",
            "blocked_document_counts": {"low_score": 60},
            "estimated_error_rate": None,
            "confidence_threshold": 0.9,
            "is_quality_estimate": False,
            "blockers": [
                {
                    "blocker": "low_score",
                    "granularity": "datapoint",
                    "document_count": 60,
                    "example_annotation_ids": [4000],
                }
            ],
        },
    ],
    "estimated_error_rate_timeseries": [],
}

PROJECTIONS_FIXTURE = {
    "total_document_count": 73,
    "used_document_count": 50,
    "baseline": {
        "document_automation_rate": 0.0,
        "estimated_error_rate": 0.0,
        "document_automation_timeseries": [],
        "document_blockers": [],
        "datapoint_statistics": [],
        "document_touchless_rate": 0.438,
        "estimated_error_rate_timeseries": [],
        "is_aurora_queue": True,
    },
    "projections": [
        {
            "document_automation_rate": 0.4737,
            "estimated_error_rate": 0.00627,
            "document_automation_timeseries": [],
            "document_blockers": [],
            "datapoint_statistics": [
                {
                    "schema_id": "account_num",
                    "blocked_document_counts": {"low_score": 12},
                    "estimated_error_rate": 0.0,
                    "confidence_threshold": 0.944,
                    "is_quality_estimate": False,
                    "blockers": [],
                },
            ],
            "document_touchless_rate": 0.438,
            "estimated_error_rate_timeseries": [],
            "is_aurora_queue": True,
        },
    ],
}


@pytest.fixture()
def captured_responses(monkeypatch):
    messages = []
    monkeypatch.setattr(server, "write_message", messages.append)
    return messages


@pytest.fixture()
def connected(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_cached_base_url", "https://example.rossum.app")
    monkeypatch.setattr(server, "_cached_token", "tok")
    monkeypatch.setattr(server, "_token_validated", True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _result_payload(messages):
    assert messages, "no response captured"
    content = messages[-1]["result"]["content"][0]["text"]
    return json.loads(content)


def test_insights_tool_is_registered_read_only():
    tool = server.TOOLS["rossum_get_automation_insights"]
    assert tool["annotations"] == {"readOnlyHint": True}
    assert "queue_id" in tool["inputSchema"]["required"]


def test_projections_tool_is_registered_read_only():
    tool = server.TOOLS["rossum_get_automation_projections"]
    assert tool["annotations"] == {"readOnlyHint": True}
    assert "queue_id" in tool["inputSchema"]["required"]


def test_insights_summary_digest_truncates_example_ids_and_caches_full_payload(
    monkeypatch, connected, captured_responses
):
    calls = []

    def fake_http(request_id, url, **kwargs):
        calls.append((url, kwargs))
        return json.loads(json.dumps(INSIGHTS_FIXTURE))

    monkeypatch.setattr(server, "_http_request", fake_http)
    server.HANDLERS["rossum_get_automation_insights"](1, {"queue_id": 123})

    url = calls[0][0]
    assert url == "https://example.rossum.app/api/v1/queues/123/automation_insights"

    digest = _result_payload(captured_responses)
    assert digest["document_automation_rate"] == 0.0
    assert digest["document_touchless_rate"] == 0.438
    assert digest["is_aurora_queue"] is True
    # Window summarized, not dumped
    assert digest["window"] == {
        "start": "2026-03-12",
        "end": "2026-03-13",
        "days": 2,
        "total_documents": 73,
        "automated_documents": 0,
        "touchless_documents": 32,
    }
    # Blockers keep counts but truncate the 50-ID example lists (keeping the head)
    low_score = next(b for b in digest["document_blockers"] if b["blocker"] == "low_score")
    assert low_score["document_count"] == 72
    assert low_score["example_annotation_ids"] == [1000, 1001, 1002, 1003, 1004]
    assert low_score["example_annotation_count"] == 50
    # Per-field stats survive compaction, sorted by total blocked descending
    assert [f["schema_id"] for f in digest["datapoint_statistics"]] == ["iban", "account_num"]
    field = digest["datapoint_statistics"][1]
    assert field["schema_id"] == "account_num"
    assert field["confidence_threshold"] == 0.95
    assert "example_annotation_ids" not in json.dumps(digest["datapoint_statistics"])
    # Full payload cached for the helper script
    cache = connected / ".rossum-cache" / "automation" / "queue_123_insights.json"
    assert json.loads(cache.read_text()) == INSIGHTS_FIXTURE
    assert digest["full_payload_cache"] == str(cache.relative_to(connected))


def test_insights_summary_false_returns_full_payload(
    monkeypatch, connected, captured_responses
):
    monkeypatch.setattr(server, "_http_request", lambda *a, **k: dict(INSIGHTS_FIXTURE))
    server.HANDLERS["rossum_get_automation_insights"](1, {"queue_id": 123, "summary": False})
    assert _result_payload(captured_responses) == INSIGHTS_FIXTURE


def test_insights_http_error_propagates_as_tool_error(
    monkeypatch, connected, captured_responses
):
    # _http_request returns None after sending the error itself — handler must stay silent
    monkeypatch.setattr(server, "_http_request", lambda *a, **k: None)
    server.HANDLERS["rossum_get_automation_insights"](1, {"queue_id": 123})
    assert captured_responses == []


def test_projections_posts_fields_and_summarizes(
    monkeypatch, connected, captured_responses
):
    calls = []

    def fake_status(url, *, method="GET", body=None):
        calls.append((url, method, body))
        return 200, json.loads(json.dumps(PROJECTIONS_FIXTURE))

    monkeypatch.setattr(server, "_http_request_status", fake_status)
    server.HANDLERS["rossum_get_automation_projections"](
        1, {"queue_id": 123, "fields": [{"schema_id": "account_num", "error_rate_limit": 0.01}]}
    )

    url, method, body = calls[0]
    assert url == "https://example.rossum.app/api/v1/queues/123/automation_projections"
    assert method == "POST"
    assert body == {"fields": [{"schema_id": "account_num", "error_rate_limit": 0.01}]}

    payload = _result_payload(captured_responses)
    assert payload["available"] is True
    assert payload["total_document_count"] == 73
    assert payload["used_document_count"] == 50
    assert payload["baseline"]["document_automation_rate"] == 0.0
    scenario = payload["projections"][0]
    assert scenario["document_automation_rate"] == 0.4737
    assert scenario["estimated_error_rate"] == 0.00627
    cache = connected / ".rossum-cache" / "automation" / "queue_123_projections.json"
    assert json.loads(cache.read_text()) == PROJECTIONS_FIXTURE
    assert payload["full_payload_cache"] == str(cache.relative_to(connected))


def test_projections_defaults_to_empty_fields_list(
    monkeypatch, connected, captured_responses
):
    calls = []

    def fake_status(url, *, method="GET", body=None):
        calls.append(body)
        return 200, json.loads(json.dumps(PROJECTIONS_FIXTURE))

    monkeypatch.setattr(server, "_http_request_status", fake_status)
    server.HANDLERS["rossum_get_automation_projections"](1, {"queue_id": 123})
    assert calls[0] == {"fields": []}


def test_projections_unavailable_returns_structured_response_not_error(
    monkeypatch, connected, captured_responses
):
    monkeypatch.setattr(
        server,
        "_http_request_status",
        lambda url, **k: (404, {"detail": "Not found."}),
    )
    server.HANDLERS["rossum_get_automation_projections"](1, {"queue_id": 123})
    payload = _result_payload(captured_responses)
    assert payload["available"] is False
    assert payload["status_code"] == 404
    assert "Not found." in json.dumps(payload["reason"])
    assert not captured_responses[-1]["result"].get("isError")


def test_projections_empty_scenarios_reported_unavailable(
    monkeypatch, connected, captured_responses
):
    # Live-observed mode: 200 with projections=[] when the queue lacks reviewed data
    empty = {
        "total_document_count": 2,
        "used_document_count": 0,
        "baseline": PROJECTIONS_FIXTURE["baseline"],
        "projections": [],
    }
    monkeypatch.setattr(server, "_http_request_status", lambda url, **k: (200, empty))
    server.HANDLERS["rossum_get_automation_projections"](1, {"queue_id": 123})
    payload = _result_payload(captured_responses)
    assert payload["available"] is False
    assert payload["status_code"] == 200
    assert payload["total_document_count"] == 2
    assert payload["used_document_count"] == 0
    assert "no projection scenarios" in payload["reason"]


def test_projections_network_failure_returns_structured_response(
    monkeypatch, connected, captured_responses
):
    monkeypatch.setattr(
        server, "_http_request_status", lambda url, **k: (None, "URLError: timed out")
    )
    server.HANDLERS["rossum_get_automation_projections"](1, {"queue_id": 123})
    payload = _result_payload(captured_responses)
    assert payload["available"] is False
    assert payload["status_code"] is None
    assert "timed out" in payload["reason"]


@pytest.mark.parametrize("body", [None, "a string", [1, 2], 42])
def test_projections_malformed_200_body_never_raises(
    monkeypatch, connected, captured_responses, body
):
    # A misbehaving proxy/gateway can answer 200 with non-dict JSON — the
    # never-raise contract must absorb it as a structured unavailable response.
    monkeypatch.setattr(server, "_http_request_status", lambda url, **k: (200, body))
    server.HANDLERS["rossum_get_automation_projections"](1, {"queue_id": 123})
    payload = _result_payload(captured_responses)
    assert payload["available"] is False
    assert payload["status_code"] == 200
    assert "malformed" in payload["reason"]
    assert not captured_responses[-1]["result"].get("isError")


def test_http_request_status_survives_undecodable_error_body(monkeypatch):
    def fake_urlopen(req, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.rossum.app/x", 502, "Bad Gateway", {},
            io.BytesIO(b"\xff\xfe broken \xff"),
        )

    monkeypatch.setattr(server, "_cached_token", "tok")
    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    status, body = server._http_request_status("https://example.rossum.app/x")
    assert status == 502
    assert isinstance(body, str)


def test_http_request_status_survives_read_failure_in_error_body(monkeypatch):
    class ExplodingFp:
        def read(self):
            raise ConnectionResetError("connection dropped mid-read")

        def close(self):
            pass

    def fake_urlopen(req, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.rossum.app/x", 500, "Server Error", {}, ExplodingFp()
        )

    monkeypatch.setattr(server, "_cached_token", "tok")
    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    status, body = server._http_request_status("https://example.rossum.app/x")
    assert status == 500
    assert isinstance(body, str)
