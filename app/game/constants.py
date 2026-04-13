"""Game constants for Zhao Peng You."""

from enum import Enum

# Card suits (non-joker)
SUITS: list[str] = ["hearts", "diamonds", "clubs", "spades"]

# Card ranks in ascending order
RANKS: list[str] = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

# Map rank -> position index (0=lowest, 12=highest)
RANK_ORDER: dict[str, int] = {r: i for i, r in enumerate(RANKS)}

# Point values for scoring cards
POINT_VALUES: dict[str, int] = {"5": 5, "10": 10, "K": 10}

# Joker constants
JOKER_SMALL = "small_joker"
JOKER_BIG = "big_joker"
JOKER_SUIT = "joker"

# Player limits
MIN_PLAYERS = 4
MAX_PLAYERS = 11

# Deck limits (1 deck per 2 players, clamped)
MIN_DECKS = 2
MAX_DECKS = 5

# Cards per deck: 52 regular + 2 jokers
CARDS_PER_DECK = 54

# Minimum cards to bury
MIN_BURIED = 6


class GamePhase(str, Enum):
    WAITING = "waiting"
    DRAWING = "drawing"
    BURYING = "burying"
    CALLING = "calling"
    PLAYING = "playing"
    ROUND_END = "round_end"
    GAME_END = "game_end"


class HandType(str, Enum):
    SINGLE = "single"
    PAIR = "pair"
    TRIPLE = "triple"
    QUAD = "quad"
    NUPLE = "nuple"       # 5 or more identical (possible with 5 decks)
    PAIR_RUN = "pair_run"
    TRIPLE_RUN = "triple_run"
    QUAD_RUN = "quad_run"


# Map group size -> HandType for plain sets
GROUP_SIZE_TO_HAND_TYPE: dict[int, HandType] = {
    1: HandType.SINGLE,
    2: HandType.PAIR,
    3: HandType.TRIPLE,
    4: HandType.QUAD,
}

# Map group size -> run HandType
GROUP_SIZE_TO_RUN_HAND_TYPE: dict[int, HandType] = {
    2: HandType.PAIR_RUN,
    3: HandType.TRIPLE_RUN,
    4: HandType.QUAD_RUN,
}

# Minimum run length per group size (in number of consecutive groups)
MIN_RUN_GROUPS: dict[int, int] = {
    2: 3,   # pair runs: min 3 pairs
    3: 2,   # triple runs: min 2 triples
    4: 2,   # quad runs: min 2 quads
}
