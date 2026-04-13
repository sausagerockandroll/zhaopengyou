"""Tests for hand parsing, validation, and comparison."""

from __future__ import annotations

import pytest

from app.game.card import Card
from app.game.constants import JOKER_BIG, JOKER_SMALL, HandType
from app.game.hand import (
    HandPlay,
    beats,
    determine_trick_winner,
    parse_hand,
    validate_follow,
)

TRUMP_SUIT = "hearts"
TRUMP_NUM = "2"


def c(rank: str, suit: str, deck_index: int = 0) -> Card:
    return Card(suit=suit, rank=rank, deck_index=deck_index)


def parse(cards: list[Card]) -> HandPlay | None:
    return parse_hand(cards, TRUMP_SUIT, TRUMP_NUM)


# ---------------------------------------------------------------------------
# Single cards
# ---------------------------------------------------------------------------

class TestSingleCard:
    def test_single_non_trump(self):
        hp = parse([c("A", "spades")])
        assert hp is not None
        assert hp.hand_type == HandType.SINGLE
        assert hp.effective_suit == "spades"
        assert hp.card_count == 1

    def test_single_trump_suit(self):
        hp = parse([c("5", "hearts")])
        assert hp is not None
        assert hp.effective_suit == "trump"

    def test_single_trump_number(self):
        hp = parse([c("2", "clubs")])
        assert hp is not None
        assert hp.effective_suit == "trump"

    def test_big_joker(self):
        hp = parse([c(JOKER_BIG, "joker")])
        assert hp is not None
        assert hp.effective_suit == "trump"

    def test_empty_is_none(self):
        assert parse([]) is None


# ---------------------------------------------------------------------------
# Pairs
# ---------------------------------------------------------------------------

class TestPairs:
    def test_valid_pair(self):
        hp = parse([c("A", "spades", 0), c("A", "spades", 1)])
        assert hp is not None
        assert hp.hand_type == HandType.PAIR
        assert hp.card_count == 2

    def test_pair_different_suits_invalid(self):
        hp = parse([c("A", "spades"), c("A", "hearts")])
        # hearts is trump, spades is not → different effective suits
        assert hp is None

    def test_pair_trump_numbers_same_suit(self):
        # Two trump numbers in the SAME non-trump suit (two decks) → valid pair
        hp = parse([c("2", "clubs", 0), c("2", "clubs", 1)])
        assert hp is not None
        assert hp.hand_type == HandType.PAIR
        assert hp.effective_suit == "trump"

    def test_pair_trump_numbers_different_suits_not_a_pair(self):
        # Two trump numbers in DIFFERENT non-trump suits → NOT a pair (must be identical)
        hp = parse([c("2", "clubs", 0), c("2", "diamonds", 0)])
        assert hp is None

    def test_joker_pair(self):
        hp = parse([c(JOKER_BIG, "joker", 0), c(JOKER_BIG, "joker", 1)])
        assert hp is not None
        assert hp.hand_type == HandType.PAIR
        assert hp.effective_suit == "trump"

    def test_big_small_joker_not_a_pair(self):
        # Big and small joker are different ranks → not identical
        hp = parse([c(JOKER_BIG, "joker", 0), c(JOKER_SMALL, "joker", 0)])
        assert hp is None  # different group keys → invalid


# ---------------------------------------------------------------------------
# Triples / Quads
# ---------------------------------------------------------------------------

class TestMultiples:
    def test_triple(self):
        hp = parse([c("K", "clubs", 0), c("K", "clubs", 1), c("K", "clubs", 2)])
        assert hp is not None
        assert hp.hand_type == HandType.TRIPLE

    def test_quad(self):
        hp = parse([c("Q", "spades", i) for i in range(4)])
        assert hp is not None
        assert hp.hand_type == HandType.QUAD


# ---------------------------------------------------------------------------
# Pair runs
# ---------------------------------------------------------------------------

class TestPairRuns:
    def test_valid_pair_run_3(self):
        # 3♠-4♠-5♠ pairs (non-trump)
        cards = [
            c("3", "spades", 0), c("3", "spades", 1),
            c("4", "spades", 0), c("4", "spades", 1),
            c("5", "spades", 0), c("5", "spades", 1),
        ]
        hp = parse(cards)
        assert hp is not None
        assert hp.hand_type == HandType.PAIR_RUN
        assert hp.num_groups == 3
        assert hp.group_size == 2

    def test_pair_run_too_short(self):
        cards = [
            c("3", "spades", 0), c("3", "spades", 1),
            c("4", "spades", 0), c("4", "spades", 1),
        ]
        hp = parse(cards)
        assert hp is None  # 2 pairs = too short

    def test_pair_run_gap_invalid(self):
        # 3♠-5♠ (gap at 4) — but 4♠ is not trump, so it should be in the run
        cards = [
            c("3", "spades", 0), c("3", "spades", 1),
            c("5", "spades", 0), c("5", "spades", 1),
            c("6", "spades", 0), c("6", "spades", 1),
        ]
        hp = parse(cards)
        assert hp is None  # gap at 4

    def test_pair_run_excludes_trump_number(self):
        # If trump number is 4, then 3♠-5♠-6♠ are consecutive (4 is excluded from runs)
        hp4 = parse_hand(
            [
                c("3", "spades", 0), c("3", "spades", 1),
                c("5", "spades", 0), c("5", "spades", 1),
                c("6", "spades", 0), c("6", "spades", 1),
            ],
            trump_suit="hearts",
            trump_number="4",
        )
        # 3-5-6 with 4 excluded: 3 and 5 are consecutive in valid_run_ranks (index 0 and 1
        # since 4 is removed), and 6 is index 2 → valid
        assert hp4 is not None
        assert hp4.hand_type == HandType.PAIR_RUN

    def test_pair_run_wraps_around_ace_invalid(self):
        # A-2-3 should not be valid run (no wrap around)
        cards = [
            c("A", "spades", 0), c("A", "spades", 1),
            c("3", "spades", 0), c("3", "spades", 1),  # 2 is trump number, skip
            c("4", "spades", 0), c("4", "spades", 1),
        ]
        hp = parse(cards)
        # A is at index 12, 3 is at index 1 (after removing 2) → not consecutive
        assert hp is None


# ---------------------------------------------------------------------------
# Triple runs
# ---------------------------------------------------------------------------

class TestTripleRuns:
    def test_valid_triple_run(self):
        cards = [
            c("7", "clubs", 0), c("7", "clubs", 1), c("7", "clubs", 2),
            c("8", "clubs", 0), c("8", "clubs", 1), c("8", "clubs", 2),
        ]
        hp = parse(cards)
        assert hp is not None
        assert hp.hand_type == HandType.TRIPLE_RUN
        assert hp.num_groups == 2

    def test_triple_run_one_group_invalid(self):
        cards = [c("7", "clubs", 0), c("7", "clubs", 1), c("7", "clubs", 2)]
        hp = parse(cards)
        assert hp.hand_type == HandType.TRIPLE   # just a triple, not a run


# ---------------------------------------------------------------------------
# Hand comparison (beats)
# ---------------------------------------------------------------------------

class TestBeats:
    def test_trump_single_beats_non_trump(self):
        non_trump = parse([c("A", "spades")])
        trump = parse([c("3", "hearts")])  # hearts is trump suit
        assert beats(non_trump, trump)
        assert not beats(trump, non_trump)

    def test_higher_trump_beats_lower_trump(self):
        sj = parse([c(JOKER_SMALL, "joker")])
        bj = parse([c(JOKER_BIG, "joker")])
        assert beats(sj, bj)
        assert not beats(bj, sj)

    def test_higher_rank_beats_lower_same_suit(self):
        low = parse([c("3", "spades")])
        high = parse([c("K", "spades")])
        assert beats(low, high)
        assert not beats(high, low)

    def test_different_non_trump_suits_challenger_loses(self):
        lead = parse([c("A", "spades")])
        other = parse([c("A", "clubs")])
        assert not beats(lead, other)

    def test_pair_beats_pair(self):
        low = parse([c("3", "spades", 0), c("3", "spades", 1)])
        high = parse([c("K", "spades", 0), c("K", "spades", 1)])
        assert beats(low, high)

    def test_different_format_does_not_beat(self):
        single = parse([c("A", "spades")])
        pair   = parse([c("3", "spades", 0), c("3", "spades", 1)])
        assert not beats(single, pair)


# ---------------------------------------------------------------------------
# Trick winner determination
# ---------------------------------------------------------------------------

class TestTrickWinner:
    def test_lead_wins_with_no_challengers(self):
        plays = {"p0": [c("A", "spades")], "p1": [c("5", "clubs")]}
        order = ["p0", "p1"]
        winner = determine_trick_winner(plays, order, "hearts", "2")
        assert winner == "p0"  # clubs can't beat spades

    def test_trump_beats_lead(self):
        plays = {"p0": [c("A", "spades")], "p1": [c("3", "hearts")]}  # hearts = trump
        order = ["p0", "p1"]
        winner = determine_trick_winner(plays, order, "hearts", "2")
        assert winner == "p1"

    def test_highest_trump_wins(self):
        plays = {
            "p0": [c("A", "spades")],
            "p1": [c("3", "hearts")],
            "p2": [c(JOKER_BIG, "joker")],
        }
        order = ["p0", "p1", "p2"]
        winner = determine_trick_winner(plays, order, "hearts", "2")
        assert winner == "p2"

    def test_first_player_wins_tie(self):
        # Both play same non-trump suit → first player wins
        plays = {"p0": [c("A", "spades")], "p1": [c("A", "spades", 1)]}
        order = ["p0", "p1"]
        winner = determine_trick_winner(plays, order, "hearts", "2")
        # p1's A♠ has same rank → does not STRICTLY beat p0's A♠
        assert winner == "p0"


# ---------------------------------------------------------------------------
# Follow validation
# ---------------------------------------------------------------------------

class TestValidateFollow:
    def test_must_follow_suit(self):
        lead = parse([c("3", "spades")])
        full_hand = [c("7", "spades"), c("K", "clubs")]
        # Must play 7♠ since we have a spade
        valid, _ = validate_follow([c("7", "spades")], full_hand, lead, "hearts", "2")
        assert valid
        invalid, msg = validate_follow([c("K", "clubs")], full_hand, lead, "hearts", "2")
        assert not invalid

    def test_can_play_any_when_void(self):
        lead = parse([c("3", "spades")])
        full_hand = [c("K", "clubs"), c("A", "diamonds")]
        valid, _ = validate_follow([c("K", "clubs")], full_hand, lead, "hearts", "2")
        assert valid

    def test_must_play_pair_if_available(self):
        lead = parse([c("3", "spades", 0), c("3", "spades", 1)])
        full_hand = [
            c("7", "spades", 0), c("7", "spades", 1),
            c("K", "clubs", 0),
        ]
        # Must play the spade pair (7♠+7♠)
        valid, _ = validate_follow(
            [c("7", "spades", 0), c("7", "spades", 1)], full_hand, lead, "hearts", "2"
        )
        assert valid

        # Playing 7♠ + K♣ is invalid (has a pair in suit, must use it)
        invalid, msg = validate_follow(
            [c("7", "spades", 0), c("K", "clubs", 0)], full_hand, lead, "hearts", "2"
        )
        assert not invalid

    def test_can_play_off_suit_when_void_of_suit(self):
        lead = parse([c("A", "spades", 0), c("A", "spades", 1)])
        full_hand = [c("3", "clubs", 0), c("5", "clubs", 0)]
        # No spades → play anything
        valid, _ = validate_follow(
            [c("3", "clubs", 0), c("5", "clubs", 0)], full_hand, lead, "hearts", "2"
        )
        assert valid

    def test_wrong_card_count_invalid(self):
        lead = parse([c("A", "spades", 0), c("A", "spades", 1)])
        full_hand = [c("3", "clubs", 0), c("5", "clubs", 0)]
        invalid, msg = validate_follow([c("3", "clubs", 0)], full_hand, lead, "hearts", "2")
        assert not invalid
        assert "2" in msg
