"""WebSocket message handler for Zhao Peng You."""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.database import get_db, run_in_db
from app.game.engine import GameEngine, GameEvent
from app.models.db import GameSession, Player, Room
from app.ws.manager import manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory game registry (room_code → GameEngine)
# Active games live here; DB is used for persistence / reconnection only.
# ---------------------------------------------------------------------------
_active_games: dict[str, GameEngine] = {}


def get_or_restore_game(room_code: str) -> GameEngine | None:
    """Returns the in-memory engine for a room, or None if it doesn't exist."""
    return _active_games.get(room_code)


def _persist_game(engine: GameEngine) -> None:
    """Synchronous: save (upsert) the latest engine state to the database."""
    with get_db() as db:
        existing = (
            db.query(GameSession)
            .filter_by(room_code=engine.room_code)
            .order_by(GameSession.id.desc())
            .first()
        )
        if existing:
            existing.set_state(engine.to_dict())
            existing.round_number = engine.round_number
        else:
            session = GameSession(room_code=engine.room_code, round_number=engine.round_number)
            session.set_state(engine.to_dict())
            db.add(session)

        # Sync player levels back to DB
        for p in engine.players:
            db_player = db.query(Player).filter_by(player_id=p.player_id).first()
            if db_player:
                db_player.level = p.level

        # Update room status
        room = db.query(Room).filter_by(room_code=engine.room_code).first()
        if room:
            room.status = engine.phase.value


async def _dispatch_events(room_code: str, events: list[GameEvent]) -> None:
    """Sends engine events to the appropriate WebSocket recipients."""
    for event in events:
        payload = {"event": event.event_type, **event.data}
        if event.target:
            await manager.send_to_player(room_code, event.target, payload)
        else:
            await manager.broadcast(room_code, payload)


# ---------------------------------------------------------------------------
# WebSocket endpoint handler
# ---------------------------------------------------------------------------


async def websocket_handler(websocket: WebSocket, room_code: str) -> None:
    """
    Main WebSocket handler for a game room connection.

    Clients first send a 'join' message with their credentials, then
    subsequent game-action messages.
    """
    player_id: str | None = None

    try:
        await websocket.accept()

        # --- Authentication handshake ---
        try:
            auth_data = await websocket.receive_json()
        except Exception:
            await websocket.close(code=1008)
            return

        player_id, events = await _handle_auth(websocket, room_code, auth_data)
        if player_id is None:
            return  # Connection closed inside _handle_auth

        if events:
            await _dispatch_events(room_code, events)

        # --- Main message loop ---
        async for raw in _iter_messages(websocket):
            if raw is None:
                break
            engine = get_or_restore_game(room_code)
            if engine is None:
                await manager.send_to_player(room_code, player_id, {
                    "event": "error",
                    "message": "Game not found",
                })
                continue

            events = _route_message(engine, player_id, raw)

            if events:
                await _dispatch_events(room_code, events)
                # Persist state after every successful mutation
                await run_in_db(lambda: _persist_game(engine))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Unhandled error in WebSocket handler: %s", exc)
    finally:
        if player_id:
            manager.disconnect(room_code, player_id)
            engine = get_or_restore_game(room_code)
            if engine:
                disc_events = engine.remove_player(player_id) if engine.phase.value == "waiting" else []
                # Mark disconnected but keep in game
                p = engine.get_player(player_id)
                if p:
                    p.is_connected = False
                disc_events.append(GameEvent("state_update", engine._public_state()))
                await _dispatch_events(room_code, disc_events)
                await run_in_db(lambda: _persist_game(engine))


async def _handle_auth(
    websocket: WebSocket,
    room_code: str,
    data: dict,
) -> tuple[str | None, list[GameEvent]]:
    """
    Handles the initial join/reconnect message.
    Returns (player_id, events) or (None, []) on failure.
    """
    msg_type = data.get("type")

    if msg_type not in ("join", "reconnect"):
        await websocket.send_json({"event": "error", "message": "First message must be join or reconnect"})
        await websocket.close()
        return None, []

    if msg_type == "reconnect":
        session_key = data.get("session_key", "")
        return await _handle_reconnect(websocket, room_code, session_key)
    else:
        player_name = data.get("player_name", "").strip()
        if not player_name:
            await websocket.send_json({"event": "error", "message": "player_name required"})
            await websocket.close()
            return None, []
        return await _handle_join(websocket, room_code, player_name)


async def _handle_join(
    websocket: WebSocket,
    room_code: str,
    player_name: str,
) -> tuple[str | None, list[GameEvent]]:
    """Creates or joins a room, returning (player_id, events)."""
    player_id = str(uuid.uuid4())
    session_key = secrets.token_hex(32)

    def db_setup():
        with get_db() as db:
            room = db.query(Room).filter_by(room_code=room_code).first()
            if not room:
                room = Room(room_code=room_code)
                db.add(room)
                db.flush()

            if room.status not in ("waiting",):
                return None, "Room is not accepting new players"

            existing_count = db.query(Player).filter_by(room_code=room_code).count()
            is_host = existing_count == 0

            player = Player(
                player_id=player_id,
                room_code=room_code,
                name=player_name,
                session_key=session_key,
                seat_order=existing_count,
                is_host=is_host,
            )
            db.add(player)
            return player_id, None

    result = await run_in_db(db_setup)
    pid, error = result if result else (None, "DB error")
    if error:
        await websocket.send_json({"event": "error", "message": error})
        await websocket.close()
        return None, []

    # Register WebSocket connection
    manager._connections[room_code][player_id] = websocket

    # Update in-memory engine
    engine = _active_games.get(room_code)
    if engine is None:
        engine = GameEngine(room_code=room_code)

        # Load existing players from DB — extract primitives inside the session
        def load_players():
            with get_db() as db:
                rows = db.query(Player).filter_by(room_code=room_code).order_by(Player.seat_order).all()
                return [(r.player_id, r.name, r.level) for r in rows]

        db_players = await run_in_db(load_players)
        for pid_, name_, level_ in db_players:
            if not any(p.player_id == pid_ for p in engine.players):
                from app.game.engine import PlayerInfo
                engine.players.append(PlayerInfo(
                    player_id=pid_,
                    name=name_,
                    level=level_,
                    is_connected=manager.is_connected(room_code, pid_),
                ))
                engine.hands[pid_] = []
                engine.won_cards[pid_] = []

        _active_games[room_code] = engine

    events = engine.add_player(player_id, player_name)

    # Send credentials to the joining player
    await websocket.send_json({
        "event": "joined",
        "player_id": player_id,
        "session_key": session_key,
        "room_code": room_code,
    })

    return player_id, events


async def _handle_reconnect(
    websocket: WebSocket,
    room_code: str,
    session_key: str,
) -> tuple[str | None, list[GameEvent]]:
    """Re-authenticates an existing player by session_key."""
    def lookup():
        with get_db() as db:
            row = db.query(Player).filter_by(session_key=session_key, room_code=room_code).first()
            # Extract primitive values inside the session to avoid DetachedInstanceError
            return (row.player_id, row.name, row.level) if row else None

    result = await run_in_db(lookup)
    if not result:
        await websocket.send_json({"event": "error", "message": "Invalid session key"})
        await websocket.close()
        return None, []

    player_id, _player_name, _player_level = result

    # Restore game state if not in memory
    engine = _active_games.get(room_code)
    if engine is None:
        def restore():
            with get_db() as db:
                gs = (
                    db.query(GameSession)
                    .filter_by(room_code=room_code)
                    .order_by(GameSession.id.desc())
                    .first()
                )
                if gs:
                    return gs.get_state(), None
                # No saved game yet — rebuild engine from player roster (lobby state)
                rows = db.query(Player).filter_by(room_code=room_code).order_by(Player.seat_order).all()
                if rows:
                    return None, [(r.player_id, r.name, r.level) for r in rows]
                return None, None

        state, players_data = await run_in_db(restore)
        if state:
            engine = GameEngine.from_dict(state)
            _active_games[room_code] = engine
        elif players_data:
            # Game not started yet — rebuild waiting room
            engine = GameEngine(room_code=room_code)
            from app.game.engine import PlayerInfo
            for pid_, name_, level_ in players_data:
                engine.players.append(PlayerInfo(player_id=pid_, name=name_, level=level_))
                engine.hands[pid_] = []
                engine.won_cards[pid_] = []
            _active_games[room_code] = engine
        else:
            await websocket.send_json({"event": "error", "message": "Room not found"})
            await websocket.close()
            return None, []

    manager._connections[room_code][player_id] = websocket

    events = engine.reconnect_player(player_id)

    await websocket.send_json({
        "event": "reconnected",
        "player_id": player_id,
        "room_code": room_code,
    })

    return player_id, events


async def _iter_messages(websocket: WebSocket):
    """Async generator yielding parsed JSON messages from the WebSocket."""
    try:
        while True:
            data = await websocket.receive_json()
            yield data
    except WebSocketDisconnect:
        yield None
    except Exception as exc:
        logger.warning("WebSocket receive error: %s", exc)
        yield None


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------


def _route_message(engine: GameEngine, player_id: str, data: dict) -> list[GameEvent]:
    """Dispatches a player action to the appropriate engine method."""
    msg_type = data.get("type", "")

    if msg_type == "start_game":
        return engine.start_game(player_id)

    elif msg_type == "draw_card":
        return engine.draw_card(player_id)

    elif msg_type == "flip_trump":
        card_ids = data.get("card_ids", [])
        return engine.flip_trump(player_id, card_ids)

    elif msg_type == "bury_cards":
        card_ids = data.get("card_ids", [])
        return engine.bury_cards(player_id, card_ids)

    elif msg_type == "call_teammates":
        calls = data.get("calls", [])
        return engine.call_teammates(player_id, calls)

    elif msg_type == "play_cards":
        card_ids = data.get("card_ids", [])
        return engine.play_cards(player_id, card_ids)

    elif msg_type == "next_round":
        return engine.start_next_round(player_id)

    elif msg_type == "get_state":
        return [GameEvent("state_update", engine._public_state())]

    elif msg_type == "chat":
        message = str(data.get("message", "")).strip()[:200]
        if not message:
            return []
        p = engine.get_player(player_id)
        name = p.name if p else player_id
        return [GameEvent("chat", {"player_id": player_id, "player_name": name, "message": message})]

    else:
        return [GameEvent("error", {"message": f"Unknown message type: {msg_type!r}"}, player_id)]
