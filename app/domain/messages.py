"""What we actually say to the user, in their language.

The message names the exact step and, when we know it, the exact problem ("OTP didn't
arrive?"). A specific message converts far better than "complete your KYC".
In Phase 4, Gemini's analysis of past calls can feed better wording back into here.
"""

from app.domain.journey import STEP_INDEX, Step
from app.domain.policy import Channel

STEP_NAMES = {
    "en": {
        Step.SIGNUP: "sign-up",
        Step.PAN_VERIFICATION: "PAN verification",
        Step.AADHAAR_OTP: "Aadhaar OTP",
        Step.VIDEO_KYC: "video KYC",
        Step.BANK_LINKING: "bank account linking",
    },
    "hinglish": {
        Step.SIGNUP: "sign-up",
        Step.PAN_VERIFICATION: "PAN verification",
        Step.AADHAAR_OTP: "Aadhaar OTP",
        Step.VIDEO_KYC: "video KYC",
        Step.BANK_LINKING: "bank account link",
    },
    "hi": {
        Step.SIGNUP: "साइन-अप",
        Step.PAN_VERIFICATION: "पैन वेरिफिकेशन",
        Step.AADHAAR_OTP: "आधार OTP",
        Step.VIDEO_KYC: "वीडियो KYC",
        Step.BANK_LINKING: "बैंक खाता लिंक",
    },
}

# Problem-specific openers for the most common failure codes.
ERROR_HINTS = {
    "en": {
        "OTP_NOT_RECEIVED": "Didn't get the OTP? We can send a fresh one.",
        "OTP_TIMEOUT": "Your OTP expired. A new one takes 10 seconds.",
        "PAN_MISMATCH": "Your PAN name didn't match. We can help you fix it.",
    },
    "hinglish": {
        "OTP_NOT_RECEIVED": "OTP nahi aaya? Hum naya bhej sakte hain.",
        "OTP_TIMEOUT": "OTP expire ho gaya. Naya OTP 10 second mein aa jayega.",
        "PAN_MISMATCH": "PAN par naam match nahi hua. Hum theek karne mein help karenge.",
    },
    "hi": {
        "OTP_NOT_RECEIVED": "OTP नहीं आया? हम नया भेज सकते हैं।",
        "OTP_TIMEOUT": "OTP की समय-सीमा खत्म हो गई। नया OTP 10 सेकंड में आ जाएगा।",
        "PAN_MISMATCH": "पैन पर नाम मेल नहीं खाया। हम इसे ठीक करने में मदद करेंगे।",
    },
}

TEMPLATES = {  # long lines on purpose: message text reads better unwrapped
    (Channel.IN_APP, "en"): "{greet}{hint}You're {left} {steps_word} away from finishing. Continue your {step}.",  # noqa: E501
    (Channel.IN_APP, "hinglish"): "{greet}{hint}Bas {left} step baaki hain. Apna {step} continue karein.",  # noqa: E501
    (Channel.IN_APP, "hi"): "{greet}{hint}बस {left} स्टेप बाकी हैं। अपना {step} पूरा करें।",  # noqa: E501
    # For voice this is the agent's opening line; the conversation itself happens in Phase 3.
    (Channel.VOICE, "en"): "{greet}This is your onboarding assistant. {hint}I can help you finish your {step} in two minutes.",  # noqa: E501
    (Channel.VOICE, "hinglish"): "{greet}Main aapka onboarding assistant bol raha hoon. {hint}Aapka {step} do minute mein complete karwa sakta hoon.",  # noqa: E501
    (Channel.VOICE, "hi"): "{greet}मैं आपका ऑनबोर्डिंग असिस्टेंट बोल रहा हूँ। {hint}आपका {step} दो मिनट में पूरा करवा सकता हूँ।",  # noqa: E501
}  # fmt: skip

GREETING = {"en": "Hi {name}! ", "hinglish": "Namaste {name}! ", "hi": "नमस्ते {name}! "}


def steps_left(step: Step) -> int:
    return STEP_INDEX[Step.COMPLETED] - STEP_INDEX[step]


def render(
    channel: Channel, language: str | None, step: Step, name: str | None, error_code: str | None
) -> str:
    # Unknown or unsupported language: Hinglish is the safest default for Indian fintech users.
    lang = language if language in STEP_NAMES else "hinglish"
    first_name = (name or "").split(" ")[0]
    greet = GREETING[lang].format(name=first_name).replace(" !", "!")  # no name: "Hi! "
    hint = ERROR_HINTS[lang].get(error_code or "", "")
    return TEMPLATES[(channel, lang)].format(
        greet=greet,
        hint=f"{hint} " if hint else "",
        left=steps_left(step),
        steps_word="step" if steps_left(step) == 1 else "steps",
        step=STEP_NAMES[lang][step],
    )
