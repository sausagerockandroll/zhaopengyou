"""Hand parsing, validation, and comparison for Zhao Peng You."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.game.card import Card
from app.game.constants import (
    GROUP_SIZE_TO_HAND_TYPE,
    GROUP_SIZE_TO_RUN_HAND_TYPE,
    JOKER_BIG,
    JOKER_SMALL,
    MIN_RUN_GROUPS,
    RANK_ORDER,
    RANKS,
    HandType,
)


@dataclass
class HandPlay:
    """A parsed, classified set of cards played as a single play."""

    cards: list[Card]
    hand_type: HandType
    effective_suit: str      # 'trump' or a suit name
    group_size: int          # cards per group (1 for single, 2 for pair, etc.)
    num_groups: int          # groups in run (1 for non-run types)
    lead_strength: int       # overall_strength of the "best" card, used for comparison

    @property
    def card_count(self) -> int:
        return len(self.cards)

    @property
    def is_run(self) -> bool:
        return self.hand_type in (HandType.PAIR_RUN, HandType.TRIPLE_RUN, HandType.QUAD_RUN)


def _run_eligible_ranks(trump_number: str) -> list[str]:
    """Returns ranks that can participate in runs (all ranks except the trump number)."""
    return [r for r in RANKS if r != trump_number]


def _card_group_key(card: Card, trump_suit: str, trump_number: str) -> tuple[str, str]:
    """
    Returns a grouping key for cards that count as 'identical' for pairs/triples.
    Jokers group by rank only (big jokers together, small jokers together).
    All other cards — including off-suit trump-number cards — must share the same
    exact (rank, suit) to count as a pair/triple.
    """
    if card.is_joker:
        return (card.rank, card.rank)  # e.g. ('big_joker', 'big_joker')
    return (card.rank, card.suit)


def parse_hand(
    cards: list[Card],
    trump_suit: str,
    trump_number: str,
) -> HandPlay | None:
    """
    Parses a list of cards into a HandPlay.
    Returns None if the cards do not form a valid hand type.

    Valid hand types:
      - Single (1 card)
      - Set of identical cards: Pair, Triple, Quad, Nuple
      - Pair run: ≥3 consecutive pairs of the same effective suit
      - Triple run: ≥2 consecutive triples of the same effective suit
      - Quad run: ≥2 consecutive quads of the same effective suit
    """
    if not cards:
        return None

    n = len(cards)
    eff_suits = [c.effective_suit(trump_suit, trump_number) for c in cards]

    # ---------- Single card ----------
    if n == 1:
        card = cards[0]
        return HandPlay(
            cards=cards,
            hand_type=HandType.SINGLE,
            effective_suit=eff_suits[0],
            group_size=1,
            num_groups=1,
            lead_strength=card.overall_strength(trump_suit, trump_number),
        )

    # All cards must share the same effective suit for multi-card hands
    if len(set(eff_suits)) != 1:
        return None
    eff_suit = eff_suits[0]

    # Group cards by their "identical" key
    group_counts: Counter = Counter(
        _card_group_key(c, trump_suit, trump_number) for c in cards
    )
    group_sizes = list(group_counts.values())

    # All groups must be the same size
    if len(set(group_sizes)) != 1:
        return None

    group_size = group_sizes[0]
    num_groups = len(group_counts)
    total_expected = group_size * num_groups

    if total_expected != n:
        return None

    # ---------- Non-run set (single group) ----------
    if num_groups == 1:
        hand_type = GROUP_SIZE_TO_HAND_TYPE.get(group_size, HandType.NUPLE)
        # Lead strength = the card itself
        rep_card = cards[0]
        return HandPlay(
            cards=cards,
            hand_type=hand_type,
            effective_suit=eff_suit,
            group_size=group_size,
            num_groups=1,
            lead_strength=rep_card.overall_strength(trump_suit, trump_number),
        )

    # ---------- Run (multiple groups) ----------
    min_groups = MIN_RUN_GROUPS.get(group_size)
    if min_groups is None or num_groups < min_groups:
        return None  # No run type defined for this group size, or too short

    # Determine run rank keys for each group
    # Jokers and trump-number-off-suit cards cannot participate in runs
    run_ranks_valid = _run_eligible_ranks(trump_number)

    group_ranks: list[str] = []
    for (rank_key, suit_key) in group_counts.keys():
        # Jokers: not eligible for runs
        if rank_key in (JOKER_SMALL, JOKER_BIG):
            return None
        # Trump-number off-suit: not eligible for runs
        if suit_key == "__trump_number_off__":
            return None
        # Trump-number in trump suit: not eligible for runs
        if rank_key == trump_number:
            return None
        group_ranks.append(rank_key)

    # All group ranks must be in the valid run rank list
    try:
        indices = [run_ranks_valid.index(r) for r in group_ranks]
    except ValueError:
        return None

    sorted_indices = sorted(indices)
    expected_consecutive = list(range(sorted_indices[0], sorted_indices[0] + num_groups))
    if sorted_indices != expected_consecutive:
        return None  # Not consecutive

    # For trump runs: all cards must be from the actual trump suit (not jokers/trump numbers)
    if eff_suit == "trump":
        for c in cards:
            if c.is_joker or c.rank == trump_number:
                return None
            if c.suit != trump_suit:
                return None

    # Lead strength = highest group's representative card strength
    highest_rank = run_ranks_valid[sorted_indices[-1]]
    rep_card = next(c for c in cards if c.rank == highest_rank)

    run_hand_type = GROUP_SIZE_TO_RUN_HAND_TYPE.get(group_size)
    if run_hand_type is None:
        return None

    return HandPlay(
        cards=cards,
        hand_type=run_hand_type,
        effective_suit=eff_suit,
        group_size=group_size,
        num_groups=num_groups,
        lead_strength=rep_card.overall_strength(trump_suit, trump_number),
    )


def beats(
    current_best: HandPlay,
    challenger: HandPlay,
) -> bool:
    """
    Returns True if challenger beats current_best.

    Rules:
    - Must have the same total card count (same format overall)
    - Must have the same group_size and num_groups (same structural format)
    - Trump beats non-trump
    - Within same effective suit, higher lead_strength wins
    - Different non-trump suits: challenger cannot beat
    """
    if current_best.card_count != challenger.card_count:
        return False
    if current_best.group_size != challenger.group_size:
        return False
    if current_best.num_groups != challenger.num_groups:
        return False

    # Trump beats non-trump
    if challenger.effective_suit == "trump" and current_best.effective_suit != "trump":
        return True
    if challenger.effective_suit != "trump" and current_best.effective_suit == "trump":
        return False

    # Same effective suit: compare strength
    if challenger.effective_suit == current_best.effective_suit:
        return challenger.lead_strength > current_best.lead_strength

    # Different non-trump suits: challenger cannot beat
    return False


def determine_trick_winner(
    plays: dict[str, list[Card]],
    play_order: list[str],
    trump_suit: str,
    trump_number: str,
) -> str:
    """
    Determines the winner of a trick.

    Args:
        plays: Mapping of player_id → cards they played.
        play_order: Players in the order they played (first = lead).
        trump_suit: Current trump suit.
        trump_number: Current trump number (level).

    Returns:
        The player_id of the trick winner.
    """
    lead_player = play_order[0]
    lead_hand = parse_hand(plays[lead_player], trump_suit, trump_number)
    assert lead_hand is not None, "Lead player's hand must be parseable"

    current_winner = lead_player
    current_best = lead_hand

    for player_id in play_order[1:]:
        challenger_hand = parse_hand(plays[player_id], trump_suit, trump_number)
        if challenger_hand is None:
            continue
        if beats(current_best, challenger_hand):
            current_winner = player_id
            current_best = challenger_hand

    return current_winner


def _count_groups_in_hand(
    hand: list[Card],
    group_size: int,
    eff_suit: str,
    trump_suit: str,
    trump_number: str,
) -> int:
    """Count how many complete groups of `group_size` exist in `hand` for `eff_suit`."""
    suit_cards = [c for c in hand if c.effective_suit(trump_suit, trump_number) == eff_suit]
    counts: Counter = Counter(_card_group_key(c, trump_suit, trump_number) for c in suit_cards)
    return sum(cnt // group_size for cnt in counts.values())


def _count_consecutive_run_groups(
    hand: list[Card],
    group_size: int,
    min_run_len: int,
    eff_suit: str,
    trump_suit: str,
    trump_number: str,
) -> int:
    """
    Returns the total number of groups that are part of any valid run of ≥ min_run_len
    in the hand for the given effective suit and group size.
    """
    suit_cards = [c for c in hand if c.effective_suit(trump_suit, trump_number) == eff_suit]
    counts: Counter = Counter(_card_group_key(c, trump_suit, trump_number) for c in suit_cards)
    run_ranks = _run_eligible_ranks(trump_number)

    # Build rank → how many complete groups available
    rank_group_count: dict[str, int] = {}
    for (rank_key, suit_key), cnt in counts.items():
        if rank_key in (JOKER_SMALL, JOKER_BIG):
            continue
        if suit_key == "__trump_number_off__":
            continue
        if rank_key == trump_number:
            continue
        if rank_key not in run_ranks:
            continue
        if eff_suit == "trump":
            # Must be actual trump suit for trump runs
            # Find a card of this rank to check its suit
            for c in suit_cards:
                if c.rank == rank_key:
                    if c.suit != trump_suit:
                        break
                    else:
                        rank_group_count[rank_key] = cnt // group_size
                        break
        else:
            rank_group_count[rank_key] = cnt // group_size

    # Find runs
    total_in_runs = 0
    in_run_indices: set[int] = set()

    sorted_rank_indices = sorted(
        (run_ranks.index(r), r) for r in rank_group_count if rank_group_count[r] >= 1
    )

    # Simple greedy: find consecutive sequences of length >= min_run_len
    if not sorted_rank_indices:
        return 0

    indices_only = [idx for idx, _ in sorted_rank_indices]
    i = 0
    while i < len(indices_only):
        j = i
        while j + 1 < len(indices_only) and indices_only[j + 1] == indices_only[j] + 1:
            j += 1
        run_len = j - i + 1
        if run_len >= min_run_len:
            total_in_runs += run_len
        i = j + 1

    return total_in_runs


def validate_follow(
    played_cards: list[Card],
    full_hand: list[Card],
    lead: HandPlay,
    trump_suit: str,
    trump_number: str,
) -> tuple[bool, str]:
    """
    Validates that played_cards is a legal follow to the lead play.

    Returns (True, "") if valid, or (False, reason) if not.

    Follow rules:
    1. Must play exactly len(lead.cards) cards.
    2. All played cards must be in the player's hand.
    3. If player has cards of lead suit → must play as many as possible (up to lead count).
    4. Among suit-matched cards, must use as many matching groups (pairs/triples) as possible.
    5. For runs, must use as many complete run-groups as possible.
    """
    lead_count = lead.card_count
    lead_suit = lead.effective_suit

    if len(played_cards) != lead_count:
        return False, f"Must play exactly {lead_count} cards (played {len(played_cards)})"

    # Verify all played cards exist in hand
    hand_copy = list(full_hand)
    for c in played_cards:
        if c in hand_copy:
            hand_copy.remove(c)
        else:
            return False, f"Card {c.id} not in your hand"

    # Cards in hand matching the lead effective suit
    available_suit_cards = [
        c for c in full_hand
        if c.effective_suit(trump_suit, trump_number) == lead_suit
    ]
    played_suit_cards = [
        c for c in played_cards
        if c.effective_suit(trump_suit, trump_number) == lead_suit
    ]

    avail_suit_count = len(available_suit_cards)

    # --- Suit following ---
    if avail_suit_count >= lead_count:
        # Must fill all played slots with lead-suit cards
        if len(played_suit_cards) < lead_count:
            return False, f"You have enough {lead_suit} cards — must play only {lead_suit} cards"
    else:
        # Must use all available suit cards
        if len(played_suit_cards) < avail_suit_count:
            return False, f"Must play all your {lead_suit} cards first"

    # --- Format following (groups) ---
    if lead.group_size > 1 and len(played_suit_cards) >= lead.group_size:
        available_groups = _count_groups_in_hand(
            available_suit_cards, lead.group_size, lead_suit, trump_suit, trump_number
        )
        played_groups = _count_groups_in_hand(
            played_suit_cards, lead.group_size, lead_suit, trump_suit, trump_number
        )
        needed_groups = min(lead.num_groups, available_groups)
        if played_groups < needed_groups:
            return (
                False,
                f"Must include {needed_groups} groups of {lead.group_size} "
                f"(found {played_groups})",
            )

    return True, ""


def is_valid_hand(
    cards: list[Card],
    trump_suit: str,
    trump_number: str,
) -> bool:
    """Returns True if cards form a valid playable hand."""
    return parse_hand(cards, trump_suit, trump_number) is not None
