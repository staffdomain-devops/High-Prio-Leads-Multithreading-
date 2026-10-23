"""Thin HubSpot REST client (CRM v3 objects + v4 associations) with retry."""

import os
from datetime import datetime, timezone

import requests
from tenacity import retry, retry_if_exception

from utils import _is_requests_transient, REQ_RETRY_KWARGS

BASE = "https://api.hubapi.com"
OPPORTUNITY = "p5402982_opportunities"

_req_retry = retry(retry=retry_if_exception(_is_requests_transient), **REQ_RETRY_KWARGS)


def _headers():
    return {"Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}"}


@_req_retry
def _request(method, path, **kwargs):
    resp = requests.request(method, f"{BASE}{path}", headers=_headers(), timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def get_object(object_type, object_id, properties):
    data = _request(
        "GET", f"/crm/v3/objects/{object_type}/{object_id}",
        params={"properties": ",".join(properties)},
    )
    return data.get("properties") or {}


def batch_read(object_type, ids, properties):
    """{id: properties} for every ID (chunks of 100)."""
    out = {}
    ids = [str(i) for i in ids]
    for i in range(0, len(ids), 100):
        data = _request(
            "POST", f"/crm/v3/objects/{object_type}/batch/read",
            json={"inputs": [{"id": x} for x in ids[i:i + 100]], "properties": properties},
        )
        for r in data.get("results", []):
            out[str(r["id"])] = r.get("properties") or {}
    return out


def associated_ids(from_type, from_id, to_type):
    ids, after = [], None
    while True:
        params = {"limit": 500}
        if after:
            params["after"] = after
        data = _request("GET", f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}", params=params)
        ids.extend(str(r["toObjectId"]) for r in data.get("results", []))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            return ids


def update_object(object_type, object_id, properties):
    return _request("PATCH", f"/crm/v3/objects/{object_type}/{object_id}", json={"properties": properties})


def create_note(object_type, object_id, body_html):
    """Create a note on a record (default association). Returns the note ID."""
    note = _request("POST", "/crm/v3/objects/notes", json={"properties": {
        "hs_note_body": body_html,
        "hs_timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }})
    note_id = str(note["id"])
    _request("PUT", f"/crm/v4/objects/notes/{note_id}/associations/default/{object_type}/{object_id}")
    return note_id


def batch_update(object_type, updates):
    """updates: {id: properties} (chunks of 100)."""
    items = [{"id": str(k), "properties": v} for k, v in updates.items()]
    for i in range(0, len(items), 100):
        _request("POST", f"/crm/v3/objects/{object_type}/batch/update", json={"inputs": items[i:i + 100]})
