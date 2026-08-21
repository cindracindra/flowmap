from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from groq import Groq, GroqError
from openai import OpenAI, OpenAIError

Provider = Literal["groq", "together"]

PROVIDERS: tuple[Provider, ...] = ("groq", "together")

# Call sites ask for a capability role, not a model id, so that swapping
# providers doesn't mean rewriting every call. "small" is the workhorse for
# labeling and classification; "large" is reserved for whole-corpus reasoning.
Role = Literal["small", "large"]

_MODELS: dict[Provider, dict[Role, str]] = {
    "groq": {
        "small": "openai/gpt-oss-20b",
        "large": "openai/gpt-oss-120b",
    },
    "together": {
        "small": "openai/gpt-oss-20b",
        "large": "openai/gpt-oss-120b",
    },
}

_TOGETHER_BASE_URL = "https://api.together.xyz/v1"

_KEY_HELP: dict[Provider, str] = {
    "groq": (
        "Get a key from https://console.groq.com/keys and set GROQ_API_KEY "
        "in your environment or .env file."
    ),
    "together": (
        "Get a key from https://api.together.xyz/settings/api-keys and set "
        "TOGETHERAI_API_KEY in your environment or .env file."
    ),
}


class LLMError(RuntimeError):
    """Any provider-side failure, normalised across SDKs."""


@dataclass(frozen=True)
class LLMClient:
    """
    Thin provider-agnostic wrapper over an OpenAI-shaped chat SDK.

    Both supported providers speak the same chat/completions surface, but
    they disagree on the edges: Groq accepts `max_completion_tokens` and the
    Groq-only `include_reasoning` flag, while Together expects `max_tokens`
    and rejects `include_reasoning`. `complete` absorbs that difference so
    the services stay provider-neutral.
    """

    provider: Provider
    sdk: Any

    def model(self, role: Role) -> str:
        return _MODELS[self.provider][role]

    def complete(
        self,
        *,
        role: Role,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float = 0,
        reasoning_effort: str | None = "low",
        json_object: bool = False,
    ) -> str:
        """
        Run one chat completion and return the message content (never None;
        an empty response comes back as ""). Raises LLMError on any provider
        failure, so call sites catch one exception type regardless of SDK.
        """
        kwargs: dict[str, Any] = {
            "model": self.model(role),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_object:
            kwargs["response_format"] = {"type": "json_object"}
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        if self.provider == "groq":
            if max_tokens is not None:
                kwargs["max_completion_tokens"] = max_tokens
            # Groq-only: keeps chain-of-thought out of `content` entirely.
            kwargs["include_reasoning"] = False
        else:
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens

        try:
            response = self.sdk.chat.completions.create(**kwargs)
        except (GroqError, OpenAIError) as exc:
            raise LLMError(f"{self.provider} call failed: {exc}") from exc

        return response.choices[0].message.content or ""


def get_client(provider: Provider = "groq") -> LLMClient:
    """
    Construct a client for `provider`, reading its key from the environment
    (load_dotenv() must have run first -- both SDKs infer the key implicitly
    rather than taking it as an argument here).
    """
    if provider not in PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )
    try:
        if provider == "groq":
            return LLMClient(provider=provider, sdk=Groq())
        api_key = os.environ.get("TOGETHERAI_API_KEY")
        if not api_key:
            raise OpenAIError("TOGETHERAI_API_KEY is not set.")
        return LLMClient(
            provider=provider,
            sdk=OpenAI(api_key=api_key, base_url=_TOGETHER_BASE_URL),
        )
    except (GroqError, OpenAIError) as exc:
        raise RuntimeError(f"{exc} {_KEY_HELP[provider]}") from exc
