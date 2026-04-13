"""Card and deck data structures for Zhao Peng You."""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.game.constants import (
    CARDS_PER_DECK,
    JOKER_BIG,
    JOKER_SMALL,
    JOKER_SUIT,
    MAX_DECKS,
    MIN_BURIED,
    MIN_DECKS,
    POINT_VALUES,
    RANK_ORDER,
    RANKS,
    SUITS,
)


@dataclass(frozen=True)
class Card:
    """Immutable representation of a playing card."""

    suit: str       # 'hearts' | 'diamonds' | 'clubs' | 'spades' | 'joker'
    rank: str       # '2'-'A' | 'small_joker' | 'big_joker'
    deck_index: int  # Which physical deck this card came from (0-based)

    @property
    def id(self) -> str:
        """Unique identifier for this card instance."""
        return f"{self.rank}_{self.suit}_{self.deck_index}"

    @property
    def is_joker(self) -> bool:
        return self.rank in (JOKER_SMALL, JOKER_BIG)

    @property
    def is_big_joker(self) -> bool:
        return self.rank == JOKER_BIG

    @property
    def is_small_joker(self) -> bool:
        return self.rank == JOKER_SMALL

    def point_value(self) -> int:
        """Returns the scoring value of this card (5→5, 10→10, K→10, others→0)."""
        return POINT_VALUES.get(self.rank, 0)

    def is_trump(self, trump_suit: str, trump_number: str) -> bool:
        """True if this card is a trump card in the given trump context."""
        if self.is_joker:
            return True
        if self.rank == trump_number:
            return True
        if self.suit == trump_suit:
            return True
        return False

    def effective_suit(self, trump_suit: str, trump_number: str) -> str:
        """Returns 'trump' for trump cards; original suit for non-trump cards."""
        return "trump" if self.is_trump(trump_suit, trump_number) else self.suit

    def trump_strength(self, trump_suit: str, trump_number: str) -> int:
        """
        Returns the trump ordering value (higher = stronger).
        Returns -1 if this card is not a trump.

        Ordering: Big Joker > Small Joker > trump_number+trump_suit
                  > trump_number+other_suits > trump_suit cards by rank
        """
        if self.rank == JOKER_BIG:
            return 1000
        if self.rank == JOKER_SMALL:
            return 999
        if self.rank == trump_number and self.suit == trump_suit:
            return 998
        if self.rank == trump_number:
            # All non-trump-suit trump numbers are equal in strength
            return 997
        if self.suit == trump_suit:
            # Trump suit cards ranked by their natural order (excluding trump number)
            return 500 + RANK_ORDER[self.rank]
        return -1

    def overall_strength(self, trump_suit: str, trump_number: str) -> int:
        """
        Returns the card's strength for comparison in trick-taking.
        Trump cards use trump_strength; non-trump cards use RANK_ORDER.
        """
        ts = self.trump_strength(trump_suit, trump_number)
        if ts >= 0:
            return ts
        return RANK_ORDER[self.rank]

    def to_dict(self) -> dict:
        return {"id": self.id, "suit": self.suit, "rank": self.rank, "deck_index": self.deck_index}

    @classmethod
    def from_id(cls, card_id: str) -> "Card":
        """Reconstruct a Card from its id string."""
        parts = card_id.rsplit("_", 1)
        deck_index = int(parts[1])
        remainder = parts[0]
        # Handle special joker ranks
        if remainder == f"{JOKER_SMALL}_{JOKER_SUIT}":
            return cls(suit=JOKER_SUIT, rank=JOKER_SMALL, deck_index=deck_index)
        if remainder == f"{JOKER_BIG}_{JOKER_SUIT}":
            return cls(suit=JOKER_SUIT, rank=JOKER_BIG, deck_index=deck_index)
        # Regular card: rank_suit_deckindex → split on last two underscores
        # id format: rank_suit_deckindex
        rank_suit = remainder
        last_under = rank_suit.rfind("_")
        suit = rank_suit[last_under + 1:]
        rank = rank_suit[:last_under]
        return cls(suit=suit, rank=rank, deck_index=deck_index)

    @classmethod
    def from_dict(cls, d: dict) -> "Card":
        return cls(suit=d["suit"], rank=d["rank"], deck_index=d["deck_index"])


def create_deck(deck_index: int) -> list[Card]:
    """Creates a single ordered deck of 54 cards (52 + 2 jokers)."""
    cards: list[Card] = []
    for suit in SUITS:
        for rank in RANKS:
            cards.append(Card(suit=suit, rank=rank, deck_index=deck_index))
    cards.append(Card(suit=JOKER_SUIT, rank=JOKER_SMALL, deck_index=deck_index))
    cards.append(Card(suit=JOKER_SUIT, rank=JOKER_BIG, deck_index=deck_index))
    return cards


def create_shuffled_decks(num_decks: int) -> list[Card]:
    """Creates multiple decks, combines them, and shuffles."""
    all_cards: list[Card] = []
    for i in range(num_decks):
        all_cards.extend(create_deck(i))
    random.shuffle(all_cards)
    return all_cards


def num_decks_for_players(num_players: int) -> int:
    """Returns the number of decks to use: 1 per 2 players, clamped to [2, 5]."""
    return max(MIN_DECKS, min(MAX_DECKS, num_players // 2))


def buried_card_count(num_players: int, num_decks: int) -> int:
    """
    Returns the minimum number of cards to remove from the deck so that:
    - The remaining cards divide evenly among players
    - At least MIN_BURIED cards are removed
    """
    total = num_decks * CARDS_PER_DECK
    n = MIN_BURIED
    while n < total and (total - n) % num_players != 0:
        n += 1
    return n


def cards_to_dicts(cards: list[Card]) -> list[dict]:
    return [c.to_dict() for c in cards]


def dicts_to_cards(dicts: list[dict]) -> list[Card]:
    return [Card.from_dict(d) for d in dicts]
