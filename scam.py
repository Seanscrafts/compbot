"""
SA-specific scam scoring for CompBot.
Rule-based — no extra API call, uses data already extracted by Claude.
"""

import re
from dataclasses import dataclass, field

SUSPICIOUS_DOMAINS = ["blogspot", "wix", "weebly", "wordpress.com", "tripod", "freewebs", "yolasite"]

RISKY_FIELD_KEYS = ["id_number", "banking", "bank", "account", "password", "pin"]

SMS_PATTERNS = [re.compile(p, re.I) for p in [
    r"sms\s+to\s+\d+",
    r"text\s+to\s+\d+",
    r"whatsapp\s+to\s+enter",
    r"send\s+an?\s+sms",
    r"R\d+\s+per\s+sms",
    r"premium\s+rate",
]]

PURCHASE_PATTERNS = [re.compile(p, re.I) for p in [
    r"purchase\s+required",
    r"buy\s+to\s+enter",
    r"valid\s+receipt",
    r"proof\s+of\s+purchase",
]]

SHARE_PATTERNS = [re.compile(p, re.I) for p in [
    r"share\s+to\s+win",
    r"forward\s+to\s+\d+",
    r"tag\s+\d+\s+friends",
]]


@dataclass
class ScamResult:
    score: int
    flags: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        if self.score <= 25:
            return "low"
        elif self.score <= 55:
            return "medium"
        return "high"

    @property
    def label(self) -> str:
        return {"low": "LOW risk", "medium": "MEDIUM risk", "high": "HIGH risk"}[self.level]

    @property
    def colour(self) -> str:
        return {"low": "green", "medium": "yellow", "high": "red"}[self.level]


def score(url: str, extraction: dict) -> ScamResult:
    """Score a competition for scam likelihood. Returns ScamResult."""
    points = 0
    flags = []

    warnings = [w.lower() for w in extraction.get("warnings", [])]
    fields = extraction.get("fields", [])
    full_text = " ".join(warnings)

    # --- Domain checks ---
    for bad in SUSPICIOUS_DOMAINS:
        if bad in url.lower():
            points += 25
            flags.append(f"Suspicious hosting domain ({bad})")

    # --- Field-level checks ---
    for f in fields:
        key = (f.get("mapped_profile_key") or "").lower()
        label = (f.get("label") or "").lower()
        combined = key + " " + label
        if "id_number" in key or "id number" in label or "id no" in label:
            points += 40
            flags.append("ID number requested")
        for risky in ["banking", "bank account", "account number", "password", "pin"]:
            if risky in combined:
                points += 50
                flags.append(f"Sensitive field: {risky}")

    # --- Warning text checks ---
    for pattern in SMS_PATTERNS:
        if pattern.search(full_text):
            points += 30
            flags.append("SMS/premium rate entry method")
            break

    for pattern in PURCHASE_PATTERNS:
        if pattern.search(full_text):
            points += 35
            flags.append("Purchase required to enter")
            break

    for pattern in SHARE_PATTERNS:
        if pattern.search(full_text):
            points += 20
            flags.append("Share/forward to win requirement")
            break

    # --- Missing info ---
    if not extraction.get("closing_date"):
        points += 10
        flags.append("No closing date listed")

    if not extraction.get("competition_name"):
        points += 10
        flags.append("No competition name found")

    return ScamResult(score=min(points, 100), flags=flags)
