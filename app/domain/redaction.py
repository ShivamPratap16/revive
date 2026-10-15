"""Mask personal data in transcripts before they leave our system.

Under India's DPDP Act (and every BFSI client's security review), sending a customer's
Aadhaar or PAN number to a third-party AI service is a serious problem. The LLM doesn't
need them anyway: "my OTP 482913 didn't work" means the same with "[NUMBER]".

Redaction happens in our code, before the request is built, so it can be unit-tested
on its own, and a test can prove the raw values never appear in what we send.

Limits, stated honestly: regexes catch written numbers, not spelled-out ones
("chaar aath do"). Speech-to-text usually writes digits, but it isn't guaranteed.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

# Devanagari digits (०-९) are real digits too. Normalise them first, or "४८२९" slips through.
_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")

# Order matters: the most specific patterns run first, so a 12-digit Aadhaar is labelled
# AADHAAR rather than caught by the generic NUMBER rule.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("AADHAAR", re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)),
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?!\d)")),
    # Anything else with 3+ digits: OTPs, account numbers, partial OTPs ("482...").
    ("NUMBER", re.compile(r"(?<!\d)\d{3,}(?!\d)")),
]


@dataclass
class Redacted:
    text: str
    counts: Counter = field(default_factory=Counter)


def redact(text: str, names: list[str] | None = None) -> Redacted:
    out = text.translate(_DEVANAGARI)
    counts: Counter = Counter()
    for label, pattern in _PATTERNS:
        out, n = pattern.subn(f"[{label}]", out)
        counts[label] += n
    for name in names or []:
        if len(name) >= 3:  # don't blank out "Ra" everywhere
            out, n = re.subn(rf"\b{re.escape(name)}\b", "[NAME]", out, flags=re.IGNORECASE)
            counts["NAME"] += n
    return Redacted(out, +counts)  # +counts drops zero entries


def redact_turns(turns: list[dict], names: list[str] | None = None) -> tuple[list[dict], Counter]:
    total: Counter = Counter()
    result = []
    for turn in turns:
        r = redact(turn["text"], names)
        total += r.counts
        result.append({**turn, "text": r.text})
    return result, total
