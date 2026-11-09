"""Plan-usage aggregation across agents (each adapter computes its own bars)."""
import threading
import time

TTL = 50

_lock = threading.Lock()
_cached = None


def _agents():
    from .agents import AGENTS
    return AGENTS


def get(force=False):
    """``{agent name: usage, ..., "at": epoch}``; agents without plan limits are omitted."""
    global _cached
    with _lock:
        now = time.time()
        if not force and _cached is not None and now - _cached["at"] < TTL:
            return dict(_cached)
        result = {}
        for agent in _agents():
            try:
                usage = agent.usage()
            except Exception as e:
                usage = {"error": str(e)}
            if usage is not None:
                result[agent.name] = usage
        result["at"] = now
        _cached = result
        return dict(result)
