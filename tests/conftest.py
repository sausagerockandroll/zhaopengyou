"""Shared fixtures for Zhao Peng You tests."""

from __future__ import annotations

import pytest

from app.game.card import Card, create_deck, create_shuffled_decks
from app.game.constants import GamePhase
from app.game.engine import GameEngine, PlayerInfo


# ---------------------------------------------------------------------------
# Card factories
# ---------------------------------------------------------------------------

def make_card(rank: str, suit: str, deck_index: int = 0) -> Card:
    return Card(suit=suit, rank=rank, deck_index=deck_index)


def hand_of(*args: tuple[str, str, int]) -> list[Card]:
    """Build a hand from (rank, suit, deck_index) tuples."""
    return [Card(suit=s, rank=r, deck_index=d) for r, s, d in args]


# ---------------------------------------------------------------------------
# Engine factories
# ---------------------------------------------------------------------------

def make_engine(num_players: int = 4, room_code: str = "TEST01") -> GameEngine:
    """Creates a GameEngine with `num_players` dummy players in WAITING phase."""
    engine = GameEngine(room_code=room_code)
    for i in range(num_players):
        pid = f"player{i}"
        engine.add_player(pid, f"Player {i}")
    return engine


def start_engine(num_players: int = 4) -> GameEngine:
    """
    Creates an engine, starts the game, and returns it in DRAWING phase.
    All state has been initialised; hands are empty (cards not yet drawn).
    """
    engine = make_engine(num_players)
    events = engine.start_game("player0")
    assert engine.phase == GamePhase.DRAWING, f"Expected DRAWING, got {engine.phase}"
    return engine


def fully_draw(engine: GameEngine) -> GameEngine:
    """
    Draws all cards for a started engine (processes full draw phase).
    Returns the engine after drawing is complete (now in BURYING phase).
    """
    while engine.deck and engine.phase == GamePhase.DRAWING:
        current = engine.players[engine.current_drawer_idx].player_id
        engine.draw_card(current)
    assert engine.phase == GamePhase.BURYING, f"Expected BURYING, got {engine.phase}"
    return engine


def skip_burying(engine: GameEngine) -> GameEngine:
    """
    Leader buries the excess cards (those added from the buried pile) to return
    everyone to equal hand sizes, then transitions to CALLING.
    """
    assert engine.phase == GamePhase.BURYING
    leader = engine.leader_id

    other_sizes = [
        len(engine.hands[p.player_id])
        for p in engine.players
        if p.player_id != leader
    ]
    target_size = other_sizes[0] if other_sizes else 0
    leader_hand = engine.hands[leader]
    excess = len(leader_hand) - target_size
    cards_to_bury = [c.id for c in leader_hand[-excess:]] if excess > 0 else []
    events = engine.bury_cards(leader, cards_to_bury)
    assert not any(e.event_type == "error" for e in events), \
        f"bury_cards error: {[e.data for e in events if e.event_type == 'error']}"
    assert engine.phase == GamePhase.CALLING
    return engine


def skip_calling(engine: GameEngine) -> GameEngine:
    """Leader submits an empty calls list → transitions to PLAYING."""
    assert engine.phase == GamePhase.CALLING
    engine.call_teammates(engine.leader_id, [])
    assert engine.phase == GamePhase.PLAYING
    return engine


def reach_playing(num_players: int = 4) -> GameEngine:
    """Returns an engine fully set up and ready for the PLAYING phase."""
    engine = start_engine(num_players)
    fully_draw(engine)
    skip_burying(engine)
    skip_calling(engine)
    return engine
