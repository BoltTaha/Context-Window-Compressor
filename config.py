import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Token thresholds
MAX_CONTEXT_TOKENS = 8000       # trigger compression before this
RECENT_TURNS_TO_KEEP = 4        # always keep last N turns uncompressed
CHUNK_SIZE_TURNS = 6            # compress this many turns at once

# Models
GEMINI_MODEL          = "gemini-2.5-flash"       # primary  — 10 RPM, 250 RPD
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash-lite"  # fallback — 15 RPM, 1000 RPD (used when primary returns 503)

# Compression levels
LEVEL_1_SUMMARY_TOKENS = 300    # per chunk summary
LEVEL_2_SUMMARY_TOKENS = 100    # archive-level (summary of summaries)
