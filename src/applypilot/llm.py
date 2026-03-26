"""
Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY  -> Google Gemini (default: gemini-2.0-flash)
  OPENAI_API_KEY  -> OpenAI (default: gpt-4o-mini)
  LLM_URL         -> Local llama.cpp / Ollama compatible endpoint

LLM_MODEL env var overrides the model name for any provider.

Local LLM extras:
  LLM_LOCAL_MODELS   Comma-separated list of models to cycle through on failure.
                     e.g. "mistral,llama3,phi3"  (overrides LLM_MODEL for local)
  LLM_LOCAL_TIMEOUT  HTTP timeout in seconds for local calls (default: 600)
  LLM_LOCAL_RETRIES  Max retry attempts for local calls (default: 5)
"""

import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cloud provider defaults (unchanged from original)
# ---------------------------------------------------------------------------

_CLOUD_MAX_RETRIES = 8
_CLOUD_TIMEOUT = 240  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10

_GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _detect_provider() -> tuple[str, list[str], str, bool]:
    """Return (base_url, models_list, api_key, is_local) based on environment.

    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.

    Returns:
        base_url:    API base URL string.
        models_list: Ordered list of model names to try (length >= 1).
        api_key:     Bearer token / API key (empty string for unauthenticated local).
        is_local:    True when pointing at a local llama.cpp / Ollama endpoint.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    local_url = os.environ.get("LLM_URL", "")
    model_override = os.environ.get("LLM_MODEL", "")

    if gemini_key and not local_url:
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            [model_override or "gemini-2.0-flash"],
            gemini_key,
            False,
        )

    if openai_key and not local_url:
        return (
            "https://api.openai.com/v1",
            [model_override or "gpt-4o-mini"],
            openai_key,
            False,
        )

    if local_url:
        # LLM_LOCAL_MODELS wins over LLM_MODEL for local provider.
        local_models_env = os.environ.get("LLM_LOCAL_MODELS", "").strip()
        if local_models_env:
            models = [m.strip() for m in local_models_env.split(",") if m.strip()]
        else:
            models = [model_override or "local-model"]
        return (
            local_url.rstrip("/"),
            models,
            os.environ.get("LLM_API_KEY", ""),
            True,
        )

    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL in your environment."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 403 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.

    For local endpoints, supports a list of models (LLM_LOCAL_MODELS) that
    are cycled through on non-rate-limit failures, allowing fallback across
    multiple locally served models.
    """

    def __init__(self, base_url: str, models: list[str], api_key: str, is_local: bool = False) -> None:
        self.base_url = base_url
        self.models = models
        self.api_key = api_key
        self.is_local = is_local

        # Per-provider timeout and retry configuration
        if is_local:
            self._timeout = float(os.environ.get("LLM_LOCAL_TIMEOUT", "600"))
            self._max_retries = int(os.environ.get("LLM_LOCAL_RETRIES", "5"))
        else:
            self._timeout = float(_CLOUD_TIMEOUT)
            self._max_retries = _CLOUD_MAX_RETRIES

        self._client = httpx.Client(timeout=self._timeout)

        # Index into self.models for the current attempt
        self._model_index: int = 0

        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        self._is_gemini: bool = base_url.startswith(_GEMINI_COMPAT_BASE)

    @property
    def model(self) -> str:
        """Current active model name."""
        return self.models[self._model_index % len(self.models)]

    def _next_model(self) -> bool:
        """Advance to the next model in the list.

        Returns:
            True if a new model is available; False if all models exhausted.
        """
        if self._model_index + 1 < len(self.models):
            self._model_index += 1
            return True
        return False

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str]:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 403,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.

        Returns:
            (text, finish_reason) where finish_reason is "stop", "length", or "unknown".
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{_GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            params={"key": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()

        candidate = data["candidates"][0]
        text_out = candidate["content"]["parts"][0]["text"]

        # Map Gemini finish reason to normalised form
        gemini_reason = candidate.get("finishReason", "STOP")
        finish_reason = "length" if gemini_reason == "MAX_TOKENS" else "stop"

        return text_out, finish_reason

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str]:
        """Call the OpenAI-compatible endpoint.

        Returns:
            (text, finish_reason) where finish_reason is "stop", "length",
            "content_filter", or "unknown".
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        # 403 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code == 403 and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        return self._handle_compat_response(resp)

    @staticmethod
    def _handle_compat_response(resp: httpx.Response) -> tuple[str, str]:
        """Parse an OpenAI-compat response.

        Returns:
            (text, finish_reason)
        """
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        text = choice["message"]["content"]
        finish_reason = choice.get("finish_reason") or "unknown"
        return text, finish_reason

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> tuple[str, str]:
        """Send a chat completion request.

        Returns:
            (assistant_text, finish_reason)
            finish_reason is one of: "stop", "length", "content_filter", "unknown"

        For local providers with multiple models (LLM_LOCAL_MODELS), cycles to
        the next model on non-rate-limit failures before retrying.
        """
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        # Reset model index at the start of each top-level chat() call
        self._model_index = 0
        models_tried: set[str] = set()

        for attempt in range(self._max_retries):
            current_model = self.model
            models_tried.add(current_model)

            try:
                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    text, finish_reason = self._chat_native_gemini(messages, temperature, max_tokens)
                else:
                    text, finish_reason = self._chat_compat(messages, temperature, max_tokens)

                if finish_reason == "length":
                    log.warning(
                        "LLM response truncated at token limit (%d tokens) on model '%s'. "
                        "Consider increasing LLM_LOCAL_TIMEOUT, using a model with a larger "
                        "context window, or reducing input size.",
                        max_tokens, current_model,
                    )

                return text, finish_reason

            except _GeminiCompatForbidden:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned 403 for model '%s'. "
                    "Switching to native generateContent API.",
                    self.model,
                )
                self._use_native_gemini = True
                try:
                    text, finish_reason = self._chat_native_gemini(messages, temperature, max_tokens)
                    return text, finish_reason
                except httpx.HTTPStatusError as native_exc:
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: 403 Forbidden. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                if resp.status_code in (429, 503) and attempt < self._max_retries - 1:
                    # Respect Retry-After header if provided (Gemini sends this).
                    retry_after = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-RateLimit-Reset-Requests")
                    )
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except (ValueError, TypeError):
                            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)

                    log.warning(
                        "LLM rate limited (HTTP %s) on model '%s'. Waiting %ds before retry %d/%d.",
                        resp.status_code, current_model, wait, attempt + 1, self._max_retries,
                    )
                    time.sleep(wait)
                    continue

                # Non-rate-limit HTTP error: for local providers, try next model
                if self.is_local and attempt < self._max_retries - 1:
                    if self._next_model():
                        log.warning(
                            "Local model '%s' failed (HTTP %s: %s). Trying next model: '%s'",
                            current_model,
                            resp.status_code,
                            resp.text[:120],
                            self.model,
                        )
                        continue
                raise

            except httpx.TimeoutException:
                if attempt < self._max_retries - 1:
                    # For local providers, try the next model on timeout
                    if self.is_local and self._next_model():
                        log.warning(
                            "Local model '%s' timed out (timeout=%ds). Trying next model: '%s'",
                            current_model, int(self._timeout), self.model,
                        )
                        continue

                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "LLM request timed out on model '%s', retrying in %ds (attempt %d/%d). "
                        "Tip: increase LLM_LOCAL_TIMEOUT for slow local models.",
                        current_model, wait, attempt + 1, self._max_retries,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError(
            f"LLM request failed after {self._max_retries} retries "
            f"(models tried: {', '.join(models_tried)})"
        )

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response text only."""
        text, _ = self.chat([{"role": "user", "content": prompt}], **kwargs)
        return text

    def close(self) -> None:
        self._client.close()


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 403. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat 403: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        base_url, models, api_key, is_local = _detect_provider()
        if is_local:
            timeout = float(os.environ.get("LLM_LOCAL_TIMEOUT", "600"))
            retries = int(os.environ.get("LLM_LOCAL_RETRIES", "5"))
            log.info(
                "LLM provider: local  url: %s  models: [%s]  timeout: %ds  max_retries: %d",
                base_url, ", ".join(models), timeout, retries,
            )
        else:
            log.info("LLM provider: %s  model: %s", base_url, models[0])
        _instance = LLMClient(base_url, models, api_key, is_local)
    return _instance
