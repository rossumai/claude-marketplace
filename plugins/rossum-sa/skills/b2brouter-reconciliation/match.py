"""Pure reconciliation logic: join one B2Brouter invoice to its Rossum
annotations and produce a verdict. No I/O, no HTTP, no client imports.

The classify() function returns one of these twelve note strings:
  - "ok": Single healthy annotation (imported, confirmed, exported, etc.)
  - "ok +xml_twin": Single healthy annotation plus a failed_import sibling
  - "DUPLICATE": Multiple healthy annotations for one invoice
  - "STRANDED_CREATED": Annotation present but stuck in created state
  - "FAILED_IMPORT": Annotation failed to import, no healthy sibling
  - "SPLIT_CONTAINER": Only a split annotation (container), no healthy sibling
  - "SPLIT_CONTAINER +xml_twin": Split container plus a failed_import sibling
  - "DELETED": Every annotation for this invoice is deleted/purged -- neutral,
    says nothing about why (see below)
  - "MISSING_IN_ROSSUM": No trace in Rossum, outside grace period
  - "PENDING": No trace yet, within grace period (recent invoice)
  - "UNVERIFIED_SOURCE": Source side incomplete, verdict untrustworthy
  - "UNKNOWN_STATUS:<status>": Annotation has unexpected status (development error)

A further note, "DELETED_AS_DUPLICATE", is never returned by classify()
itself -- classify() only sees one invoice's own annotations, and has no way
to know whether a *different* e-invoice id elsewhere in the organization
carries a surviving copy. "DELETED_AS_DUPLICATE" is EARNED downstream, in
recon.py, only after a Rossum search on the invoice NUMBER (which a survivor
carries regardless of which id or queue it landed under) actually confirms a
healthy annotation exists elsewhere -- see DELETED and DELETED_AS_DUPLICATE
below, and recon.py's `_verify_deleted_rows`. Until that search runs (or if
it can't -- no invoice number, a cap, a failed search), the row stays
DELETED: the word "duplicate" is never used on the strength of an assumption,
only on the strength of a confirmed second copy. Neither this tool nor its
report reasons about *why* an annotation was deleted -- these two notes are
distinguished purely by whether a surviving copy was found, nothing else.

A `split` annotation is a CONTAINER, not a processed document: the invoice was
divided into child annotations, which are the real documents. SPLIT_CONTAINER
exists to say that explicitly rather than let a lone `split` fall through to
FAILED_IMPORT -- that label is the operator's recovery instruction, and the
README tells a FAILED_IMPORT reader to re-drive the document. For a split
container that guidance is wrong twice over: the container must never be
re-imported (re-driving it risks double-posting the children, which may
already have arrived and processed perfectly well), and FAILED_IMPORT must
keep meaning what it says -- only returned when a `failed_import` annotation
actually exists.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

# Reached processing, however it ended up.
ARRIVED_STATUSES = frozenset({
    "importing", "to_review", "reviewing", "confirmed", "exporting", "exported",
    "failed_export", "postponed", "rejected",
})
# Present in Rossum but NOT successfully processed. `split` belongs here: a
# split annotation is a container that was divided into others, not a processed
# document, so it must never be read as "the invoice arrived".
NOT_ARRIVED_STATUSES = frozenset({
    "created", "failed_import", "deleted", "purged", "split",
})
ALL_STATUSES = tuple(sorted(ARRIVED_STATUSES | NOT_ARRIVED_STATUSES))


@dataclass(frozen=True)
class B2bInvoice:
    einvoice_id: str
    account_id: str
    number: str | None
    sender: str | None
    total: str | None
    currency: str | None
    state: str | None
    created_at: str
    ack_at: str | None


@dataclass(frozen=True)
class RossumAnn:
    annotation_id: int
    status: str
    filename: str
    einvoice_flag: bool
    # The annotation's own creation timestamp (ISO 8601, as Rossum returns
    # it). einvoice_index() deliberately indexes further back than the
    # reporting window (see rossum.py's INDEX_LOOKBACK), so this is what lets
    # a consumer tell an id that genuinely arrived IN the window apart from
    # one that only exists in the lookback tail -- see recon.py's
    # window-scoped unmatched-id count and per-account attribution check.
    created_at: str


CSV_COLUMNS = (
    "channel", "account", "einvoice_id", "filename", "arrived_at", "acked_at",
    "invoice_number", "sender", "total", "currency", "b2b_state",
    "annotation_status", "annotation_link", "note",
)

# `acked_at` markers -- see recon.py's exception-row backfill. The listing
# endpoint (received.json) returns `ack_at: null` for EVERY invoice
# regardless of the truth (only the per-invoice detail endpoint populates
# it), so a bare empty cell in this column is structurally ambiguous: it
# could mean "genuinely never acknowledged" or just "never looked up". These
# two markers make that distinction explicit instead of leaving both cases
# as the same blank string:
#   - NOT_CHECKED: this row's acked_at was never fetched from the detail
#     endpoint at all -- either because it is a clean row (recon.py only
#     backfills exception rows) or because the per-channel lookup cap was
#     reached before this row's turn. Draws NO conclusion about the real
#     acknowledgement state.
#   - LOOKUP_FAILED: a detail lookup WAS attempted for this row but did not
#     resolve (network failure, 404, or an unrecognised response shape).
# A genuinely empty acked_at cell is reserved for the one case where the
# detail lookup succeeded and the invoice really carries no acknowledgement
# timestamp -- see build_row below.
NOT_CHECKED = "not checked"
LOOKUP_FAILED = "lookup failed"

# Two flat, neutral labels for a row whose every annotation is
# deleted/purged -- named here, once, so classify() and recon.py's
# downstream verification (`_verify_deleted_rows`) both match these exact
# strings, never a substring/startswith test. Deliberately just two strings,
# no "+xml_twin" variant: the user does not want this bucket's note to carry
# any information beyond "was a surviving copy found or not" -- that other
# information (e.g. a failed_import sibling) is still visible in the
# `annotation_status` column, just not folded into this note.
#
#   - DELETED: what classify() itself always returns for this bucket. The
#     NEUTRAL default -- no cause implied, nothing asserted about why the
#     annotations were deleted. Also what an UNVERIFIED row keeps (no
#     invoice number to search by, the verification cap reached, or the
#     search itself failed) -- so DELETED on its own means "no surviving
#     copy was found", which for an unverified row means "we did not look",
#     not "nothing exists". See the README for this exact distinction.
#   - DELETED_AS_DUPLICATE: ONLY ever reached by promotion, in recon.py,
#     after a Rossum search actually confirms a healthy annotation exists
#     elsewhere for the same invoice number. The word "duplicate" is earned
#     here, never assumed.
#
# Both are exempt from the actionable/exit-code count (see
# recon.py's ACTIONABLE_EXEMPT_NOTES) -- neither implies an action is
# needed, by the user's explicit decision.
DELETED = "DELETED"
DELETED_AS_DUPLICATE = "DELETED_AS_DUPLICATE"


@dataclass(frozen=True)
class Row:
    channel: str
    account: str
    einvoice_id: str
    filename: str
    arrived_at: str
    acked_at: str
    invoice_number: str
    sender: str
    total: str
    currency: str
    b2b_state: str
    annotation_status: str
    annotation_link: str
    note: str

    def as_csv_dict(self) -> dict[str, str]:
        return asdict(self)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def classify(
    invoice: B2bInvoice,
    anns: Sequence[RossumAnn],
    *,
    now: datetime,
    grace_minutes: int,
    source_ok: bool,
) -> str:
    """Return the note for one invoice. The priority order is deliberate."""
    if not source_ok:
        # An incomplete left-hand side makes every other verdict untrustworthy.
        return "UNVERIFIED_SOURCE"

    unknown = [
        a.status for a in anns
        if a.status not in ARRIVED_STATUSES and a.status not in NOT_ARRIVED_STATUSES
    ]
    if unknown:
        return f"UNKNOWN_STATUS:{unknown[0]}"

    if not anns:
        if _parse_iso(invoice.created_at) > now - timedelta(minutes=grace_minutes):
            return "PENDING"
        return "MISSING_IN_ROSSUM"

    healthy = [a for a in anns if a.status in ARRIVED_STATUSES]
    has_failed_import = any(a.status == "failed_import" for a in anns)
    has_split = any(a.status == "split" for a in anns)
    twin = " +xml_twin" if has_failed_import else ""

    if len(healthy) > 1:
        return "DUPLICATE"
    if healthy:
        # A `created`, `failed_import` or `split` sibling beside a healthy
        # annotation is recovery debris (or the container of a sibling that
        # itself arrived healthy), not a loss.
        return "ok" + twin
    if has_split:
        # A container that was divided into children, not a processed
        # document -- and never a candidate for re-import. Checked before
        # DELETED/STRANDED_CREATED/FAILED_IMPORT so it gets its own note
        # instead of falling through to one of those, which would prescribe
        # re-driving the container.
        return "SPLIT_CONTAINER" + twin
    if any(a.status in {"deleted", "purged"} for a in anns):
        # Neutral by design -- no "+xml_twin" here, and no claim about WHY
        # this was deleted. recon.py's `_verify_deleted_rows` is the only
        # thing that may ever upgrade this to DELETED_AS_DUPLICATE, and only
        # once it has actually confirmed a surviving copy exists elsewhere.
        return DELETED
    if any(a.status == "created" for a in anns):
        return "STRANDED_CREATED"
    return "FAILED_IMPORT"


def build_row(
    channel_name: str,
    invoice: B2bInvoice,
    anns: Sequence[RossumAnn],
    *,
    now: datetime,
    grace_minutes: int,
    source_ok: bool,
    ui_host: str,
) -> Row:
    """Assemble one CSV row, linking the healthy annotation when there is one."""
    note = classify(invoice, anns, now=now, grace_minutes=grace_minutes, source_ok=source_ok)
    healthy = [a for a in anns if a.status in ARRIVED_STATUSES]
    primary = healthy[0] if healthy else (anns[0] if anns else None)
    return Row(
        channel=channel_name,
        account=invoice.account_id,
        einvoice_id=invoice.einvoice_id,
        filename=primary.filename if primary else f"einvoice{invoice.einvoice_id}.pdf",
        arrived_at=invoice.created_at,
        # The LIST endpoint's own `ack_at` is used if a caller ever supplies
        # one (it never does in production -- see b2brouter.py's module
        # docstring), but the common case is None, which becomes NOT_CHECKED
        # here rather than "": at build time no per-invoice detail lookup has
        # happened yet, so there is no basis for claiming "never
        # acknowledged". recon.py overwrites this for exception rows once it
        # has looked -- see its post-join backfill.
        acked_at=invoice.ack_at if invoice.ack_at else NOT_CHECKED,
        invoice_number=invoice.number or "",
        sender=invoice.sender or "",
        total=invoice.total or "",
        currency=invoice.currency or "",
        b2b_state=invoice.state or "",
        annotation_status="|".join(sorted(a.status for a in anns)),
        annotation_link=f"https://{ui_host}/document/{primary.annotation_id}" if primary else "",
        note=note,
    )


def unverified_row(channel_name: str, account_id: str, reason: str) -> Row:
    """A synthetic row for an account whose invoices could not be enumerated at all.

    Used when a whole account is uncovered, errors out, or is caught by the
    enumeration-contradiction check -- cases where NO B2Brouter invoice was
    ever listed for it, so there is nothing real to build a Row from, yet the
    account's incompleteness must still show up in the CSV: the file is what
    travels (gets emailed, filed, opened in a spreadsheet), and a reader of
    it never sees stderr or the process exit code.

    Every invoice/annotation field is left empty -- the point of this row is
    that the account's true state is UNKNOWN, not that it is empty or clean.
    `b2b_state` is repurposed to carry `reason` (e.g. the B2bError text, or
    "no API key can see it"): it is otherwise meaningless on a row with no
    underlying invoice, and it is the column a reader scanning the row will
    look to for "what happened here".
    """
    return Row(
        channel=channel_name,
        account=account_id,
        einvoice_id="",
        filename="",
        arrived_at="",
        # Every other field here is left "" because nothing was ever
        # fabricated to look like a real invoice existed. acked_at is the
        # one deliberate exception: an empty acked_at cell specifically means
        # "checked, and genuinely un-acknowledged" (see NOT_CHECKED's
        # docstring above), which would be actively wrong here -- there is no
        # invoice to have checked at all.
        acked_at=NOT_CHECKED,
        invoice_number="",
        sender="",
        total="",
        currency="",
        b2b_state=reason,
        annotation_status="",
        annotation_link="",
        note="UNVERIFIED_SOURCE",
    )


def enumeration_contradiction(listed_invoices: int, rossum_einvoice_ids: int) -> bool:
    """True when the source enumerated nothing but Rossum holds e-invoices from it.

    That cannot be reality: it means the invoice index is not returning what the
    key can actually see, so the run must be reported incomplete.
    """
    return listed_invoices == 0 and rossum_einvoice_ids > 0
