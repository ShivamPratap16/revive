"""Integration tests: real HTTP requests through FastAPI into a real Postgres."""

import asyncio
from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 10, 15, 4, 30, tzinfo=UTC)


def event(eid: str, type_: str, step: str | None, minute: int, session="s1", user="u1", **extra):
    body = {
        "event_id": eid,
        "session_id": session,
        "type": type_,
        "occurred_at": (T0 + timedelta(minutes=minute)).isoformat(),
        "user": {"id": user, "phone": "+919876543210", "name": "Asha", "language": "hi"},
        **extra,
    }
    if step:
        body["step"] = step
    return body


async def post(client, key, *events):
    return await client.post(
        "/v1/events", json={"events": list(events)}, headers={"X-API-Key": key}
    )


async def only_session(client, key):
    r = await client.get("/v1/sessions", headers={"X-API-Key": key})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    return items[0]


async def test_requires_api_key(client):
    r = await client.post("/v1/events", json={"events": []})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "missing_api_key"
    r = await post(client, "rv_live_wrong", event("e1", "step_started", "signup", 0))
    assert r.json()["error"]["code"] == "invalid_api_key"


async def test_ingest_builds_session_state(client, api_key):
    r = await post(
        client,
        api_key,
        event("e1", "step_completed", "signup", 0),
        event("e2", "step_completed", "pan_verification", 2),
        event("e3", "step_failed", "aadhaar_otp", 4, error_code="OTP_TIMEOUT"),
    )
    assert r.status_code == 202
    assert r.json()["accepted"] == 3

    s = await only_session(client, api_key)
    assert s["current_step"] == "aadhaar_otp"
    assert s["last_error_code"] == "OTP_TIMEOUT"
    assert s["customer"]["phone_masked"] == "+91******3210"


async def test_retried_request_is_idempotent(client, api_key):
    batch = [
        event("e1", "step_started", "pan_verification", 0),
        event("e2", "step_failed", "pan_verification", 1, error_code="PAN_MISMATCH"),
    ]
    await post(client, api_key, *batch)
    r = await post(client, api_key, *batch)  # app retries after a network timeout
    assert r.json() == {
        "accepted": 0,
        "duplicates": 2,
        "results": [
            {"event_id": "e1", "status": "duplicate", "clock_skew_corrected": False},
            {"event_id": "e2", "status": "duplicate", "clock_skew_corrected": False},
        ],
    }
    s = await only_session(client, api_key)
    assert s["failure_count"] == 1  # not 2


async def test_out_of_order_batches(client, api_key):
    await post(client, api_key, event("e2", "step_completed", "aadhaar_otp", 10))
    await post(client, api_key, event("e1", "step_started", "pan_verification", 1))  # arrives late
    s = await only_session(client, api_key)
    assert s["current_step"] == "video_kyc"
    assert s["started_at"].startswith("2026-10-15T04:31")  # started_at moved back


async def test_concurrent_requests_for_same_session_lose_nothing(client, api_key):
    # 20 requests at once, each failing OTP once. Without the row lock, two requests
    # read failure_count=5 at the same time, both write 6, and one failure is lost.
    # Each request uses a different user id on purpose: the customer upsert also takes a
    # row lock, and a shared customer would serialise the requests and hide the race.
    reqs = [
        post(
            client,
            api_key,
            event(f"e{i}", "step_failed", "aadhaar_otp", i, user=f"u{i}", error_code="OTP_TIMEOUT"),
        )
        for i in range(20)
    ]
    responses = await asyncio.gather(*reqs)
    assert all(r.status_code == 202 for r in responses)
    s = await only_session(client, api_key)
    assert s["failure_count"] == 20


async def test_future_timestamps_are_clamped(client, api_key):
    future = datetime.now(UTC) + timedelta(days=2)
    body = event("e1", "step_started", "signup", 0)
    body["occurred_at"] = future.isoformat()
    r = await post(client, api_key, body)
    assert r.json()["results"][0]["clock_skew_corrected"] is True


async def test_validation_errors_are_clear(client, api_key):
    bad = event("e1", "step_failed", None, 0)  # step_failed without a step
    bad["user"]["phone"] = "12345"
    r = await post(client, api_key, bad)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error"
    fields = {d["field"] for d in body["details"]}
    assert "body.events.0.user.phone" in fields


async def test_naive_timestamp_rejected(client, api_key):
    body = event("e1", "step_started", "signup", 0)
    body["occurred_at"] = "2026-10-15T10:00:00"
    r = await post(client, api_key, body)
    assert r.status_code == 422


async def test_tenants_are_isolated(client, api_key, other_api_key):
    await post(client, api_key, event("e1", "step_started", "signup", 0))
    s = await only_session(client, api_key)

    # Same event_id from another tenant is NOT a duplicate: ids are per tenant
    r = await post(client, other_api_key, event("e1", "step_started", "signup", 0))
    assert r.json()["accepted"] == 1

    r = await client.get(f"/v1/sessions/{s['id']}", headers={"X-API-Key": other_api_key})
    assert r.status_code == 404


async def test_session_detail_lists_events_in_time_order(client, api_key):
    await post(
        client,
        api_key,
        event("late", "step_completed", "signup", 5),
        event("early", "step_started", "signup", 1),
    )
    s = await only_session(client, api_key)
    r = await client.get(f"/v1/sessions/{s['id']}", headers={"X-API-Key": api_key})
    assert [e["external_event_id"] for e in r.json()["events"]] == ["early", "late"]


async def test_cursor_pagination(client, api_key):
    events = [
        event(f"e{i}", "step_started", "signup", i, session=f"s{i}", user=f"u{i}") for i in range(5)
    ]
    await post(client, api_key, *events)

    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/v1/sessions", params=params, headers={"X-API-Key": api_key})
        page = r.json()
        seen += [i["external_session_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == ["s4", "s3", "s2", "s1", "s0"]


async def test_customer_details_not_wiped_by_partial_event(client, api_key):
    await post(client, api_key, event("e1", "step_started", "signup", 0))
    partial = event("e2", "step_completed", "signup", 1)
    partial["user"] = {"id": "u1"}  # no phone this time
    await post(client, api_key, partial)
    s = await only_session(client, api_key)
    assert s["customer"]["phone_masked"] == "+91******3210"


async def test_user_details_merge_within_one_batch(client, api_key):
    # Regression: found in manual testing. The 2nd event only had the user id, and it
    # wiped the phone/name/language sent by the 1st event in the same batch.
    first = event("e1", "step_completed", "pan_verification", 0)
    first["user"]["language"] = "hinglish"
    second = event("e2", "step_failed", "aadhaar_otp", 3, error_code="OTP_NOT_RECEIVED")
    second["user"] = {"id": "u1"}
    await post(client, api_key, first, second)
    c = (await only_session(client, api_key))["customer"]
    assert c == {
        "external_user_id": "u1",
        "name": "Asha",
        "phone_masked": "+91******3210",
        "preferred_language": "hinglish",
    }
