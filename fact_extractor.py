import json
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODEL
from rate_limiter import gemini_generate

client = genai.Client(api_key=GEMINI_API_KEY)


def extract_facts(turns: list[dict]) -> list[str]:
    """Extract bullet-point key facts from a conversation chunk."""
    text = "\n".join([f"{t['role']}: {t['content']}" for t in turns])

    prompt = f"""Extract key facts from this conversation as a JSON array of short strings.
Include: names, numbers, decisions, preferences, commitments.
Return ONLY a JSON array, no explanation.

CONVERSATION:
{text}

OUTPUT (JSON array only):"""

    raw = gemini_generate(client, GEMINI_MODEL, prompt, fallback_model=GEMINI_FALLBACK_MODEL)

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return [raw]
