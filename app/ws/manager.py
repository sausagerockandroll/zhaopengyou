"""WebSocket connection manager for Zhao Peng You."""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Tracks active WebSocket connections keyed by (room_code, player_id).
    Provides broadcast and targeted send helpers.
    """

    def __init__(self) -> None:
        # room_code → {player_id → WebSocket}
        self._connections: dict[str, dict[str, WebSocket]] = defaultdict(dict)

    async def connect(self, websocket: WebSocket, room_code: str, player_id: str) -> None:
        await websocket.accept()
        # Close any existing connection for this player
        old = self._connections[room_code].get(player_id)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self._connections[room_code][player_id] = websocket
        logger.info("Player %s connected to room %s", player_id, room_code)

    def disconnect(self, room_code: str, player_id: str) -> None:
        room = self._connections.get(room_code, {})
        room.pop(player_id, None)
        logger.info("Player %s disconnected from room %s", player_id, room_code)

    def is_connected(self, room_code: str, player_id: str) -> bool:
        return player_id in self._connections.get(room_code, {})

    def connected_players(self, room_code: str) -> list[str]:
        return list(self._connections.get(room_code, {}).keys())

    async def send_to_player(self, room_code: str, player_id: str, data: dict) -> None:
        ws = self._connections.get(room_code, {}).get(player_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception as exc:
                logger.warning("Failed to send to %s/%s: %s", room_code, player_id, exc)
                self.disconnect(room_code, player_id)

    async def broadcast(self, room_code: str, data: dict, exclude: str | None = None) -> None:
        """Sends `data` to all connected players in a room, optionally excluding one."""
        room = dict(self._connections.get(room_code, {}))
        for player_id, ws in room.items():
            if player_id == exclude:
                continue
            try:
                await ws.send_json(data)
            except Exception as exc:
                logger.warning("Broadcast failed for %s/%s: %s", room_code, player_id, exc)
                self.disconnect(room_code, player_id)

    def room_exists(self, room_code: str) -> bool:
        return room_code in self._connections and bool(self._connections[room_code])

    def all_rooms(self) -> list[str]:
        return list(self._connections.keys())


# Singleton instance shared across the app
manager = ConnectionManager()
