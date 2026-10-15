"""Analytics: a small, fully known scenario, so every summary number can be checked exactly."""

from datetime import UTC, datetime, timedelta

from app.services.delivery import deliver_attempt
from app.services.detector import detect_dropoffs
from tests.test_outreach import channels, claim, ev, post

# Recent enough to be inside the default 30-day window.
T = (datetime.now(UTC) - timedelta(days=2)).replace(hour=5, minute=0, second=0, microsecond=0)


async def summary(client, api_key, days=30):
    r = await client.get(f"/v1/analytics/summary?days={days}", headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    return r.json()


async def test_summary_numbers(client, api_key):
    # u1 sails through; u2 is stuck on OTP, nudged, comes back; u3 never leaves video KYC.
    flow = ["signup", "pan_verification", "aadhaar_otp", "video_kyc", "bank_linking"]
    await post(client, api_key, *[
        ev(f"a{i}", "step_completed", s, T + timedelta(minutes=i), session="s1", user="u1")
        for i, s in enumerate(flow)
    ])  # fmt: skip
    await post(client, api_key,
               ev("b1", "step_completed", "pan_verification", T, session="s2", user="u2"),
               ev("b2", "step_failed", "aadhaar_otp", T, session="s2", user="u2",
                  error_code="OTP_NOT_RECEIVED"),
               ev("c1", "step_started", "video_kyc", T, session="s3", user="u3"))  # fmt: skip

    nudge_at = T + timedelta(minutes=50)
    await detect_dropoffs(now=nudge_at)
    for attempt_id in await claim(nudge_at):
        await deliver_attempt(attempt_id, channels(), now=nudge_at)
    await post(client, api_key,
               ev("b3", "journey_completed", None, nudge_at + timedelta(minutes=12),
                  session="s2", user="u2"))  # fmt: skip

    d = await summary(client, api_key)
    assert d["sessions"] == {
        "started": 3,
        "completed": 2,
        "completed_without_help": 1,
        "dropped_off": 2,
        "recovered": 1,
        "recovery_rate": 0.5,
        "median_minutes_to_recover": 12.0,
    }
    funnel = {f["step"]: f["reached"] for f in d["funnel"]}
    assert funnel == {"signup": 3, "pan_verification": 3, "aadhaar_otp": 3, "video_kyc": 3,
                      "bank_linking": 2, "completed": 2}  # fmt: skip
    assert [(x["step"], x["dropped"], x["recovered"]) for x in d["dropoffs_by_step"]] == [
        ("aadhaar_otp", 1, 1),
        ("video_kyc", 1, 0),
    ]
    assert d["recovered_by_channel"] == [{"key": "in_app", "count": 1}]
    assert d["by_language"] == [
        {"language": "hinglish", "dropped": 2, "recovered": 1, "recovery_rate": 0.5}
    ]


async def test_summary_is_per_tenant_and_handles_no_data(client, api_key, other_api_key):
    await post(client, api_key, ev("x", "step_started", "signup", T))
    d = await summary(client, other_api_key)
    assert d["sessions"]["started"] == 0
    assert d["sessions"]["recovery_rate"] == 0.0  # no division by zero
    assert d["sessions"]["median_minutes_to_recover"] is None


async def test_period_filter(client, api_key):
    old = datetime.now(UTC) - timedelta(days=40)
    await post(client, api_key, ev("old", "step_started", "signup", old))
    assert (await summary(client, api_key, days=30))["sessions"]["started"] == 0
    assert (await summary(client, api_key, days=60))["sessions"]["started"] == 1


async def test_dashboard_page_is_served(client):
    r = await client.get("/dashboard")
    assert r.status_code == 200 and "Revive" in r.text
