"""
rag/llm_client.py
─────────────────
Handles all communication with the Ollama LLM API.

Features:
  - Semaphore limits max concurrent LLM calls to MAX_CONCURRENT (default 4)
  - Configurable timeout (default 45s)
  - Automatic retry on transient failures (up to MAX_RETRIES)
  - Structured error responses — never crashes the caller
  - Model is read from Django settings so it can be changed via .env

Swapping models:
  Set OLLAMA_MODEL in .env → "llama3", "mistral", "llama3.2", "gemma2", etc.
  The model must already be pulled: ollama pull <model_name>
"""

import os
import time
import logging
import threading
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# ── Concurrency guard: max 4 simultaneous Ollama calls
# Increase if you have a beefy GPU; decrease if you share a small server
MAX_CONCURRENT = int(os.getenv("OLLAMA_MAX_CONCURRENT", "4"))
_semaphore = threading.Semaphore(MAX_CONCURRENT)

# ── LLM call config
LLM_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT",    "45"))   # seconds per call
MAX_RETRIES    = int(os.getenv("OLLAMA_MAX_RETRIES", "2"))    # retries on network error
RETRY_DELAY    = float(os.getenv("OLLAMA_RETRY_DELAY", "1.5"))  # seconds between retries


def _get_ollama_config():
    """Read Ollama URL + model from Django settings (or env fallback)."""
    try:
        from django.conf import settings
        url   = getattr(settings, "OLLAMA_URL",   "http://localhost:11434/api/generate")
        model = getattr(settings, "OLLAMA_MODEL", "llama3")
    except Exception:
        url   = os.getenv("OLLAMA_URL",   "http://localhost:11434/api/generate")
        model = os.getenv("OLLAMA_MODEL", "llama3")
    return url, model


def call_llm(
    prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 512,
    model_override: Optional[str] = None,
) -> str:
    """
    Send a prompt to Ollama and return the response text.

    Args:
        prompt:         Full formatted prompt string.
        temperature:    0.0 = deterministic, 1.0 = creative.
                        0.3 is good for factual Q&A.
        max_tokens:     Maximum tokens in the response.
        model_override: Override the model for this call only.
                        Useful for testing without changing settings.

    Returns:
        The LLM response string, or a safe error message on failure.
    """
    url, model = _get_ollama_config()
    if model_override:
        model = model_override

    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature":   temperature,
            "num_predict":   max_tokens,
            "stop":          ["User:", "User Question:"],  # prevent role bleed
        },
    }

    # ── Acquire semaphore (blocks if MAX_CONCURRENT calls already running)
    logger.debug(f"Waiting for LLM semaphore (max_concurrent={MAX_CONCURRENT})")
    acquired = _semaphore.acquire(timeout=LLM_TIMEOUT)
    if not acquired:
        logger.warning("LLM semaphore timeout — too many concurrent requests")
        return _offline_fallback("concurrency_timeout")

    try:
        return _call_with_retry(url, payload)
    finally:
        _semaphore.release()


def _call_with_retry(url: str, payload: dict) -> str:
    """Attempt the Ollama HTTP call with up to MAX_RETRIES retries."""
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            if attempt > 0:
                logger.info(f"Retrying Ollama call (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                time.sleep(RETRY_DELAY * attempt)

            response = requests.post(
                url,
                json=payload,
                timeout=LLM_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                result = response.json()
                text   = result.get("response", "").strip()
                if text:
                    logger.debug(f"LLM response ({len(text)} chars) OK")
                    return text
                else:
                    logger.warning("Ollama returned empty response")
                    return _offline_fallback("empty_response")

            elif response.status_code == 404:
                # Model not found — don't retry
                logger.error(f"Ollama model not found: {payload['model']}")
                return _offline_fallback("model_not_found", payload["model"])

            else:
                logger.warning(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
                last_error = f"HTTP {response.status_code}"

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Ollama connection error (attempt {attempt + 1}): {e}")
            last_error = "connection_error"

        except requests.exceptions.Timeout:
            logger.warning(f"Ollama timeout after {LLM_TIMEOUT}s (attempt {attempt + 1})")
            last_error = "timeout"

        except Exception as e:
            logger.error(f"Unexpected LLM error: {e}", exc_info=True)
            last_error = str(e)
            break  # Non-transient error — don't retry

    logger.error(f"All Ollama retries failed. Last error: {last_error}")
    return _offline_fallback(last_error)


def _offline_fallback(reason: str = "unknown", detail: str = "") -> str:
    """Return a helpful human-readable error message."""
    if reason == "connection_error":
        return (
            "Our AI assistant is temporarily offline. "
            "For immediate help, please call +91-98844-00741 (Bengaluru) "
            "or +91-98400-88509 (Chennai), or email info@anupambearings.com."
        )
    elif reason == "timeout":
        return (
            "The response is taking too long. Please try a shorter question, "
            "or contact us directly at info@anupambearings.com."
        )
    elif reason == "model_not_found":
        return (
            f"AI model '{detail}' is not available. "
            "Please contact our team directly at info@anupambearings.com."
        )
    elif reason == "concurrency_timeout":
        return (
            "Our AI assistant is busy right now. "
            "Please try again in a moment, or contact us at +91-98844-00741."
        )
    else:
        return (
            "I encountered an issue processing your request. "
            "Please contact us at info@anupambearings.com or call +91-98844-00741."
        )


def check_ollama_health() -> dict:
    """
    Quick health check for Ollama service.
    Returns {"healthy": bool, "model": str, "error": str|None}
    """
    url, model = _get_ollama_config()
    # Check the tags endpoint (list of models)
    base_url = url.rsplit("/api/", 1)[0]
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            model_available = any(model in m for m in models)
            return {
                "healthy": True,
                "model": model,
                "model_available": model_available,
                "available_models": models,
                "error": None,
            }
        return {"healthy": False, "model": model, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"healthy": False, "model": model, "error": str(e)}
