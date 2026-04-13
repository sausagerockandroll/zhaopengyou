"""Pydantic schemas for API request/response validation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateRoomRequest(BaseModel):
    pass  # no body required


class CreateRoomResponse(BaseModel):
    room_code: str


class RoomStatusResponse(BaseModel):
    room_code: str
    status: str
    player_count: int
    players: list[dict]
