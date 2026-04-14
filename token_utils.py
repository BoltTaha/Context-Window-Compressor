def estimate_tokens(text: str) -> int:
    """Rough estimate: 1 token ≈ 4 chars for English."""
    return len(text) // 4


def count_conversation_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(msg["role"] + ": " + msg["content"])
    return total


def is_over_threshold(messages: list[dict], threshold: int) -> bool:
    return count_conversation_tokens(messages) >= threshold
