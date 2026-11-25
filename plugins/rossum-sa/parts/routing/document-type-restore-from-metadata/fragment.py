"""Restore the intake's document type from annotation.metadata when a document arrives.

Pair: `routing/document-type-record-in-metadata` writes the key this hook reads.
Fill «document_type_field» and «metadata_key» with the SAME values in both parts.

Runs on `annotation_content.initialize` on every DESTINATION queue - never on the
intake queue, where the engine's prediction is the decision. A `change_queue` move
with `reimport: true` re-extracts the document, and the destination's engine
re-predicts «document_type_field». This hook puts the intake decision back. It runs
before rules on `annotation_imported` evaluate, so those rules see the restored value.

Resolution (first match wins):

1. Fixed type: the queue is listed in `settings.fixed_document_types`. Arriving on
   that queue IS the decision, so it beats any older type in metadata, and the
   metadata is rewritten so the next hop restores this type. A person can still
   retype the field afterwards; the Record part captures that.
2. `metadata[«metadata_key»]`: the type recorded on the intake queue, or a later
   human correction.
3. Nothing resolves: the field keeps its current value - the engine's prediction,
   or a value mapped from a structured e-invoice. It is never blanked, because a
   blank destroys a usable value. An error is shown only when the field is empty
   too: then the document has no type at all.

The metadata write reads the annotation fresh, then PATCHes the whole object: a
metadata PATCH replaces the object, and other hooks keep their own keys in it.

Hook object requirements (JSON, not code):
  events      : ["annotation_content.initialize"]
  queues      : every destination queue (not the intake queue)
  sideload    : ["schemas"]                    <- REQUIRED; TxScript.from_payload
                                                  fails without it
  settings    : {"fixed_document_types": [{"queue_id": <int>, "document_type": "<value>"}]}
                a list of objects with ids as VALUES, so rdc migrate / prd2 deploy
                remap them per environment; [] when no queue has a fixed type
  token_owner : <user id>                      <- REQUIRED; supplies
                                                  rossum_authorization_token
  config.third_party_library_pack : "default"  <- REQUIRED; provides rossum_api
"""

from __future__ import annotations

from rossum_api import ElisAPIClientSync
from txscript import TxScript

DOCUMENT_TYPE_FIELD = "«document_type_field»"
METADATA_KEY = "«metadata_key»"


def rossum_hook_request_handler(payload: dict) -> dict:
    if payload.get("event") != "annotation_content" or payload.get("action") != "initialize":
        return {}

    t = TxScript.from_payload(payload)
    metadata = payload["annotation"].get("metadata") or {}

    fixed_type = get_fixed_document_type(payload)
    if fixed_type is not None:
        if metadata.get(METADATA_KEY) != fixed_type:
            write_metadata_key(payload, fixed_type)
        setattr(t.field, DOCUMENT_TYPE_FIELD, fixed_type)
        return t.hook_response()

    recorded = metadata.get(METADATA_KEY)
    if recorded:
        setattr(t.field, DOCUMENT_TYPE_FIELD, str(recorded))
        return t.hook_response()

    if get_current_document_type(t) is None:
        t.show_error(f"Could not resolve the document type ({DOCUMENT_TYPE_FIELD}).")
    else:
        print(f"No metadata.{METADATA_KEY}; keeping the value already in {DOCUMENT_TYPE_FIELD}.")
    return t.hook_response()


def get_fixed_document_type(payload: dict) -> str | None:
    queue_id = int(payload["annotation"]["queue"].rstrip("/").rsplit("/", 1)[1])
    for entry in payload.get("settings", {}).get("fixed_document_types") or []:
        if entry.get("queue_id") == queue_id and entry.get("document_type"):
            return str(entry["document_type"])
    return None


def get_current_document_type(t: TxScript) -> str | None:
    try:
        value = getattr(t.field, DOCUMENT_TYPE_FIELD)
    except AttributeError:
        return None
    return str(value) if value else None


def write_metadata_key(payload: dict, value: str) -> None:
    annotation_id = payload["annotation"]["id"]
    client = get_rossum_client(payload)
    path = f"annotations/{annotation_id}"
    fresh = dict(client.request_json("GET", path).get("metadata") or {})
    client.request_json("PATCH", path, json={"metadata": {**fresh, METADATA_KEY: value}})


def get_rossum_client(payload: dict) -> ElisAPIClientSync:
    token = payload.get("rossum_authorization_token")
    if not token:
        raise RuntimeError(
            "Authorization token not found in the payload. "
            f"Set a token_owner on hook {payload.get('hook')}."
        )
    return ElisAPIClientSync(token=token, base_url=payload["base_url"] + "/api/v1")
