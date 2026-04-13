/* ===================================================
   Zhao Peng You – Frontend Game Client
   =================================================== */

'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const SUIT_SYMBOLS = { hearts: '♥', diamonds: '♦', clubs: '♣', spades: '♠', joker: '🃏' };
const SUIT_NAMES   = { hearts: 'Hearts', diamonds: 'Diamonds', clubs: 'Clubs', spades: 'Spades' };
const RANKS_DISPLAY = { small_joker: 'SJ', big_joker: 'BJ' };
const RANK_LABELS = ['2','3','4','5','6','7','8','9','10','J','Q','K','A'];
const RANK_ORDER  = {'2':0,'3':1,'4':2,'5':3,'6':4,'7':5,'8':6,'9':7,'10':8,'J':9,'Q':10,'K':11,'A':12};
const SUIT_SORT   = { clubs: 0, diamonds: 1, hearts: 2, spades: 3 };

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let ws = null;
let myPlayerId  = null;
let mySessionKey = null;
let myRoomCode  = null;
let gameState   = null;   // latest public state from server
let myHand      = [];     // [{id, suit, rank, deck_index}, ...]
let selectedIds = new Set();
let buryReturn  = new Set(); // card ids to bury
let autoDraw    = false;

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
const $  = id => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  $(`screen-${name}`).classList.add('active');
}

function showToast(msg, color = '#fff') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const t = el('div', 'toast');
  t.textContent = msg;
  t.style.borderColor = color;
  container.appendChild(t);
  setTimeout(() => t.remove(), 3100);
}

function setStatus(msg) { $('status-bar').textContent = msg; }

// ---------------------------------------------------------------------------
// Card sorting
// ---------------------------------------------------------------------------
function cardSortKey(card, trumpSuit, trumpNumber) {
  const rankIdx = RANK_ORDER[card.rank] ?? -1;
  const suitIdx = SUIT_SORT[card.suit] ?? 4;

  if (!trumpSuit || !trumpNumber) {
    return [suitIdx, rankIdx, card.deck_index || 0];
  }

  const isJoker = card.suit === 'joker';
  const isTrumpNumber = card.rank === trumpNumber;
  const isTrumpSuit = card.suit === trumpSuit;
  const isTrump = isJoker || isTrumpNumber || isTrumpSuit;

  if (!isTrump) {
    // Non-trump: group by suit (skip trump suit from non-trump group), then rank
    return [0, suitIdx, rankIdx, card.deck_index || 0];
  }

  // Trump: sort by ascending strength so weakest are on the left
  let strength;
  if (card.rank === 'big_joker')        strength = 50;
  else if (card.rank === 'small_joker') strength = 40;
  else if (isTrumpNumber && isTrumpSuit) strength = 30;
  else if (isTrumpNumber)               strength = 20; // off-suit trump number
  else                                  strength = rankIdx; // trump-suit card by rank
  return [1, strength, card.deck_index || 0];
}

function sortHand(hand) {
  const ts = gameState?.trump_suit;
  const tn = gameState?.trump_number;
  return [...hand].sort((a, b) => {
    const ka = cardSortKey(a, ts, tn);
    const kb = cardSortKey(b, ts, tn);
    for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
      const d = (ka[i] ?? 0) - (kb[i] ?? 0);
      if (d !== 0) return d;
    }
    return 0;
  });
}

function getRequiredBuryCount() {
  if (!gameState || !myHand.length) return 0;
  const nonLeaders = (gameState.players || []).filter(p => p.player_id !== gameState.leader_id);
  if (!nonLeaders.length) return 0;
  const targetSize = Math.min(...nonLeaders.map(p => p.card_count));
  return Math.max(0, myHand.length - targetSize);
}

// ---------------------------------------------------------------------------
// Lobby
// ---------------------------------------------------------------------------
$('btn-join').addEventListener('click', async () => {
  const name = $('player-name').value.trim();
  const code = $('room-code-input').value.trim().toUpperCase();
  const errEl = $('lobby-error');
  errEl.classList.add('hidden');

  if (!name) { errEl.textContent = 'Please enter your name.'; errEl.classList.remove('hidden'); return; }

  try {
    let roomCode = code;
    if (!roomCode) {
      // Create a new empty room first, then join via WebSocket
      const resp = await fetch('/api/rooms', { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Failed to create room');
      roomCode = data.room_code;
    }
    // Player credentials come from WebSocket join — store name for WS handshake
    myRoomCode = roomCode;
    // playerName is read from the input in connectWebSocket's onopen handler
    enterRoom(false);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
});

// Try to auto-reconnect from sessionStorage (per-tab, so multiple tabs stay independent)
window.addEventListener('DOMContentLoaded', () => {
  const saved = sessionStorage.getItem('zpySession');
  if (saved) {
    try {
      const { myPlayerId: pid, mySessionKey: sk, myRoomCode: rc } = JSON.parse(saved);
      if (pid && sk && rc) {
        myPlayerId = pid; mySessionKey = sk; myRoomCode = rc;
        enterRoom(true);
        return;
      }
    } catch (_) {}
  }
});

// ---------------------------------------------------------------------------
// Room screen
// ---------------------------------------------------------------------------
function enterRoom(reconnect = false) {
  showScreen('room');
  $('room-code-display').textContent = myRoomCode;
  $('btn-copy-code').onclick = () => {
    navigator.clipboard.writeText(myRoomCode).then(() => showToast('Room code copied!'));
  };

  connectWebSocket(reconnect);
}

$('btn-start').addEventListener('click', () => {
  sendWS({ type: 'start_game' });
});

function updateRoomScreen(state) {
  const players = state.players || [];
  $('btn-start').disabled = players.length < 4;
  $('room-status-label').textContent = state.phase || 'waiting';

  const list = $('player-list');
  list.innerHTML = '';
  players.forEach(p => {
    const div = el('div', 'player-entry');
    const connDot = el('span', `p-conn${p.is_connected ? ' online' : ''}`);
    const name = el('span', 'p-name', p.name);
    const level = el('span', 'p-level', `Lv.${p.level}`);
    div.appendChild(connDot); div.appendChild(name); div.appendChild(level);
    if (p.player_id === myPlayerId) {
      const you = el('span', '', ' (you)'); you.style.color = '#aaa'; div.appendChild(you);
    }
    list.appendChild(div);
  });
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connectWebSocket(reconnect = false) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/${myRoomCode}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setStatus('Connected');
    if (reconnect && mySessionKey) {
      sendWS({ type: 'reconnect', session_key: mySessionKey });
    } else {
      // Fall back to join if no session key yet (e.g. first connect or dropped before joined event)
      const name = $('player-name')?.value?.trim() || myPlayerId || 'Player';
      sendWS({ type: 'join', player_name: name });
    }
  };

  ws.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    handleServerMessage(data);
  };

  ws.onclose = () => {
    setStatus('Disconnected – reconnecting…');
    setTimeout(() => connectWebSocket(true), 3000);
  };

  ws.onerror = () => setStatus('Connection error');
}

function sendWS(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

// ---------------------------------------------------------------------------
// Server message dispatcher
// ---------------------------------------------------------------------------
function handleServerMessage(data) {
  const evt = data.event;

  if (evt === 'joined' || evt === 'reconnected') {
    if (data.player_id) myPlayerId = data.player_id;
    if (data.room_code) myRoomCode = data.room_code;
    if (data.session_key) mySessionKey = data.session_key;
    // Always persist after join/reconnect so reload can restore the session
    sessionStorage.setItem('zpySession', JSON.stringify({ myPlayerId, mySessionKey, myRoomCode }));
    sendWS({ type: 'get_state' });
    return;
  }

  if (evt === 'error') {
    showToast('⚠ ' + data.message, '#e74c3c');
    setStatus('Error: ' + data.message);
    return;
  }

  if (evt === 'state_update') {
    gameState = data;
    renderGameState(data);
    return;
  }

  if (evt === 'hand_update') {
    myHand = data.hand || [];
    renderHand();
    return;
  }

  if (evt === 'card_drawn') {
    const pid = data.player_id;
    const p = gameState?.players?.find(x => x.player_id === pid);
    if (pid !== myPlayerId) showToast(`${p?.name || pid} drew a card`);
    return;
  }

  if (evt === 'trump_flipped') {
    const auto = data.auto ? ' (auto)' : '';
    const p = gameState?.players?.find(x => x.player_id === data.player_id);
    showToast(`🃏 Trump: ${data.rank} of ${data.suit}${auto} — set by ${p?.name || data.player_id}`, '#f0c040');
    return;
  }

  if (evt === 'card_played') {
    // State update will follow, no extra action needed
    return;
  }

  if (evt === 'trick_won') {
    const p = gameState?.players?.find(x => x.player_id === data.winner_id);
    const pts = (data.cards || []).reduce((s, c) => s + cardPointValue(c), 0);
    showToast(`${p?.name || data.winner_id} wins the trick${pts ? ` (+${pts} pts)` : ''}`);
    return;
  }

  if (evt === 'teammate_revealed') {
    const p = gameState?.players?.find(x => x.player_id === data.player_id);
    showToast(`🤝 ${p?.name || data.player_id} joined the attacking team!`, '#f0c040');
    return;
  }

  if (evt === 'round_end') {
    showRoundEnd(data);
    return;
  }

  if (evt === 'game_end') {
    showGameEnd(data);
    return;
  }

  if (evt === 'chat') {
    appendChatMessage(data.player_name, data.message, data.player_id === myPlayerId);
    return;
  }
}

function cardPointValue(card) {
  if (card.rank === '5')  return 5;
  if (card.rank === '10') return 10;
  if (card.rank === 'K')  return 10;
  return 0;
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------
function renderGameState(state) {
  if (!state) return;
  const phase = state.phase;

  // Switch to game screen if game has started
  if (phase && phase !== 'waiting') {
    showScreen('game');
    $('g-room-code').textContent = state.room_code;
    $('g-phase-label').textContent = phase.replace('_', ' ');
    $('g-round-label').textContent = `Round ${state.round_number}`;
    updateTrumpLabel(state);
    // My points
    const myPts = (state.scores || {})[myPlayerId];
    const myPtsEl = $('g-my-pts');
    if (myPtsEl) myPtsEl.textContent = myPts != null ? `You: ${myPts} pts` : '';
    renderOpponents(state);
    renderTrickArea(state);
    renderPhaseUI(state);
  } else {
    updateRoomScreen(state);
  }
}

function updateTrumpLabel(state) {
  const el = $('g-trump-label');
  if (state.trump_suit && state.trump_number) {
    const sym = SUIT_SYMBOLS[state.trump_suit] || state.trump_suit;
    el.textContent = `Trump: ${state.trump_number} ${sym}${state.trump_locked ? ' 🔒' : ''}`;
  } else {
    el.textContent = 'No trump yet';
  }
}

function renderOpponents(state) {
  const row = $('opponents-row');
  row.innerHTML = '';
  const currentPlayer = currentTurnPlayer(state);
  const scores = state.scores || {};
  (state.players || []).forEach(p => {
    if (p.player_id === myPlayerId) return;
    const isActive = p.player_id === currentPlayer;
    const box = el('div', `opponent-box${isActive ? ' active-player' : ''}`);
    const nameEl = el('div', 'opp-name', p.name);
    const levelEl = el('div', 'opp-level', `Lv.${p.level}`);
    const cardsEl = el('div', 'opp-cards', `${p.card_count || 0} cards`);
    const ptsEl   = el('div', 'opp-pts', scores[p.player_id] != null ? `${scores[p.player_id]} pts` : '');
    box.appendChild(nameEl);
    box.appendChild(levelEl);
    box.appendChild(cardsEl);
    box.appendChild(ptsEl);

    if (p.on_attacking_team !== undefined) {
      const badge = el('span', `team-badge ${p.on_attacking_team ? 'attacker' : 'defender'}`,
        p.on_attacking_team ? '⚔ Attacker' : '🛡 Defender');
      box.appendChild(badge);
    }
    if (!p.is_connected) {
      const dc = el('span', '', ' ⚡'); dc.style.color = '#e74c3c'; dc.title = 'Disconnected';
      nameEl.appendChild(dc);
    }
    row.appendChild(box);
  });
}

function currentTurnPlayer(state) {
  if (state.phase === 'drawing') return state.current_drawer_id;
  if (state.phase === 'playing' && state.trick_play_order) {
    return state.trick_play_order[state.current_play_idx];
  }
  return null;
}

function renderTrickArea(state) {
  const trick = state.current_trick || {};
  const playOrder = state.trick_play_order || [];
  const container = $('trick-plays');
  container.innerHTML = '';

  playOrder.forEach(pid => {
    if (!trick[pid]) return;
    const p = (state.players || []).find(x => x.player_id === pid);
    const grp = el('div', 'trick-play-group');
    const nameEl = el('div', 'trick-player-name', p ? p.name : pid);
    const cards = el('div', 'trick-cards');
    trick[pid].forEach(c => cards.appendChild(buildCardEl(c, false, 'small')));
    grp.appendChild(cards);
    grp.appendChild(nameEl);
    container.appendChild(grp);
  });

  // Prompt
  const prompt = $('trick-prompt');
  const phase = state.phase;
  const myTurn = currentTurnPlayer(state) === myPlayerId;
  if (phase === 'playing') {
    if (myTurn) prompt.textContent = 'Your turn — select and play cards';
    else {
      const tp = (state.players || []).find(x => x.player_id === currentTurnPlayer(state));
      prompt.textContent = tp ? `Waiting for ${tp.name}…` : 'Waiting…';
    }
  } else {
    prompt.textContent = '';
  }
}

function renderPhaseUI(state) {
  const phase = state.phase;
  const isLeader = state.leader_id === myPlayerId;
  const myTurn = currentTurnPlayer(state) === myPlayerId;

  // Hide all phase-specific areas
  $('draw-deck-area').classList.add('hidden');
  $('bury-area').classList.add('hidden');
  $('call-area').classList.add('hidden');
  $('round-end-area').classList.add('hidden');
  $('game-end-area').classList.add('hidden');
  $('btn-flip-trump').classList.add('hidden');
  $('btn-play-cards').classList.add('hidden');

  if (phase === 'drawing') {
    $('draw-deck-area').classList.remove('hidden');
    $('deck-remaining').textContent = state.deck_remaining;
    const drawDeck = $('draw-deck-visual');
    drawDeck.style.cursor = myTurn ? 'pointer' : 'default';
    drawDeck.style.opacity = myTurn ? '1' : '0.5';
    const prompt = $('draw-prompt');
    if (myTurn) {
      prompt.textContent = 'Your turn — click the deck to draw';
      drawDeck.onclick = () => sendWS({ type: 'draw_card' });
      // Auto-draw: fire after short delay so state is settled
      if (autoDraw) {
        setTimeout(() => {
          if (gameState?.phase === 'drawing' && currentTurnPlayer(gameState) === myPlayerId) {
            sendWS({ type: 'draw_card' });
          }
        }, 400);
      }
    } else {
      prompt.textContent = state.current_drawer_id ?
        `Waiting for ${(state.players || []).find(p => p.player_id === state.current_drawer_id)?.name || '?'} to draw…` : '';
      drawDeck.onclick = null;
    }
    // Show flip button if player has eligible cards
    if (myHand.length > 0) {
      $('btn-flip-trump').classList.remove('hidden');
    }

  } else if (phase === 'burying' && isLeader) {
    $('bury-area').classList.remove('hidden');
    renderBuryArea();

  } else if (phase === 'calling' && isLeader) {
    $('call-area').classList.remove('hidden');
    renderCallArea(state);

  } else if (phase === 'playing') {
    $('btn-play-cards').classList.remove('hidden');
    $('btn-play-cards').disabled = selectedIds.size === 0 || !myTurn;

  } else if (phase === 'round_end') {
    // Round end handled by round_end event
  }

  renderHand();
}

// ---------------------------------------------------------------------------
// Hand rendering
// ---------------------------------------------------------------------------
function renderHand() {
  const container = $('my-hand');
  container.innerHTML = '';
  const phase = gameState?.phase;
  const isLeader = gameState?.leader_id === myPlayerId;

  sortHand(myHand).forEach(card => {
    const isSelected = selectedIds.has(card.id);
    const cardEl = buildCardEl(card, isSelected);

    cardEl.addEventListener('click', () => {
      if (phase === 'drawing') {
        // Toggle for flip
        if (selectedIds.has(card.id)) selectedIds.delete(card.id);
        else selectedIds.add(card.id);
      } else if (phase === 'burying' && isLeader) {
        if (buryReturn.has(card.id)) buryReturn.delete(card.id);
        else buryReturn.add(card.id);
      } else if (phase === 'playing') {
        if (selectedIds.has(card.id)) selectedIds.delete(card.id);
        else selectedIds.add(card.id);
      }
      renderHand();
      updateActionButtons();
    });

    container.appendChild(cardEl);
  });
}

const FACE_RANKS = new Set(['J', 'Q', 'K']);
const FACE_ART   = { J: '♞', Q: '♛', K: '♚' }; // knight / queen / king chess glyphs

function buildCardEl(card, selected = false, sizeClass = '') {
  // Determine trump status from current game state
  const isTrump = gameState?.trump_suit && gameState?.trump_number
    ? isCardTrump(card, gameState.trump_suit, gameState.trump_number)
    : false;

  const classes = ['card', `suit-${card.suit}`];
  if (selected)  classes.push('selected');
  if (isTrump)   classes.push('trump-card');
  if (sizeClass) classes.push(sizeClass);

  const div = document.createElement('div');
  div.className = classes.join(' ');
  div.dataset.cardId = card.id;

  const rank    = RANKS_DISPLAY[card.rank] || card.rank;
  const suitSym = SUIT_SYMBOLS[card.suit] || '';

  if (card.suit === 'joker') {
    // ── Joker ──────────────────────────────────
    const isBig = card.rank === 'big_joker';
    classes.push(isBig ? 'big-joker' : 'small-joker');
    div.className = classes.join(' ');          // re-set with joker variant class

    const inner = document.createElement('div');
    inner.className = 'card-joker-inner';
    inner.innerHTML =
      `<span class="joker-label">${isBig ? 'BIG' : 'SML'}</span>` +
      `<span class="joker-sym">★</span>` +
      `<span class="joker-word">JOKER</span>`;
    div.appendChild(inner);

  } else {
    // ── Regular card ───────────────────────────
    // Top-left corner
    const tl = document.createElement('div');
    tl.className = 'card-corner card-tl';
    tl.innerHTML = `<span class="cn-rank">${rank}</span><span class="cn-suit">${suitSym}</span>`;

    // Centre
    let ctr;
    if (FACE_RANKS.has(card.rank)) {
      // Face card: chess-glyph portrait + suit symbol beneath
      ctr = document.createElement('div');
      ctr.className = 'card-face-ctr';
      ctr.innerHTML =
        `<span class="face-art">${FACE_ART[card.rank]}</span>` +
        `<span class="face-suit">${suitSym}</span>`;
    } else {
      ctr = document.createElement('div');
      ctr.className = 'card-center-sym';
      ctr.textContent = suitSym;
    }

    // Bottom-right corner (rotated 180°)
    const br = document.createElement('div');
    br.className = 'card-corner card-br';
    br.innerHTML = `<span class="cn-rank">${rank}</span><span class="cn-suit">${suitSym}</span>`;

    div.appendChild(tl);
    div.appendChild(ctr);
    div.appendChild(br);
  }

  return div;
}

function isCardTrump(card, trumpSuit, trumpNumber) {
  if (card.suit === 'joker') return true;
  if (card.rank === trumpNumber) return true;
  if (card.suit === trumpSuit) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Action buttons
// ---------------------------------------------------------------------------
function updateActionButtons() {
  const phase = gameState?.phase;
  const myTurn = currentTurnPlayer(gameState) === myPlayerId;

  if (phase === 'playing') {
    const playBtn = $('btn-play-cards');
    playBtn.disabled = selectedIds.size === 0 || !myTurn;
  }
  if (phase === 'burying') {
    const required = getRequiredBuryCount();
    $('btn-confirm-bury').disabled = buryReturn.size !== required || required === 0;
  }
}

$('btn-flip-trump').addEventListener('click', () => {
  const sel = Array.from(selectedIds);
  if (sel.length === 0) { showToast('Select card(s) to flip as trump'); return; }
  sendWS({ type: 'flip_trump', card_ids: sel });
  selectedIds.clear();
});

$('btn-auto-draw').addEventListener('click', () => {
  autoDraw = !autoDraw;
  $('btn-auto-draw').textContent = `Auto-draw: ${autoDraw ? 'ON' : 'OFF'}`;
  $('btn-auto-draw').style.opacity = autoDraw ? '1' : '0.6';
  // If it's already our turn, trigger immediately
  if (autoDraw && gameState?.phase === 'drawing' && currentTurnPlayer(gameState) === myPlayerId) {
    setTimeout(() => sendWS({ type: 'draw_card' }), 400);
  }
});

$('btn-play-cards').addEventListener('click', () => {
  const sel = Array.from(selectedIds);
  if (sel.length === 0) return;
  sendWS({ type: 'play_cards', card_ids: sel });
  selectedIds.clear();
  $('btn-play-cards').disabled = true;
});

// ---------------------------------------------------------------------------
// Burying phase UI
// ---------------------------------------------------------------------------
function renderBuryArea() {
  const required = getRequiredBuryCount();
  const buryCardsEl = $('bury-cards');
  buryCardsEl.innerHTML = '';

  $('bury-hint').textContent =
    `Select exactly ${required} card(s) to bury — ${buryReturn.size} selected`;

  sortHand(myHand).forEach(card => {
    const isSel = buryReturn.has(card.id);
    const cardEl = buildCardEl(card, isSel);
    cardEl.title = isSel ? 'Selected to bury (click to deselect)' : 'Click to mark for burying';
    cardEl.addEventListener('click', () => {
      if (buryReturn.has(card.id)) buryReturn.delete(card.id);
      else buryReturn.add(card.id);
      renderBuryArea();
      updateActionButtons();
    });
    buryCardsEl.appendChild(cardEl);
  });

  $('btn-confirm-bury').disabled = buryReturn.size !== required || required === 0;
}

$('btn-confirm-bury').addEventListener('click', () => {
  const cardIds = Array.from(buryReturn);
  sendWS({ type: 'bury_cards', card_ids: cardIds });
  buryReturn.clear();
});

// ---------------------------------------------------------------------------
// Calling phase UI
// ---------------------------------------------------------------------------
function renderCallArea(state) {
  const maxCalls = Math.floor((state.players || []).length / 2) - 1;
  $('call-hint').textContent = `You can call up to ${maxCalls} teammate(s). Cannot call trump cards.`;

  const slotsEl = $('call-slots');
  slotsEl.innerHTML = '';

  // Build call slots
  for (let i = 0; i < maxCalls; i++) {
    const slot = el('div', 'call-slot');

    const rankSel = el('select'); rankSel.name = `call-rank-${i}`;
    const blankOpt = el('option'); blankOpt.value = ''; blankOpt.textContent = '— skip —';
    rankSel.appendChild(blankOpt);
    RANK_LABELS.forEach(r => {
      const opt = el('option'); opt.value = r; opt.textContent = r;
      rankSel.appendChild(opt);
    });

    const suitSel = el('select'); suitSel.name = `call-suit-${i}`;
    Object.entries(SUIT_NAMES).forEach(([k, v]) => {
      const opt = el('option'); opt.value = k; opt.textContent = v;
      suitSel.appendChild(opt);
    });

    const orderInput = el('input'); orderInput.type = 'number'; orderInput.min = '1';
    orderInput.value = '1'; orderInput.style.width = '55px'; orderInput.name = `call-order-${i}`;

    const rankLbl = el('label', '', 'Rank:');
    const suitLbl = el('label', '', 'Suit:');
    const orderLbl = el('label', '', 'Order:');

    slot.append(rankLbl, rankSel, suitLbl, suitSel, orderLbl, orderInput);
    slotsEl.appendChild(slot);
  }
}

$('btn-confirm-calls').addEventListener('click', () => {
  const slotsEl = $('call-slots');
  const slots = slotsEl.querySelectorAll('.call-slot');
  const calls = [];
  slots.forEach(slot => {
    const rank = slot.querySelector('select[name^="call-rank"]').value;
    const suit = slot.querySelector('select[name^="call-suit"]').value;
    const order = parseInt(slot.querySelector('input[name^="call-order"]').value, 10) || 1;
    if (rank) calls.push({ rank, suit, order });
  });
  sendWS({ type: 'call_teammates', calls });
});

// ---------------------------------------------------------------------------
// Round end / Game end
// ---------------------------------------------------------------------------
function showRoundEnd(data) {
  gameState && renderGameState({ ...gameState, phase: 'round_end' });
  $('round-end-area').classList.remove('hidden');

  const summary = $('round-summary');
  summary.innerHTML = '';

  // ── Winner banner ──────────────────────────────────────────────
  const isAttackerWin = data.winner_team === 'attacker';
  const bannerText = isAttackerWin ? '⚔ Attackers win!' : '🛡 Defenders win!';
  const banner = el('p', 'round-winner-banner', bannerText);
  banner.style.color = isAttackerWin ? '#f0c040' : '#74b9ff';
  summary.appendChild(banner);

  // ── Score line ─────────────────────────────────────────────────
  const pct = data.total_points > 0
    ? Math.round(data.attacker_points / data.total_points * 100)
    : 0;
  const scoreRow = el('div', 'score-row');
  const aEntry = el('div', 'score-entry');
  aEntry.appendChild(el('div', 'score-pts', `${data.attacker_points}`));
  aEntry.appendChild(el('div', '', 'attacker pts'));
  const slashEntry = el('div', 'score-entry');
  slashEntry.appendChild(el('div', 'score-pts', `${data.total_points}`));
  slashEntry.appendChild(el('div', '', 'total pts'));
  const pctEntry = el('div', 'score-entry');
  pctEntry.appendChild(el('div', 'score-pts', `${pct}%`));
  pctEntry.appendChild(el('div', '', 'share'));
  scoreRow.appendChild(aEntry);
  scoreRow.appendChild(slashEntry);
  scoreRow.appendChild(pctEntry);
  summary.appendChild(scoreRow);

  // ── Buried points note ──────────────────────────────────────────
  if (data.buried_points > 0) {
    const lastWinner = data.last_trick_winner;
    const mult = (gameState?.defending_team || []).includes(lastWinner) ? 2 : 1;
    summary.appendChild(el('p', 'hint',
      `Buried ${data.buried_points} pts${mult === 2 ? ' (×2 — last trick to defenders)' : ''}`));
  }

  // ── Level gain explanation ──────────────────────────────────────
  const gainParts = [];
  if (data.base_gain > 0) gainParts.push(`+${data.base_gain} base (${pct}% ≥ ${pct >= 100 ? 100 : pct >= 80 ? 80 : 60}%)`);
  if (data.bonus_gain > 0) gainParts.push(`+${data.bonus_gain} under-strength bonus`);
  if (data.total_gain > 0) {
    summary.appendChild(el('p', 'round-gain-line',
      `Level gain: ${gainParts.join(' + ')} = +${data.total_gain}`));
  } else {
    summary.appendChild(el('p', 'hint', 'No level gain this round'));
  }

  // ── Per-player level table ─────────────────────────────────────
  const gains = data.player_gains || {};
  const players = gameState?.players || [];
  if (players.length && Object.keys(gains).length) {
    const table = el('table', 'level-table');
    players.forEach(p => {
      const info = gains[p.player_id];
      if (!info) return;
      const tr = el('tr', '');
      const isWinner = (data.winner_ids || []).includes(p.player_id);
      const teamIcon = isWinner
        ? (isAttackerWin ? '⚔' : '🛡')
        : (isAttackerWin ? '🛡' : '⚔');

      const nameTd = el('td', 'lt-name', `${teamIcon} ${info.name}`);
      const levelTd = el('td', 'lt-level', '');
      if (info.gain > 0) {
        levelTd.innerHTML =
          `Lv.<span class="lt-old">${info.old_level}</span>` +
          ` → <span class="lt-new">Lv.${info.new_level}</span>` +
          ` <span class="lt-gain">(+${info.gain})</span>`;
      } else {
        levelTd.innerHTML = `Lv.${info.new_level}`;
        levelTd.style.color = 'var(--text-dim)';
      }
      tr.appendChild(nameTd);
      tr.appendChild(levelTd);
      table.appendChild(tr);
    });
    summary.appendChild(table);
  }
}

$('btn-next-round').addEventListener('click', () => {
  // Reset auto-draw to OFF for the fresh round
  autoDraw = false;
  $('btn-auto-draw').textContent = 'Auto-draw: OFF';
  $('btn-auto-draw').style.opacity = '0.6';

  sendWS({ type: 'next_round' });
  $('round-end-area').classList.add('hidden');
});

function showGameEnd(data) {
  showScreen('game');
  $('game-end-area').classList.remove('hidden');

  const winner = data.players?.find(p => p.player_id === data.winner_id);
  const summary = $('game-end-summary');
  summary.innerHTML = '';
  summary.appendChild(el('h3', '', `🏆 ${winner?.name || 'Someone'} wins the game!`));

  const table = el('table');
  table.style.cssText = 'margin:12px auto;border-collapse:collapse;';
  const hdr = el('tr');
  ['Player','Level'].forEach(h => {
    const th = el('th', '', h); th.style.cssText = 'padding:4px 12px;border-bottom:1px solid #555;';
    hdr.appendChild(th);
  });
  table.appendChild(hdr);
  (data.players || []).forEach(p => {
    const row = el('tr');
    const nameCell = el('td', '', p.name); nameCell.style.padding = '4px 12px';
    const levelCell = el('td', '', p.level); levelCell.style.cssText = 'padding:4px 12px;color:#f0c040;font-weight:bold;text-align:center;';
    row.appendChild(nameCell); row.appendChild(levelCell);
    table.appendChild(row);
  });
  summary.appendChild(table);

  // Clear session
  sessionStorage.removeItem('zpySession');
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
function appendChatMessage(name, message, isSelf = false) {
  const box = $('chat-messages');
  if (!box) return;
  const div = el('div', 'chat-msg');
  const nameEl = el('span', 'chat-name', name + ': ');
  const msgEl  = el('span', '', message);
  div.appendChild(nameEl);
  div.appendChild(msgEl);
  box.appendChild(div);
  // Auto-scroll to bottom
  box.scrollTop = box.scrollHeight;
}

function appendSystemChat(message) {
  const box = $('chat-messages');
  if (!box) return;
  const div = el('div', 'chat-msg system-msg');
  const nameEl = el('span', 'chat-name', '— ');
  const msgEl  = el('span', '', message);
  div.appendChild(nameEl);
  div.appendChild(msgEl);
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function sendChat() {
  const input = $('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  sendWS({ type: 'chat', message: msg });
  input.value = '';
}

$('btn-send-chat').addEventListener('click', sendChat);
$('chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
setStatus('Enter your name and room code (or leave blank to create a room).');
