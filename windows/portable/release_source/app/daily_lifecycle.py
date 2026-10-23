"""Small local-service admission gate; no detector or job execution changes.

The pure ASGI middleware enters before multipart parsing and leaves only after
the final response/background work.  The shutdown decision and admission use
the same async lock, including while the active-job snapshot runs off-thread.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from starlette.responses import JSONResponse


class AdmissionCoordinator:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.inflight_mutations = 0
        self.draining = False

    async def enter(self) -> bool:
        async with self._lock:
            if self.draining:
                return False
            self.inflight_mutations += 1
            return True

    async def leave(self):
        async with self._lock:
            self.inflight_mutations -= 1

    async def request_shutdown(self, active_jobs: Callable[[], int]) -> dict:
        async with self._lock:
            # Holding the async gate does not block the event loop while SQLite
            # or the worker's terminal-state lock is consulted in a thread.
            active = await asyncio.to_thread(active_jobs)
            if active or self.inflight_mutations:
                return {'accepted': False, 'active_jobs': active,
                        'inflight_mutations': self.inflight_mutations,
                        'draining': self.draining}
            self.draining = True
            return {'accepted': True, 'active_jobs': 0,
                    'inflight_mutations': 0, 'draining': True}

    async def snapshot(self) -> dict:
        async with self._lock:
            return {'draining': self.draining,
                    'inflight_mutations': self.inflight_mutations}


class MutationAdmissionMiddleware:
    """Count every unsafe HTTP method, including uploads not yet in jobs.

    This must be registered last (outermost user middleware).  The authenticated
    lifecycle controls are exceptions; they never write user task data. The
    mode forwarder must not count itself as a mutation while asking the SAME
    atomic shutdown gate to test all other accepted writes.
    """
    def __init__(self, app, coordinator: AdmissionCoordinator):
        self.app = app
        self.coordinator = coordinator

    async def __call__(self, scope, receive, send):
        mutation = (scope['type'] == 'http'
                    and scope.get('method', 'GET') not in ('GET', 'HEAD', 'OPTIONS')
                    and scope.get('path') not in ('/api/daily/shutdown', '/api/daily/mode'))
        if not mutation:
            return await self.app(scope, receive, send)
        if not await self.coordinator.enter():
            response = JSONResponse(
                {'detail': '图鉴正在正常关闭，已停止接收新任务和修改。请重新启动后再试。'},
                status_code=503, headers={'Retry-After': '3'})
            return await response(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            # Cancellation/disconnect must release the gate as well.  Shield
            # cleanup so the counter cannot be stranded by task cancellation.
            await asyncio.shield(self.coordinator.leave())


def cache_state(stats: dict, *, enabled: bool | None = None) -> dict:
    """Translate observations, never equating configuration with a cache hit."""
    enabled = stats.get('enabled', False) if enabled is None else enabled
    kinds = stats.get('by_kind') or {}
    hits = sum(int(value.get('hit', 0)) for value in kinds.values())
    fallbacks = sum(int(count) for value in kinds.values()
                    for event, count in value.items()
                    if event == 'fallback' or event.endswith('_fallback'))
    errors = sum(int(value.get('configuration_failure', 0))
                 + int(value.get('root_unavailable', 0)) for value in kinds.values())
    if not enabled:
        state, label = 'disabled', '缓存已关闭 · 使用原计算路径'
    elif stats.get('available') is False or errors:
        state, label = 'fallback', '缓存不可用 · 已回退计算（不代表检测失败）'
    elif stats.get('available') is None:
        state, label = 'not_initialized', '缓存已开启 · 尚未被任务初始化'
    elif fallbacks:
        # The adapter's root initialization snapshot may remain available even
        # after a later root/read/snapshot failure. Preserve observed hits as
        # a separate count, but never hide real fallback behind that snapshot.
        state, label = 'fallback', '部分缓存请求已回退计算（不代表检测失败）'
    elif hits:
        state, label = 'hit', '缓存实际可用 · 本进程已发生持久化命中'
    else:
        state, label = 'available', '缓存实际可用 · 本进程尚未发生命中'
    return {'state': state, 'label': label, 'enabled': bool(enabled),
            'available': stats.get('available'), 'persistent_hits': hits,
            'fallback_events': fallbacks,
            'observations_scope': 'current_process'}
