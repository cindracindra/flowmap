"""
Groq connection only -- constructs a client and surfaces a clear error if
the API key isn't configured. Parallel to joern/joern_session.py's role:
this knows nothing about topics, clusters, or prompts, so it stays
reusable independent of what flowmap specifically uses Groq for. The
domain-specific calls that use this client (label_cluster,
discover_topics_whole_corpus) live in service/topic.py instead, next to
extract_class_documents -- the same "one round-trip against an external
system" role, just backed by Groq instead of Joern.
"""

from __future__ import annotations

from groq import Groq, GroqError


def get_client() -> Groq:
    """
    Constructs a Groq client.
    """
    try:
        return Groq()
    except GroqError as e:
        raise RuntimeError(
            f"{e} Get a key from https://console.groq.com/keys and set "
            f"GROQ_API_KEY in your environment or .env file."
        ) from e
