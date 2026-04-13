"""SQLite database setup using SQLAlchemy (synchronous) wrapped in asyncio.to_thread."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db import Base

# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent.parent / "zhaopengyou.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Creates all tables if they do not exist."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Provides a synchronous database session."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_in_db(fn):
    """Runs a synchronous DB function in a thread pool (for async contexts)."""
    return await asyncio.to_thread(fn)
