"""
Game engine for Zhao Peng You.

Contains the GameEngine class that manages all game state transitions
for a single room/game instance.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.game.card import (
    Card,
    buried_card_count,
    cards_to_dicts,
    create_shuffled_decks,
    dicts_to_cards,
    num_decks_for_players,
)
from app.game.constants import (
    JOKER_BIG,
    JOKER_SMALL,
    MAX_PLAYERS,
    MIN_PLAYERS,
    RANK_ORDER,
    RANKS,
    GamePhase,
)
from app.game.hand import (
    HandPlay,
    determine_trick_winner,
    parse_hand,
    validate_follow,
)


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PlayerInfo:
    player_id: str
    name: str
    level: str = "2"          # Current level ('2' through 'A')
    is_connected: bool = True

    @property
    def level_index(self) -> int:
        return RANK_ORDER[self.level]

    def advance_level(self, steps: int) -> bool:
        """Advances level by `steps`. Returns True if player reaches Ace (wins game)."""
        idx = min(self.level_index + steps, len(RANKS) - 1)
        self.level = RANKS[idx]
        return self.level == "A"

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "level": self.level,
            "is_connected": self.is_connected,
        }


@dataclass
class TrumpFlipState:
    """Current best trump claim during the drawing phase."""
    player_id: str
    rank: str        # Trump number
    suit: str        # Trump suit
    count: int       # How many identical cards were flipped
    card_ids: list[str] = field(default_factory=list)
    locked: bool = False

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "rank": self.rank,
            "suit": self.suit,
            "count": self.count,
            "card_ids": self.card_ids,
            "locked": self.locked,
        }


@dataclass
class TeammateCall:
    """A single teammate-calling condition issued by the round leader."""
    rank: str
    suit: str
    order: int            # N-th occurrence of this card triggers teammate join
    times_played: int = 0
    fulfilled: bool = False
    fulfiller_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "suit": self.suit,
            "order": self.order,
            "times_played": self.times_played,
            "fulfilled": self.fulfilled,
            "fulfiller_id": self.fulfiller_id,
        }


@dataclass
class GameEvent:
    """An event emitted by the engine to be broadcast over WebSocket."""
    event_type: str
    data: dict
    target: str | None = None  # None → broadcast to all; player_id → private


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class GameEngine:
    """
    Manages the full lifecycle of a Zhao Peng You game room.

    All methods are synchronous (no I/O). They mutate state and return
    a list of GameEvent objects for the WebSocket handler to dispatch.
    """

    def __init__(self, room_code: str) -> None:
        self.room_code = room_code
        self.phase: GamePhase = GamePhase.WAITING

        # Player registry (ordered clockwise from seat 0)
        self.players: list[PlayerInfo] = []

        # Deck & hands
        self.num_decks: int = 0
        self.deck: list[Card] = []
        self.buried_cards: list[Card] = []
        self.hands: dict[str, list[Card]] = {}   # player_id → private hand

        # Trump state
        self.trump_suit: str | None = None
        self.trump_number: str | None = None
        self.trump_flip_state: TrumpFlipState | None = None
        self.trump_locked: bool = False

        # Draw phase
        self.draw_start_idx: int = 0    # who draws first THIS round
        self.current_drawer_idx: int = 0

        # Leader & teams
        self.leader_id: str | None = None
        self.calls: list[TeammateCall] = []
        self.attacking_team: set[str] = set()
        self.defending_team: set[str] = set()
        self.max_attackers: int = 0

        # Play phase
        self.trick_lead_player_id: str | None = None
        self.trick_play_order: list[str] = []        # full clockwise order for this trick
        self.current_play_idx: int = 0               # how many players have played
        self.current_trick: dict[str, list[Card]] = {}

        # Round scoring
        self.won_cards: dict[str, list[Card]] = {}   # player_id → cards won
        self.last_trick_winner: str | None = None
        self.round_number: int = 0

        # Game-level
        self.game_winner_id: str | None = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def player_ids(self) -> list[str]:
        return [p.player_id for p in self.players]

    def get_player(self, player_id: str) -> PlayerInfo | None:
        return next((p for p in self.players if p.player_id == player_id), None)

    def player_index(self, player_id: str) -> int:
        for i, p in enumerate(self.players):
            if p.player_id == player_id:
                return i
        raise ValueError(f"Player {player_id!r} not found")

    def clockwise_order_from(self, start_player_id: str) -> list[str]:
        """Returns all player_ids in clockwise order starting from start_player_id."""
        idx = self.player_index(start_player_id)
        n = len(self.players)
        return [self.players[(idx + i) % n].player_id for i in range(n)]

    # ------------------------------------------------------------------
    # WAITING phase — lobby management
    # ------------------------------------------------------------------

    def add_player(self, player_id: str, name: str) -> list[GameEvent]:
        if self.phase != GamePhase.WAITING:
            return [GameEvent("error", {"message": "Game already started"}, player_id)]
        if len(self.players) >= MAX_PLAYERS:
            return [GameEvent("error", {"message": "Room is full"}, player_id)]
        if any(p.player_id == player_id for p in self.players):
            return []  # Already in room
        self.players.append(PlayerInfo(player_id=player_id, name=name))
        self.hands[player_id] = []
        self.won_cards[player_id] = []
        return [GameEvent("state_update", self._public_state())]

    def remove_player(self, player_id: str) -> list[GameEvent]:
        if self.phase != GamePhase.WAITING:
            p = self.get_player(player_id)
            if p:
                p.is_connected = False
            return [GameEvent("state_update", self._public_state())]
        self.players = [p for p in self.players if p.player_id != player_id]
        self.hands.pop(player_id, None)
        return [GameEvent("state_update", self._public_state())]

    def reconnect_player(self, player_id: str) -> list[GameEvent]:
        p = self.get_player(player_id)
        if not p:
            return [GameEvent("error", {"message": "Player not in this game"}, player_id)]
        p.is_connected = True
        # Send full state + private hand
        events: list[GameEvent] = [GameEvent("state_update", self._public_state())]
        events.append(GameEvent("hand_update", {"hand": cards_to_dicts(self.hands.get(player_id, []))}, player_id))
        return events

    def start_game(self, requester_id: str) -> list[GameEvent]:
        if self.phase != GamePhase.WAITING:
            return [GameEvent("error", {"message": "Game already started"}, requester_id)]
        if len(self.players) < MIN_PLAYERS:
            return [GameEvent("error", {"message": f"Need at least {MIN_PLAYERS} players"}, requester_id)]
        return self._start_round()

    # ------------------------------------------------------------------
    # DRAWING phase
    # ------------------------------------------------------------------

    def draw_card(self, player_id: str) -> list[GameEvent]:
        if self.phase != GamePhase.DRAWING:
            return [GameEvent("error", {"message": "Not in drawing phase"}, player_id)]

        current_player = self.players[self.current_drawer_idx]
        if current_player.player_id != player_id:
            return [GameEvent("error", {"message": "Not your turn to draw"}, player_id)]
        if not self.deck:
            return [GameEvent("error", {"message": "Deck is empty"}, player_id)]

        card = self.deck.pop(0)
        self.hands[player_id].append(card)

        events: list[GameEvent] = [
            GameEvent("card_drawn", {
                "player_id": player_id,
                "deck_remaining": len(self.deck),
            }),
            GameEvent("hand_update", {"hand": cards_to_dicts(self.hands[player_id])}, player_id),
        ]

        # Advance drawer clockwise
        self.current_drawer_idx = (self.current_drawer_idx + 1) % len(self.players)

        if not self.deck:
            # All cards drawn → transition to BURYING
            events.extend(self._finalize_drawing())
        else:
            events.append(GameEvent("state_update", self._public_state()))

        return events

    def flip_trump(self, player_id: str, card_ids: list[str]) -> list[GameEvent]:
        """
        Player attempts to flip card(s) of their level to set/override trump.
        card_ids: list of card ids (must all be same rank+suit, equal to player's level)
        """
        if self.phase != GamePhase.DRAWING:
            return [GameEvent("error", {"message": "Not in drawing phase"}, player_id)]
        if self.trump_locked:
            return [GameEvent("error", {"message": "Trump is already locked"}, player_id)]

        player = self.get_player(player_id)
        if not player:
            return [GameEvent("error", {"message": "Player not found"}, player_id)]

        hand = self.hands[player_id]
        cards = self._resolve_card_ids(card_ids, hand)
        if cards is None:
            return [GameEvent("error", {"message": "Card(s) not in your hand"}, player_id)]

        if not cards:
            return [GameEvent("error", {"message": "No cards specified"}, player_id)]

        # All cards must match the player's current level
        for c in cards:
            if c.rank != player.level:
                return [GameEvent("error", {"message": "Cards must match your current level"}, player_id)]
            if c.is_joker:
                return [GameEvent("error", {"message": "Cannot flip jokers as trump"}, player_id)]

        # All cards must be identical (same rank+suit)
        if len({(c.rank, c.suit) for c in cards}) != 1:
            return [GameEvent("error", {"message": "All flipped cards must be identical"}, player_id)]

        rank = cards[0].rank
        suit = cards[0].suit
        count = len(cards)

        # Must exceed current flip count
        current_count = self.trump_flip_state.count if self.trump_flip_state else 0
        if count <= current_count:
            return [GameEvent("error", {
                "message": f"Need more than {current_count} cards to override"
            }, player_id)]

        # Check if locked (count == num_decks means no more copies possible)
        locked = count >= self.num_decks

        self.trump_flip_state = TrumpFlipState(
            player_id=player_id,
            rank=rank,
            suit=suit,
            count=count,
            card_ids=card_ids,
            locked=locked,
        )
        self.trump_suit = suit
        self.trump_number = rank
        self.leader_id = player_id
        self.trump_locked = locked

        events: list[GameEvent] = [
            GameEvent("trump_flipped", {
                "player_id": player_id,
                "rank": rank,
                "suit": suit,
                "count": count,
                "locked": locked,
            }),
            GameEvent("state_update", self._public_state()),
        ]
        return events

    # ------------------------------------------------------------------
    # BURYING phase
    # ------------------------------------------------------------------

    def bury_cards(
        self,
        player_id: str,
        cards_to_bury: list[str],
    ) -> list[GameEvent]:
        """
        Leader selects which cards to place into the buried pile.

        After drawing, the leader's hand contains both their dealt cards AND the
        original buried pile.  They must return exactly (leader_hand_size −
        non_leader_hand_size) cards to restore equal hand sizes.
        """
        if self.phase != GamePhase.BURYING:
            return [GameEvent("error", {"message": "Not in burying phase"}, player_id)]
        if player_id != self.leader_id:
            return [GameEvent("error", {"message": "Only the leader can bury cards"}, player_id)]

        hand = self.hands[player_id]

        # Calculate how many the leader must bury
        non_leader_sizes = [
            len(self.hands[p.player_id])
            for p in self.players
            if p.player_id != player_id
        ]
        target_size = non_leader_sizes[0] if non_leader_sizes else len(hand)
        required_bury = len(hand) - target_size

        if len(cards_to_bury) != required_bury:
            return [GameEvent("error", {
                "message": f"Must bury exactly {required_bury} card(s) (got {len(cards_to_bury)})"
            }, player_id)]

        bury_cards_objs = self._resolve_card_ids(cards_to_bury, hand)
        if bury_cards_objs is None:
            return [GameEvent("error", {"message": "Some cards are not in your hand"}, player_id)]

        for c in bury_cards_objs:
            hand.remove(c)
            self.buried_cards.append(c)

        # Transition to CALLING
        self.phase = GamePhase.CALLING
        events: list[GameEvent] = [
            GameEvent("hand_update", {"hand": cards_to_dicts(hand)}, player_id),
            GameEvent("state_update", self._public_state()),
        ]
        return events

    # ------------------------------------------------------------------
    # CALLING phase
    # ------------------------------------------------------------------

    def call_teammates(
        self,
        player_id: str,
        calls: list[dict],
    ) -> list[GameEvent]:
        """
        Leader declares teammate conditions.
        calls: list of {"rank": str, "suit": str, "order": int}
        """
        if self.phase != GamePhase.CALLING:
            return [GameEvent("error", {"message": "Not in calling phase"}, player_id)]
        if player_id != self.leader_id:
            return [GameEvent("error", {"message": "Only the leader can call teammates"}, player_id)]

        max_calls = self.max_attackers - 1
        if len(calls) > max_calls:
            return [GameEvent("error", {
                "message": f"Can call at most {max_calls} teammate(s)"
            }, player_id)]

        validated: list[TeammateCall] = []
        for c in calls:
            rank = c.get("rank", "")
            suit = c.get("suit", "")
            order = int(c.get("order", 1))

            if not rank or not suit:
                return [GameEvent("error", {"message": "Each call needs rank and suit"}, player_id)]
            if order < 1:
                return [GameEvent("error", {"message": "Order must be ≥ 1"}, player_id)]

            # Cannot call trump cards
            if self.trump_suit and self.trump_number:
                dummy = Card(suit=suit, rank=rank, deck_index=0)
                if dummy.is_trump(self.trump_suit, self.trump_number):
                    return [GameEvent("error", {
                        "message": f"{rank} of {suit} is a trump card — cannot call it"
                    }, player_id)]

            validated.append(TeammateCall(rank=rank, suit=suit, order=order))

        self.calls = validated

        # Start play phase
        self.phase = GamePhase.PLAYING
        self._setup_first_trick()

        events: list[GameEvent] = [
            GameEvent("state_update", self._public_state()),
        ]
        return events

    # ------------------------------------------------------------------
    # PLAYING phase
    # ------------------------------------------------------------------

    def play_cards(
        self,
        player_id: str,
        card_ids: list[str],
    ) -> list[GameEvent]:
        if self.phase != GamePhase.PLAYING:
            return [GameEvent("error", {"message": "Not in playing phase"}, player_id)]

        # Verify it is this player's turn
        if self.current_play_idx >= len(self.trick_play_order):
            return [GameEvent("error", {"message": "Trick is already complete"}, player_id)]
        expected = self.trick_play_order[self.current_play_idx]
        if player_id != expected:
            return [GameEvent("error", {"message": "Not your turn"}, player_id)]

        hand = self.hands[player_id]
        cards = self._resolve_card_ids(card_ids, hand)
        if cards is None:
            return [GameEvent("error", {"message": "Card(s) not in your hand"}, player_id)]

        assert self.trump_suit and self.trump_number

        # Validate the play
        if self.current_play_idx == 0:
            # Lead player: any valid hand
            lead_play = parse_hand(cards, self.trump_suit, self.trump_number)
            if lead_play is None:
                return [GameEvent("error", {"message": "Invalid hand format"}, player_id)]
        else:
            # Follower: must comply with follow rules
            lead_cards = self.current_trick[self.trick_play_order[0]]
            lead_play = parse_hand(lead_cards, self.trump_suit, self.trump_number)
            assert lead_play is not None

            valid, reason = validate_follow(
                cards, hand, lead_play, self.trump_suit, self.trump_number
            )
            if not valid:
                return [GameEvent("error", {"message": reason}, player_id)]

        # Commit the play
        for c in cards:
            hand.remove(c)
        self.current_trick[player_id] = cards
        self.current_play_idx += 1

        events: list[GameEvent] = [
            GameEvent("card_played", {
                "player_id": player_id,
                "cards": cards_to_dicts(cards),
            }),
            GameEvent("hand_update", {"hand": cards_to_dicts(hand)}, player_id),
        ]

        # Check if any teammate-calling conditions are met
        events.extend(self._check_calls(player_id, cards))

        if self.current_play_idx == len(self.trick_play_order):
            # All players have played — resolve trick
            events.extend(self._resolve_trick())
        else:
            events.append(GameEvent("state_update", self._public_state()))

        return events

    # ------------------------------------------------------------------
    # Internal: round & game transitions
    # ------------------------------------------------------------------

    def _start_round(self) -> list[GameEvent]:
        """Initializes a new round (called from start_game or after round_end)."""
        n = len(self.players)
        self.num_decks = num_decks_for_players(n)
        self.deck = create_shuffled_decks(self.num_decks)
        buried_count = buried_card_count(n, self.num_decks)

        # Remove buried cards from deck
        self.buried_cards = self.deck[:buried_count]
        self.deck = self.deck[buried_count:]

        # Reset hands
        for pid in self.player_ids:
            self.hands[pid] = []
            self.won_cards[pid] = []

        # Reset trump
        self.trump_suit = None
        self.trump_number = None
        self.trump_flip_state = None
        self.trump_locked = False

        # Reset calls
        self.calls = []
        self.leader_id = None
        self.attacking_team = set()
        self.defending_team = set(self.player_ids)
        self.max_attackers = n // 2

        # Reset trick state
        self.current_trick = {}
        self.trick_play_order = []
        self.trick_lead_player_id = None
        self.current_play_idx = 0
        self.last_trick_winner = None

        self.phase = GamePhase.DRAWING
        self.round_number += 1

        # Who draws first this round
        self.current_drawer_idx = self.draw_start_idx

        return [GameEvent("state_update", self._public_state())]

    def _finalize_drawing(self) -> list[GameEvent]:
        """Called after all cards have been drawn. Handles auto-flip and transitions."""
        events: list[GameEvent] = []

        if self.trump_flip_state is None:
            # Auto-flip: find a random player with a card matching their level
            candidates: list[tuple[str, Card]] = []
            for p in self.players:
                for c in self.hands[p.player_id]:
                    if c.rank == p.level and not c.is_joker:
                        candidates.append((p.player_id, c))

            if candidates:
                pid, card = random.choice(candidates)
                self.trump_suit = card.suit
                self.trump_number = card.rank
                self.leader_id = pid
                self.trump_flip_state = TrumpFlipState(
                    player_id=pid,
                    rank=card.rank,
                    suit=card.suit,
                    count=1,
                    card_ids=[card.id],
                    locked=False,
                )
                events.append(GameEvent("trump_flipped", {
                    "player_id": pid,
                    "rank": card.rank,
                    "suit": card.suit,
                    "count": 1,
                    "locked": False,
                    "auto": True,
                }))
            # Edge case: no player has their level card (highly unlikely with multiple decks)
            # In this case, just pick a random card from a random player's hand
            else:
                p = random.choice(self.players)
                if self.hands[p.player_id]:
                    card = random.choice(self.hands[p.player_id])
                    if not card.is_joker:
                        self.trump_suit = card.suit
                        self.trump_number = p.level
                        self.leader_id = p.player_id

        # Leader gets buried pile added to hand temporarily
        if self.leader_id:
            self.attacking_team = {self.leader_id}
            self.defending_team = set(self.player_ids) - self.attacking_team
            self.hands[self.leader_id].extend(self.buried_cards)
            self.buried_cards = []  # Now empty; leader holds them

        self.phase = GamePhase.BURYING
        events.append(GameEvent("state_update", self._public_state()))
        # Send leader their expanded hand
        if self.leader_id:
            events.append(GameEvent(
                "hand_update",
                {"hand": cards_to_dicts(self.hands[self.leader_id])},
                self.leader_id,
            ))
        return events

    def _setup_first_trick(self) -> None:
        """Sets up the first trick of the round (leader leads)."""
        assert self.leader_id is not None
        self.trick_lead_player_id = self.leader_id
        self.trick_play_order = self.clockwise_order_from(self.leader_id)
        self.current_trick = {}
        self.current_play_idx = 0

    def _setup_next_trick(self, lead_player_id: str) -> None:
        """Sets up a subsequent trick with the given lead player."""
        self.trick_lead_player_id = lead_player_id
        self.trick_play_order = self.clockwise_order_from(lead_player_id)
        self.current_trick = {}
        self.current_play_idx = 0

    def _resolve_trick(self) -> list[GameEvent]:
        """Determines trick winner, updates scores, and advances to next trick or round end."""
        assert self.trump_suit and self.trump_number

        winner_id = determine_trick_winner(
            self.current_trick,
            self.trick_play_order,
            self.trump_suit,
            self.trump_number,
        )
        self.last_trick_winner = winner_id

        # Collect cards won by winner
        all_trick_cards: list[Card] = []
        for cards in self.current_trick.values():
            all_trick_cards.extend(cards)
        self.won_cards[winner_id].extend(all_trick_cards)

        events: list[GameEvent] = [
            GameEvent("trick_won", {
                "winner_id": winner_id,
                "cards": cards_to_dicts(all_trick_cards),
            }),
        ]

        # Check if all hands are empty
        hands_empty = all(len(h) == 0 for h in self.hands.values())
        if hands_empty:
            events.extend(self._resolve_round())
        else:
            self._setup_next_trick(winner_id)
            events.append(GameEvent("state_update", self._public_state()))

        return events

    def _check_calls(self, player_id: str, played_cards: list[Card]) -> list[GameEvent]:
        """Checks whether any played cards trigger a teammate call condition."""
        events: list[GameEvent] = []
        if not self.calls or not self.trump_suit or not self.trump_number:
            return events

        for call in self.calls:
            if call.fulfilled:
                continue
            for card in played_cards:
                if card.rank == call.rank and card.suit == call.suit:
                    call.times_played += 1
                    if call.times_played == call.order:
                        # Condition met
                        if player_id != self.leader_id:
                            call.fulfilled = True
                            call.fulfiller_id = player_id
                            # Move player to attacking team
                            self.attacking_team.add(player_id)
                            self.defending_team.discard(player_id)
                            events.append(GameEvent("teammate_revealed", {
                                "player_id": player_id,
                                "call": call.to_dict(),
                            }))
                        else:
                            # Leader played own called card — condition "wasted"
                            call.fulfilled = True
                            call.fulfiller_id = None
        return events

    def _resolve_round(self) -> list[GameEvent]:
        """Calculates scores, advances levels, and starts next round or ends game."""
        assert self.trump_suit and self.trump_number

        total_points = self.num_decks * 100
        attacker_points = 0

        # Sum points from won_cards
        for pid, cards in self.won_cards.items():
            if pid in self.attacking_team:
                attacker_points += sum(c.point_value() for c in cards)

        # Handle buried pile: goes to last_trick_winner
        buried_point_total = sum(c.point_value() for c in self.buried_cards)
        if buried_point_total > 0 and self.last_trick_winner:
            multiplier = 2 if self.last_trick_winner in self.defending_team else 1
            buried_contribution = buried_point_total * multiplier
            if self.last_trick_winner in self.attacking_team:
                attacker_points += buried_contribution

        # Determine winner and level gain
        winner_team, base_gain = _calculate_level_gain(attacker_points, total_points)

        # Under-strength bonus for attacking team
        actual_attackers = len(self.attacking_team)
        bonus_gain = 0
        if winner_team == "attacker" and actual_attackers < self.max_attackers:
            bonus_gain = self.max_attackers - actual_attackers
        total_gain = base_gain + bonus_gain

        # Snapshot levels before applying gains
        old_levels = {p.player_id: p.level for p in self.players}

        # Apply level gains
        game_over = False
        winner_ids: list[str] = []
        if winner_team == "attacker":
            winner_ids = list(self.attacking_team)
        else:
            winner_ids = list(self.defending_team)

        for pid in winner_ids:
            p = self.get_player(pid)
            if p and p.advance_level(total_gain):
                self.game_winner_id = pid
                game_over = True

        # Per-player level change summary
        player_gains = {
            p.player_id: {
                "name": p.name,
                "old_level": old_levels[p.player_id],
                "new_level": p.level,
                "gain": total_gain if p.player_id in winner_ids else 0,
            }
            for p in self.players
        }

        # Prepare round summary
        round_summary = {
            "attacker_points": attacker_points,
            "total_points": total_points,
            "buried_points": buried_point_total,
            "winner_team": winner_team,
            "base_gain": base_gain,
            "bonus_gain": bonus_gain,
            "total_gain": total_gain,
            "winner_ids": winner_ids,
            "last_trick_winner": self.last_trick_winner,
            "player_gains": player_gains,
        }

        events: list[GameEvent] = [
            GameEvent("round_end", round_summary),
        ]

        if game_over:
            self.phase = GamePhase.GAME_END
            events.append(GameEvent("game_end", {
                "winner_id": self.game_winner_id,
                "players": [p.to_dict() for p in self.players],
            }))
        else:
            # Advance draw_start_idx for next round
            self.draw_start_idx = (self.draw_start_idx + 1) % len(self.players)
            self.phase = GamePhase.ROUND_END
            events.append(GameEvent("state_update", self._public_state()))

        return events

    def start_next_round(self, requester_id: str) -> list[GameEvent]:
        """Transitions from ROUND_END to the next round."""
        if self.phase != GamePhase.ROUND_END:
            return [GameEvent("error", {"message": "Not in round-end phase"}, requester_id)]
        return self._start_round()

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _public_state(self) -> dict:
        """Returns the full game state safe to broadcast to all players (no private hands)."""
        return {
            "room_code": self.room_code,
            "phase": self.phase.value,
            "round_number": self.round_number,
            "players": [
                {
                    **p.to_dict(),
                    "card_count": len(self.hands.get(p.player_id, [])),
                    "on_attacking_team": p.player_id in self.attacking_team,
                }
                for p in self.players
            ],
            "num_decks": self.num_decks,
            "deck_remaining": len(self.deck),
            "trump_suit": self.trump_suit,
            "trump_number": self.trump_number,
            "trump_flip_state": self.trump_flip_state.to_dict() if self.trump_flip_state else None,
            "trump_locked": self.trump_locked,
            "leader_id": self.leader_id,
            "current_drawer_id": (
                self.players[self.current_drawer_idx].player_id
                if self.phase == GamePhase.DRAWING
                else None
            ),
            "calls": [c.to_dict() for c in self.calls],
            "attacking_team": list(self.attacking_team),
            "defending_team": list(self.defending_team),
            "current_trick": {
                pid: cards_to_dicts(cards)
                for pid, cards in self.current_trick.items()
            },
            "trick_play_order": self.trick_play_order,
            "current_play_idx": self.current_play_idx,
            "trick_lead_player_id": self.trick_lead_player_id,
            "last_trick_winner": self.last_trick_winner,
            "scores": {
                pid: sum(c.point_value() for c in cards)
                for pid, cards in self.won_cards.items()
            },
        }

    def get_player_view(self, player_id: str) -> dict:
        """Returns public state plus the requesting player's private hand."""
        state = self._public_state()
        state["my_hand"] = cards_to_dicts(self.hands.get(player_id, []))
        state["my_player_id"] = player_id
        return state

    def to_dict(self) -> dict:
        """Full serialization for database persistence."""
        return {
            "room_code": self.room_code,
            "phase": self.phase.value,
            "round_number": self.round_number,
            "players": [p.to_dict() for p in self.players],
            "num_decks": self.num_decks,
            "deck": cards_to_dicts(self.deck),
            "buried_cards": cards_to_dicts(self.buried_cards),
            "hands": {pid: cards_to_dicts(cards) for pid, cards in self.hands.items()},
            "trump_suit": self.trump_suit,
            "trump_number": self.trump_number,
            "trump_flip_state": self.trump_flip_state.to_dict() if self.trump_flip_state else None,
            "trump_locked": self.trump_locked,
            "draw_start_idx": self.draw_start_idx,
            "current_drawer_idx": self.current_drawer_idx,
            "leader_id": self.leader_id,
            "calls": [c.to_dict() for c in self.calls],
            "attacking_team": list(self.attacking_team),
            "defending_team": list(self.defending_team),
            "max_attackers": self.max_attackers,
            "trick_lead_player_id": self.trick_lead_player_id,
            "trick_play_order": self.trick_play_order,
            "current_play_idx": self.current_play_idx,
            "current_trick": {pid: cards_to_dicts(c) for pid, c in self.current_trick.items()},
            "won_cards": {pid: cards_to_dicts(c) for pid, c in self.won_cards.items()},
            "last_trick_winner": self.last_trick_winner,
            "game_winner_id": self.game_winner_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameEngine":
        """Reconstructs engine state from a serialized dict."""
        engine = cls(room_code=data["room_code"])
        engine.phase = GamePhase(data["phase"])
        engine.round_number = data["round_number"]
        engine.players = [PlayerInfo(**p) for p in data["players"]]
        engine.num_decks = data["num_decks"]
        engine.deck = dicts_to_cards(data["deck"])
        engine.buried_cards = dicts_to_cards(data["buried_cards"])
        engine.hands = {pid: dicts_to_cards(cards) for pid, cards in data["hands"].items()}
        engine.trump_suit = data["trump_suit"]
        engine.trump_number = data["trump_number"]
        if data["trump_flip_state"]:
            tfs = data["trump_flip_state"]
            engine.trump_flip_state = TrumpFlipState(
                player_id=tfs["player_id"],
                rank=tfs["rank"],
                suit=tfs["suit"],
                count=tfs["count"],
                card_ids=tfs["card_ids"],
                locked=tfs["locked"],
            )
        engine.trump_locked = data["trump_locked"]
        engine.draw_start_idx = data["draw_start_idx"]
        engine.current_drawer_idx = data["current_drawer_idx"]
        engine.leader_id = data["leader_id"]
        engine.calls = [
            TeammateCall(
                rank=c["rank"],
                suit=c["suit"],
                order=c["order"],
                times_played=c["times_played"],
                fulfilled=c["fulfilled"],
                fulfiller_id=c["fulfiller_id"],
            )
            for c in data["calls"]
        ]
        engine.attacking_team = set(data["attacking_team"])
        engine.defending_team = set(data["defending_team"])
        engine.max_attackers = data["max_attackers"]
        engine.trick_lead_player_id = data["trick_lead_player_id"]
        engine.trick_play_order = data["trick_play_order"]
        engine.current_play_idx = data["current_play_idx"]
        engine.current_trick = {pid: dicts_to_cards(c) for pid, c in data["current_trick"].items()}
        engine.won_cards = {pid: dicts_to_cards(c) for pid, c in data["won_cards"].items()}
        engine.last_trick_winner = data["last_trick_winner"]
        engine.game_winner_id = data["game_winner_id"]
        # Ensure won_cards entries exist for all players
        for pid in engine.player_ids:
            engine.won_cards.setdefault(pid, [])
        return engine

    # ------------------------------------------------------------------
    # Private utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_card_ids(card_ids: list[str], hand: list[Card]) -> list[Card] | None:
        """Looks up cards by id in hand. Returns None if any are missing."""
        hand_map: dict[str, Card] = {}
        for c in hand:
            hand_map.setdefault(c.id, c)

        result: list[Card] = []
        remaining = dict(hand_map)
        for cid in card_ids:
            if cid not in remaining:
                return None
            result.append(remaining.pop(cid))
        return result


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------


def _calculate_level_gain(attacker_points: int, total_points: int) -> tuple[str, int]:
    """
    Determines which team wins and how many levels the winners gain.

    Attacker wins threshold: ≥ 60% of total points.
    Level gain thresholds (applied to attacker % for attackers;
    symmetrically inverted for defenders):
      - ≥ 100%: +3
      - ≥ 80%:  +2
      - ≥ 60%:  +1
      - < 60% attackers (defenders win):
          attacker < 20%  → defenders +3
          attacker < 40%  → defenders +2
          else            → defenders +1
    """
    if total_points == 0:
        return ("defender", 1)

    pct = attacker_points / total_points * 100

    if pct >= 100:
        return ("attacker", 3)
    elif pct >= 80:
        return ("attacker", 2)
    elif pct >= 60:
        return ("attacker", 1)
    elif pct < 20:
        return ("defender", 3)
    elif pct < 40:
        return ("defender", 2)
    else:
        return ("defender", 1)
