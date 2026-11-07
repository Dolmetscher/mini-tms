"""Индексированные по пользователю WebSocket-соединения и адресные рассылки.

push() потокобезопасен: sync-роутеры FastAPI исполняются в threadpool,
поэтому корутина отправки передается в главный event loop приложения.
"""
import asyncio
import json
from datetime import datetime, timezone

from fastapi import WebSocket


def make_event(type_: str, **fields) -> dict:
    return {"type": type_, "ts": datetime.now(timezone.utc).isoformat(), **fields}


class ConnectionManager:
    def __init__(self):
        self.active: dict[int, set[WebSocket]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self.active.get(user_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                self.active.pop(user_id, None)

    async def _send(self, websocket: WebSocket, message: str) -> None:
        try:
            await websocket.send_text(message)
        except Exception:
            pass

    async def send_users(self, user_ids, event: dict) -> None:
        """Адресная доставка: событие получают только перечисленные пользователи."""
        message = json.dumps(event, ensure_ascii=False)
        for user_id in set(user_ids):
            sockets = list(self.active.get(user_id, ()))
            for ws in sockets:
                try:
                    await ws.send_text(message)
                except Exception:
                    self.disconnect(user_id, ws)

    def push(self, user_ids, event: dict) -> None:
        """Fire-and-forget из любого потока (sync-роутеры, фоновые задачи)."""
        if not user_ids or not self.active:
            return
        coro = self.send_users(user_ids, event)
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            try:
                asyncio.run(coro)
            except RuntimeError:
                pass


manager = ConnectionManager()
