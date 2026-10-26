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


# --- base URL --------------------------------------------------------------

def test_a_base_url_ending_in_api_v1_does_not_double_the_path():
    """Users copy `https://<org>.rossum.app/api/v1` from other Rossum
    tooling; every request path already starts with /api/v1."""
    urls = []
    client = RossumClient("tok", "https://rossum.invalid/api/v1/")
    client._request = lambda url, body, method: urls.append(url) or {}

    client.list_hooks()

    assert urls == ["https://rossum.invalid/api/v1/hooks?page_size=200"]


# --- survivor search by number and total ------------------------------------

def _store_searcher(annotations):
    """A fake search endpoint that answers $and of $eq/$in clauses from a
    store of annotations: {"id", "status", "field.<id>.<type>": value, ...}."""
    def matches(annotation, clause):
        (key, condition), = clause.items()
        if "$eq" in condition:
            return annotation.get(key) == condition["$eq"]
        return annotation.get(key) in condition["$in"]

    def searcher(path, body):
        clauses = body["query"]["$and"]
        return {"pagination": {"next": None},
                "results": [a for a in annotations if all(matches(a, c) for c in clauses)]}
    return searcher


def test_a_survivor_must_carry_the_same_total_when_a_total_field_is_given():
    store = [{"id": 5, "status": "exported",
              "field.document_id.string": "INV-7", "field.amount_total.number": 99.0}]
    client = RossumClient("tok", "https://rossum.invalid", searcher=_store_searcher(store))

    assert client.surviving_annotation_id("INV-7") == 5
    assert client.surviving_annotation_id("INV-7", total="99.00", total_field="amount_total") == 5
    assert client.surviving_annotation_id("INV-7", total="10.0", total_field="amount_total") is None


def test_schema_datapoints_reads_nested_fields_of_the_queue_schemas():
    payloads = {
        "/api/v1/queues/111": {"schema": "https://rossum.invalid/api/v1/schemas/9"},
        "https://rossum.invalid/api/v1/schemas/9": {"content": [{
            "category": "section", "id": "header", "children": [
                {"category": "datapoint", "id": "document_id", "type": "string"},
                {"category": "multivalue", "id": "items", "children": {
                    "category": "tuple", "id": "item", "children": [
                        {"category": "datapoint", "id": "item_total", "type": "number"},
                    ],
                }},
            ],
        }]},
    }
    client = RossumClient("tok", "https://rossum.invalid", transport=payloads.__getitem__)

    assert client.schema_datapoints([111]) == {"document_id": "string", "item_total": "number"}
