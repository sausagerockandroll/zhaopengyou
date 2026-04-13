"""Tests for the drawing and trump-flipping phase."""

from __future__ import annotations

import pytest

from app.game.card import Card, num_decks_for_players
from app.game.constants import GamePhase, JOKER_BIG, JOKER_SMALL
from tests.conftest import make_engine, start_engine, fully_draw


# ---------------------------------------------------------------------------
# Game startup
# ---------------------------------------------------------------------------

class TestGameStart:
    def test_needs_min_4_players(self):
        engine = make_engine(3)
        events = engine.start_game("player0")
        assert engine.phase == GamePhase.WAITING
        assert any(e.event_type == "error" for e in events)

    def test_starts_with_4_players(self):
        engine = make_engine(4)
        events = engine.start_game("player0")
        assert engine.phase == GamePhase.DRAWING

    def test_correct_deck_size(self):
        engine = start_engine(6)
        num_decks = num_decks_for_players(6)
        total = num_decks * 54
        assert len(engine.deck) + len(engine.buried_cards) == total

    def test_buried_pile_not_empty(self):
        engine = start_engine(4)
        assert len(engine.buried_cards) >= 6

    def test_cards_divide_evenly(self):
        engine = start_engine(6)
        total_in_play = len(engine.deck)
        assert total_in_play % 6 == 0

    def test_hands_start_empty(self):
        engine = start_engine(4)
        for pid in engine.player_ids:
            assert engine.hands[pid] == []


# ---------------------------------------------------------------------------
# Drawing cards
# ---------------------------------------------------------------------------

class TestDrawCard:
    def test_sequential_drawing(self):
        engine = start_engine(4)
        # Player 0 is first drawer
        pid0 = engine.players[engine.current_drawer_idx].player_id

        events = engine.draw_card(pid0)
        assert len(engine.hands[pid0]) == 1
        assert any(e.event_type == "card_drawn" for e in events)

    def test_wrong_player_cannot_draw(self):
        engine = start_engine(4)
        current = engine.players[engine.current_drawer_idx].player_id
        other = next(p.player_id for p in engine.players if p.player_id != current)

        events = engine.draw_card(other)
        assert any(e.event_type == "error" for e in events)
        assert len(engine.hands[other]) == 0

    def test_draw_advances_turn(self):
        engine = start_engine(4)
        first_idx = engine.current_drawer_idx
        first_pid = engine.players[first_idx].player_id
        engine.draw_card(first_pid)
        expected_next = (first_idx + 1) % len(engine.players)
        assert engine.current_drawer_idx == expected_next

    def test_all_cards_distributed_after_full_draw(self):
        engine = start_engine(4)
        fully_draw(engine)
        total_in_hands = sum(len(h) for h in engine.hands.values())
        # Cards in hands should be deck size (buried cards given to leader after draw)
        # After fully_draw: deck=0, leader has their hand + former buried cards
        # But we count the leader's extra cards in their hand
        assert engine.deck == []

    def test_after_full_draw_phase_is_burying(self):
        engine = start_engine(4)
        fully_draw(engine)
        assert engine.phase == GamePhase.BURYING

    def test_all_players_equal_card_count(self):
        """After full draw (before burying), non-leaders have equal hands."""
        engine = start_engine(6)
        fully_draw(engine)
        non_leader_counts = [
            len(engine.hands[p.player_id])
            for p in engine.players
            if p.player_id != engine.leader_id
        ]
        assert len(set(non_leader_counts)) == 1


# ---------------------------------------------------------------------------
# Trump flipping
# ---------------------------------------------------------------------------

class TestTrumpFlip:
    def _draw_until_player_has_level_card(self, engine, player_id):
        """Draw cards until the target player has a card matching their level."""
        max_draws = len(engine.deck)
        for _ in range(max_draws):
            if engine.phase != GamePhase.DRAWING:
                break
            current = engine.players[engine.current_drawer_idx].player_id
            engine.draw_card(current)
            player = engine.get_player(player_id)
            if player and any(
                c.rank == player.level and not c.is_joker
                for c in engine.hands[player_id]
            ):
                return True
        return False

    def test_flip_sets_trump(self):
        engine = start_engine(4)
        pid = "player0"
        # Draw cards for player0 until they have their level card
        found = self._draw_until_player_has_level_card(engine, pid)
        if not found:
            pytest.skip("Player never drew their level card — statistical edge case")

        player = engine.get_player(pid)
        eligible = [
            c for c in engine.hands[pid]
            if c.rank == player.level and not c.is_joker
        ]
        card = eligible[0]
        events = engine.flip_trump(pid, [card.id])
        assert engine.trump_suit == card.suit
        assert engine.trump_number == card.rank
        assert engine.leader_id == pid

    def test_cannot_flip_wrong_level(self):
        engine = start_engine(4)
        # Draw until any player has any card that is NOT their level
        for _ in range(30):
            if engine.phase != GamePhase.DRAWING:
                break
            current = engine.players[engine.current_drawer_idx].player_id
            engine.draw_card(current)

        pid = "player0"
        player = engine.get_player(pid)
        non_level = [
            c for c in engine.hands[pid]
            if c.rank != player.level
        ]
        if not non_level:
            pytest.skip("No non-level cards in hand")

        events = engine.flip_trump(pid, [non_level[0].id])
        assert any(e.event_type == "error" for e in events)

    def test_higher_count_overrides(self):
        engine = start_engine(4)
        # Set up a state with a trump already flipped by manually setting the flip state
        from app.game.engine import TrumpFlipState
        engine.trump_flip_state = TrumpFlipState(
            player_id="player0", rank="2", suit="spades", count=1,
            card_ids=[], locked=False
        )
        engine.trump_suit = "spades"
        engine.trump_number = "2"
        engine.leader_id = "player0"

        # Give player1 two 2-of-hearts cards
        p1 = engine.get_player("player1")
        if p1:
            card_a = Card(suit="hearts", rank=p1.level, deck_index=0)
            card_b = Card(suit="hearts", rank=p1.level, deck_index=1)
            engine.hands["player1"] = [card_a, card_b]

            events = engine.flip_trump("player1", [card_a.id, card_b.id])
            assert engine.trump_suit == "hearts"
            assert engine.leader_id == "player1"

    def test_trump_lock_at_max_decks(self):
        engine = start_engine(4)  # 2 decks
        engine.num_decks = 2
        from app.game.engine import TrumpFlipState
        engine.trump_flip_state = TrumpFlipState(
            player_id="player0", rank="2", suit="spades", count=1,
            card_ids=[], locked=False
        )

        p0 = engine.get_player("player0")
        card_a = Card(suit="spades", rank=p0.level, deck_index=0)
        card_b = Card(suit="spades", rank=p0.level, deck_index=1)
        engine.hands["player0"] = [card_a, card_b]

        events = engine.flip_trump("player0", [card_a.id, card_b.id])
        # count=2 == num_decks=2 → locked
        assert engine.trump_locked

    def test_auto_flip_if_no_manual_flip(self):
        engine = start_engine(4)
        fully_draw(engine)
        # After full draw with no manual flip, auto-flip should have been triggered
        assert engine.trump_suit is not None
        assert engine.trump_number is not None
        assert engine.leader_id is not None
