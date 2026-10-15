"""A fake voice provider that behaves like a real one, warts included.

Run:  uvicorn simulator.main:app --port 8099

    POST /v1/calls   (Bearer key, Idempotency-Key)  ->  {"id": "sim_call_..."}

then, in the background, it "plays" the call and POSTs signed webhooks to webhook_url:
    queued -> ringing -> answered -> completed -> transcript_ready -> recording_ready
or      queued -> ringing -> no_answer      (and busy / failed variants)

On purpose, like real providers on a bad day, it also:
  * re-sends some webhooks (duplicates)            SIM_DUPLICATE_RATE
  * swaps the order of some webhooks               SIM_REORDER_RATE
  * retries webhook delivery when we return non-2xx
  * rejects numbers ending 0000 (422) and sometimes fails with 503   SIM_API_ERROR_RATE
Time runs faster than reality: SIM_TIME_SCALE=0.1 makes a 60 s call take 6 s.
"""

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.webhook_security import sign
from simulator import scenarios

log = logging.getLogger("simulator")


class Config:
    def __init__(self):
        # When mounted inside the Revive server (all-in-one mode), fall back to Revive's
        # own settings, so the two sides always agree on the key and the signing secret.
        self.api_key = os.getenv("SIM_API_KEY") or os.getenv("VOICE_API_KEY") or "sim_key_dev"
        revive_secrets = (os.getenv("VOICE_WEBHOOK_SECRETS") or "").split(",")[0].strip()
        self.secret = (
            os.getenv("SIM_WEBHOOK_SECRET") or revive_secrets or "whsec_dev_only_change_me"
        )
        self.time_scale = float(os.getenv("SIM_TIME_SCALE", "0.1"))
        self.duplicate_rate = float(os.getenv("SIM_DUPLICATE_RATE", "0.15"))
        self.reorder_rate = float(os.getenv("SIM_REORDER_RATE", "0.15"))
        self.api_error_rate = float(os.getenv("SIM_API_ERROR_RATE", "0.0"))
        self.force_scenario: str | None = os.getenv("SIM_FORCE_SCENARIO") or None
        self.rng = random.Random(os.getenv("SIM_SEED"))


class CreateCall(BaseModel):
    to: str
    client_reference: str
    language: str | None = None
    opening_line: str | None = None
    webhook_url: str


app = FastAPI(title="Voice provider simulator")
app.state.config = Config()
app.state.calls = {}  # call id -> details
app.state.idempotency = {}  # idempotency key -> response body
app.state.tasks = set()
app.state.webhook_client = None  # tests inject a client wired straight to the Revive app


@app.post("/v1/calls", status_code=201)
async def create_call(
    body: CreateCall,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    cfg: Config = app.state.config
    if authorization != f"Bearer {cfg.api_key}":
        raise HTTPException(401, "bad api key")

    # Same key as an earlier request -> same answer, and no second call is placed.
    if idempotency_key and idempotency_key in app.state.idempotency:
        return app.state.idempotency[idempotency_key]

    if not re.fullmatch(r"\+91[6-9]\d{9}", body.to) or body.to.endswith("0000"):
        raise HTTPException(422, "invalid destination number")
    if cfg.rng.random() < cfg.api_error_rate:
        raise HTTPException(503, "temporarily overloaded")

    call_id = f"sim_call_{uuid.uuid4().hex[:12]}"
    scenario = scenarios.pick(cfg.rng, cfg.force_scenario, scenarios.topic_of(body.opening_line))
    app.state.calls[call_id] = {"request": body.model_dump(), "scenario": scenario.name}
    response = {"id": call_id, "status": "queued"}
    if idempotency_key:
        app.state.idempotency[idempotency_key] = response

    task = asyncio.create_task(_play_call(call_id, body, scenario))
    app.state.tasks.add(task)  # keep a reference so the task isn't garbage-collected
    task.add_done_callback(app.state.tasks.discard)
    log.info("call accepted", extra={"call_id": call_id, "scenario": scenario.name})
    return response


@app.get("/v1/calls/{call_id}")
async def get_call(call_id: str):
    if call_id not in app.state.calls:
        raise HTTPException(404, "no such call")
    return app.state.calls[call_id]


def _timeline(scenario: scenarios.Scenario, req: CreateCall) -> list[tuple[float, str, dict]]:
    """(seconds after start, event type, data) for the whole call."""
    if scenario.outcome == "busy":
        return [(0, "call.queued", {}), (3, "call.busy", {"reason": "user_busy"})]
    if scenario.outcome == "failed":
        return [(0, "call.queued", {}), (1, "call.failed", {"reason": "network_error"})]
    if scenario.outcome == "no_answer":
        return [
            (0, "call.queued", {}),
            (2, "call.ringing", {}),
            (32, "call.no_answer", {"reason": "no_answer"}),
        ]

    first_name = (req.opening_line or "").split("!")[0].split(" ")[-1] or "ji"
    language, turns = scenarios.transcript(scenario, req.language, first_name)
    talk = max(20, turns[-1]["start_ms"] // 1000 + 4) if turns else 20
    return [
        (0, "call.queued", {}),
        (2, "call.ringing", {}),
        (9, "call.answered", {}),
        (9 + talk, "call.completed", {"duration_seconds": talk}),
        (9 + talk + 4, "call.transcript_ready", {"language": language, "turns": turns}),
        (
            9 + talk + 5,
            "call.recording_ready",
            {"url": f"https://recordings.sim.local/{uuid.uuid4().hex}.mp3"},
        ),
    ]


async def _play_call(call_id: str, req: CreateCall, scenario: scenarios.Scenario) -> None:
    cfg: Config = app.state.config
    start = datetime.now(UTC)
    events = []
    for offset, type_, data in _timeline(scenario, req):
        events.append(
            {
                "event_id": f"evt_{uuid.uuid4().hex[:16]}",
                "type": type_,
                "occurred_at": (start + timedelta(seconds=offset)).isoformat(),
                "call": {"id": call_id, "client_reference": req.client_reference},
                "data": data,
            }
        )

    # Chaos: swap a neighbouring pair, so e.g. "completed" can arrive before "answered".
    if len(events) > 2 and cfg.rng.random() < cfg.reorder_rate:
        i = cfg.rng.randrange(1, len(events) - 1)
        events[i], events[i + 1] = events[i + 1], events[i]

    elapsed = 0.0
    for event in events:
        offset = (datetime.fromisoformat(event["occurred_at"]) - start).total_seconds()
        await asyncio.sleep(max(0.0, offset - elapsed) * cfg.time_scale)
        elapsed = max(elapsed, offset)
        await _deliver(req.webhook_url, event)
        if cfg.rng.random() < cfg.duplicate_rate:
            await _deliver(req.webhook_url, event)  # the same event_id, again


async def _deliver(url: str, event: dict, attempts: int = 4) -> None:
    """POST one signed webhook, retrying with backoff on failure (as real providers do)."""
    cfg: Config = app.state.config
    client = app.state.webhook_client or httpx.AsyncClient(timeout=5)
    body = json.dumps(event).encode()
    try:
        for attempt in range(1, attempts + 1):
            headers = {
                "content-type": "application/json",
                "x-voice-signature": sign(cfg.secret, int(time.time()), body),
            }
            try:
                r = await client.post(url, content=body, headers=headers)
                if r.status_code < 300:
                    return
                log.warning("webhook rejected", extra={"status": r.status_code, "body": r.text})
                if 400 <= r.status_code < 500:
                    return  # the receiver says it's our fault: retrying won't help
            except httpx.HTTPError as exc:
                log.warning("webhook delivery failed", extra={"error": repr(exc)})
            await asyncio.sleep(2 ** (attempt - 1) * cfg.time_scale)
    finally:
        if app.state.webhook_client is None:
            await client.aclose()
