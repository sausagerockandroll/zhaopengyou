"""SQLAlchemy ORM models for Zhao Peng You."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Room(Base):
    __tablename__ = "rooms"

    room_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    # "waiting" | "active" | "finished"

    players: Mapped[list[Player]] = relationship("Player", back_populates="room", cascade="all, delete-orphan")
    sessions: Mapped[list[GameSession]] = relationship("GameSession", back_populates="room", cascade="all, delete-orphan")


class Player(Base):
    __tablename__ = "players"

    player_id: Mapped[str] = mapped_column(String(36), primary_key=True)   # UUID
    room_code: Mapped[str] = mapped_column(ForeignKey("rooms.room_code"))
    name: Mapped[str] = mapped_column(String(50))
    session_key: Mapped[str] = mapped_column(String(64), unique=True)       # reconnect token
    seat_order: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(3), default="2")
    is_host: Mapped[bool] = mapped_column(Boolean, default=False)

    room: Mapped[Room] = relationship("Room", back_populates="players")


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_code: Mapped[str] = mapped_column(ForeignKey("rooms.room_code"))
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    state_json: Mapped[str] = mapped_column(Text)      # Full GameEngine.to_dict() JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    room: Mapped[Room] = relationship("Room", back_populates="sessions")

    def get_state(self) -> dict:
        return json.loads(self.state_json)

    def set_state(self, state: dict) -> None:
        self.state_json = json.dumps(state)
