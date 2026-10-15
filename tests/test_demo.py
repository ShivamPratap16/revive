"""The public demo: no key needed, but scoped to the demo tenant and rate-limited."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from app.config import get_settings
from app.db import SessionFactory
from app.domain.policy import OutreachPolicy
from app.models import Tenant
from app.security import generate_api_key, hash_api_key
from app.services.delivery import deliver_attempt
from app.services.detector import detect_dropoffs
from simulator import scenarios
from tests.test_outreach import channels, claim, ev, post

STEPS = ("signup", "pan_verification", "aadhaar_otp", "video_kyc", "bank_linking")


@pytest.fixture
async def demo_tenant():
    key = generate_api_key()
    # Same fast policy as the live demo tenant: stuck after 1 minute, call 1 minute later.
    fast = OutreachPolicy.model_validate({
        "idle_minutes": {step: 1 for step in STEPS},
        "cadence": [{"channel": "in_app", "delay_minutes": 0},
                    {"channel": "voice", "delay_minutes": 1}],
        "in_app_window": {"start": "00:00", "end": "23:59"},
    })  # fmt: skip
    async with SessionFactory() as db:
        db.add(Tenant(name=get_settings().demo_tenant_name, api_key_hash=hash_api_key(key),
                      api_key_prefix=key[:12], policy=fast.model_dump(mode="json")))  # fmt: skip
        await db.commit()
    return key


@pytest.fixture(autouse=True)
async def clear_demo_rate_limits():
    r = Redis.from_url(get_settings().redis_url)
    keys = [k async for k in r.scan_iter("revive:demo:*")]
    if keys:
        await r.delete(*keys)
    yield
    await r.aclose()


async def test_no_demo_tenant_means_404(client):
    assert (await client.get("/v1/demo/summary")).status_code == 404


async def test_summary_is_the_demo_tenant_only(client, demo_tenant, api_key):
    await post(client, demo_tenant, ev("d1", "step_started", "signup", datetime.now(UTC)))
    await post(client, api_key, ev("o1", "step_started", "signup", datetime.now(UTC), session="x"))
    r = await client.get("/v1/demo/summary")
    assert r.status_code == 200
    assert r.json()["sessions"]["started"] == 1  # the other tenant's user isn't counted


async def test_try_then_watch_the_timeline(client, demo_tenant):
    r = await client.post("/v1/demo/try")
    assert r.status_code == 200
    run = r.json()
    view = (await client.get(f"/v1/demo/sessions/{run['session_id']}")).json()
    assert view["session"]["phone"].endswith("1111")  # the simulator's always-answers number
    # Detection runs immediately on /try, so the visitor doesn't wait for the cron tick
    assert [x["kind"] for x in view["timeline"]] == ["event", "detect"]
    assert view["finished"] is False
    assert await detect_dropoffs(now=datetime.now(UTC)) == 0  # the cron run finds nothing left

    # The dispatcher's next tick sends the nudge
    now = datetime.now(UTC) + timedelta(seconds=10)
    for attempt_id in await claim(now):
        await deliver_attempt(attempt_id, channels(), now=now)
    view = (await client.get(f"/v1/demo/sessions/{run['session_id']}")).json()
    assert [x["kind"] for x in view["timeline"]] == ["event", "detect", "nudge"]


async def test_cannot_read_other_tenants_sessions(client, demo_tenant, api_key):
    await post(client, api_key, ev("o1", "step_started", "signup", datetime.now(UTC)))
    r = await client.get("/v1/sessions", headers={"X-API-Key": api_key})
    other = r.json()["items"][0]["id"]
    assert (await client.get(f"/v1/demo/sessions/{other}")).status_code == 404


async def test_try_is_rate_limited_per_visitor(client, demo_tenant):
    codes = [(await client.post("/v1/demo/try")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


async def test_landing_page(client):
    r = await client.get("/")
    assert r.status_code == 200 and "Revive" in r.text


def test_magic_number_always_answers():
    rng = random.Random(3)
    outcomes = {scenarios.pick(rng, topic="otp", answered_only=True).outcome for _ in range(200)}
    assert outcomes == {"completed"}


async def test_stream_sends_the_timeline(client, demo_tenant, monkeypatch):
    # The in-process test client buffers whole responses, so keep the stream short.
    monkeypatch.setattr(get_settings(), "demo_stream_seconds", 2)
    run = (await client.post("/v1/demo/try")).json()
    r = await client.get(f"/v1/demo/sessions/{run['session_id']}/stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    lines = r.text.splitlines()
    assert lines[0] == "event: update"
    assert '"kind": "detect"' in lines[1]
    assert '"at": "20' in lines[1] and "T" in lines[1].split('"at": "')[1][:12]  # ISO 8601
    assert lines.count("event: update") == 1  # unchanged state is not re-sent


async def test_calls_endpoints_only_show_demo_calls(client, demo_tenant):
    assert (await client.get("/v1/demo/calls")).json() == []
    import uuid

    assert (await client.get(f"/v1/demo/calls/{uuid.uuid4()}")).status_code == 404
