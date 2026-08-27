from datetime import datetime, timedelta, timezone

import pytest

from match import B2bInvoice, RossumAnn, classify

NOW = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)


def inv(created="2026-01-18T19:00:00Z", eid="1001"):
    return B2bInvoice(
        einvoice_id=eid, account_id="900001", number="INV-1", sender="Example Supplier",
        total="2886.1", currency="EUR", state="new", created_at=created, ack_at=None,
    )


def ann(status, aid=1, name="einvoice1001.pdf", created="2026-01-18T19:30:00Z"):
    return RossumAnn(annotation_id=aid, status=status, filename=name, einvoice_flag=True,
                      created_at=created)


# classify() returns one of twelve fixed note strings (see match.py's module
# docstring). One representative case per verdict -- not the combinatorial
# input matrix (e.g. "ok" can be reached a dozen ways; only one is pinned
# here, chosen to also exercise the priority rule that makes it the verdict).
CASES = [
    pytest.param([ann("exported")], "ok", id="single-healthy"),
    pytest.param(
        [ann("exported"), ann("failed_import", aid=2, name="einvoice1001.xml")],
        "ok +xml_twin", id="healthy-plus-failed-import-twin",
    ),
    pytest.param(
        [ann("exported", aid=1), ann("exported", aid=2)], "DUPLICATE", id="two-healthy",
    ),
    pytest.param([ann("created")], "STRANDED_CREATED", id="created-alone"),
    pytest.param([ann("failed_import")], "FAILED_IMPORT", id="failed-import-alone"),
    pytest.param([ann("split")], "SPLIT_CONTAINER", id="split-alone"),
    pytest.param(
        [ann("split", aid=1), ann("failed_import", aid=2, name="einvoice1001.xml")],
        "SPLIT_CONTAINER +xml_twin", id="split-plus-failed-import-twin",
    ),
    pytest.param([ann("deleted")], "DELETED", id="deleted-alone"),
    pytest.param([], "MISSING_IN_ROSSUM", id="no-trace-outside-grace"),
    pytest.param([ann("teleported")], "UNKNOWN_STATUS:teleported", id="unmodelled-status"),
]


@pytest.mark.parametrize("anns, expected", CASES)
def test_classify_one_representative_per_verdict(anns, expected):
    assert classify(inv(), anns, now=NOW, grace_minutes=30, source_ok=True) == expected


def test_classify_pending_within_grace():
    recent = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert classify(inv(created=recent), [], now=NOW, grace_minutes=30, source_ok=True) == "PENDING"


def test_classify_unverified_source_outranks_every_other_verdict():
    # source_ok=False must win even over an otherwise-healthy annotation --
    # an incomplete left-hand side makes every other verdict untrustworthy.
    assert classify(inv(), [ann("exported")], now=NOW, grace_minutes=30, source_ok=False) == \
        "UNVERIFIED_SOURCE"
