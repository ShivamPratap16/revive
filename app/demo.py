"""Generate a realistic month of demo data through the REAL pipeline.

    python -m app.demo [--users 200] [--seed 7]

Each synthetic user goes through the actual code: events -> ingest service -> detector ->
delivery -> signed webhooks -> analysis. Nothing is written to the tables directly.
Every function takes `now`, so we can "time-travel" through 25 days in seconds.

Call analysis here uses the rule-based analyzer, to save your Gemini quota. The
dashboard's "analysed by" row will therefore say rules.
"""

import argparse
import asyncio
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionFactory, engine
from app.domain.journey import Step
from app.llm.resilience import CircuitBreaker
from app.models import Call, OnboardingSession, OutreachAttempt, Tenant
from app.schemas import IngestEvent
from app.security import generate_api_key, hash_api_key
from app.services.analysis import AnalysisEngine, claim_analyses, process_analysis
from app.services.calls import process_webhook
from app.services.delivery import claim_due, deliver_attempt
from app.services.detector import detect_for_tenant
from app.services.events import ingest_batch
from app.webhook_security import sign
from simulator import scenarios

FLOW = [Step.SIGNUP, Step.PAN_VERIFICATION, Step.AADHAAR_OTP, Step.VIDEO_KYC, Step.BANK_LINKING]
# Where real KYC journeys tend to break: the OTP step most, then video KYC.
STUCK_AT = {Step.AADHAAR_OTP: 40, Step.VIDEO_KYC: 25, Step.PAN_VERIFICATION: 20,
            Step.BANK_LINKING: 10, Step.SIGNUP: 5}  # fmt: skip
ERRORS = {
    Step.AADHAAR_OTP: ["OTP_NOT_RECEIVED", "OTP_NOT_RECEIVED", "OTP_TIMEOUT"],
    Step.PAN_VERIFICATION: ["PAN_MISMATCH"],
    Step.VIDEO_KYC: ["VKYC_AGENT_BUSY", "VKYC_DROPPED"],
    Step.BANK_LINKING: ["PENNY_DROP_FAILED"],
    Step.SIGNUP: ["EMAIL_INVALID"],
}
FIRST = ["Aarav", "Diya", "Kabir", "Isha", "Rohan", "Sneha", "Arjun", "Meera", "Vivek", "Ananya",
         "Rahul", "Pooja", "Imran", "Fatima", "Karthik", "Lakshmi", "Sandeep", "Neha", "Aditya",
         "Riya", "Manoj", "Kavya", "Suresh", "Divya"]  # fmt: skip
LAST = ["Sharma", "Patel", "Reddy", "Iyer", "Khan", "Gupta", "Singh", "Das", "Nair", "Joshi"]
# Chance that a user completes after each kind of contact.
RECOVERY = {"otp_resent_and_done": 0.85, "pan_name_mismatch_confused": 0.6,
            "call_back_evening": 0.5, "language_switch": 0.6, "not_interested_stop_calling": 0.0,
            "no_answer": 0.12, "busy": 0.12, "network_failure": 0.1}  # fmt: skip


class DemoChannel:
    async def send(self, msg) -> str:
        return f"demo_{uuid.uuid4().hex[:10]}"


CHANNELS = {"in_app": DemoChannel(), "voice": DemoChannel()}


def ev(uid: str, sid: str, n: int, type_: str, step, at: datetime, user: dict, **kw):
    return IngestEvent.model_validate({
        "event_id": f"{sid}-{n}", "session_id": sid, "type": type_, "step": step,
        "occurred_at": at.isoformat(), "user": user, **kw,
    })  # fmt: skip


async def ingest(tenant: Tenant, events: list[IngestEvent]) -> None:
    async with SessionFactory() as db:
        t = await db.get(Tenant, tenant.id)
        await ingest_batch(db, t, events, now=datetime.now(UTC))


async def deliver_due(tenant: Tenant, at: datetime) -> list[uuid.UUID]:
    async with SessionFactory() as db:
        claimed = [i for i, _ in await claim_due(db, at, tenant_id=tenant.id)]
    for attempt_id in claimed:
        await deliver_attempt(attempt_id, CHANNELS, now=at)
    return claimed


async def webhook(call: Call, type_: str, at: datetime, data: dict) -> None:
    body = json.dumps({
        "event_id": f"evt_{uuid.uuid4().hex[:12]}", "type": type_, "occurred_at": at.isoformat(),
        "call": {"id": call.provider_call_id, "client_reference": str(call.id)}, "data": data,
    }).encode()  # fmt: skip
    ts = int(at.timestamp())
    async with SessionFactory() as db:
        secret = get_settings().webhook_secret_list[0]
        await process_webhook(db, call.provider, body, sign(secret, ts, body), at)


async def play_call(call: Call, scenario, language: str, name: str, at: datetime) -> datetime:
    """Send the webhooks a provider would send for this outcome. Returns the end time."""
    if scenario.outcome != "completed":
        await webhook(call, "call.ringing", at + timedelta(seconds=2), {})
        end = at + timedelta(seconds=30)
        await webhook(call, f"call.{scenario.outcome}", end, {"reason": scenario.outcome})
        return end
    lang, turns = scenarios.transcript(scenario, language, name)
    duration = max(25, turns[-1]["start_ms"] // 1000 + 5)
    end = at + timedelta(seconds=9 + duration)
    await webhook(call, "call.answered", at + timedelta(seconds=9), {})
    await webhook(call, "call.completed", end, {"duration_seconds": duration})
    await webhook(
        call,
        "call.transcript_ready",
        end + timedelta(seconds=4),
        {"language": lang, "turns": turns},
    )
    return end


async def analyse_all(analysis_engine: AnalysisEngine, at: datetime) -> None:
    async with SessionFactory() as db:
        ids = await claim_analyses(db, at, 50)
    for analysis_id in ids:
        await process_analysis(analysis_id, analysis_engine, now=at)


async def simulate_user(tenant: Tenant, rng: random.Random, i: int, analysis_engine) -> str:
    """One user's story. Afterwards, anything still scheduled for them is cancelled, so a
    later user's time travel doesn't "deliver" this user's leftover calls."""
    outcome = await _story(tenant, rng, i, analysis_engine)
    async with SessionFactory() as db:
        await db.execute(
            update(OutreachAttempt)
            .where(
                OutreachAttempt.tenant_id == tenant.id,
                OutreachAttempt.status == "scheduled",
                OutreachAttempt.session_id.in_(
                    select(OnboardingSession.id).where(
                        OnboardingSession.external_session_id == f"demo_s{i}",
                        OnboardingSession.tenant_id == tenant.id,
                    )
                ),
            )
            .values(status="cancelled", status_reason="demo_story_ended")
        )
        await db.commit()
    return outcome


async def _story(tenant: Tenant, rng: random.Random, i: int, analysis_engine) -> str:
    now = datetime.now(UTC)
    start = now - timedelta(days=rng.uniform(1, 25))
    start = start.replace(hour=rng.randint(4, 15), minute=rng.randint(0, 59))  # 09:30-21:30 IST
    lang = rng.choices(["hinglish", "en", "hi"], weights=[50, 30, 20])[0]
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    uid, sid = f"demo_u{i}", f"demo_s{i}"
    user = {"id": uid, "phone": f"+9198{rng.randint(10_000_000, 99_999_999)}", "name": name,
            "language": lang}  # fmt: skip

    # 1. The journey up to where the user gets stuck (or all the way, for 45% of users).
    stuck = None if rng.random() < 0.45 else rng.choices(
        list(STUCK_AT), weights=list(STUCK_AT.values()))[0]  # fmt: skip
    t, n, events = start, 0, []
    for step in FLOW:
        if step == stuck:
            n += 1
            events.append(ev(uid, sid, n, "step_failed", step, t, user,
                             error_code=rng.choice(ERRORS[step])))  # fmt: skip
            break
        n += 1
        events.append(ev(uid, sid, n, "step_completed", step, t, user))
        t += timedelta(minutes=rng.uniform(1, 6))
    else:
        n += 1
        events.append(ev(uid, sid, n, "journey_completed", None, t, user))
    await ingest(tenant, events)
    if stuck is None:
        return "completed_unaided"

    # 2. The detector notices (the OTP limit is 15 min, video KYC 45 min, so wait an hour).
    t += timedelta(minutes=61)
    await detect_for_tenant(tenant.id, tenant.policy, t)

    # 3. The in-app nudge goes out. Some users come back after just that.
    await deliver_due(tenant, t)
    if rng.random() < 0.18:
        await ingest(tenant, [ev(uid, sid, n + 1, "journey_completed", None,
                                 t + timedelta(minutes=rng.uniform(3, 40)), user)])  # fmt: skip
        return "recovered_by_nudge"

    # 4. The voice call (possibly deferred to calling hours), then the conversation.
    async with SessionFactory() as db:
        session = await db.scalar(select(OnboardingSession).where(
            OnboardingSession.tenant_id == tenant.id,
            OnboardingSession.external_session_id == sid))  # fmt: skip
        voice = await db.scalar(
            select(OutreachAttempt)
            .where(OutreachAttempt.session_id == session.id, OutreachAttempt.channel == "voice",
                   OutreachAttempt.status == "scheduled")
            .order_by(OutreachAttempt.scheduled_for).limit(1)
        )  # fmt: skip
    if voice is None:
        return "no_call"
    at = voice.scheduled_for
    for _ in range(3):  # deliveries can be deferred into calling hours
        await deliver_due(tenant, at)
        async with SessionFactory() as db:
            v = await db.get(OutreachAttempt, voice.id)
        if v.status != "scheduled":
            break
        at = v.scheduled_for
    async with SessionFactory() as db:
        call = await db.scalar(select(Call).where(Call.attempt_id == voice.id)
                               .order_by(Call.created_at.desc()).limit(1))  # fmt: skip
    if call is None:
        return "call_not_placed"

    topic = {Step.AADHAAR_OTP: "otp", Step.PAN_VERIFICATION: "pan",
             Step.VIDEO_KYC: "video_kyc"}.get(stuck)  # fmt: skip
    scenario = scenarios.pick(rng, topic=topic)
    end = await play_call(call, scenario, lang, name.split()[0], at)
    await analyse_all(analysis_engine, end + timedelta(seconds=10))

    # 5. Did the call bring them back?
    if rng.random() < RECOVERY[scenario.name]:
        back = end + timedelta(minutes=rng.uniform(2, 600))
        await ingest(tenant, [ev(uid, sid, n + 1, "journey_completed", None, back, user)])
        return f"recovered_after_{scenario.name}"
    return f"not_recovered_{scenario.name}"


async def main(users: int, seed: int) -> None:
    rng = random.Random(seed)
    key = generate_api_key()
    async with SessionFactory() as db:
        tenant = Tenant(name=f"Demo Fintech (seed {seed})", api_key_hash=hash_api_key(key),
                        api_key_prefix=key[:12])  # fmt: skip
        db.add(tenant)
        await db.commit()
    rules_only = AnalysisEngine(None, None, None, CircuitBreaker())

    from collections import Counter

    outcomes = Counter()
    for i in range(users):
        outcomes[await simulate_user(tenant, rng, i, rules_only)] += 1
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{users} users simulated")
    await engine.dispose()

    print("\nOutcomes:")
    for k, v in outcomes.most_common():
        print(f"  {v:4}  {k}")
    print(f"\nTenant: {tenant.name}\nAPI key (shown once): {key}")
    print("Open http://localhost:8077/dashboard and paste the key.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()
    asyncio.run(main(args.users, args.seed))
