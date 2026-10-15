"""Call scenarios for the simulator: how a call ends, and what was said.

The transcripts are written like real Indian fintech calls: Hinglish, short replies,
people who are busy, confused, or annoyed. Phase 4 hands them to Gemini, so they include
the kinds of things worth extracting: a callback time, a specific problem, a request to
stop calling.
"""

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    name: str
    outcome: str  # completed | no_answer | busy | failed
    weight: int
    turns: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # lang -> turns
    # Which KYC step this conversation is about. None = fits any call (busy, not interested).
    topic: str | None = None


A, U = "agent", "user"

SCENARIOS = [
    Scenario(
        "otp_resent_and_done",
        "completed",
        22,
        topic="otp",
        turns={
            "hinglish": [
                (
                    A,
                    "Namaste {name} ji, main KreditBee se onboarding assistant bol raha hoon. Aapka Aadhaar OTP step pending hai.",
                ),
                (U, "Haan, OTP aa hi nahi raha tha. Do baar try kiya."),
                (
                    A,
                    "Samajh gaya. Kabhi kabhi network ki wajah se delay hota hai. Main abhi naya OTP trigger kar deta hoon, aap app khol ke rakhiye.",
                ),
                (U, "Ok, app khula hai."),
                (A, "OTP bhej diya hai. Kya aapko mila?"),
                (U, "Haan aa gaya, 482... haan daal diya. Ho gaya lagta hai."),
                (A, "Bahut badhiya! Ab sirf video KYC baaki hai, usme 3 minute lagenge."),
                (U, "Theek hai, woh main abhi kar leta hoon. Thank you."),
            ],
            "en": [
                (
                    A,
                    "Hi {name}, this is the onboarding assistant from KreditBee. Your Aadhaar OTP step is pending.",
                ),
                (U, "Yes, the OTP never came. I tried twice."),
                (
                    A,
                    "Understood, it's sometimes delayed by the network. I'll send a fresh OTP now. Please keep the app open.",
                ),
                (U, "Okay, it's open."),
                (A, "Sent. Did you get it?"),
                (U, "Yes, got it. Entered it. Looks done."),
                (A, "Great! Only video KYC is left, it takes about 3 minutes."),
                (U, "Okay, I'll do it right now. Thanks."),
            ],
        },
    ),
    Scenario(
        "call_back_evening",
        "completed",
        18,
        {
            "hinglish": [
                (
                    A,
                    "Namaste {name} ji, aapka KYC thoda sa pending hai, do minute mein complete karwa sakta hoon.",
                ),
                (U, "Abhi main office meeting mein hoon yaar, abhi nahi ho payega."),
                (A, "Koi baat nahi. Kab call karun aapko?"),
                (U, "Shaam ko 6 baje ke baad karo, tab free rahunga."),
                (A, "Theek hai, aaj shaam 6 baje ke baad call karta hoon. Dhanyavaad."),
            ],
            "hi": [
                (A, "नमस्ते {name} जी, आपका KYC थोड़ा सा बाकी है, दो मिनट में पूरा करवा सकता हूँ।"),
                (U, "अभी मैं गाड़ी चला रहा हूँ, अभी बात नहीं हो पाएगी।"),
                (A, "कोई बात नहीं। आपको कब कॉल करूँ?"),
                (U, "कल सुबह 11 बजे के बाद कर लेना।"),
                (A, "ठीक है, कल सुबह 11 बजे के बाद कॉल करूँगा। धन्यवाद।"),
            ],
            "en": [
                (A, "Hi {name}, your KYC is almost done, I can help you finish it in two minutes."),
                (U, "I'm in a meeting right now, can't do it."),
                (A, "No problem. When should I call you back?"),
                (U, "Call after 6 in the evening."),
                (A, "Sure, I'll call after 6 pm today. Thank you."),
            ],
        },
    ),
    Scenario(
        "not_interested_stop_calling",
        "completed",
        10,
        {
            "hinglish": [
                (A, "Namaste {name} ji, aapka loan application ka KYC pending hai."),
                (U, "Mujhe loan nahi chahiye ab. Kahin aur se le liya."),
                (A, "Samajh gaya. Kya main poochh sakta hoon kyun?"),
                (U, "Aapka interest rate zyada tha. Aur please mujhe dobara call mat karna."),
                (A, "Bilkul, main note kar leta hoon. Aapko aage call nahi aayega. Dhanyavaad."),
            ],
            "en": [
                (A, "Hi {name}, your loan application KYC is pending."),
                (U, "I don't need the loan anymore, I got one elsewhere."),
                (A, "Understood. May I ask why?"),
                (U, "Your interest rate was higher. And please don't call me again."),
                (A, "Of course, I've noted that. You won't get further calls. Thank you."),
            ],
        },
    ),
    Scenario(
        "pan_name_mismatch_confused",
        "completed",
        10,
        topic="pan",
        turns={
            "hinglish": [
                (A, "Namaste {name} ji, aapke PAN verification mein ek issue aaya tha."),
                (
                    U,
                    "Haan, woh bol raha hai name mismatch. Mera naam PAN pe 'Kumar' hai, app mein maine full naam dala tha.",
                ),
                (
                    A,
                    "Ji, naam bilkul PAN card jaisa hona chahiye. Aap app mein naam edit karke PAN wala naam daal dijiye.",
                ),
                (U, "Accha, edit option kahan hai?"),
                (A, "Profile mein jaake 'Edit details' par tap kijiye."),
                (U, "Ok mil gaya. Raat ko karta hoon, abhi bahar hoon."),
            ],
            "en": [
                (A, "Hi {name}, there was an issue with your PAN verification."),
                (U, "Yes, it says name mismatch. My PAN has a shorter name than what I typed."),
                (A, "The name must match the PAN card exactly. Please edit your name in the app."),
                (U, "Where is the edit option?"),
                (A, "Go to Profile and tap 'Edit details'."),
                (U, "Found it. I'll do it tonight, I'm outside now."),
            ],
        },
    ),
    Scenario(
        "language_switch",
        "completed",
        6,
        topic="video_kyc",
        turns={
            "en": [
                (A, "Hi {name}, this is your onboarding assistant. Your video KYC is pending."),
                (U, "Hindi mein baat karo please, English thoda mushkil hai."),
                (
                    A,
                    "Zaroor. Aapka video KYC pending hai. Isme ek agent aapse video call pe baat karega.",
                ),
                (U, "Usme kya chahiye hoga?"),
                (A, "Bas original PAN card aur achhi roshni. Teen minute lagenge."),
                (U, "Theek hai, kal subah kar lunga."),
            ],
        },
    ),
    Scenario("no_answer", "no_answer", 22),
    Scenario("busy", "busy", 8),
    Scenario("network_failure", "failed", 4),
]

BY_NAME = {s.name: s for s in SCENARIOS}


# How to tell what a call is about from the agent's opening line (any language).
TOPIC_KEYWORDS = {
    "otp": ("otp", "ओटीपी"),
    "pan": ("pan ", "pan verification", "पैन"),
    "video_kyc": ("video kyc", "वीडियो"),
}


def topic_of(opening_line: str | None) -> str | None:
    line = (opening_line or "").lower()
    return next((t for t, words in TOPIC_KEYWORDS.items() if any(w in line for w in words)), None)


def pick(
    rng: random.Random,
    forced: str | None = None,
    topic: str | None = None,
    answered_only: bool = False,
) -> Scenario:
    """Weighted random choice among scenarios that fit the call's topic.

    A call about video KYC must not produce a transcript about an OTP: Phase 4 analyses
    these, and contradictory test data would hide real bugs.
    answered_only: for "magic" test numbers that always pick up (see simulator/main.py).
    """
    if forced:
        return BY_NAME[forced]
    fits = [s for s in SCENARIOS if s.topic is None or s.topic == topic]
    if answered_only:
        fits = [s for s in fits if s.outcome == "completed"]
    return rng.choices(fits, weights=[s.weight for s in fits])[0]


def transcript(scenario: Scenario, language: str | None, name: str) -> tuple[str, list[dict]]:
    """Pick the user's language if the scenario has it, else fall back sensibly."""
    lang = (
        language
        if language in scenario.turns
        else next((fallback for fallback in ("hinglish", "en") if fallback in scenario.turns), None)
    )
    turns, t = [], 0
    for speaker, text in scenario.turns.get(lang, []):
        turns.append({"speaker": speaker, "text": text.format(name=name), "start_ms": t})
        t += 2500 + len(text) * 45  # roughly how long it takes to say
    return lang or "en", turns
