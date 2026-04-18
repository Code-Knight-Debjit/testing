"""
chatbot/views.py  — RAG-powered chat with async polling + rate limiting + validation
"""
import json, uuid, logging
from django.http      import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.conf      import settings
from django.core.cache import cache

from core.validators import validate_chat
from contact.models  import ChatMessage

logger = logging.getLogger(__name__)

SYNC_TIMEOUT = 60


# ── helpers ────────────────────────────────────────────────────────────────

def _get_or_create_session(request):
    sid = request.session.get('chat_session_id')
    if not sid:
        sid = str(uuid.uuid4())
        request.session['chat_session_id'] = sid
    return sid


def _rate_key(request):
    """Per-IP rate-limit key (falls back to session if IP unknown)."""
    ip = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR', 'unknown')
    )
    return f'chat_rl:{ip}'


def _check_rate_limit(request, limit=20, window=60):
    """
    Simple Redis-backed rate limiter: max `limit` requests per `window` seconds.
    Returns True if limit exceeded.
    """
    key   = _rate_key(request)
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, timeout=window)
    return False


# ── SYNC endpoint (backward-compatible — frontend currently uses this) ──────

@csrf_exempt
@require_POST
def chat(request):
    """
    POST /api/chat/
    Synchronous RAG chat with:
      • Rate limit: 20 req / 60 s per IP
      • Input validation
      • Redis query caching
      • FAISS retrieval (if index exists)
      • Ollama LLM
      • DB persistence
    """
    # Rate limit
    if _check_rate_limit(request, limit=20, window=60):
        return JsonResponse(
            {'success': False, 'reply': 'Too many requests. Please wait a moment.'},
            status=429,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'reply': 'Invalid JSON payload.'}, status=400)

    cleaned, errors = validate_chat(data)
    if errors:
        first_err = next(iter(errors.values()))
        return JsonResponse({'success': False, 'reply': first_err, 'errors': errors}, status=400)

    session_id = _get_or_create_session(request)

    from chatbot.tasks import run_rag_pipeline
    try:
        result = run_rag_pipeline(
            query=cleaned['message'],
            history=cleaned['history'],
            session_id=session_id,
        )
    except Exception as e:
        logger.error(f'run_rag_pipeline failed: {e}', exc_info=True)
        result = {
            'reply': (
                'I encountered an error. Please contact us at '
                'info@anupambearings.com or call +91-98844-00741.'
            ),
            'sources': [], 'cached': False, 'chunks_found': 0,
        }

    return JsonResponse({
        'success':      True,
        'reply':        result['reply'],
        'sources':      result.get('sources', []),
        'cached':       result.get('cached', False),
        'chunks_found': result.get('chunks_found', 0),
    })


# ── ASYNC endpoint — enqueue Celery task, return task_id immediately ─────────

@csrf_exempt
@require_POST
def chat_async(request):
    """
    POST /api/chat/async/
    Enqueues a Celery task and returns {task_id} immediately.
    Frontend polls GET /api/chat/result/<task_id>/ for the answer.
    Rate limit: 10 req / 60 s per IP (stricter than sync).
    """
    if _check_rate_limit(request, limit=10, window=60):
        return JsonResponse(
            {'success': False, 'reply': 'Too many requests. Please wait a moment.'},
            status=429,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'reply': 'Invalid JSON payload.'}, status=400)

    cleaned, errors = validate_chat(data)
    if errors:
        first_err = next(iter(errors.values()))
        return JsonResponse({'success': False, 'reply': first_err, 'errors': errors}, status=400)

    session_id = _get_or_create_session(request)

    from chatbot.tasks import run_rag_pipeline
    task = run_rag_pipeline.delay(
        query=cleaned['message'],
        history=cleaned['history'],
        session_id=session_id,
    )

    return JsonResponse({'success': True, 'task_id': task.id, 'status': 'queued'})


@require_GET
def chat_result(request, task_id):
    """
    GET /api/chat/result/<task_id>/
    Poll for the result of an async chat task.
    States: pending | processing | success | failure
    """
    # Validate task_id format (UUID only — prevents probing arbitrary task IDs)
    try:
        uuid.UUID(task_id)
    except ValueError:
        return JsonResponse({'status': 'error', 'reply': 'Invalid task ID.'}, status=400)

    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)

        if result.state == 'PENDING':
            return JsonResponse({'status': 'pending'})
        elif result.state == 'STARTED':
            return JsonResponse({'status': 'processing'})
        elif result.state == 'SUCCESS':
            d = result.result or {}
            return JsonResponse({
                'status':       'success',
                'reply':        d.get('reply', ''),
                'sources':      d.get('sources', []),
                'cached':       d.get('cached', False),
                'chunks_found': d.get('chunks_found', 0),
            })
        elif result.state == 'FAILURE':
            return JsonResponse({
                'status': 'failure',
                'reply': (
                    'The request failed. Please contact us at '
                    'info@anupambearings.com or call +91-98844-00741.'
                ),
            })
        else:
            return JsonResponse({'status': result.state.lower()})
    except Exception as e:
        logger.error(f'chat_result error: {e}', exc_info=True)
        return JsonResponse({'status': 'error', 'reply': str(e)}, status=500)


# ── HEALTH + STATS ────────────────────────────────────────────────────────────

@require_GET
def chat_health(request):
    """GET /api/chat/health/ — overall system health."""
    from rag.llm_client import check_ollama_health
    from rag.retriever  import get_index_stats

    ollama = check_ollama_health()
    faiss  = get_index_stats()

    redis_ok = False
    try:
        cache.set('_health_ping', 'pong', 5)
        redis_ok = cache.get('_health_ping') == 'pong'
    except Exception:
        pass

    if ollama['healthy'] and faiss['exists'] and redis_ok:
        overall = 'healthy'
    elif ollama['healthy']:
        overall = 'degraded'
    else:
        overall = 'offline'

    return JsonResponse({
        'ollama':  ollama,
        'faiss':   faiss,
        'redis':   {'healthy': redis_ok},
        'overall': overall,
    })


@require_GET
def chat_stats(request):
    """GET /api/chat/stats/ — RAG index statistics."""
    from rag.retriever import get_index_stats
    return JsonResponse(get_index_stats())
