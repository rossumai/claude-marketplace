"""Record the intake's document type in annotation.metadata, so it survives a move.

Pair: `routing/document-type-restore-from-metadata` reads the same key back on the
destination queue. Fill «document_type_field» and «metadata_key» with the SAME
values in both parts.

Why it exists: a `change_queue` move with `reimport: true` re-extracts the document
on the destination, and the destination's engine re-predicts every captured field,
the document type included. The type decided on the intake queue - by the engine at
a confident score, or by a person - is lost unless it travels with the annotation.
`annotation.metadata` travels with it; extracted content does not.

Two paths write the key:

1. Intake (action `initialize`, queue listed in `settings.intake_queue_ids`): the
   intake queue is where the type is decided, so its current value is recorded.
   The engine's prediction is already in the `initialize` payload. On any other
   queue an `initialize` value is a re-prediction and is never recorded.
2. Human correction (action `updated`, any attached queue): the field's datapoint
   is in `updated_datapoints` AND its `validation_sources` contains "human". Both
   are needed: "human" alone only means a person visited the field. A write by a
   hook (the Restore part included) or an engine re-prediction never carries
   "human", so neither loops back into the key.

Every write reads the annotation fresh, then PATCHes the whole metadata object:
a metadata PATCH replaces the object, and other hooks keep their own keys in it.

Hook object requirements (JSON, not code):
  events      : ["annotation_content.initialize", "annotation_content.updated"]
  queues      : every intake queue AND every destination queue (a person can
                correct the type on any of them)
  settings    : {"intake_queue_ids": [<int>, ...]}  <- ids as list VALUES, so
                rdc migrate / prd2 deploy remap them per environment
  token_owner : <user id>                           <- REQUIRED; supplies
                                                       rossum_authorization_token
  config.third_party_library_pack : "default"       <- REQUIRED; provides rossum_api
  sideload    : not needed (the hook does not use TxScript)

Cost: an `annotation_content.updated` hook blocks the validation screen while it
runs, and it fires on EVERY field edit, not only this field. Measured: about 0.2 s
warm, 4.3-5.0 s on a cold start (first run after several idle minutes).
"""

from __future__ import annotations

from typing import Any

from rossum_api import ElisAPIClientSync

DOCUMENT_TYPE_FIELD = "«document_type_field»"
METADATA_KEY = "«metadata_key»"
HUMAN_VALIDATION_SOURCE = "human"


def rossum_hook_request_handler(payload: dict) -> dict:
    if payload.get("event") != "annotation_content":
        return {}
    action = payload.get("action")
    if action == "initialize":
        return record_intake_type(payload)
    if action == "updated":
        return record_human_correction(payload)
    return {}


def record_intake_type(payload: dict) -> dict:
    annotation = payload["annotation"]
    intake_queue_ids = set(payload.get("settings", {}).get("intake_queue_ids") or [])
    queue_id = queue_id_from_url(annotation["queue"])
    if queue_id not in intake_queue_ids:
        return {"skipped": f"queue {queue_id} is not an intake queue"}

    datapoint = find_datapoint(annotation.get("content") or [], DOCUMENT_TYPE_FIELD)
    value = datapoint_value(datapoint)
    if not value:
        return {"skipped": f"{DOCUMENT_TYPE_FIELD} is blank"}
    return write_metadata_key(payload, value, source="intake")


def record_human_correction(payload: dict) -> dict:
    annotation = payload["annotation"]
    updated = set(payload.get("updated_datapoints") or [])
    if not updated:
        return {"skipped": "no updated datapoints"}

    datapoint = find_datapoint(annotation.get("content") or [], DOCUMENT_TYPE_FIELD)
    if datapoint is None:
        return {"skipped": f"{DOCUMENT_TYPE_FIELD} has no datapoint on this schema"}

    # The public API documents updated_datapoints as datapoint (content-node) ids;
    # the schema id is accepted too, so the check holds under either reading.
    if datapoint.get("id") not in updated and DOCUMENT_TYPE_FIELD not in updated:
        return {"skipped": f"{DOCUMENT_TYPE_FIELD} was not among the changed fields"}

    if HUMAN_VALIDATION_SOURCE not in (datapoint.get("validation_sources") or []):
        return {"skipped": f"{DOCUMENT_TYPE_FIELD} change was not made by a person"}

    value = datapoint_value(datapoint)
    if not value:
        return {"skipped": f"{DOCUMENT_TYPE_FIELD} is blank"}
    return write_metadata_key(payload, value, source="human")


def write_metadata_key(payload: dict, value: str, source: str) -> dict:
    annotation_id = payload["annotation"]["id"]
    client = get_rossum_client(payload)
    path = f"annotations/{annotation_id}"
    metadata = dict(client.request_json("GET", path).get("metadata") or {})
    if metadata.get(METADATA_KEY) == value:
        return {"skipped": "metadata already matches"}
    client.request_json("PATCH", path, json={"metadata": {**metadata, METADATA_KEY: value}})
    print(f"annotation {annotation_id}: metadata.{METADATA_KEY} = {value!r} ({source})")
    return {"annotation_id": annotation_id, METADATA_KEY: value, "source": source}


def find_datapoint(content: list[dict[str, Any]], schema_id: str) -> dict[str, Any] | None:
    for node in content:
        if node.get("category") == "datapoint" and node.get("schema_id") == schema_id:
            return node
        children = node.get("children")
        if children:
            found = find_datapoint(children, schema_id)
            if found is not None:
                return found
    return None


def datapoint_value(datapoint: dict[str, Any] | None) -> str | None:
    return ((datapoint or {}).get("content") or {}).get("value") or None


def queue_id_from_url(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[1])


def get_rossum_client(payload: dict) -> ElisAPIClientSync:
    token = payload.get("rossum_authorization_token")
    if not token:
        raise RuntimeError(
            "Authorization token not found in the payload. "
            f"Set a token_owner on hook {payload.get('hook')}."
        )
    return ElisAPIClientSync(token=token, base_url=payload["base_url"] + "/api/v1")
