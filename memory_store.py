# Three-tier memory system:
# Tier 1 — recent_turns     : last N raw messages (full fidelity)
# Tier 2 — compressed_chunks: summarized older chunks
# Tier 3 — archive          : summary of summaries (oldest history)

class MemoryStore:
    def __init__(self):
        self.recent_turns: list[dict] = []       # list of {role, content}
        self.compressed_chunks: list[dict] = []  # list of {summary, facts, turn_range}
        self.archive_summary: str = ""           # single string, ultra-compressed

    def add_turn(self, role: str, content: str):
        self.recent_turns.append({"role": role, "content": content})

    def get_all_recent(self) -> list[dict]:
        return self.recent_turns

    def push_chunk_to_compressed(self, chunk_summary: str, facts: list[str], turn_range: str):
        self.compressed_chunks.append({
            "summary": chunk_summary,
            "facts": facts,
            "turn_range": turn_range,
        })

    def archive_old_chunks(self, archive_text: str):
        self.archive_summary = archive_text
        self.compressed_chunks = []  # clear after archiving

    def get_memory_snapshot(self) -> dict:
        return {
            "archive": self.archive_summary,
            "compressed": self.compressed_chunks,
            "recent": self.recent_turns,
        }
