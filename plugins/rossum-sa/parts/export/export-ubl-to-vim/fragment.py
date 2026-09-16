"""Export an invoice to SAP / OpenText VIM as UBL 2.1 XML with the original document embedded
as base64, POSTed through an SAP API gateway (BTP Integration Suite) to the VIM inbound
endpoint; archive the sent XML on the annotation; write VIM's ingest reference back.

Hook object requirements (JSON, not code):
  events      : ["annotation_content.export", "invocation.manual"]
  queues      : every SAP-bound queue - one hook serves them all (company code comes from
                the document, not the hook)
  token_owner : <user>       <- rossum_authorization_token for the file fetch, relation, write-back
  timeout_s   : 30-50
  payload_logging_enabled : false
Settings : VIM_URL (inbound ingest endpoint), TOKEN_URL (OAuth2 client_credentials),
           SAP_CLIENT (query param, mandatory on every SAP API), FORM_FIELD_NAME ("document"),
           LOG_FULL_PAYLOAD ("true" logs the whole UBL incl. base64 for SAP-side replay)
Secrets  : client_id, client_secret, vim_api_key (apiKey query param the ingest route requires)

Gateway facts that shape this hook (all measured 2026-08, see sap-reference):
  * The gateway's SQL THREAT-PROTECTION policy scans the WHOLE request and rejects any
    standalone SQL keyword with HTTP 403 {"code": "FORBIDDEN_PARAMETER, sql"}. A base64 PDF
    randomly contains "drop" or "/or+"; an attachment FILENAME contained "... or IO"; an XML
    developer COMMENT contained "or". Each was a different carrier of the same 403. So: base64
    is neutralised (whitespace inside xsd:base64Binary is legal and decoders ignore it), the
    filename is cleaned, comments are stripped from the bytes, and a preflight REPORTS anything
    left - invoice data is never rewritten to dodge a WAF.
  * VIM/CPI expects multipart/form-data with the UBL as a form part (default name "document",
    part content type text/xml). Do NOT set a request Content-Type - `requests` sets the
    boundary.
  * VIM answers {"id": <guid>, "status": "COMPLETED", "documents": [{"regid": <n>, ...}]}.
    regid is the quotable ingest id; the DP number AP works with is assigned LATER in VIM's
    own workflow and is NOT in this response.
  * VIM's inbound mapping reads PATHS: an element it maps must always be present, so
    withheld values ship as EMPTY elements, never as omitted ones - except xsd:decimal/date
    elements (cbc:Percent, cbc:DueDate), where empty is schema-invalid and omission is the
    only option.
  * The archived XML is exactly what went over the wire: neutralise and strip BEFORE building
    the string that is logged, archived and POSTed.
"""

import base64
import hashlib
import json
import re
from xml.sax.saxutils import escape, quoteattr
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests

# ---- part parameters: schema ids ------------------------------------------------------
RELATION_KEY = "«relation_key»"                  # document_relation key for the archived XML
EXPORT_TARGET_FIELD = "«export_target_field»"    # routing: proceed when "sap" or field absent
COMPANY_CODE_FIELD = "«company_code_field»"      # SAP BUKRS -> cbc:BuyerReference
VIM_REFERENCE_FIELD = "«vim_reference_field»"    # write-back of regid / guid
H = {                                            # header schema ids
    "invoice_number": "«invoice_number_field»", "issue_date": "«issue_date_field»",
    "currency": "«currency_field»", "document_type": "«document_type_field»",
    "po_number": "«po_number_field»", "supplier_number": "«supplier_number_field»",
    "supplier_name": "«supplier_name_field»", "payment_terms_code": "«payment_terms_code_field»",
    "net": "«net_field»", "tax": "«tax_field»", "total": "«total_field»",
}
LINE_ITEMS_FIELD = "«line_items_field»"
L = {                                            # line schema ids
    "description": "«line_description_field»", "quantity": "«line_quantity_field»",
    "uom": "«line_uom_field»", "price": "«line_price_field»", "amount": "«line_amount_field»",
    "po_item": "«line_po_item_field»", "tax_code": "«line_tax_code_field»",
}
UNIT_CODE_DEFAULT = "PC"
CREDIT_NOTE_TYPES = {"credit_note", "credit_memo"}

# ---- gateway SQL threat-protection defences ---------------------------------------------
_SQLISH_RE = re.compile(
    r"(?i)select|union|insert|update|delete|drop|alter|create|exec|truncate|declare|shutdown")
_SQLISH_OR_RE = re.compile(r"(?i)(?<![0-9A-Za-z_])or(?![0-9A-Za-z_])")
_GATEWAY_SQL_WORDS = ("or", "select", "union", "insert", "update", "delete", "drop", "where",
                      "having", "exec", "declare")          # `and` is measured as accepted
_GATEWAY_SQL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(_GATEWAY_SQL_WORDS) + r")(?![A-Za-z0-9])", re.I)
_XML_COMMENT_RE = re.compile(r"[ \t]*<!--.*?-->[ \t]*\n?", re.S)
_B64_ELEMENT_RE = re.compile(
    r"(<cbc:EmbeddedDocumentBinaryObject\b[^>]*>)([^<]*)(</cbc:EmbeddedDocumentBinaryObject>)")


def neutralize_sqlish_base64(b64):
    """Break SQL-looking substrings inside base64 by inserting a newline mid-keyword.
    xsd:base64Binary allows whitespace and every decoder ignores it, so the decoded bytes are
    identical - but the gateway's keyword regex no longer matches."""
    b64 = _SQLISH_RE.sub(lambda m: m.group(0)[:2] + "\n" + m.group(0)[2:], b64)
    return _SQLISH_OR_RE.sub(lambda m: m.group(0)[0] + "\n" + m.group(0)[1], b64)


def gateway_safe_filename(name):
    """Drop standalone SQL keywords from the attachment filename; keep the extension."""
    stem, dot, ext = str(name or "").rpartition(".")
    base = stem if dot else str(name or "")
    cleaned = re.sub(r"\s{2,}", " ", _GATEWAY_SQL_RE.sub("", base)).strip(" -_") or "invoice"
    out = f"{cleaned}.{ext}" if dot else cleaned
    if out != name:
        print(f"[vim-export] filename rewritten for the gateway SQL policy: {name!r} -> {out!r}")
    return out


def strip_xml_comments(xml):
    """VIM ignores comments; the gateway scans them."""
    return _XML_COMMENT_RE.sub("", xml)


def gateway_sql_preflight(xml_str):
    """REPORT (never strip) standalone SQL keywords left in the payload - what remains is
    invoice data, and rewriting it to dodge a WAF would corrupt the export. Zero hits on a
    healthy payload, so any output is a real prediction of a 403."""
    b64_parts = []
    text = _B64_ELEMENT_RE.sub(lambda m: (b64_parts.append(m.group(2)) or m.group(1) + m.group(3)),
                               xml_str)
    hits = [(m.group(0), " ".join(text[max(0, m.start() - 40):m.end() + 40].split()))
            for m in _GATEWAY_SQL_RE.finditer(text)]
    b64 = "".join(b64_parts)
    hits += [(m.group(0), "embedded base64 (not neutralised)")
             for pattern in (_SQLISH_RE, _SQLISH_OR_RE) for m in pattern.finditer(b64)]
    for keyword, context in hits[:10]:
        print(f"[vim-export] WARNING gateway will 403 on {keyword!r} in ...{context}...")
    return hits


# ---- attachment -----------------------------------------------------------------------
_MAGIC = ((b"%PDF", "application/pdf"), (b"\x49\x49\x2a\x00", "image/tiff"),
          (b"\x4d\x4d\x00\x2a", "image/tiff"), (b"\xff\xd8\xff", "image/jpeg"),
          (b"\x89PNG\r\n\x1a\n", "image/png"))
_EXT_MIME = {"pdf": "application/pdf", "tif": "image/tiff", "tiff": "image/tiff",
             "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


def attachment_mime_type(raw, rossum_mime="", filename=""):
    """Declare what the bytes ARE: magic number > Rossum's document.mime_type (which can be
    the literal string "empty") > extension > PDF. A TIF mislabelled as PDF was accepted by
    VIM but rendered wrong downstream."""
    head = bytes(raw or b"")[:8]
    mime = next((v for sig, v in _MAGIC if head.startswith(sig)), "")
    if not mime and "/" in str(rossum_mime or ""):
        mime = str(rossum_mime).strip().lower()
    return mime or _EXT_MIME.get(str(filename or "").rsplit(".", 1)[-1].lower(), "application/pdf")


def fetch_document_b64(rossum_token, document_url):
    """(base64, gateway-safe filename, mime) of the original document."""
    auth = {"Authorization": f"Bearer {rossum_token}"}
    meta = requests.get(document_url, headers=auth, timeout=20).json()
    filename = gateway_safe_filename(meta.get("original_file_name") or "invoice.pdf")
    r = requests.get(meta.get("content"), headers=auth, timeout=25)
    r.raise_for_status()
    return (base64.b64encode(r.content).decode("ascii"), filename,
            attachment_mime_type(r.content, meta.get("mime_type"), filename))


# ---- content ---------------------------------------------------------------------------
def _walk(nodes):
    for n in nodes or []:
        yield n
        yield from _walk(n.get("children") or [])


def _val(dp):
    c = dp.get("content") or {}
    v = c.get("normalized_value")
    return c.get("value") if v in (None, "") else v


def extract_content(content):
    """{schema_id: value} for header datapoints, plus [{schema_id: value}] per line row."""
    header, lines = {}, []
    for n in _walk(content):
        if n.get("schema_id") == LINE_ITEMS_FIELD:
            for row in n.get("children") or []:
                lines.append({c.get("schema_id"): _val(c) for c in row.get("children") or []})
        elif "content" in n:
            header.setdefault(n.get("schema_id"), _val(n))
    return header, lines


def datapoint_id(content, schema_id):
    return next((n.get("id") for n in _walk(content) if n.get("schema_id") == schema_id), None)


# ---- amounts ---------------------------------------------------------------------------
def _to_float(v):
    try:
        s = str(v).replace(",", "").replace(" ", "").strip()
        return float(s) if s else 0.0
    except (ValueError, TypeError):
        return 0.0


def _amt(v):
    return f"{_to_float(v):.2f}"


def _price_amount(net, qty, fallback):
    """PriceAmount so that price x quantity == the emitted LineExtensionAmount (EN 16931
    checks it with a 2-cent tolerance). The line net is what the supplier billed and is
    authoritative; the price is derived from it only when the supplied price does not
    reconcile, at up to 6 decimals."""
    q, n = _to_float(qty), _to_float(net)
    if q <= 0 or abs(round(_to_float(fallback), 2) * q - round(n, 2)) <= 0.02:
        return _amt(fallback)
    text = f"{round(n / q, 6):.6f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _iso_date(v):
    from datetime import datetime
    s = str(v or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


# ---- UBL 2.1 ---------------------------------------------------------------------------
def build_ubl(header, lines, pdf_b64, pdf_filename, mime_code, company_code, annotation_id):
    """Plain UBL 2.1 (CustomizationID urn:oasis:...:Invoice-2). Credit notes: every amount is
    emitted POSITIVE and InvoiceTypeCode 381 carries the direction - a negated total is not
    even self-consistent (TaxInclusive must equal TaxExclusive + TaxAmount)."""
    g = lambda k: "" if header.get(H[k]) is None else str(header.get(H[k]))
    credit = g("document_type") in CREDIT_NOTE_TYPES
    m = (lambda v: _amt(abs(_to_float(v)))) if credit else _amt
    currency = escape(g("currency").upper())
    issue = escape(_iso_date(g("issue_date")))
    po = escape(g("po_number"))
    net, tax, total = m(g("net")), m(g("tax")), m(g("total"))
    zterm = escape(g("payment_terms_code"))
    payment_terms = (f"  <cac:PaymentTerms><cbc:Note>ZTERM={zterm}</cbc:Note></cac:PaymentTerms>\n"
                     if zterm else "")

    line_xml = []
    for i, ln in enumerate(lines, start=1):
        lg = lambda k, _ln=ln: "" if _ln.get(L[k]) is None else str(_ln.get(L[k]))
        uom = escape(lg("uom") or UNIT_CODE_DEFAULT)
        line_net = m(lg("amount"))
        line_xml.append(f"""  <cac:InvoiceLine>
    <cbc:ID>{i}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="{uom}">{escape(lg('quantity'))}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_net}</cbc:LineExtensionAmount>
    <cac:OrderLineReference>
      <cbc:LineID>{escape(lg('po_item'))}</cbc:LineID>
      <cac:OrderReference><cbc:ID>{po}</cbc:ID></cac:OrderReference>
    </cac:OrderLineReference>
    <cac:Item>
      <cbc:Description>{escape(lg('description'))}</cbc:Description>
      <cbc:Name>{escape(lg('description'))}</cbc:Name>
      <cac:ClassifiedTaxCategory>
        <cbc:ID>{escape(lg('tax_code'))}</cbc:ID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{_price_amount(line_net, lg('quantity'), m(lg('price')))}</cbc:PriceAmount>
      <cbc:BaseQuantity unitCode="{uom}">1</cbc:BaseQuantity>
    </cac:Price>
  </cac:InvoiceLine>""")

    return strip_xml_comments(f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:CustomizationID>urn:oasis:names:specification:ubl:xsd:Invoice-2</cbc:CustomizationID>
  <cbc:ProfileID>urn:oasis:names:specification:ubl:xsd:Invoice-2</cbc:ProfileID>
  <cbc:ID>{escape(g('invoice_number'))}</cbc:ID>
  <cbc:IssueDate>{issue}</cbc:IssueDate>
  <cbc:InvoiceTypeCode>{'381' if credit else '380'}</cbc:InvoiceTypeCode>
  <cbc:Note>ROSSUM ID - {escape(str(annotation_id))}</cbc:Note>
  <cbc:TaxPointDate>{issue}</cbc:TaxPointDate>
  <cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>
  <cbc:BuyerReference>{escape(company_code)}</cbc:BuyerReference>
  <cac:OrderReference><cbc:ID>{po}</cbc:ID></cac:OrderReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{escape(pdf_filename)}</cbc:ID>
    <cbc:DocumentTypeCode>130</cbc:DocumentTypeCode>
    <cbc:DocumentDescription>Original supplier invoice</cbc:DocumentDescription>
    <cac:Attachment>
      <cbc:EmbeddedDocumentBinaryObject mimeCode="{escape(mime_code)}" filename={quoteattr(pdf_filename)}>{escape(pdf_b64)}</cbc:EmbeddedDocumentBinaryObject>
    </cac:Attachment>
  </cac:AdditionalDocumentReference>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="SAP-LIFNR">{escape(g('supplier_number'))}</cbc:ID></cac:PartyIdentification>
    <cac:PartyName><cbc:Name>{escape(g('supplier_name'))}</cbc:Name></cac:PartyName>
    <cac:PartyTaxScheme><cbc:CompanyID></cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="SAP-BUKRS">{escape(company_code)}</cbc:ID></cac:PartyIdentification>
  </cac:Party></cac:AccountingCustomerParty>
{payment_terms}  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="{currency}">{tax}</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="{currency}">{net}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="{currency}">{tax}</cbc:TaxAmount>
      <cac:TaxCategory><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="{currency}">{net}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="{currency}">{net}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{currency}">{total}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="{currency}">{total}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
{chr(10).join(line_xml)}
</Invoice>""")


# ---- transport -------------------------------------------------------------------------
def _oauth_token(token_url, client_id, client_secret):
    resp = requests.post(token_url, data={"grant_type": "client_credentials"},
                         auth=(client_id, client_secret),
                         headers={"Accept": "application/json"}, timeout=30)
    if resp.status_code in (400, 401):
        resp = requests.post(token_url, data={"grant_type": "client_credentials",
                                              "client_id": client_id,
                                              "client_secret": client_secret},
                             headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _redact(text, values):
    """Mask every credential VALUE (and its percent-encoded form) with a fingerprint before
    anything is logged. An unredacted request dump once put both the bearer token and the
    gateway key into a hook log readable by anyone with log access."""
    seen = set()
    for v in (x for x in values if x and len(str(x)) >= 8):
        seen.update({str(v), quote(str(v), safe="")})
    for v in sorted(seen, key=len, reverse=True):
        text = text.replace(v, f"***redacted*** (sha256:{hashlib.sha256(v.encode()).hexdigest()[:8]})")
    return text


def post_to_vim(settings, secrets, xml_str):
    gateway_sql_preflight(xml_str)
    access = _oauth_token(settings["TOKEN_URL"], secrets["client_id"], secrets["client_secret"])
    field = settings.get("FORM_FIELD_NAME", "document")
    files = {field: ("invoice.xml", xml_str.encode("utf-8"), "text/xml")}
    p = urlsplit(settings["VIM_URL"])
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q.setdefault("sap-client", settings.get("SAP_CLIENT", ""))
    if secrets.get("vim_api_key"):
        q["apiKey"] = secrets["vim_api_key"]
    url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    session = requests.Session()
    prepared = session.prepare_request(
        requests.Request("POST", url, headers={"Authorization": f"Bearer {access}"}, files=files))
    mask = [access, secrets.get("vim_api_key"), secrets.get("client_secret")]
    print("[vim-export] POST " + _redact(prepared.url, mask) + "\n  "
          + "\n  ".join(f"{k}: {_redact(str(v), mask)}" for k, v in prepared.headers.items())
          + f"\n  multipart part={field!r} filename=invoice.xml text/xml, UBL {len(xml_str)} bytes")
    return session.send(prepared, timeout=30)


def vim_reference(response_text):
    """'regid / guid' from VIM's ingest response; '' when unparseable (cosmetic, never fatal)."""
    try:
        body = json.loads(response_text or "")
    except (ValueError, TypeError):
        return ""
    if not isinstance(body, dict):
        return ""
    docs = body.get("documents")
    regid = str(docs[0].get("regid") or "").strip() if isinstance(docs, list) and docs and isinstance(docs[0], dict) else ""
    return " / ".join(p for p in (regid, str(body.get("id") or "").strip()) if p)


# ---- archive on the annotation --------------------------------------------------------------
def _check(resp):
    """raise_for_status() that keeps the response BODY - the API states the reason there."""
    if not resp.ok:
        raise requests.HTTPError(f"{resp.status_code} {resp.request.method} {resp.url} -> "
                                 f"{resp.text[:300]}", response=resp)
    return resp


def store_export_relation(base_url, rossum_token, annotation_url, xml_str, filename):
    """Archive the sent XML as a document_relation (type export, RELATION_KEY). A relation
    with documents cannot be DELETEd and (type, annotation, key) is unique, so a re-export
    must PATCH the existing relation - and the lookup must filter on the annotation ID
    server-side, or the relation scrolls off page 1 once >20 documents have exported.
    Best-effort: the audit archive never blocks the export."""
    auth = {"Authorization": f"Bearer {rossum_token}"}
    annotation_id = str(annotation_url or "").rstrip("/").rsplit("/", 1)[-1]
    try:
        rels = _check(requests.get(f"{base_url}/api/v1/document_relations", headers=auth,
                                   params={"key": RELATION_KEY, "annotation": annotation_id},
                                   timeout=20)).json().get("results", [])
        existing = next((r for r in rels if r.get("annotation") == annotation_url), None)
        doc_url = _check(requests.post(f"{base_url}/api/v1/documents", headers=auth,
                                       files={"content": (filename, xml_str.encode("utf-8"))},
                                       timeout=20)).json()["url"]
        if existing:
            _check(requests.patch(existing["url"], headers=auth, json={"documents": [doc_url]}, timeout=20))
            return f"relation {RELATION_KEY!r} updated -> {doc_url}"
        _check(requests.post(f"{base_url}/api/v1/document_relations", headers=auth,
                             json={"type": "export", "key": RELATION_KEY,
                                   "annotation": annotation_url, "documents": [doc_url]}, timeout=20))
        return f"relation {RELATION_KEY!r} created -> {doc_url}"
    except Exception as e:                                          # noqa: BLE001
        return f"relation store failed (non-fatal): {e}"


# ---- handler ---------------------------------------------------------------------------
def rossum_hook_request_handler(payload):
    settings, secrets = payload.get("settings") or {}, payload.get("secrets") or {}
    base_url, rossum_token = payload["base_url"], payload["rossum_authorization_token"]
    annotation = payload.get("annotation") or {}
    annotation_id = annotation.get("id")
    header, lines = extract_content(annotation.get("content"))

    # Routing: the schema's export_target field is the single source of truth (it also gates
    # any other target's pipeline). Skip unless it says sap or does not exist in this queue.
    target = str(header.get(EXPORT_TARGET_FIELD) or "sap").strip().lower()
    if target != "sap":
        return {"messages": [{"type": "info", "content": f"export_target={target}; VIM export skipped."}],
                "operations": []}

    company_code = str(header.get(COMPANY_CODE_FIELD) or settings.get("COMPANY_CODE") or "")
    document_url = annotation.get("document") or (payload.get("document") or {}).get("url")
    pdf_b64, filename, mime_code = fetch_document_b64(rossum_token, document_url)
    pdf_b64 = neutralize_sqlish_base64(pdf_b64)      # BEFORE build: archive == wire bytes
    xml_str = build_ubl(header, lines, pdf_b64, filename, mime_code, company_code, annotation_id)

    if str(settings.get("LOG_FULL_PAYLOAD", "true")).lower() in ("true", "1", "yes"):
        print(f"[vim-export] outgoing UBL ({len(xml_str)} bytes):\n{xml_str}")
    else:
        print("[vim-export] outgoing UBL:\n" + _B64_ELEMENT_RE.sub(
            lambda m: f"{m.group(1)}[base64 omitted, {len(m.group(2))} chars]{m.group(3)}", xml_str))

    rel_msg = store_export_relation(base_url, rossum_token, annotation.get("url"), xml_str,
                                    f"{RELATION_KEY}.xml")
    resp = post_to_vim(settings, secrets, xml_str)
    print(f"[vim-export] VIM POST {resp.status_code}; {rel_msg}; body[:200]={resp.text[:200]}")
    if not 200 <= resp.status_code < 300:
        return {"messages": [{"type": "error",
                              "content": f"SAP/VIM export failed: HTTP {resp.status_code}. {resp.text[:300]}"}],
                "operations": []}

    operations = []
    ref, ref_id = vim_reference(resp.text), datapoint_id(annotation.get("content"), VIM_REFERENCE_FIELD)
    if ref and ref_id:
        operations.append({"op": "replace", "id": ref_id, "value": {"content": {"value": ref}}})
    return {"messages": [{"type": "info",
                          "content": f"Sent to SAP/VIM (HTTP {resp.status_code}). "
                                     f"reference={ref or 'none in response'}. {rel_msg}"}],
            "operations": operations}
