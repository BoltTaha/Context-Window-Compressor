import logging
import time
import json
import gradio as gr
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODEL
from memory_store import MemoryStore
from compressor import maybe_compress
from context_builder import build_chat_history
from token_utils import count_conversation_tokens
from rate_limiter import _limiter, _is_retryable, _is_503, MAX_RETRIES, BASE_BACKOFF

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

client = genai.Client(api_key=GEMINI_API_KEY)
memory = MemoryStore()


# ── helpers ───────────────────────────────────────────────────────────────────

def _send_with_retry(chat_history: list, message: str) -> tuple[str, str]:
    """
    Send a chat message with rate-limit + network-error retry.
    On persistent 503 from primary model, recreates the session on the fallback model.
    Returns (reply_text, model_used).
    """
    def _attempt_model(model: str) -> str:
        """Try sending on a given model. 503 raises immediately; other errors retry."""
        last_exc = None
        for attempt in range(MAX_RETRIES):
            _limiter.wait()
            try:
                session = client.chats.create(model=model, history=chat_history)
                response = session.send_message(message)
                return response.text.strip()
            except Exception as exc:
                # 503 = server overloaded — bail immediately so fallback kicks in fast
                if _is_503(exc):
                    raise
                if _is_retryable(exc) and attempt < MAX_RETRIES - 1:
                    backoff = BASE_BACKOFF * (2 ** attempt)
                    logging.warning(
                        "[%s] Retryable error attempt %d/%d (%s) — retrying in %ds.",
                        model, attempt + 1, MAX_RETRIES, type(exc).__name__, backoff,
                    )
                    time.sleep(backoff)
                    last_exc = exc
                    continue
                last_exc = exc
                break
        raise last_exc

    try:
        reply = _attempt_model(GEMINI_MODEL)
        return reply, GEMINI_MODEL
    except Exception as exc:
        if _is_503(exc):
            logging.warning(
                "Primary model %s returned 503. Switching immediately to fallback %s.",
                GEMINI_MODEL, GEMINI_FALLBACK_MODEL,
            )
            reply = _attempt_model(GEMINI_FALLBACK_MODEL)
            return reply, GEMINI_FALLBACK_MODEL
        raise


def render_memory() -> str:
    """Render all 3 memory tiers as a readable string for the UI panel."""
    snapshot = memory.get_memory_snapshot()
    out = ""

    if snapshot["archive"]:
        out += "🗄️  ARCHIVE  (ultra-compressed)\n"
        out += "─" * 40 + "\n"
        out += snapshot["archive"] + "\n\n"
    else:
        out += "🗄️  ARCHIVE\n"
        out += "─" * 40 + "\n"
        out += "(empty — triggers when 3+ chunks accumulate)\n\n"

    out += f"📦  COMPRESSED CHUNKS  ({len(snapshot['compressed'])} stored)\n"
    out += "─" * 40 + "\n"
    if snapshot["compressed"]:
        for i, chunk in enumerate(snapshot["compressed"]):
            out += f"\n[ Chunk {i+1} │ Turns {chunk['turn_range']} ]\n"
            out += f"Summary : {chunk['summary']}\n"
            facts_str = " │ ".join(chunk["facts"][:5])
            if len(chunk["facts"]) > 5:
                facts_str += f" … +{len(chunk['facts'])-5} more"
            out += f"Facts   : {facts_str}\n"
    else:
        out += "(none yet)\n"

    out += f"\n💬  RECENT TURNS  ({len(snapshot['recent'])} raw)\n"
    out += "─" * 40 + "\n"
    if snapshot["recent"]:
        for turn in snapshot["recent"]:
            preview = turn["content"][:120].replace("\n", " ")
            ellipsis = "…" if len(turn["content"]) > 120 else ""
            out += f"{turn['role'].upper():>10}: {preview}{ellipsis}\n"
    else:
        out += "(empty)\n"

    return out


# ── gradio callbacks ──────────────────────────────────────────────────────────

def chat(user_input: str, history: list):
    global memory

    if not user_input.strip():
        return "", history, render_memory()

    try:
        memory.add_turn("user", user_input)

        compressed = maybe_compress(memory)

        # Print full memory dump to terminal whenever compression fires
        if compressed:
            print("\n" + "="*60)
            print("MEMORY SNAPSHOT (compression fired)")
            print("="*60)
            print(json.dumps(memory.get_memory_snapshot(), indent=2))
            print("="*60 + "\n")

        chat_history = build_chat_history(memory)
        reply, model_used = _send_with_retry(chat_history, user_input)

        memory.add_turn("assistant", reply)

        token_count = count_conversation_tokens(memory.recent_turns)
        compressed_chunks = len(memory.compressed_chunks)
        has_archive = bool(memory.archive_summary)
        fallback_note = f" | ⚠️ Used fallback: `{GEMINI_FALLBACK_MODEL}`" if model_used != GEMINI_MODEL else ""

        stats = (
            f"📊 Recent tokens: {token_count} | "
            f"Compressed chunks: {compressed_chunks} | "
            f"Archive: {'✅' if has_archive else '❌'} | "
            f"Compression fired: {'🗜️ YES' if compressed else 'No'}"
            f"{fallback_note}"
        )

        history.append({"role": "user",      "content": user_input})
        history.append({"role": "assistant",  "content": reply + f"\n\n---\n{stats}"})

    except Exception as exc:
        err_msg = str(exc)
        if "name resolution" in err_msg.lower() or "connecterror" in type(exc).__name__.lower():
            friendly = "⚠️ Network error: could not reach the Gemini API. Check your internet connection and try again."
        elif "429" in err_msg or "resource_exhausted" in err_msg.lower():
            friendly = "⚠️ Rate limit hit: too many requests. Please wait a moment and try again."
        elif "api_key" in err_msg.lower() or "invalid" in err_msg.lower():
            friendly = "⚠️ API key error: check your GEMINI_API_KEY in the .env file."
        else:
            friendly = f"⚠️ Unexpected error: {err_msg}"

        logging.error("Chat error: %s", exc)
        if memory.recent_turns and memory.recent_turns[-1]["role"] == "user":
            memory.recent_turns.pop()
        history.append({"role": "user",     "content": user_input})
        history.append({"role": "assistant", "content": friendly})

    return "", history, render_memory()


def reset():
    global memory
    memory = MemoryStore()
    return [], [], render_memory()


# ── UI ────────────────────────────────────────────────────────────────────────

def build_demo():
    with gr.Blocks(title="Context Window Compressor") as demo:

        gr.Markdown(
            """
            # 🗜️ Context Window Compressor
            Chat with **infinite memory** — powered by hierarchical compression.
            Old turns are automatically summarized and archived so the model never forgets.
            > Model: `gemini-2.5-flash` · Free tier: **10 RPM / 250 RPD** · Rate limiter: ✅ active
            """
        )

        with gr.Row():

            # ── left column: chat ──────────────────────────────────────────
            with gr.Column(scale=2):
                chatbot = gr.Chatbot(height=520, label="Chat")
                msg = gr.Textbox(
                    placeholder="Type your message and press Enter…",
                    label="You",
                    lines=2,
                    autofocus=True,
                )
                with gr.Row():
                    send_btn  = gr.Button("Send",           variant="primary", scale=3)
                    reset_btn = gr.Button("🔄 Reset Memory", variant="stop",    scale=1)

                gr.Markdown(
                    "**Memory tiers:** Recent (full) → Compressed chunks → Archive"
                )

            # ── right column: live memory viewer ───────────────────────────
            with gr.Column(scale=1):
                gr.Markdown("### 🧠 Live Memory Inspector")
                memory_display = gr.Textbox(
                    label="Memory tiers (archive → chunks → recent)",
                    lines=28,
                    interactive=False,
                    value=render_memory(),
                    elem_classes=["memory-panel"],
                )
                refresh_btn = gr.Button("🔄 Refresh Memory View", variant="secondary")

        # ── events ────────────────────────────────────────────────────────
        send_btn.click(
            chat,
            inputs=[msg, chatbot],
            outputs=[msg, chatbot, memory_display],
        )
        msg.submit(
            chat,
            inputs=[msg, chatbot],
            outputs=[msg, chatbot, memory_display],
        )
        reset_btn.click(
            reset,
            inputs=[],
            outputs=[chatbot, chatbot, memory_display],
        )
        refresh_btn.click(
            render_memory,
            inputs=[],
            outputs=[memory_display],
        )

    return demo


if __name__ == "__main__":
    demo = build_demo()
    try:
        demo.launch(
            theme=gr.themes.Soft(),
            css=".memory-panel textarea { font-family: monospace !important; font-size: 12px !important; }",
        )
    except KeyboardInterrupt:
        print("\nServer stopped.")
