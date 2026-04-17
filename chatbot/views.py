"""
chatbot/views.py
────────────────
RAG-powered chat API endpoints.

Endpoints:
  POST /api/chat/              → sync mode (waits for LLM, good for dev)
  POST /api/chat/async/        → async mode (returns task_id immediately)
  GET  /api/chat/result/<id>/  → poll for async result
  GET  /api/chat/health/       → Ollama + FAISS health check
  GET  /api/chat/stats/        → RAG index statistics
"""

import json
import uuid
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.conf import settings
from django.core.cache import cache

from contact.models import ChatMessage

logger = logging.getLogger(__name__)

# ── Sync timeout — if RAG pipeline takes longer, return error
SYNC_TIMEOUT = 60  # seconds


# ─────────────────────────────────────────────────────────────────────────────
# SYNCHRONOUS ENDPOINT (existing /api/chat/ — backward compatible)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def chat(request):
    """
    Synchronous RAG chat endpoint.
    Replaces the old Ollama-only chatbot/views.py chat() function.

    Request JSON:
        {"message": "...", "history": [...]}

    Response JSON:
        {"success": bool, "reply": str, "sources": [...], "cached": bool}

    Backward compatible: same URL (/api/chat/), same request/response shape.
    Now upgraded with: FAISS retrieval → structured prompt → Ollama → Redis cache.
    """
    try:
        data         = json.loads(request.body)
        user_message = data.get("message", "").strip()
        history      = data.get("history", [])

        if not user_message:
            return JsonResponse(
                {"success": False, "reply": "Please enter a message."},
                status=400,
            )

        # Get or create session ID
        session_id = request.session.get("chat_session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
            request.session["chat_session_id"] = session_id

        # ── Run the full RAG pipeline inline (synchronous)
        from chatbot.tasks import run_rag_pipeline
        result = run_rag_pipeline(
            query=user_message,
            history=history,
            session_id=session_id,
        )

        return JsonResponse({
            "success": True,
            "reply":        result["reply"],
            "sources":      result.get("sources", []),
            "cached":       result.get("cached", False),
            "chunks_found": result.get("chunks_found", 0),
        })

    except json.JSONDecodeError:
        return JsonResponse(
            {"success": False, "reply": "Invalid request format."},
            status=400,
        )
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}", exc_info=True)
        return JsonResponse(
            {
                "success": False,
                "reply": (
                    "I encountered an error. Please contact us at "
                    "info@anupambearings.com or call +91-98844-00741."
                ),
            },
            status=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ASYNCHRONOUS ENDPOINT (non-blocking — returns task_id immediately)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def chat_async(request):
    """
    Async RAG chat endpoint — enqueues Celery task, returns task_id.

    Request JSON:
        {"message": "...", "history": [...]}

    Response JSON:
        {"success": true, "task_id": "abc-123", "status": "queued"}

    Then poll: GET /api/chat/result/<task_id>/
    """
    try:
        data         = json.loads(request.body)
        user_message = data.get("message", "").strip()
        history      = data.get("history", [])

        if not user_message:
            return JsonResponse(
                {"success": False, "reply": "Please enter a message."},
                status=400,
            )

        session_id = request.session.get("chat_session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
            request.session["chat_session_id"] = session_id

        # Enqueue Celery task
        from chatbot.tasks import run_rag_pipeline
        task = run_rag_pipeline.delay(
            query=user_message,
            history=history,
            session_id=session_id,
        )

        return JsonResponse({
            "success": True,
            "task_id": task.id,
            "status":  "queued",
        })

    except Exception as e:
        logger.error(f"Async chat error: {e}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_GET
def chat_result(request, task_id):
    """
    Poll for the result of an async chat task.

    Response JSON (pending):
        {"status": "pending"}

    Response JSON (complete):
        {"status": "success", "reply": str, "sources": [...]}

    Response JSON (failed):
        {"status": "failure", "reply": "fallback message"}
    """
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)

        if result.state == "PENDING":
            return JsonResponse({"status": "pending"})

        elif result.state == "SUCCESS":
            data = result.result
            return JsonResponse({
                "status":       "success",
                "reply":        data.get("reply", ""),
                "sources":      data.get("sources", []),
                "cached":       data.get("cached", False),
                "chunks_found": data.get("chunks_found", 0),
            })

        elif result.state == "FAILURE":
            return JsonResponse({
                "status": "failure",
                "reply": (
                    "The request failed. Please contact us at "
                    "info@anupambearings.com or call +91-98844-00741."
                ),
            })

        else:
            return JsonResponse({"status": result.state.lower()})

    except Exception as e:
        logger.error(f"Result fetch error: {e}")
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH + STATS ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@require_GET
def chat_health(request):
    """
    Health check for all RAG components.

    GET /api/chat/health/

    Response:
        {
            "ollama": {"healthy": bool, "model": str, ...},
            "faiss":  {"exists": bool, "total_vectors": int, ...},
            "redis":  {"healthy": bool},
            "overall": "healthy" | "degraded" | "offline"
        }
    """
    from rag.llm_client import check_ollama_health
    from rag.retriever import get_index_stats

    ollama_health = check_ollama_health()
    faiss_stats   = get_index_stats()

    # Check Redis
    redis_healthy = False
    try:
        cache.set("health_check_ping", "pong", timeout=5)
        redis_healthy = cache.get("health_check_ping") == "pong"
    except Exception:
        pass

    # Overall health
    if ollama_health["healthy"] and faiss_stats["exists"] and redis_healthy:
        overall = "healthy"
    elif ollama_health["healthy"]:
        overall = "degraded"  # LLM works but RAG or cache is down
    else:
        overall = "offline"

    return JsonResponse({
        "ollama":  ollama_health,
        "faiss":   faiss_stats,
        "redis":   {"healthy": redis_healthy},
        "overall": overall,
    })


@require_GET
def chat_stats(request):
    """
    RAG index statistics.
    GET /api/chat/stats/
    """
    from rag.retriever import get_index_stats
    return JsonResponse(get_index_stats())
