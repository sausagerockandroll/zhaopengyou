"""Tests for card and deck utilities."""

import pytest

from app.game.card import (
    Card,
    buried_card_count,
    create_deck,
    create_shuffled_decks,
    dicts_to_cards,
    num_decks_for_players,
)
from app.game.constants import CARDS_PER_DECK, JOKER_BIG, JOKER_SMALL, RANKS, SUITS


# ---------------------------------------------------------------------------
# Single deck
# ---------------------------------------------------------------------------

class TestCreateDeck:
    def test_deck_has_correct_size(self):
        deck = create_deck(0)
        assert len(deck) == CARDS_PER_DECK  # 54

    def test_deck_has_all_suits_and_ranks(self):
        deck = create_deck(0)
        non_jokers = [c for c in deck if not c.is_joker]
        assert len(non_jokers) == 52
        for suit in SUITS:
            for rank in RANKS:
                assert any(c.suit == suit and c.rank == rank for c in non_jokers)

    def test_deck_has_both_jokers(self):
        deck = create_deck(0)
        jokers = [c for c in deck if c.is_joker]
        assert len(jokers) == 2
        assert any(c.is_big_joker for c in jokers)
        assert any(c.is_small_joker for c in jokers)

    def test_deck_index_is_set(self):
        deck = create_deck(3)
        assert all(c.deck_index == 3 for c in deck)

    def test_card_ids_are_unique(self):
        deck = create_deck(0)
        ids = [c.id for c in deck]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Multiple decks
# ---------------------------------------------------------------------------

class TestCreateShuffledDecks:
    def test_two_decks_size(self):
        cards = create_shuffled_decks(2)
        assert len(cards) == 2 * CARDS_PER_DECK

    def test_five_decks_size(self):
        cards = create_shuffled_decks(5)
        assert len(cards) == 5 * CARDS_PER_DECK

    def test_all_deck_indices_present(self):
        cards = create_shuffled_decks(3)
        indices = {c.deck_index for c in cards}
        assert indices == {0, 1, 2}

    def test_shuffled_order_differs(self):
        """Two calls should (almost certainly) produce different orderings."""
        a = create_shuffled_decks(2)
        b = create_shuffled_decks(2)
        assert [c.id for c in a] != [c.id for c in b]  # vanishingly rare false failure


# ---------------------------------------------------------------------------
# Deck count per player count
# ---------------------------------------------------------------------------

class TestNumDecks:
    @pytest.mark.parametrize("players,expected", [
        (4, 2), (5, 2), (6, 3), (7, 3), (8, 4), (9, 4), (10, 5), (11, 5),
    ])
    def test_deck_count(self, players, expected):
        assert num_decks_for_players(players) == expected


# ---------------------------------------------------------------------------
# Buried card count
# ---------------------------------------------------------------------------

class TestBuriedCount:
    def test_six_players_three_decks(self):
        n = buried_card_count(6, 3)
        total = 3 * CARDS_PER_DECK
        assert (total - n) % 6 == 0
        assert n >= 6

    def test_four_players_two_decks(self):
        n = buried_card_count(4, 2)
        total = 2 * CARDS_PER_DECK
        assert (total - n) % 4 == 0
        assert n >= 6

    @pytest.mark.parametrize("players", range(4, 12))
    def test_divisibility_all_player_counts(self, players):
        decks = num_decks_for_players(players)
        n = buried_card_count(players, decks)
        total = decks * CARDS_PER_DECK
        assert (total - n) % players == 0
        assert n >= 6


# ---------------------------------------------------------------------------
# Card trump logic
# ---------------------------------------------------------------------------

class TestCardTrump:
    TRUMP_SUIT = "hearts"
    TRUMP_NUM = "2"

    def test_big_joker_is_trump(self):
        c = Card(suit="joker", rank=JOKER_BIG, deck_index=0)
        assert c.is_trump(self.TRUMP_SUIT, self.TRUMP_NUM)

    def test_small_joker_is_trump(self):
        c = Card(suit="joker", rank=JOKER_SMALL, deck_index=0)
        assert c.is_trump(self.TRUMP_SUIT, self.TRUMP_NUM)

    def test_trump_number_in_trump_suit_is_trump(self):
        c = Card(suit="hearts", rank="2", deck_index=0)
        assert c.is_trump(self.TRUMP_SUIT, self.TRUMP_NUM)

    def test_trump_number_in_other_suit_is_trump(self):
        c = Card(suit="spades", rank="2", deck_index=0)
        assert c.is_trump(self.TRUMP_SUIT, self.TRUMP_NUM)

    def test_trump_suit_card_is_trump(self):
        c = Card(suit="hearts", rank="5", deck_index=0)
        assert c.is_trump(self.TRUMP_SUIT, self.TRUMP_NUM)

    def test_non_trump_card_is_not_trump(self):
        c = Card(suit="spades", rank="K", deck_index=0)
        assert not c.is_trump(self.TRUMP_SUIT, self.TRUMP_NUM)

    def test_trump_strength_ordering(self):
        bj = Card(suit="joker", rank=JOKER_BIG, deck_index=0)
        sj = Card(suit="joker", rank=JOKER_SMALL, deck_index=0)
        tn_ts = Card(suit="hearts", rank="2", deck_index=0)   # trump number + trump suit
        tn_os = Card(suit="spades", rank="2", deck_index=0)   # trump number + other suit
        ts_k  = Card(suit="hearts", rank="K", deck_index=0)   # trump suit King
        ts_3  = Card(suit="hearts", rank="3", deck_index=0)   # trump suit 3

        ts = self.TRUMP_SUIT
        tn = self.TRUMP_NUM
        assert bj.trump_strength(ts, tn) > sj.trump_strength(ts, tn)
        assert sj.trump_strength(ts, tn) > tn_ts.trump_strength(ts, tn)
        assert tn_ts.trump_strength(ts, tn) > tn_os.trump_strength(ts, tn)
        assert tn_os.trump_strength(ts, tn) > ts_k.trump_strength(ts, tn)
        assert ts_k.trump_strength(ts, tn) > ts_3.trump_strength(ts, tn)

    def test_non_trump_trump_strength_is_negative(self):
        c = Card(suit="clubs", rank="A", deck_index=0)
        assert c.trump_strength("hearts", "2") == -1


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestCardSerialization:
    def test_to_and_from_dict(self):
        c = Card(suit="spades", rank="A", deck_index=2)
        assert Card.from_dict(c.to_dict()) == c

    def test_joker_round_trip(self):
        c = Card(suit="joker", rank=JOKER_BIG, deck_index=1)
        assert Card.from_dict(c.to_dict()) == c

    def test_dicts_to_cards(self):
        cards = [Card(suit="hearts", rank="5", deck_index=0), Card(suit="joker", rank=JOKER_SMALL, deck_index=1)]
        dicts = [c.to_dict() for c in cards]
        restored = dicts_to_cards(dicts)
        assert restored == cards
