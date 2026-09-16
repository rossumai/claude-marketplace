"""Export a confirmed invoice into an ERP through a two-call HTTPS middleware - CREATE the
financial document, then ATTACH the original file - with the state that makes a retry safe.

Hook object requirements (JSON, not code):
  events      : ["annotation_content.export", "invocation.manual"]
  queues      : the entity queues  <- a hook with no queues never fires on an annotation event
  token_owner : <user>             <- rossum_authorization_token for state + write-back
  timeout_s   : 50; the middleware calls get REQUEST_TIMEOUT_S each
  payload_logging_enabled : false  <- the payload carries `secrets`
Settings : api_base_url, token_path, create_path, attach_path, invoice_status ("A" parks,
           "5" posts - never default to posting), posting_date_today (true), request_timeout_s
Secrets  : api_key, user_id, user_pw  (adapt `MiddlewareClient.authenticate`)

UNLIKE A MASTER-DATA IMPORTER, THIS WRITES FINANCIAL DOCUMENTS. A wasted import run costs
time; a mistaken retry here creates a DUPLICATE INVOICE. Three rules follow, all measured:

  * THE TRANSPORT STATUS IS NOT THE BUSINESS OUTCOME. A middleware wrapping an SAP BAPI answers
    HTTP 200 with its own success flag even when SAP rejected the document; the verdict is in
    the BAPIRET2 return table. Any row whose TYPE is A, E or X means the request failed.
  * A TIMEOUT LEAVES THE OUTCOME UNKNOWN. With no read-back endpoint, a timed-out create
    cannot be reconciled from here. The hook records `unknown` and REFUSES to retry
    automatically - a human checks the ERP first. This is a design constraint, not a gap.
  * STATE IS PERSISTED THE INSTANT THE CREATE SUCCEEDS, before the attachment is attempted,
    in its own Data Storage collection. Returning it as hook `operations` would lose it if
    the attachment crashed the run - and the next run would create a second invoice.

The attachment's external document id is derived from the annotation id, so a repeated
attachment is recognised by the ERP as a duplicate (already-exists flag) instead of creating a
second archive entry. Verified: re-attaching with the same id returned the same archive id.

Invocation payload flags:
  {"dry_run": true}   build and validate the payload from the annotation, send nothing
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

import requests

# ---- part parameters: schema ids ------------------------------------------------------
STATE_DATASET = "«state_dataset»"
HEADER_FIELDS = {                       # BAPI semantic -> schema id read from the annotation
    "reference_number": "«reference_field»",         # supplier's invoice number (REF_DOC_NO)
    "document_date": "«document_date_field»",
    "currency": "«currency_field»",
    "gross_amount": "«gross_amount_field»",
    "document_type": "«document_type_field»",        # invoice | credit_note
    "company_code": "«company_code_field»",
}
LINE_ITEMS_FIELD = "«line_items_field»"
LINE_FIELDS = {
    "po_number": "«line_po_number_field»",           # from MATCHING, never the page
    "po_item": "«line_po_item_field»",
    "amount": "«line_amount_field»",
    "quantity": "«line_quantity_field»",
    "unit": "«line_uom_field»",
    "text": "«line_text_field»",
    # receipt facts written by PO matching; only the guard below reads them
    "gr_required": "«line_gr_required_field»",
    "open_qty": "«line_open_qty_field»",
    "unit_price": "«line_unit_price_field»",
    "tol_pct": "«line_tolerance_pct_field»",
}
WRITEBACK_FIELDS = {                    # outcome written back onto the annotation
    "doc_number": "«doc_number_field»",
    "fiscal_year": "«fiscal_year_field»",
    "archive_id": "«archive_id_field»",
    "status": "«status_field»",
    "message": "«message_field»",
}

# ---- ADAPT: middleware response contract -----------------------------------------------
RESPONSE_ENVELOPE = "DATA"              # results sit under this key; "" = top level
RETURN_TABLE_KEY = "ET_RETURN"          # BAPIRET2 rows: TYPE, ID, NUMBER, MESSAGE
DOC_NUMBER_KEY, FISCAL_YEAR_KEY = "Invoice Document Num", "Fiscal Year"
ARCHIVE_ID_KEY, ALREADY_EXISTS_KEY = "Document identifier.", "Exist Ind"   # copied VERBATIM
FAILURE_TYPES = {"A", "E", "X"}         # BAPIRET2 severities that fail the request
PERIOD_ERROR_FRAGMENT = "posting only possible in periods"   # SAP message M8 535
ENFORCE_RECEIPT_COVERAGE = True
CREDIT_NOTE_TYPES = {"credit_note", "credit_memo"}

STATUS_CREATED, STATUS_ATTACHED = "created", "attached"
STATUS_FAILED, STATUS_UNKNOWN = "failed", "unknown"
STATE_ID = "annotation_id"


class ExportError(RuntimeError):
    """The export cannot proceed; the message is shown to the operator."""


class UnknownOutcome(ExportError):
    """The create call returned no verdict - ERP state is indeterminate."""


# ---- pure helpers ---------------------------------------------------------------------
def sap_date(value: Any) -> str:
    """YYYYMMDD from an ISO or common date string; '' when unparseable."""
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return ""


def pad(value: Any, width: int) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(width) if digits else ""


def to_number(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").replace(" ", "") or 0)
    except (ValueError, TypeError):
        return 0.0


def evaluate_return(et_return: Any) -> tuple[bool, str]:
    """Decide success from the BAPIRET2 table, never from the transport flag. Every row is
    rendered so warnings survive into the operator message."""
    rows = et_return if isinstance(et_return, list) else []
    rendered, failed = [], False
    for row in rows:
        if not isinstance(row, dict):
            continue
        severity = str(row.get("TYPE") or "").strip().upper()
        ident = f"{row.get('ID') or ''}/{row.get('NUMBER') or ''}".strip("/")
        rendered.append(f"[{severity or '?'}]{' ' + ident if ident else ''} "
                        f"{str(row.get('MESSAGE') or '').strip()}".strip())
        failed = failed or severity in FAILURE_TYPES
    return (not failed), "; ".join(rendered)


def posting_date_in_open_period(message: str, today: str) -> str:
    """When SAP names the open periods (M8 535: '... periods 2026/08 and 2026/09 ...'), pick a
    posting date inside them: today if its period is open, else the last day of the latest
    open period. '' when nothing parseable."""
    periods = sorted({(int(y), int(m)) for y, m in re.findall(r"(\d{4})/(\d{1,2})", message or "")})
    if not periods:
        return ""
    if (int(today[:4]), int(today[4:6])) in periods:
        return today
    year, month = periods[-1]
    nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
    last_day = (datetime(nxt_y, nxt_m, 1) - __import__("datetime").timedelta(days=1))
    return last_day.strftime("%Y%m%d")


def check_receipt_coverage(lines: list[dict]) -> None:
    """Refuse an invoice that bills more than has been received - only on lines whose PO
    line expects a goods receipt. open_qty is the GR-aware invoiceable quantity written by
    matching (delivered - invoiced); tol_pct is the PO line's own over-delivery tolerance,
    so this enforces exactly what the ERP would allow. Quantity first; a scanned invoice
    often has none, so fall back to value; if neither can be evaluated, refuse - an
    unverifiable line must not pass silently."""
    for index, line in enumerate(lines, start=1):
        if str(line.get("gr_required") or "").strip().upper() != "X":
            continue
        where = f"line {index} (PO {line.get('po_number')} item {line.get('po_item')})"
        open_qty = to_number(line.get("open_qty"))
        if open_qty <= 0:
            raise ExportError(f"{where}: nothing received that is not already invoiced")
        tolerance = 1 + to_number(line.get("tol_pct")) / 100
        quantity = to_number(line.get("quantity"))
        if quantity > 0:
            if quantity > open_qty * tolerance + 0.001:
                raise ExportError(f"{where}: invoiced quantity {quantity:g} exceeds the "
                                  f"{open_qty:g} received and not yet invoiced")
            continue
        unit_price = to_number(line.get("unit_price"))
        if unit_price <= 0:
            raise ExportError(f"{where}: no quantity on the invoice and no PO unit price, "
                              "so receipt coverage cannot be verified")
        if to_number(line.get("amount")) > open_qty * unit_price * tolerance + 0.01:
            raise ExportError(f"{where}: invoiced amount exceeds the value received and not "
                              f"yet invoiced ({open_qty:g} x {unit_price:g})")


# ---- ADAPT: payload builders (BAPI_INCOMINGINVOICE_CREATE semantics) -------------------------
def build_create_body(header: dict, lines: list[dict], invoice_status: str,
                      posting_date_today: bool) -> dict:
    """Header as an ARRAY OF EXACTLY ONE object plus items. Conventions that bit: dates are
    YYYYMMDD (the import APIs of the same integration used 14-digit stamps); flags are the
    strings "X" / ""; ids are strings with leading zeros kept; amounts are JSON numbers with
    a period separator; the invoice status has NO default and must be sent (RBKP RBSTAT:
    "A" parks, "5" posts); item amounts are in the HEADER currency. Rename the keys to your
    middleware's contract - copy them VERBATIM from a working call, the documented RFC
    parameter names (IV_*) were rejected by one deployment."""
    if not lines:
        raise ExportError("No invoice lines to export")
    if not invoice_status:
        raise ExportError("Invoice status has no default and must be set")
    is_credit = str(header.get("document_type") or "") in CREDIT_NOTE_TYPES
    document_date = sap_date(header.get("document_date"))
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    items = []
    for index, line in enumerate(lines, start=1):
        if not line.get("po_number") or not line.get("po_item"):
            # Extraction on a scanned invoice produces spurious rows (a stray total, a
            # netted discount). A line with no matched PO line is not exportable.
            raise ExportError(f"line {index} has no matched purchase-order line")
        items.append({
            "INVOICE_DOC_ITEM": pad(index, 6),
            "PO_NUMBER": str(line["po_number"]).strip(),
            "PO_ITEM": pad(line["po_item"], 5),
            "ITEM_AMOUNT": round(abs(to_number(line.get("amount"))), 2),
            "QUANTITY": round(abs(to_number(line.get("quantity"))), 3),
            "PO_UNIT": str(line.get("unit") or "").strip(),
            "ITEM_TEXT": str(line.get("text") or "")[:50],
        })
    gross = round(abs(to_number(header.get("gross_amount"))), 2)
    item_sum = round(sum(i["ITEM_AMOUNT"] for i in items), 2)
    if abs(gross - item_sum) > 0.01:
        print(f"[payload] WARNING gross {gross} != sum of items {item_sum}; SAP tolerance/tax "
              "config decides whether this posts")
    return {
        "HEADER": [{
            "INVOICE_IND": "" if is_credit else "X",      # initial = credit memo
            "DOC_DATE": document_date,
            "PSTNG_DATE": today if posting_date_today else document_date,  # must be an OPEN period
            "REF_DOC_NO": str(header.get("reference_number") or "")[:16],  # the dedup + reconciliation key
            "COMP_CODE": str(header.get("company_code") or ""),
            "CURRENCY": str(header.get("currency") or "").upper(),
            "GROSS_AMOUNT": gross,
            "RBSTAT": invoice_status,
        }],
        "ITEMS": items,
    }


def build_attach_body(doc_number: str, fiscal_year: str, annotation_id: int,
                      filename: str, pdf: bytes) -> dict:
    """The external document id MUST be stable per logical file so a retry is a recognised
    duplicate; the annotation id is stable by construction."""
    return {
        "Invoice number": doc_number, "Fiscal year": fiscal_year,
        "External document ID": str(annotation_id), "Filename": filename,
        "Description": f"Original supplier invoice ({filename})",
        "Encoded Contents": base64.b64encode(pdf).decode("ascii"),
    }


# ---- annotation content helpers ------------------------------------------------------
def _walk(nodes):
    for node in nodes or []:
        yield node
        yield from _walk(node.get("children") or [])


def extract(content: list, schema_id: str) -> Any:
    for node in _walk(content):
        if node.get("schema_id") == schema_id and "content" in node:
            return (node.get("content") or {}).get("value")
    return None


def datapoint_ids(content: list, schema_ids) -> dict:
    wanted, found = set(schema_ids), {}
    for node in _walk(content):
        if node.get("schema_id") in wanted and node.get("id") is not None:
            found.setdefault(node["schema_id"], node["id"])
    return found


def extract_lines(content: list) -> list[dict]:
    lines = []
    for node in _walk(content):
        if node.get("schema_id") == LINE_ITEMS_FIELD:
            for tuple_node in node.get("children") or []:
                values = {c.get("schema_id"): (c.get("content") or {}).get("value")
                          for c in tuple_node.get("children") or []}
                lines.append({key: values.get(sid) for key, sid in LINE_FIELDS.items()})
    return lines


# ---- middleware + state -----------------------------------------------------------------
class MiddlewareClient:
    def __init__(self, settings: dict, secrets: dict) -> None:
        self.base = str(settings["api_base_url"]).rstrip("/")
        self.token_path = settings.get("token_path", "/API/GetToken")
        self.timeout_s = int(settings.get("request_timeout_s", 30))
        self.secrets, self.session, self.token = secrets, requests.Session(), None

    def authenticate(self) -> str:
        r = self.session.post(f"{self.base}/{self.token_path.lstrip('/')}",
                              headers={"API_KEY": self.secrets["api_key"],
                                       "USER_ID": self.secrets["user_id"],
                                       "USER_PW": self.secrets["user_pw"],
                                       "Content-Type": "application/json"},
                              data="{}", timeout=self.timeout_s)
        body = r.json() if r.content else {}
        token = body.get("API_TOKEN") if isinstance(body, dict) else None
        if r.status_code != 200 or not token:
            raise ExportError(f"authentication failed: HTTP {r.status_code}")
        self.token = token
        return token

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        if self.token is None:
            self.authenticate()
        r = self.session.post(f"{self.base}/{path.lstrip('/')}",
                              headers={"API_KEY": self.secrets["api_key"],
                                       "API_TOKEN": self.token or "",
                                       "Content-Type": "application/json"},
                              json=body, timeout=self.timeout_s)
        try:
            parsed = r.json()
        except ValueError:
            parsed = {}
        return r.status_code, parsed if isinstance(parsed, dict) else {}


def _results(parsed: dict) -> dict:
    if RESPONSE_ENVELOPE and isinstance(parsed.get(RESPONSE_ENVELOPE), dict):
        return parsed[RESPONSE_ENVELOPE]
    return parsed


class StateStore:
    """Export state in its own Data Storage collection - the duplicate guard."""

    def __init__(self, payload: dict) -> None:
        self.base = payload["base_url"].rstrip("/")
        self.headers = {"Authorization": f"Bearer {payload['rossum_authorization_token']}"}

    def load(self, annotation_id: int) -> dict:
        r = requests.post(f"{self.base}/svc/data-storage/api/v1/data/aggregate",
                          headers={**self.headers, "Content-Type": "application/json"},
                          json={"collectionName": STATE_DATASET,
                                "pipeline": [{"$match": {STATE_ID: annotation_id}}]}, timeout=15)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        result = (r.json() or {}).get("result") or []
        return result[0] if result else {}

    def save(self, record: dict) -> None:
        record = {**record, "updated_at": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}
        data = [("dynamic", "true"), ("encoding", "UTF-8"), ("replace_or_new", "true"),
                ("id_keys", STATE_ID)]
        files = [("file", ("file", json.dumps([record]), "application/json"))]
        url = f"{self.base}/svc/master-data-hub/api/v1/dataset/{STATE_DATASET}"
        r = requests.request("PATCH", url, headers=self.headers, data=data, files=files, timeout=30)
        if r.status_code == 404:
            r = requests.request("POST", url, headers=self.headers, data=data, files=files, timeout=30)
        print(f"[state] {record.get('status')} annotation={record.get(STATE_ID)} "
              f"doc={record.get('doc_number') or '-'} -> {r.status_code}")
        r.raise_for_status()


# ---- handler ---------------------------------------------------------------------------
def rossum_hook_request_handler(payload: dict) -> dict:
    settings = payload["settings"]
    invoice_status = str(settings.get("invoice_status") or "A")     # park by default
    annotation = payload.get("annotation") or {}
    annotation_id = annotation.get("id") or payload.get("annotation_id")
    content = annotation.get("content") or []
    base = payload["base_url"].rstrip("/")
    auth = {"Authorization": f"Bearer {payload['rossum_authorization_token']}"}
    timeout_s = int(settings.get("request_timeout_s", 30))

    # A manual invoke carries no annotation body: fetch it, so the hook is drivable
    # per-annotation for testing without enabling the export event everywhere.
    if annotation_id and not content:
        annotation = requests.get(f"{base}/api/v1/annotations/{annotation_id}", headers=auth,
                                  timeout=timeout_s).json()
        content = requests.get(f"{base}/api/v1/annotations/{annotation_id}/content",
                               headers=auth, timeout=timeout_s).json().get("content") or []
        annotation["content"] = content

    def write_back(values: dict) -> list:
        """Record the outcome ON THE ANNOTATION, two ways on purpose: returned operations
        are the export event's native write-back, and a direct PATCH covers the paths that
        RAISE - a rejection never reaches a return statement, and that is exactly what a
        reviewer must see. Never raises: a write-back failure must not fail an invoice the
        ERP already holds."""
        ids = datapoint_ids(content, WRITEBACK_FIELDS.values())
        ops = []
        for key, value in values.items():
            dp_id = ids.get(WRITEBACK_FIELDS[key])
            if dp_id is None:
                continue
            op = {"op": "replace", "id": dp_id, "value": {"content": {"value": str(value or "")}}}
            ops.append(op)
            try:
                requests.patch(f"{base}/api/v1/annotations/{annotation_id}/content/{dp_id}",
                               json=op["value"], headers=auth, timeout=timeout_s)
            except Exception as exc:                          # noqa: BLE001
                print(f"[writeback] {dp_id} failed: {exc}")
        return ops

    header = {key: extract(content, sid) for key, sid in HEADER_FIELDS.items()}
    lines = extract_lines(content)
    if ENFORCE_RECEIPT_COVERAGE:
        check_receipt_coverage(lines)
    body = build_create_body(header, lines, invoice_status,
                             bool(settings.get("posting_date_today", True)))
    if payload.get("dry_run"):
        rendered = json.dumps(body, indent=2)
        print(rendered)
        return {"messages": [{"type": "info", "content": rendered[:4000]}], "operations": []}

    store, client = StateStore(payload), MiddlewareClient(settings, payload["secrets"])
    state = store.load(annotation_id)
    doc_number, fiscal_year = str(state.get("doc_number") or ""), str(state.get("fiscal_year") or "")
    reference = str(header.get("reference_number") or "")

    if state.get("status") == STATUS_UNKNOWN:
        raise ExportError(f"A previous export timed out and the ERP state is unknown. Check the "
                          f"ERP for reference {reference!r} before retrying; clear this "
                          "annotation's export state once resolved.")

    # ---- step 1: create, unless a previous run already did -----------------------------
    if not doc_number:
        def attempt_create():
            try:
                http_status, parsed = client.post(settings["create_path"], body)
            except requests.Timeout:
                store.save({STATE_ID: annotation_id, "status": STATUS_UNKNOWN,
                            "reference_number": reference,
                            "message": "create timed out - ERP outcome unknown, reconcile manually"})
                write_back({"status": STATUS_UNKNOWN, "message": "create timed out - reconcile in the ERP"})
                raise UnknownOutcome("Create timed out. The ERP may or may not hold the document; "
                                     "reconcile manually before any retry.")
            data = _results(parsed)
            ok, text = evaluate_return(data.get(RETURN_TABLE_KEY))
            return http_status, ok, text, str(data.get(DOC_NUMBER_KEY) or ""), str(data.get(FISCAL_YEAR_KEY) or "")

        status, ok, messages, doc_number, fiscal_year = attempt_create()
        if not ok and PERIOD_ERROR_FRAGMENT in messages.lower():
            # An M8/535 rejection created nothing, so one retry inside a period SAP just
            # named is safe and costs one call.
            retry_date = posting_date_in_open_period(messages, datetime.now(timezone.utc).strftime("%Y%m%d"))
            if retry_date and retry_date != body["HEADER"][0]["PSTNG_DATE"]:
                body["HEADER"][0]["PSTNG_DATE"] = retry_date
                status, ok, messages, doc_number, fiscal_year = attempt_create()
        if status != 200 or not ok or not doc_number:
            store.save({STATE_ID: annotation_id, "status": STATUS_FAILED,
                        "reference_number": reference, "message": messages or f"HTTP {status}"})
            write_back({"status": STATUS_FAILED, "message": (messages or f"HTTP {status}")[:200]})
            raise ExportError(f"The ERP rejected the invoice: {messages or f'HTTP {status}'}")
        # Persist BEFORE the attachment: if that step crashes the run, the next attempt
        # must not create a second invoice.
        store.save({STATE_ID: annotation_id, "status": STATUS_CREATED, "reference_number": reference,
                    "doc_number": doc_number, "fiscal_year": fiscal_year, "message": messages})

    # ---- step 2: attach the original file, unless already archived -----------------------
    archive_id = str(state.get("archive_id") or "")
    if not archive_id:
        document = annotation.get("document")
        if isinstance(document, str):        # sideloaded = object; fetched = URL
            document = requests.get(document, headers=auth, timeout=timeout_s).json()
        pdf = requests.get((document or {}).get("content"), headers=auth, timeout=timeout_s).content
        filename = (document or {}).get("original_file_name") or f"invoice-{doc_number}.pdf"
        status, parsed = client.post(settings["attach_path"],
                                     build_attach_body(doc_number, fiscal_year, annotation_id,
                                                       filename, pdf))
        data = _results(parsed)
        ok, messages = evaluate_return(data.get(RETURN_TABLE_KEY))
        archive_id = str(data.get(ARCHIVE_ID_KEY) or "")
        duplicate = str(data.get(ALREADY_EXISTS_KEY) or "") == "X"
        if status != 200 or not ok:
            store.save({STATE_ID: annotation_id, "status": STATUS_CREATED, "doc_number": doc_number,
                        "fiscal_year": fiscal_year, "message": f"attachment failed: {messages}"})
            write_back({"doc_number": doc_number, "fiscal_year": fiscal_year,
                        "status": STATUS_CREATED, "message": f"attachment failed: {messages}"[:200]})
            raise ExportError(f"Invoice {doc_number} was created but the attachment failed: "
                              f"{messages}. Retrying will not duplicate the invoice.")
        store.save({STATE_ID: annotation_id, "status": STATUS_ATTACHED, "doc_number": doc_number,
                    "fiscal_year": fiscal_year, "archive_id": archive_id,
                    "message": "duplicate attachment accepted" if duplicate else messages})

    summary = f"Exported: document {doc_number} / {fiscal_year}, archive {archive_id or '-'} (status {invoice_status})"
    print(summary)
    operations = write_back({"doc_number": doc_number, "fiscal_year": fiscal_year,
                             "archive_id": archive_id,
                             "status": STATUS_ATTACHED if archive_id else STATUS_CREATED,
                             "message": ""})
    return {"messages": [{"type": "info", "content": summary}], "operations": operations}
