"""Tests for the burying and teammate-calling phases."""

from __future__ import annotations

import pytest

from app.game.card import Card
from app.game.constants import GamePhase
from tests.conftest import (
    fully_draw,
    make_engine,
    reach_playing,
    skip_burying,
    skip_calling,
    start_engine,
)


# ---------------------------------------------------------------------------
# Burying phase
# ---------------------------------------------------------------------------

class TestBuryingPhase:
    def test_only_leader_can_bury(self):
        engine = start_engine(4)
        fully_draw(engine)
        non_leader = next(p.player_id for p in engine.players if p.player_id != engine.leader_id)
        events = engine.bury_cards(non_leader, [])
        assert any(e.event_type == "error" for e in events)

    def test_bury_no_exchange_advances_phase(self):
        engine = start_engine(4)
        fully_draw(engine)
        # Use skip_burying helper which buries the right number of cards
        from tests.conftest import skip_burying
        skip_burying(engine)
        assert engine.phase == GamePhase.CALLING

    def test_bury_wrong_count_is_error(self):
        engine = start_engine(4)
        fully_draw(engine)
        # Pass 0 cards when some must be buried → error
        leader_hand = engine.hands[engine.leader_id]
        other_size = len(engine.hands[engine.players[1].player_id])
        required = len(leader_hand) - other_size
        if required > 0:
            # burying one card when we need to bury `required` cards
            events = engine.bury_cards(engine.leader_id, [leader_hand[0].id] * 1)
            if required != 1:
                assert any(e.event_type == "error" for e in events)

    def test_leader_hand_size_equal_after_burying(self):
        engine = start_engine(4)
        fully_draw(engine)
        leader = engine.leader_id
        other_size = len(engine.hands[engine.players[1].player_id])
        leader_hand = engine.hands[leader]
        excess = len(leader_hand) - other_size
        cards_to_bury = [c.id for c in leader_hand[-excess:]] if excess > 0 else []
        engine.bury_cards(leader, cards_to_bury)
        assert len(engine.hands[leader]) == other_size

    def test_bury_wrong_phase_is_error(self):
        engine = start_engine(4)
        # Still in drawing phase
        events = engine.bury_cards("player0", [])
        assert any(e.event_type == "error" for e in events)


# ---------------------------------------------------------------------------
# Calling phase
# ---------------------------------------------------------------------------

class TestCallingPhase:
    def _engine_at_calling(self):
        engine = start_engine(4)
        fully_draw(engine)
        skip_burying(engine)
        return engine

    def test_only_leader_can_call(self):
        engine = self._engine_at_calling()
        non_leader = next(p.player_id for p in engine.players if p.player_id != engine.leader_id)
        events = engine.call_teammates(non_leader, [])
        assert any(e.event_type == "error" for e in events)

    def test_empty_calls_proceeds_to_playing(self):
        engine = self._engine_at_calling()
        events = engine.call_teammates(engine.leader_id, [])
        assert engine.phase == GamePhase.PLAYING

    def test_single_valid_call(self):
        engine = self._engine_at_calling()
        # Pick a non-trump suit and a non-trump-number rank
        from app.game.constants import SUITS, RANKS
        non_trump_suit = next(s for s in SUITS if s != engine.trump_suit)
        non_trump_rank = next(r for r in RANKS if r != engine.trump_number)
        events = engine.call_teammates(
            engine.leader_id,
            [{"rank": non_trump_rank, "suit": non_trump_suit, "order": 1}]
        )
        assert engine.phase == GamePhase.PLAYING
        assert len(engine.calls) == 1
        assert engine.calls[0].rank == non_trump_rank
        assert engine.calls[0].suit == non_trump_suit

    def test_cannot_call_trump_suit_card(self):
        engine = self._engine_at_calling()
        # Call trump suit card
        trump_suit = engine.trump_suit
        events = engine.call_teammates(
            engine.leader_id,
            [{"rank": "A", "suit": trump_suit, "order": 1}]
        )
        assert any(e.event_type == "error" for e in events)

    def test_cannot_call_trump_number_card(self):
        engine = self._engine_at_calling()
        trump_num = engine.trump_number
        # trump number in any suit is trump
        events = engine.call_teammates(
            engine.leader_id,
            [{"rank": trump_num, "suit": "clubs", "order": 1}]
        )
        assert any(e.event_type == "error" for e in events)

    def test_too_many_calls_is_error(self):
        engine = self._engine_at_calling()
        # 4 players: max_attackers = 2, so max calls = 1
        calls = [
            {"rank": "A", "suit": "spades", "order": 1},
            {"rank": "K", "suit": "spades", "order": 1},
        ]
        events = engine.call_teammates(engine.leader_id, calls)
        assert any(e.event_type == "error" for e in events)

    def test_max_attackers_calculation(self):
        """6 players → max 3 attackers → 2 calls allowed."""
        engine = start_engine(6)
        fully_draw(engine)
        skip_burying(engine)
        assert engine.max_attackers == 3

        # Find two non-trump suits to call safely
        from app.game.constants import SUITS
        non_trump_suits = [s for s in SUITS if s != engine.trump_suit]
        suit1, suit2 = non_trump_suits[0], non_trump_suits[1]
        calls = [
            {"rank": "A", "suit": suit1, "order": 1},
            {"rank": "K", "suit": suit2, "order": 1},
        ]
        events = engine.call_teammates(engine.leader_id, calls)
        assert engine.phase == GamePhase.PLAYING


# ---------------------------------------------------------------------------
# Teammate revelation during play
# ---------------------------------------------------------------------------

class TestTeammateReveal:
    def test_teammate_joins_when_called_card_played(self):
        engine = reach_playing(4)
        leader = engine.leader_id

        # Manually set a call for a card we know a non-leader player holds
        from app.game.engine import TeammateCall
        # Find a non-trump, non-leader card
        non_leader = next(p.player_id for p in engine.players if p.player_id != leader)
        non_leader_hand = engine.hands[non_leader]
        non_trump_cards = [
            c for c in non_leader_hand
            if not c.is_trump(engine.trump_suit, engine.trump_number)
        ]
        if not non_trump_cards:
            pytest.skip("Non-leader has no non-trump cards")

        target_card = non_trump_cards[0]
        engine.calls = [TeammateCall(
            rank=target_card.rank, suit=target_card.suit, order=1
        )]

        # Ensure it's the non-leader's turn or simulate call check directly
        events = engine._check_calls(non_leader, [target_card])
        assert any(e.event_type == "teammate_revealed" for e in events)
        assert non_leader in engine.attacking_team

    def test_leader_playing_own_called_card_wastes_call(self):
        engine = reach_playing(4)
        leader = engine.leader_id

        from app.game.engine import TeammateCall
        leader_hand = engine.hands[leader]
        non_trump = [c for c in leader_hand if not c.is_trump(engine.trump_suit, engine.trump_number)]
        if not non_trump:
            pytest.skip("Leader has no non-trump cards")

        target = non_trump[0]
        engine.calls = [TeammateCall(rank=target.rank, suit=target.suit, order=1)]

        events = engine._check_calls(leader, [target])
        # No teammate_revealed event (leader "wasted" their own call)
        assert not any(e.event_type == "teammate_revealed" for e in events)
        assert engine.calls[0].fulfilled  # call marked fulfilled but no one joined
        assert engine.calls[0].fulfiller_id is None
