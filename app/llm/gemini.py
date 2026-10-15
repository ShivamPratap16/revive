"""Gemini over plain HTTP (the REST generateContent endpoint).

No SDK on purpose: you can see exactly what goes over the wire, and the error mapping
is ours to decide:
    200 + valid JSON matching the schema  -> CallInsight
    200 but blocked / truncated / bad JSON -> LlmInvalidOutput (retry once, then fallback)
    429, 5xx, timeout, network            -> LlmTransient (back off, retry later)
    400, 401, 403, 404                    -> LlmPermanent (bad key or model: fallback now)
"""

import json
import time
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from app.domain.insights import GEMINI_SCHEMA, SYSTEM_PROMPT, CallInsight


class LlmError(Exception):
    pass


class LlmTransient(LlmError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LlmPermanent(LlmError):
    pass


class LlmInvalidOutput(LlmError):
    pass


@dataclass(frozen=True)
class LlmResult:
    insight: CallInsight
    model: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


def _retry_delay(body: dict) -> float | None:
    """Google puts the wait time in error.details[].retryDelay, e.g. "17s"."""
    for detail in body.get("error", {}).get("details", []):
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


class GeminiAnalyzer:
    BASE_URL = "https://generativelanguage.googleapis.com"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=timeout)

    def request_body(self, prompt: str) -> dict:
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": GEMINI_SCHEMA,
                # Extraction, not creativity: the same transcript should give the same answer.
                "temperature": 0,
                "maxOutputTokens": 1024,
            },
        }

    async def analyze(self, prompt: str) -> LlmResult:
        started = time.perf_counter()
        try:
            r = await self.client.post(
                f"/v1beta/models/{self.model}:generateContent",
                json=self.request_body(prompt),
                # Key in a header, not the URL: URLs end up in proxy and server logs.
                headers={"x-goog-api-key": self.api_key},
            )
        except httpx.TimeoutException as exc:
            raise LlmTransient(f"timeout: {exc!r}") from exc
        except httpx.TransportError as exc:
            raise LlmTransient(f"network: {exc!r}") from exc

        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 429 or r.status_code >= 500:
            raise LlmTransient(f"gemini {r.status_code}", retry_after=_retry_delay(body))
        if r.status_code >= 400:
            message = body.get("error", {}).get("message", r.text[:200])
            raise LlmPermanent(f"gemini {r.status_code}: {message}")

        if block := body.get("promptFeedback", {}).get("blockReason"):
            raise LlmInvalidOutput(f"prompt blocked: {block}")
        candidates = body.get("candidates") or []
        if not candidates:
            raise LlmInvalidOutput("no candidates in response")
        finish = candidates[0].get("finishReason")
        if finish not in (None, "STOP"):
            # MAX_TOKENS means the JSON was cut off half-way; SAFETY means it was withheld.
            raise LlmInvalidOutput(f"generation stopped: {finish}")
        try:
            text = candidates[0]["content"]["parts"][0]["text"]
            insight = CallInsight.model_validate(json.loads(text))
        except (KeyError, IndexError, ValueError, ValidationError) as exc:
            raise LlmInvalidOutput(f"response did not match schema: {exc}") from exc

        usage = body.get("usageMetadata", {})
        return LlmResult(
            insight=insight,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )
