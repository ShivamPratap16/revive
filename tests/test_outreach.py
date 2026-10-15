"""Integration tests for the drop-off engine: detector -> dispatcher -> delivery.

The worker functions take `now` as a parameter, so these tests control time completely:
"detect at 10:46", "deliver at 22:00", without sleeping or patching the clock.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.channels import (
    PermanentChannelError,
    SimulatedVoiceChannel,
    TransientChannelError,
)
from app.db import SessionFactory
from app.domain.policy import Channel
from app.models import DndEntry, OnboardingSession, OutreachAttempt
from app.services.delivery import claim_due, deliver_attempt, reap_expired
from app.services.detector import detect_dropoffs

T = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)  # Thursday 10:30 IST


class FakeChannel:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.sent = []

    async def send(self, msg):
        if self.error:
            raise self.error
        self.sent.append(msg)
        return f"fake_{len(self.sent)}"


def channels(in_app=None, voice=None):
    return {Channel.IN_APP: in_app or FakeChannel(), Channel.VOICE: voice or FakeChannel()}


def ev(eid, type_, step, at, session="s1", user="u1", phone="+919812345678", **extra):
    body = {
        "event_id": eid,
        "session_id": session,
        "type": type_,
        "occurred_at": at.isoformat(),
        "user": {"id": user, "phone": phone, "name": "Ravi Kumar", "language": "hinglish"},
        **extra,
    }
    if step:
        body["step"] = step
    return body


async def post(client, key, *events):
    r = await client.post("/v1/events", json={"events": list(events)}, headers={"X-API-Key": key})
    assert r.status_code == 202, r.text
    return r


async def stuck_on_otp(client, key, session="s1", user="u1", phone="+919812345678", at=T):
    await post(
        client,
        key,
        ev(
            f"{session}-1",
            "step_completed",
            "pan_verification",
            at - timedelta(minutes=2),
            session=session,
            user=user,
            phone=phone,
        ),
        ev(
            f"{session}-2",
            "step_failed",
            "aadhaar_otp",
            at,
            session=session,
            user=user,
            phone=phone,
            error_code="OTP_NOT_RECEIVED",
        ),
    )


async def attempts(session_ext="s1") -> list[OutreachAttempt]:
    async with SessionFactory() as db:
        return list(
            await db.scalars(
                select(OutreachAttempt)
                .join(OnboardingSession, OutreachAttempt.session_id == OnboardingSession.id)
                .where(OnboardingSession.external_session_id == session_ext)
                .order_by(OutreachAttempt.drop_round, OutreachAttempt.sequence_no)
            )
        )


async def session_row(ext="s1") -> OnboardingSession:
    async with SessionFactory() as db:
        return await db.scalar(
            select(OnboardingSession).where(OnboardingSession.external_session_id == ext)
        )


async def claim(now) -> list[uuid.UUID]:
    async with SessionFactory() as db:
        return [i for i, _ in await claim_due(db, now)]


# ---- detection ----------------------------------------------------------------------


async def test_idle_session_is_detected_and_planned(client, api_key):
    await stuck_on_otp(client, api_key)

    assert await detect_dropoffs(now=T + timedelta(minutes=10)) == 0  # OTP limit is 15 min
    assert await detect_dropoffs(now=T + timedelta(minutes=16)) == 1

    s = await session_row()
    assert (s.status, s.drop_round) == ("dropped_off", 1)
    plan = await attempts()
    assert [(a.channel, a.status) for a in plan] == [
        ("in_app", "scheduled"),
        ("voice", "scheduled"),
        ("voice", "scheduled"),
    ]
    assert plan[1].scheduled_for == T + timedelta(minutes=46)


async def test_idle_limit_depends_on_step(client, api_key):
    await post(client, api_key, ev("e1", "step_started", "video_kyc", T))
    assert await detect_dropoffs(now=T + timedelta(minutes=30)) == 0  # video KYC: 45 min
    assert await detect_dropoffs(now=T + timedelta(minutes=46)) == 1


async def test_detector_is_idempotent(client, api_key):
    await stuck_on_otp(client, api_key)
    now = T + timedelta(minutes=20)
    assert await detect_dropoffs(now=now) == 1
    assert await detect_dropoffs(now=now) == 0
    assert len(await attempts()) == 3


async def test_concurrent_detectors_plan_once(client, api_key):
    # Two workers running the detector at the same instant: SKIP LOCKED means each
    # session is picked up by exactly one of them.
    for i in range(10):
        await stuck_on_otp(client, api_key, session=f"s{i}", user=f"u{i}")
    now = T + timedelta(minutes=20)
    a, b = await asyncio.gather(detect_dropoffs(now=now), detect_dropoffs(now=now))
    assert a + b == 10
    async with SessionFactory() as db:
        assert len(list(await db.scalars(select(OutreachAttempt.id)))) == 30


async def test_user_coming_back_cancels_pending_outreach(client, api_key):
    await stuck_on_otp(client, api_key)
    await detect_dropoffs(now=T + timedelta(minutes=20))
    await post(
        client, api_key, ev("e3", "step_completed", "aadhaar_otp", T + timedelta(minutes=25))
    )

    assert (await session_row()).status == "in_progress"
    assert {(a.status, a.status_reason) for a in await attempts()} == {
        ("cancelled", "user_resumed")
    }


async def test_stops_chasing_after_max_drop_rounds(client, api_key):
    await stuck_on_otp(client, api_key)
    t = T
    for round_no in (1, 2):
        t += timedelta(minutes=20)
        assert await detect_dropoffs(now=t) == 1
        t += timedelta(minutes=1)
        await post(client, api_key, ev(f"back{round_no}", "step_started", "aadhaar_otp", t))
    assert await detect_dropoffs(now=t + timedelta(minutes=20)) == 0  # max_drop_rounds = 2


# ---- dispatch and delivery ----------------------------------------------------------


async def test_nudge_is_delivered_in_users_language(client, api_key):
    await stuck_on_otp(client, api_key)
    now = T + timedelta(minutes=16)
    await detect_dropoffs(now=now)

    claimed = await claim(now)
    assert len(claimed) == 1  # only the in-app nudge is due; calls come later
    push = FakeChannel()
    assert await deliver_attempt(claimed[0], channels(in_app=push), now=now) == "sent"

    assert push.sent[0].text.startswith("Namaste Ravi! OTP nahi aaya?")
    assert push.sent[0].idempotency_key == f"revive-{claimed[0]}"
    nudge = (await attempts())[0]
    assert (nudge.status, nudge.sent_at, nudge.tries) == ("sent", now, 1)


async def test_completion_after_nudge_counts_as_recovered(client, api_key):
    await stuck_on_otp(client, api_key)
    now = T + timedelta(minutes=16)
    await detect_dropoffs(now=now)
    [nudge_id] = await claim(now)
    await deliver_attempt(nudge_id, channels(), now=now)

    await post(client, api_key, ev("done", "journey_completed", None, now + timedelta(minutes=4)))

    s = await session_row()
    assert s.status == "completed"
    assert s.recovered_by_attempt_id == nudge_id
    # the two calls that hadn't happened yet are cancelled
    assert [a.status for a in await attempts()] == ["sent", "cancelled", "cancelled"]


async def test_dnd_added_after_planning_blocks_the_call(client, api_key):
    await stuck_on_otp(client, api_key)
    await detect_dropoffs(now=T + timedelta(minutes=16))
    # Write DND straight to the table (as a nightly TRAI registry sync would), so only the
    # delivery-time check can catch it.
    async with SessionFactory() as db:
        tenant_id = (await session_row()).tenant_id
        db.add(DndEntry(tenant_id=tenant_id, phone="+919812345678", reason="trai_registry"))
        await db.commit()

    now = T + timedelta(minutes=50)
    ids = await claim(now)
    voice = FakeChannel()
    outcomes = [await deliver_attempt(i, channels(voice=voice), now=now) for i in ids]
    assert sorted(outcomes) == ["sent", "skip"]
    assert voice.sent == []


async def test_dnd_api_skips_scheduled_calls_immediately(client, api_key):
    await stuck_on_otp(client, api_key)
    await detect_dropoffs(now=T + timedelta(minutes=16))
    r = await client.post(
        "/v1/dnd", json={"phone": "+919812345678"}, headers={"X-API-Key": api_key}
    )
    assert r.status_code == 201
    assert r.json()["phone_masked"] == "+91******5678"
    assert [(a.channel, a.status) for a in await attempts()] == [
        ("in_app", "scheduled"),
        ("voice", "skipped"),
        ("voice", "skipped"),
    ]


async def test_call_outside_hours_is_deferred_to_morning(client, api_key):
    await stuck_on_otp(client, api_key)
    await detect_dropoffs(now=T + timedelta(minutes=16))
    call = (await attempts())[1]
    night = datetime(2026, 10, 15, 16, 30, tzinfo=UTC)  # 22:00 IST
    async with SessionFactory() as db:  # pretend it was scheduled late (e.g. retried)
        await db.execute(
            update(OutreachAttempt).where(OutreachAttempt.id == call.id).values(scheduled_for=night)
        )
        await db.commit()

    ids = await claim(night)
    assert call.id in ids
    assert await deliver_attempt(call.id, channels(), now=night) == "defer"
    call = (await attempts())[1]
    assert (call.status, call.status_reason) == ("scheduled", "outside_window")
    assert call.scheduled_for == datetime(2026, 10, 16, 3, 30, tzinfo=UTC)  # Fri 09:00 IST


async def test_retries_then_dead_letter_then_manual_retry(client, api_key):
    headers = {"X-API-Key": api_key}
    policy = (await client.get("/v1/policy", headers=headers)).json()
    policy["max_delivery_tries"] = 2
    assert (await client.put("/v1/policy", json=policy, headers=headers)).status_code == 200

    await stuck_on_otp(client, api_key)
    now = T + timedelta(minutes=16)
    await detect_dropoffs(now=now)
    broken = channels(in_app=FakeChannel(TransientChannelError("503 from push provider")))

    [nudge_id] = await claim(now)
    assert await deliver_attempt(nudge_id, broken, now=now) == "retry"
    nudge = (await attempts())[0]
    assert nudge.status == "scheduled" and nudge.scheduled_for > now  # backed off
    assert "503" in nudge.last_error

    later = nudge.scheduled_for
    assert await claim(later) == [nudge_id]
    assert await deliver_attempt(nudge_id, broken, now=later) == "dead"

    dlq = (await client.get("/v1/outreach", params={"status": "dead"}, headers=headers)).json()
    assert [d["id"] for d in dlq] == [str(nudge_id)]
    assert dlq[0]["status_reason"] == "max_tries"

    r = await client.post(f"/v1/outreach/{nudge_id}/retry", headers=headers)
    assert (r.json()["status"], r.json()["tries"]) == ("scheduled", 0)


async def test_permanent_error_skips_retries(client, api_key):
    await stuck_on_otp(client, api_key, phone="+919800000000")  # ends 0000: "invalid"
    await detect_dropoffs(now=T + timedelta(minutes=16))
    now = T + timedelta(minutes=50)
    ids = await claim(now)
    chans = {Channel.IN_APP: FakeChannel(), Channel.VOICE: SimulatedVoiceChannel()}
    outcomes = sorted([await deliver_attempt(i, chans, now=now) for i in ids])
    assert outcomes == ["dead", "sent"]
    call = (await attempts())[1]
    assert (call.tries, call.status_reason) == (1, "permanent_error")


async def test_hung_provider_times_out_and_retries(client, api_key, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "delivery_timeout_seconds", 0.05)

    class Hangs:
        async def send(self, msg):
            await asyncio.sleep(10)

    await stuck_on_otp(client, api_key)
    now = T + timedelta(minutes=16)
    await detect_dropoffs(now=now)
    [nudge_id] = await claim(now)
    assert await deliver_attempt(nudge_id, channels(in_app=Hangs()), now=now) == "retry"
    assert "TimeoutError" in (await attempts())[0].last_error


async def test_crashed_worker_lease_is_reclaimed(client, api_key):
    await stuck_on_otp(client, api_key)
    now = T + timedelta(minutes=16)
    await detect_dropoffs(now=now)
    [nudge_id] = await claim(now)  # ...and the worker dies before delivering

    async with SessionFactory() as db:
        assert await reap_expired(db, now + timedelta(minutes=1)) == 0  # lease still valid
        assert await reap_expired(db, now + timedelta(minutes=6)) == 1
    nudge = (await attempts())[0]
    assert (nudge.status, nudge.status_reason) == ("scheduled", "lease_expired")
    assert await claim(now + timedelta(minutes=6)) == [nudge_id]


async def test_concurrent_dispatchers_claim_disjoint_sets(client, api_key):
    for i in range(12):
        await stuck_on_otp(client, api_key, session=f"s{i}", user=f"u{i}")
    now = T + timedelta(minutes=16)
    await detect_dropoffs(now=now)
    a, b = await asyncio.gather(claim(now), claim(now))
    assert len(a) + len(b) == 12
    assert not set(a) & set(b)


async def test_session_detail_shows_outreach(client, api_key):
    await stuck_on_otp(client, api_key)
    await detect_dropoffs(now=T + timedelta(minutes=16))
    s = await session_row()
    r = await client.get(f"/v1/sessions/{s.id}", headers={"X-API-Key": api_key})
    body = r.json()
    assert body["drop_round"] == 1
    assert [a["channel"] for a in body["outreach"]] == ["in_app", "voice", "voice"]


async def test_bad_policy_is_rejected(client, api_key):
    r = await client.put(
        "/v1/policy",
        json={"voice_window": {"start": "21:00", "end": "09:00"}},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


async def test_permanent_error_class_is_not_retried():
    # Sanity check on the error hierarchy: a permanent error must not be caught as transient.
    assert not issubclass(PermanentChannelError, TransientChannelError)
