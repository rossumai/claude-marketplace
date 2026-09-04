from datetime import datetime, timezone

import pytest

from match import ALL_STATUSES
from rossum import RossumClient, RossumError

SINCE = datetime(2026, 1, 18, 0, 0, tzinfo=timezone.utc)


# --- read-only guard: the only POST is the search endpoint -----------------
#
# Asserting the refusal MESSAGE (not just "no exception" or "a request was
# made") is the point: a test that merely calls _search and checks the
# result would still pass even with the guard deleted, since a stub searcher
# happily answers whatever it is asked. Only asserting the raised error
# actually bites.

def test_search_guard_refuses_any_path_other_than_annotations_search():
    client = RossumClient("tok", "https://rossum.invalid")
    with pytest.raises(RossumError, match="refusing to POST"):
        client._search("/api/v1/annotations", {"query": {}})


def test_search_guard_is_startswith_not_substring_containment():
    """A containment check would accept any URL that merely MENTIONS the
    search path -- including inside a query string -- which is a POST to a
    completely different endpoint."""
    client = RossumClient("tok", "https://rossum.invalid")
    with pytest.raises(RossumError, match="refusing to POST"):
        client._search("/api/v1/queues?next=/api/v1/annotations/search", {"query": {}})


# --- the two-query union ----------------------------------------------------
#
# einvoice_index() issues one search with no status clause and one with an
# explicit status.$in clause, then unions the results by annotation id. Both
# are load-bearing: the explicit clause returns rows the default search
# omits (measured live), while the clause-free query is the only way an
# annotation in a status this tool has never heard of can be seen at all --
# a status clause can only name statuses already known, so anything else
# would silently vanish rather than surface as UNKNOWN_STATUS.

def test_index_issues_one_query_without_a_status_clause_and_one_with_it():
    bodies = []

    def searcher(path, body):
        bodies.append(body)
        return {"pagination": {"next": None}, "results": [], "documents": []}

    client = RossumClient("tok", "https://rossum.invalid", searcher=searcher)
    client.einvoice_index([111], since=SINCE)

    assert len(bodies) == 2
    first, second = (body["query"]["$and"] for body in bodies)
    assert not [c for c in first if "status" in c]
    assert [c for c in second if "status" in c] == [{"status": {"$in": list(ALL_STATUSES)}}]


def test_an_unmodelled_status_reaches_the_index_via_the_clause_free_query():
    """An annotation in a status outside ARRIVED/NOT_ARRIVED_STATUSES is
    invisible to the status-clause query (which can only name what it
    already knows) but is still returned by the clause-free query -- so it
    still lands in the index instead of silently reading as "no annotation"
    or, beside a healthy sibling, as a false `ok`."""
    def searcher(path, body):
        has_status_clause = any("status" in c for c in body["query"]["$and"])
        if has_status_clause:
            return {"pagination": {"next": None}, "results": [], "documents": []}
        return {
            "pagination": {"next": None},
            "results": [{"id": 601, "status": "awaiting_approval",
                         "created_at": "2026-01-19T10:00:00Z", "einvoice": True,
                         "document": "https://rossum.invalid/api/v1/documents/1"}],
            "documents": [{"id": 1, "original_file_name": "einvoice2002.pdf"}],
        }

    client = RossumClient("tok", "https://rossum.invalid", searcher=searcher)
    index = client.einvoice_index([111], since=SINCE)

    assert [a.status for a in index["2002"]] == ["awaiting_approval"]


def test_the_two_queries_are_deduplicated_by_annotation_id():
    """The same annotation returned by BOTH queries must appear exactly once
    in the union, not twice."""
    def searcher(path, body):
        return {
            "pagination": {"next": None},
            "results": [{"id": 701, "status": "exported", "created_at": "2026-01-19T10:00:00Z",
                         "einvoice": True, "document": "https://rossum.invalid/api/v1/documents/7"}],
            "documents": [{"id": 7, "original_file_name": "einvoice3003.pdf"}],
        }

    client = RossumClient("tok", "https://rossum.invalid", searcher=searcher)
    index = client.einvoice_index([111], since=SINCE)

    assert [a.annotation_id for a in index["3003"]] == [701]
