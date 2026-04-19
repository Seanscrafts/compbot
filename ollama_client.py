"""
Ollama local LLM client for CompBot.
Used for cheap/simple tasks: evaluation, closed-check, field answering.
Falls back gracefully if Ollama isn't running.
"""

import json
import httpx

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.1:70b-instruct-q4_K_M"
TIMEOUT = 120


def _ask(prompt: str, max_words: int = 200) -> str | None:
    """Send a prompt to Ollama, return response text or None if unavailable."""
    try:
        r = httpx.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception:
        return None


def is_available() -> bool:
    """Return True if Ollama is running and the model is loaded."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=5)
        data = r.json()
        return any(MODEL in m.get("name", "") for m in data.get("models", []))
    except Exception:
        return False


def check_if_closed(page_text: str) -> str | None:
    """
    Return a reason string if the competition looks closed, else None.
    Uses Ollama instead of Claude Haiku.
    """
    prompt = (
        f"Is this competition still open for entries? "
        f"Look for closing dates, 'competition closed', 'winner announced', or past dates.\n\n"
        f"PAGE TEXT:\n{page_text[:2000]}\n\n"
        f"Reply with ONLY one of:\n"
        f"OPEN\n"
        f"CLOSED: <reason in under 10 words>"
    )
    answer = _ask(prompt)
    if not answer:
        return None
    if answer.upper().startswith("CLOSED"):
        return answer[7:].strip() if len(answer) > 7 else "competition is closed"
    return None


def answer_field(label: str, page_text: str) -> str | None:
    """
    Answer a single form question using the live page text.
    Uses Ollama instead of Claude Sonnet.
    """
    prompt = (
        f"Competition entry form. Answer this question based on the page text below.\n"
        f"QUESTION: {label}\n"
        f"PAGE TEXT:\n{page_text[:3000]}\n\n"
        f"Rules: Reply with ONLY the answer (1-6 words, no punctuation, no explanation). "
        f"If the answer is not on the page, reply: UNKNOWN"
    )
    answer = _ask(prompt)
    if not answer or answer.upper() == "UNKNOWN":
        return None
    return answer.strip()


def evaluate_competition(url: str, name: str, html: str, profile: dict,
                          scam_score: int, scam_flags: list) -> dict | None:
    """
    Evaluate a competition using Ollama instead of Claude Sonnet.
    Returns evaluation dict or None if Ollama unavailable.
    """
    prompt = f"""You are evaluating a South African competition entry for this person:
{json.dumps(profile, indent=2)}

Competition: {name}
URL: {url}
Scam score: {scam_score}/100
Scam flags: {', '.join(scam_flags) if scam_flags else 'none'}

PAGE CONTENT (first 3000 chars):
{html[:3000]}

Respond with ONLY valid JSON (no markdown, no explanation):
{{
  "recommendation": "enter" or "review" or "skip",
  "legitimacy_score": 1-10,
  "effort_level": "low" or "medium" or "high",
  "prize_value_zar": integer or null,
  "prize_type": short description,
  "usable_for_you": true or false,
  "entry_method": "form" or "social" or "purchase" or "other",
  "draw_type": "lucky draw" or "skill" or "instant win" or "other",
  "barriers": [],
  "reason": one sentence explanation
}}"""

    answer = _ask(prompt)
    if not answer:
        return None
    try:
        # Strip any markdown fences if present
        clean = answer.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except Exception:
        return None
