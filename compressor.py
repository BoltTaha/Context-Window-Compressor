from google import genai
from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_FALLBACK_MODEL,
    LEVEL_1_SUMMARY_TOKENS,
    LEVEL_2_SUMMARY_TOKENS,
    MAX_CONTEXT_TOKENS,
    RECENT_TURNS_TO_KEEP,
    CHUNK_SIZE_TURNS,
)
from memory_store import MemoryStore
from fact_extractor import extract_facts
from token_utils import is_over_threshold
from rate_limiter import gemini_generate

client = genai.Client(api_key=GEMINI_API_KEY)


def summarize_chunk(turns: list[dict]) -> str:
    """Use Gemini to compress a chunk of conversation turns."""
    conversation_text = "\n".join(
        [f"{t['role'].upper()}: {t['content']}" for t in turns]
    )
    prompt = f"""You are a conversation compressor.
Summarize the following conversation segment in under {LEVEL_1_SUMMARY_TOKENS} tokens.
Preserve: key facts, decisions, names, numbers, any commitments made.
Be dense and precise. No filler.

CONVERSATION:
{conversation_text}

COMPRESSED SUMMARY:"""

    return gemini_generate(client, GEMINI_MODEL, prompt, fallback_model=GEMINI_FALLBACK_MODEL)


def archive_compressed_chunks(chunks: list[dict]) -> str:
    """Compress already-compressed summaries into a single archive."""
    combined = "\n\n".join(
        [f"[Turns {c['turn_range']}]: {c['summary']}" for c in chunks]
    )
    prompt = f"""You are compressing already-summarized conversation history into a single archive.
Keep only the most important facts, decisions, and context.
Target: under {LEVEL_2_SUMMARY_TOKENS} tokens.

SUMMARIES:
{combined}

ARCHIVE:"""

    return gemini_generate(client, GEMINI_MODEL, prompt, fallback_model=GEMINI_FALLBACK_MODEL)


def maybe_compress(memory: MemoryStore) -> bool:
    """
    Main compression trigger.
    Returns True if compression happened.
    """
    recent = memory.get_all_recent()

    if not is_over_threshold(recent, MAX_CONTEXT_TOKENS):
        return False

    turns_to_compress = recent[:-RECENT_TURNS_TO_KEEP]
    turns_to_keep = recent[-RECENT_TURNS_TO_KEEP:]

    if not turns_to_compress:
        return False

    for i in range(0, len(turns_to_compress), CHUNK_SIZE_TURNS):
        chunk = turns_to_compress[i:i + CHUNK_SIZE_TURNS]
        turn_range = f"{i + 1}-{i + len(chunk)}"

        summary = summarize_chunk(chunk)
        facts = extract_facts(chunk)

        memory.push_chunk_to_compressed(summary, facts, turn_range)

    if len(memory.compressed_chunks) >= 3:
        archive_text = archive_compressed_chunks(memory.compressed_chunks)
        memory.archive_old_chunks(archive_text)

    memory.recent_turns = turns_to_keep

    return True
