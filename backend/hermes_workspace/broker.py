from __future__ import annotations

from fastapi import WebSocket


class ConnectionBroker:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        stale: list[WebSocket] = []
        for client in self._clients:
            try:
                await client.send_json(payload)
            except RuntimeError:
                stale.append(client)
        for client in stale:
            self.disconnect(client)
