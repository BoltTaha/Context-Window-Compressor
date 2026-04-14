from google.genai import types
from memory_store import MemoryStore


def build_system_prompt(memory: MemoryStore) -> str:
    """
    Assemble the full context from all memory tiers
    into a system prompt for Gemini.
    """
    snapshot = memory.get_memory_snapshot()
    parts = []

    parts.append("You are a helpful assistant with access to compressed conversation history.\n")

    if snapshot["archive"]:
        parts.append(
            f"## ARCHIVED HISTORY (oldest, heavily compressed)\n{snapshot['archive']}\n"
        )

    if snapshot["compressed"]:
        parts.append("## COMPRESSED HISTORY (recent summaries)")
        for chunk in snapshot["compressed"]:
            facts_str = "\n".join([f"  - {f}" for f in chunk["facts"]])
            parts.append(
                f"[Turns {chunk['turn_range']}]\n"
                f"Summary: {chunk['summary']}\n"
                f"Key Facts:\n{facts_str}"
            )

    parts.append("## RECENT CONVERSATION (full fidelity, use this most)")

    return "\n\n".join(parts)


def build_chat_history(memory: MemoryStore) -> list[types.Content]:
    """
    Build the chat history list for the new google-genai SDK.
    Injects system context as the first exchange, then adds all recent turns.
    """
    system_prompt = build_system_prompt(memory)

    history: list[types.Content] = []

    # Inject memory context as the opening exchange
    history.append(types.Content(
        role="user",
        parts=[types.Part(text=system_prompt + "\n\nUnderstood. I will use this memory context.")],
    ))
    history.append(types.Content(
        role="model",
        parts=[types.Part(text="Understood. I have loaded the conversation history and will answer accordingly.")],
    ))

    # Add all recent turns except the very last user message (sent live)
    for turn in memory.get_all_recent()[:-1]:
        role = "user" if turn["role"] == "user" else "model"
        history.append(types.Content(
            role=role,
            parts=[types.Part(text=turn["content"])],
        ))

    return history
