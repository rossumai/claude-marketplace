"""Live PO line status — refresh received/invoiced consumption from Workday REST.

Fires when an annotation is opened or edited so validation rules compare the
invoice against the PO's CURRENT consumption, not the last master-data sync.
Uses the procurement REST API because the SOAP Get_Purchase_Orders export does
not carry per-line received/invoiced values.
"""
import requests
from txscript import TxScript

TOKEN_URL = "«token_url»"
PO_ENDPOINT = "«po_endpoint_url»"
# Order-type reference ids as projected onto lines by the PO-line match
GOODS_ORDER_TYPE = "«goods_order_type_id»"
SERVICE_ORDER_TYPE = "«service_order_type_id»"


def _login(payload: dict) -> str:
    """OAuth refresh-token grant; persist the bearer into hook secrets so later
    runs skip the token round-trip (requires token_owner on the hook)."""
    secrets = payload["secrets"]
    res = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": secrets["client_id"],
            "client_secret": secrets["client_secret"],
            "refresh_token": secrets["refresh_token"],
        },
        timeout=30,
    )
    res.raise_for_status()
    token = res.json()["access_token"]
    requests.patch(
        payload["hook"],
        headers={"Authorization": f"Bearer {payload['rossum_authorization_token']}"},
        json={"secrets": {"bearer_token": token}},
        timeout=30,
    )
    return token


def _get_po(po_id: str, cache: dict, payload: dict):
    """Fetch each PO once per run; re-login once on 401 (cached bearer expired)."""
    if po_id in cache:
        return cache[po_id]
    token = payload["secrets"].get("bearer_token") or _login(payload)
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(f"{PO_ENDPOINT}/{po_id}", headers=headers, timeout=30)
    if res.status_code == 401:
        headers = {"Authorization": f"Bearer {_login(payload)}"}
        res = requests.get(f"{PO_ENDPOINT}/{po_id}", headers=headers, timeout=30)
    cache[po_id] = res.json() if res.status_code == 200 else None
    return cache[po_id]


def _find_line(po: dict, line_number, order_type: str):
    """REST line objects expose no bare line-number field; the number is the
    trailing token of the human-readable descriptor."""
    array = "serviceLines" if order_type == SERVICE_ORDER_TYPE else "goodsLines"
    for line in po.get(array) or []:
        if line.get("descriptor", "").split(" ")[-1] == str(line_number):
            return line
    return None


def _num(value):
    """Workday REST returns numerics either bare or wrapped as {"value": ...}."""
    if isinstance(value, dict):
        return value.get("value", 0)
    return value or 0


def rossum_hook_request_handler(payload: dict) -> dict:
    t = TxScript.from_payload(payload)
    po_cache = {}
    for row in t.field.line_items:
        if not row.item_order_line_nr_wd:
            continue
        po = _get_po(str(row.item_order_internal_id_wd), po_cache, payload)
        if not po:
            t.show_error("Live PO lookup failed — cannot verify PO consumption.")
            return t.hook_response()
        line = _find_line(po, row.item_order_line_nr_wd, row.item_order_type_wd)
        if not line:
            continue
        if row.item_order_type_wd == SERVICE_ORDER_TYPE:
            row.item_order_line_amount_received = _num(line.get("amountReceived"))
            row.item_order_line_amount_invoiced = _num(line.get("amountInvoiced"))
        elif row.item_order_type_wd == GOODS_ORDER_TYPE:
            row.item_order_line_quantity_received = _num(line.get("quantityReceived"))
            row.item_order_line_quantity_invoiced = _num(line.get("quantityInvoiced"))
    return t.hook_response()
