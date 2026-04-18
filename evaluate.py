"""
Multi-dimensional competition evaluator for CompBot.
Uses Claude to score across 5 dimensions and return a structured recommendation.
Scam score from scam.py feeds into legitimacy_score.
"""

import json
import re
import time

import anthropic
from rich.console import Console

console = Console()
CLAUDE_MODEL = "claude-sonnet-4-6"

EVALUATION_PROMPT = """You are evaluating a South African competition for a specific user. Analyze the page and return a structured JSON evaluation across 5 dimensions.

USER PROFILE:
- Location: Cape Town, Western Cape, South Africa
- Age: {age}, Gender: {gender}
- Wants practical prizes: cash, vouchers, electronics, food, household goods, experiences

EXISTING RULE-BASED SCAM ANALYSIS (0-100 score, lower = safer):
Scam score: {scam_score}/100
Flags found: {scam_flags}

COMPETITION URL: {url}
COMPETITION NAME: {competition_name}

PAGE CONTENT:
{html}

Return ONLY this JSON (no markdown, no explanation):
{{
  "legitimacy_score": <integer 1-10, 10=fully legitimate>,
  "effort_level": "<low|medium|high>",
  "prize_value_zar": <estimated rand value as integer, null if truly unknown>,
  "prize_type": "<cash|voucher|product|experience|travel|vehicle|unknown>",
  "usable_for_you": <true|false>,
  "entry_method": "<form_only|photo|essay|purchase|social_media|combination>",
  "draw_type": "<random|skill|first_correct|unknown>",
  "closes": "<YYYY-MM-DD or null>",
  "barriers": [<zero or more of: "account_required", "social_media_follow", "photo_upload", "essay", "purchase", "mobile_verify", "captcha", "share_required">],
  "recommendation": "<enter|skip|review>",
  "reason": "<1-2 sentence plain English explanation>"
}}

SCORING RULES:
legitimacy_score — start at 10, then deduct:
  -2 if no T&Cs link visible
  -2 if sponsor brand is unrecognisable or absent
  -3 if ID number or banking details requested
  -2 if no contact info or SSL signals
  -2 if scam_score 40-59
  -4 if scam_score 60-79
  -6 if scam_score >= 80
  Minimum 1.

effort_level:
  low   = simple form, under 2 minutes, no purchase
  medium = requires a photo, creative answer, or account signup
  high  = essay, purchase required, or multiple complex steps

recommendation logic:
  enter  — legitimacy >= 7 AND effort != high AND prize_value_zar > 0 AND usable_for_you = true AND no "purchase" barrier
  skip   — legitimacy <= 4 OR scam_score >= 60 OR (effort = high AND prize_value_zar < 2000) OR usable_for_you = false
  review — everything else (human decides)
"""


def evaluate(
    url: str,
    competition_name: str | None,
    html: str,
    profile: dict,
    scam_score: int,
    scam_flags: list,
) -> dict:
    """Call Claude to evaluate a competition across 5 dimensions."""
    client = anthropic.Anthropic()

    prompt = (
        EVALUATION_PROMPT
        .replace("{age}", str(profile.get("age", "unknown")))
        .replace("{gender}", profile.get("gender", "unknown"))
        .replace("{scam_score}", str(scam_score))
        .replace("{scam_flags}", ", ".join(scam_flags) if scam_flags else "none")
        .replace("{url}", url)
        .replace("{competition_name}", competition_name or "Unknown")
        .replace("{html}", html[:20000])
    )

    console.print("[dim]Evaluating competition with Claude...[/dim]")
    start = time.time()

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    elapsed = time.time() - start
    raw = response.content[0].text.strip()
    console.print(f"[dim]Evaluation done in {elapsed:.1f}s ({response.usage.input_tokens} in / {response.usage.output_tokens} out)[/dim]")

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print("[yellow]Could not parse evaluation JSON -- using safe defaults.[/yellow]")
        result = {
            "legitimacy_score": max(1, 10 - scam_score // 10),
            "effort_level": "unknown",
            "prize_value_zar": None,
            "prize_type": "unknown",
            "usable_for_you": None,
            "entry_method": "unknown",
            "draw_type": "unknown",
            "closes": None,
            "barriers": [],
            "recommendation": "review",
            "reason": "Could not auto-evaluate -- review manually.",
        }

    return result


REC_COLOUR = {"enter": "green", "skip": "red", "review": "yellow"}
REC_LABEL  = {"enter": "ENTER", "skip": "SKIP", "review": "REVIEW"}


def format_recommendation(rec: str) -> str:
    colour = REC_COLOUR.get(rec, "white")
    label  = REC_LABEL.get(rec, rec.upper())
    return f"[{colour}]{label}[/{colour}]"
