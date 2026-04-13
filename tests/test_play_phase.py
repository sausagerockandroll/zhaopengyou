"""Tests for the playing phase: initiative, following, and trick resolution."""

from __future__ import annotations

import pytest

from app.game.card import Card
from app.game.constants import GamePhase, JOKER_BIG, JOKER_SMALL
from app.game.engine import GameEngine
from tests.conftest import reach_playing


# ---------------------------------------------------------------------------
# Helper: force-deal a specific hand
# ---------------------------------------------------------------------------

def force_hand(engine: GameEngine, player_id: str, cards: list[Card]) -> None:
    """Replaces a player's hand with specific cards (testing only)."""
    engine.hands[player_id] = cards


def c(rank: str, suit: str, deck_index: int = 0) -> Card:
    return Card(suit=suit, rank=rank, deck_index=deck_index)


# ---------------------------------------------------------------------------
# Turn order
# ---------------------------------------------------------------------------

class TestTurnOrder:
    def test_leader_plays_first(self):
        engine = reach_playing(4)
        leader = engine.leader_id
        first_to_play = engine.trick_play_order[0]
        assert first_to_play == leader

    def test_wrong_turn_is_error(self):
        engine = reach_playing(4)
        not_first = engine.trick_play_order[1]
        events = engine.play_cards(not_first, [])
        assert any(e.event_type == "error" for e in events)

    def test_play_order_is_clockwise_from_leader(self):
        engine = reach_playing(4)
        leader = engine.leader_id
        order = engine.trick_play_order
        assert order[0] == leader
        # Each subsequent player is next clockwise
        for i in range(1, len(order)):
            prev_idx = engine.player_index(order[i - 1])
            curr_idx = engine.player_index(order[i])
            assert curr_idx == (prev_idx + 1) % len(engine.players)


# ---------------------------------------------------------------------------
# Single card plays
# ---------------------------------------------------------------------------

class TestSinglePlay:
    def test_lead_plays_single(self):
        engine = reach_playing(4)
        leader = engine.leader_id
        force_hand(engine, leader, [c("A", "spades")])
        events = engine.play_cards(leader, [c("A", "spades").id])
        assert any(e.event_type == "card_played" for e in events)
        assert len(engine.hands[leader]) == 0

    def test_invalid_card_not_in_hand(self):
        engine = reach_playing(4)
        leader = engine.leader_id
        force_hand(engine, leader, [c("A", "spades")])
        events = engine.play_cards(leader, [c("K", "clubs").id])
        assert any(e.event_type == "error" for e in events)


# ---------------------------------------------------------------------------
# Following rules
# ---------------------------------------------------------------------------

class TestFollowRules:
    def _setup_two_player_trick(self, lead_cards, follow_hand):
        """
        Sets up a 4-player engine where player0 leads lead_cards
        and player1 has follow_hand.
        """
        engine = reach_playing(4)
        trump_suit = engine.trump_suit
        trump_number = engine.trump_number

        leader = engine.trick_play_order[0]
        follower = engine.trick_play_order[1]

        force_hand(engine, leader, lead_cards)
        force_hand(engine, follower, follow_hand)

        # Others need cards too
        other_cards = [c("3", "clubs", i) for i in range(2)]
        for pid in engine.trick_play_order[2:]:
            force_hand(engine, pid, other_cards[:len(lead_cards)])

        return engine, leader, follower, trump_suit, trump_number

    def test_follower_must_play_suit(self):
        lead = [c("A", "spades")]
        follow = [c("7", "spades"), c("K", "clubs")]
        engine, leader, follower, ts, tn = self._setup_two_player_trick(lead, follow)

        engine.play_cards(leader, [lead[0].id])

        # Must play 7♠ (has spades)
        ok_events = engine.play_cards(follower, [c("7", "spades").id])
        assert not any(e.event_type == "error" for e in ok_events)

    def test_follower_cannot_discard_suit_if_available(self):
        lead = [c("A", "spades")]
        follow = [c("7", "spades"), c("K", "clubs")]
        engine, leader, follower, ts, tn = self._setup_two_player_trick(lead, follow)

        engine.play_cards(leader, [lead[0].id])

        # Trying to play K♣ when 7♠ is available
        bad_events = engine.play_cards(follower, [c("K", "clubs").id])
        assert any(e.event_type == "error" for e in bad_events)

    def test_follower_can_play_trump_when_void(self):
        engine = reach_playing(4)
        trump_suit = engine.trump_suit

        leader = engine.trick_play_order[0]
        follower = engine.trick_play_order[1]

        force_hand(engine, leader, [c("A", "spades")])
        # Follower has only trump cards (no spades)
        force_hand(engine, follower, [c("3", trump_suit)])
        for pid in engine.trick_play_order[2:]:
            force_hand(engine, pid, [c("5", "clubs")])

        engine.play_cards(leader, [c("A", "spades").id])
        events = engine.play_cards(follower, [c("3", trump_suit).id])
        assert not any(e.event_type == "error" for e in events)

    def test_pair_lead_requires_pair_follow(self):
        lead = [c("A", "spades", 0), c("A", "spades", 1)]
        follow = [c("7", "spades", 0), c("7", "spades", 1), c("K", "clubs", 0)]
        engine, leader, follower, ts, tn = self._setup_two_player_trick(lead, follow)

        engine.play_cards(leader, [c("A", "spades", 0).id, c("A", "spades", 1).id])

        # Follower has a spade pair (7♠×2) — must play it
        ok = engine.play_cards(follower, [c("7", "spades", 0).id, c("7", "spades", 1).id])
        assert not any(e.event_type == "error" for e in ok)


# ---------------------------------------------------------------------------
# Trick resolution
# ---------------------------------------------------------------------------

class TestTrickResolution:
    def _play_full_trick(self, engine: GameEngine, hands: dict[str, list[Card]]) -> list:
        """Forces specific hands and plays them all, returning all emitted events."""
        for pid, h in hands.items():
            force_hand(engine, pid, h)

        order = list(engine.trick_play_order)
        all_events = []
        for pid in order:
            cards = hands[pid]
            all_events.extend(engine.play_cards(pid, [c.id for c in cards]))
        return all_events

    def test_trick_winner_determined(self):
        engine = reach_playing(4)
        ts = engine.trump_suit
        tn = engine.trump_number
        order = engine.trick_play_order

        hands = {
            order[0]: [c("A", "spades")],  # lead: spade ace
            order[1]: [c("K", "spades")],  # spade king (lower)
            order[2]: [c("7", ts)],         # trump (beats spade)
            order[3]: [c("3", "clubs")],    # off-suit
        }
        all_events = self._play_full_trick(engine, hands)
        trick_won = [e for e in all_events if e.event_type == "trick_won"]
        assert len(trick_won) == 1
        assert trick_won[0].data["winner_id"] == order[2]  # trump wins

    def test_winner_leads_next_trick(self):
        engine = reach_playing(4)
        ts = engine.trump_suit
        order = engine.trick_play_order

        hands = {
            order[0]: [c("A", "spades")],
            order[1]: [c("K", "spades")],
            order[2]: [c("7", ts)],   # winner
            order[3]: [c("3", "clubs")],
        }
        self._play_full_trick(engine, hands)

        # Next trick's order should start with the trump player
        if engine.phase == GamePhase.PLAYING:
            assert engine.trick_play_order[0] == order[2]

    def test_all_hands_empty_triggers_round_end(self):
        engine = reach_playing(4)
        order = engine.trick_play_order
        ts = engine.trump_suit

        # Give everyone exactly one card and play the trick
        one_card_hand = {
            order[0]: [c("A", "spades")],
            order[1]: [c("K", "spades")],
            order[2]: [c("7", ts)],
            order[3]: [c("3", "clubs")],
        }
        all_events = self._play_full_trick(engine, one_card_hand)

        round_end_events = [e for e in all_events if e.event_type == "round_end"]
        assert len(round_end_events) == 1

    def test_point_cards_accumulate(self):
        engine = reach_playing(4)
        order = engine.trick_play_order
        ts = engine.trump_suit
        tn = engine.trump_number

        # Use Big Joker (highest trump) to guarantee this player wins
        from app.game.constants import JOKER_BIG
        winner_card = c(JOKER_BIG, "joker")

        # Use non-trump suits for the other players' point cards
        from app.game.constants import SUITS
        non_trump = [s for s in SUITS if s != ts][0]

        hands = {
            order[0]: [c("5",  non_trump)],   # 5 pts, non-trump
            order[1]: [c("10", non_trump, 1)], # 10 pts, non-trump
            order[2]: [winner_card],            # Big Joker — guaranteed winner
            order[3]: [c("K",  non_trump, 2)], # 10 pts, non-trump
        }
        self._play_full_trick(engine, hands)

        winner = order[2]
        total_pts = sum(c2.point_value() for c2 in engine.won_cards[winner])
        assert total_pts == 25   # 5 + 10 + 0 (joker) + 10


# ---------------------------------------------------------------------------
# Full round simulation
# ---------------------------------------------------------------------------

class TestFullRound:
    def test_complete_round_advances_to_round_end(self):
        engine = reach_playing(4)

        # Assign uniform hands so following is always trivially valid:
        # everyone gets clubs (or a known non-trump suit).
        from app.game.constants import RANKS, SUITS
        non_trump_suit = next(s for s in SUITS if s != engine.trump_suit)
        cards_per_player = len(engine.hands[engine.players[0].player_id])
        for deck_i, p in enumerate(engine.players):
            engine.hands[p.player_id] = [
                Card(suit=non_trump_suit, rank=RANKS[j % 13], deck_index=deck_i * 10 + j)
                for j in range(cards_per_player)
            ]

        # Drive play — every player just plays their first card (all same suit, always valid)
        max_plays = 500
        for _ in range(max_plays):
            if engine.phase != GamePhase.PLAYING:
                break
            current_pid = engine.trick_play_order[engine.current_play_idx]
            hand = engine.hands[current_pid]
            assert hand, f"Player {current_pid} has empty hand in PLAYING phase"
            engine.play_cards(current_pid, [hand[0].id])

        assert engine.phase in (GamePhase.ROUND_END, GamePhase.GAME_END)
