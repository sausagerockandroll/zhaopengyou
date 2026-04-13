"""HTTP REST routes for room management."""

from __future__ import annotations

import secrets
import string

from fastapi import APIRouter, HTTPException

from app.database import get_db, run_in_db
from app.models.db import Player, Room
from app.models.schemas import (
    CreateRoomResponse,
    RoomStatusResponse,
)

router = APIRouter(prefix="/api")


def _generate_room_code(length: int = 6) -> str:
    """Generates a random uppercase alphanumeric room code."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.post("/rooms", response_model=CreateRoomResponse)
async def create_room() -> CreateRoomResponse:
    """Creates a new empty room and returns its code. Players join via WebSocket."""
    def create():
        with get_db() as db:
            for _ in range(10):
                code = _generate_room_code()
                if not db.query(Room).filter_by(room_code=code).first():
                    break
            else:
                raise HTTPException(status_code=500, detail="Could not generate unique room code")
            room = Room(room_code=code)
            db.add(room)
            return code

    room_code = await run_in_db(create)
    return CreateRoomResponse(room_code=room_code)


@router.get("/rooms/{room_code}", response_model=RoomStatusResponse)
async def get_room(room_code: str) -> RoomStatusResponse:
    """Returns current room status and player list."""
    def fetch():
        with get_db() as db:
            room = db.query(Room).filter_by(room_code=room_code).first()
            if not room:
                raise HTTPException(status_code=404, detail="Room not found")
            players = (
                db.query(Player)
                .filter_by(room_code=room_code)
                .order_by(Player.seat_order)
                .all()
            )
            return room.status, [
                {"player_id": p.player_id, "name": p.name, "level": p.level, "is_host": p.is_host}
                for p in players
            ]

    status, players = await run_in_db(fetch)
    return RoomStatusResponse(
        room_code=room_code,
        status=status,
        player_count=len(players),
        players=players,
    )
