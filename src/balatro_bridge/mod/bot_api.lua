-- The game-facing half of the bridge, running inside the real Balatro.
--
-- `state` returns the same shape as the headless engine's api.env_state(), so
-- an agent trained headless can drive the real game without a translation
-- layer. The difference is timing, not structure: here actions are asynchronous
-- because the game is actually animating them, so the client polls `state`
-- until the phase is actionable again rather than blocking.

local BotAPI = {}

local KEY_INDEX, KEY_ORDER

local function key_index()
  if KEY_INDEX then return KEY_INDEX end
  KEY_INDEX, KEY_ORDER = {}, {}
  for key in pairs(G.P_CENTERS) do KEY_ORDER[#KEY_ORDER + 1] = key end
  table.sort(KEY_ORDER)
  for i, key in ipairs(KEY_ORDER) do KEY_INDEX[key] = i end
  return KEY_INDEX
end

local function key_id(key) return key and key_index()[key] or 0 end

function BotAPI.key_list()
  key_index()
  local out = {}
  for i, key in ipairs(KEY_ORDER) do
    out[i] = key .. "|" .. tostring(G.P_CENTERS[key].set or "")
  end
  return table.concat(out, ",")
end

local SET_IDS = { Joker = 1, Tarot = 2, Planet = 3, Spectral = 4, Voucher = 5,
                  Booster = 6, Default = 7, Enhanced = 8 }
local RANK_IDS = { ['2']=1, ['3']=2, ['4']=3, ['5']=4, ['6']=5, ['7']=6,
                   ['8']=7, ['9']=8, ['10']=9, ['Jack']=10, ['Queen']=11,
                   ['King']=12, ['Ace']=13 }
local SUIT_IDS = { Spades = 1, Hearts = 2, Clubs = 3, Diamonds = 4 }

local STATE_NAMES
local function state_name()
  if not STATE_NAMES then
    STATE_NAMES = {}
    for name, value in pairs(G.STATES) do STATE_NAMES[value] = name end
  end
  return STATE_NAMES[G.STATE] or ("STATE_" .. tostring(G.STATE))
end

local PACK_STATES
local function in_pack()
  PACK_STATES = PACK_STATES or {
    [G.STATES.TAROT_PACK] = true, [G.STATES.PLANET_PACK] = true,
    [G.STATES.SPECTRAL_PACK] = true, [G.STATES.STANDARD_PACK] = true,
    [G.STATES.BUFFOON_PACK] = true,
  }
  return PACK_STATES[G.STATE] or false
end

local function blind_on_deck()
  local states = G.GAME.round_resets.blind_states
  local function done(s)
    return s == 'Defeated' or s == 'Skipped' or s == 'Hide'
  end
  return (not done(states.Small) and 'Small')
      or (not done(states.Big) and 'Big') or 'Boss'
end

--- Whether the Cash Out button exists yet.
---
--- It is built as its own UIBox with `major = G.round_eval` rather than as a
--- child of it, so asking round_eval for it never finds it. Scanning the UIBox
--- registry is the way to see what the player can actually click.
function BotAPI.cash_out_ready()
  if G.STATE ~= G.STATES.ROUND_EVAL or not G.round_eval then return false end
  for _, box in pairs((G.I and G.I.UIBOX) or {}) do
    if box.get_UIE_by_ID and box:get_UIE_by_ID('cash_out_button') then
      return true
    end
  end
  return false
end

local function is_highlighted(index)
  local card = G.hand.cards[index]
  if not card then return false end
  for _, c in ipairs(G.hand.highlighted) do
    if c == card then return true end
  end
  return false
end

-- --------------------------------------------------------------- setup

--- Put a fresh profile into the state a bot needs, and the headless engine
--- already assumes. Run once at install.
---
--- The modded build gets its own save directory (BalatroBot rather than
--- Balatro), which keeps the real save safe -- but it also means a brand new
--- profile every time, with the tutorial on and most content locked.
function BotAPI.configure(args)
  local changed = { tutorial = false, unlocked = 0 }

  -- Game speed. 4 is the maximum the options screen offers, and it only
  -- shortens animation: event delays are measured against a clock that runs at
  -- dt*SPEEDFACTOR, so nothing about the rules changes.
  G.SETTINGS.GAMESPEED = tonumber(args and args[1]) or 4
  changed.gamespeed = G.SETTINGS.GAMESPEED

  -- The tutorial forces specific shop items, a specific voucher and specific
  -- tags (see G.FUNCS.start_tutorial), so leaving it on quietly corrupts runs.
  if not G.SETTINGS.tutorial_complete then
    G.SETTINGS.tutorial_complete = true
    G.SETTINGS.tutorial_progress = {
      hold_parts = {},
      completed_parts = { small_blind = true, big_blind = true,
                          second_hand = true, shop_1 = true, shop_2 = true,
                          consumables = true },
    }
    changed.tutorial = true
  end

  -- A fresh profile locks 45 of 150 jokers and half the vouchers. Pool
  -- eligibility is gated on `unlocked ~= false`, so an un-unlocked profile is
  -- a different game from the one the agent trained against.
  for _, center in pairs(G.P_CENTERS) do
    if center.unlocked == false then
      center.unlocked = true
      changed.unlocked = changed.unlocked + 1
    end
    center.discovered = true
    center.alert = nil
  end
  for _, back in pairs(G.P_CENTER_POOLS.Back or {}) do
    back.unlocked = true; back.discovered = true
  end
  for _, stake in pairs(G.P_STAKES or {}) do stake.unlocked = true end
  if set_discover_tallies then set_discover_tallies() end
  return changed
end

--- Ask the game whether a card can be bought, without the side effects.
---
--- G.FUNCS.check_for_buy_space calls alert_no_space when it fails, which is a
--- predicate with consequences: it sets G.CONTROLLER.locks.no_space, creates
--- attention text, juices every card in the area and queues sound events. That
--- is right for a click and wrong for a state query polled many times a second
--- -- it spammed "No space!" over the joker area and leaked events every poll.
---
--- Swapping the global for a no-op keeps the game's own rule and drops only the
--- presentation, rather than restating the condition here where it could drift.
local function buy_space(card)
  local saved = alert_no_space
  alert_no_space = function() end
  local ok, result = pcall(G.FUNCS.check_for_buy_space, card)
  alert_no_space = saved
  return ok and result and true or false
end

-- --------------------------------------------------------------- state

function BotAPI.state()
  if not G.GAME or not G.STATES then return { state_name = "BOOTING" } end
  -- G.STAGE flips to RUN before the run's card areas exist, so it alone is not
  -- enough: reading G.jokers during that window errors.
  local in_run = G.STAGE == G.STAGES.RUN
      and G.hand ~= nil and G.jokers ~= nil and G.consumeables ~= nil
      and G.GAME.current_round ~= nil and G.GAME.round_resets ~= nil
  local blind = in_run and G.GAME.blind or nil

  local state = {
    state_name = state_name(),
    in_run = in_run,
    ante = in_run and G.GAME.round_resets.ante or 0,
    round = in_run and G.GAME.round or 0,
    dollars = in_run and G.GAME.dollars or 0,
    chips = in_run and G.GAME.chips or 0,
    blind_chips = (blind and blind.chips) or 0,
    blind_name = (blind and blind.name) or "",
    hands_left = in_run and G.GAME.current_round.hands_left or 0,
    discards_left = in_run and G.GAME.current_round.discards_left or 0,
    joker_limit = in_run and G.jokers.config.card_limit or 0,
    consumable_limit = in_run and G.consumeables.config.card_limit or 0,
    reroll_cost = in_run and (G.GAME.current_round.reroll_cost or 0) or 0,
    won = (in_run and G.GAME.won) and 1 or 0,
    in_pack = in_pack() and 1 or 0,
    selection_size = in_run and #G.hand.highlighted or 0,
    blind_on_deck = in_run and blind_on_deck() or "",
    run_pending = BOT_RUN_PENDING and 1 or 0,
    -- The client uses this to know whether it may act at all: mid-animation
    -- the game is in a transient state and inputs are ignored.
    -- G.FUNCS.select_blind is guarded by `if G.blind_select then`, so calling
    -- it before that UIBox exists silently does nothing. The blind select
    -- phase is only actionable once the screen is actually up.
    -- Each of these callbacks is guarded by `if <uibox> then`, so calling one
    -- before its screen is up silently does nothing.
    blind_select_up = (G.blind_select ~= nil) and 1 or 0,
    shop_up = (G.shop ~= nil) and 1 or 0,
    -- Diagnostic: alert_no_space sets this lock and shows "No space!" over the
    -- joker area. A state read must never raise it -- if this is ever 1 after
    -- polling, a read-only query has side effects again.
    no_space_lock = (G.CONTROLLER and G.CONTROLLER.locks
                     and G.CONTROLLER.locks.no_space) and 1 or 0,
    -- update_shop queues an event that waits for the shop to finish sliding in
    -- (`math.abs(G.shop.T.y - G.shop.VT.y) < 3`). Leaving before that event
    -- runs removes G.shop out from under it and crashes the game -- reachable
    -- only because a bot can act faster than a player physically can.
    shop_settled = (G.shop ~= nil and G.shop.T and G.shop.VT
                    and math.abs(G.shop.T.y - G.shop.VT.y) < 3) and 1 or 0,
    round_eval_up = (G.round_eval ~= nil) and 1 or 0,
    -- The round evaluation adds its rows (blind reward, unused hands,
    -- interest, each joker's payout) over several frames, and the Cash Out
    -- button is created last. Cashing out before it exists removes the
    -- round_eval box while those row events are still queued, and they then
    -- crash on a nil G.round_eval. Waiting for the button is exactly what a
    -- player does.
    cash_out_ready = BotAPI.cash_out_ready() and 1 or 0,
    -- Every phase where the client may act, plus the terminal one: leaving
    -- GAME_OVER out means any wait after a losing hand hangs until timeout.
    ready = ((not BOT_RUN_PENDING) and (G.STATE == G.STATES.SELECTING_HAND
              or (G.STATE == G.STATES.BLIND_SELECT and G.blind_select ~= nil)
              or (G.STATE == G.STATES.SHOP and G.shop ~= nil and G.shop.T
                  and G.shop.VT and math.abs(G.shop.T.y - G.shop.VT.y) < 3)
              or (G.STATE == G.STATES.ROUND_EVAL and BotAPI.cash_out_ready())
              or G.STATE == G.STATES.GAME_OVER
              or in_pack())) and 1 or 0,
  }
  if not in_run then return state end

  state.hand = {}
  for i, card in ipairs(G.hand.cards) do
    state.hand[i] = {
      rank = (card.base and RANK_IDS[card.base.value]) or 0,
      suit = (card.base and SUIT_IDS[card.base.suit]) or 0,
      center = key_id(card.config.center.key),
      chips = (card.base and card.base.nominal) or 0,
      highlighted = is_highlighted(i) and 1 or 0,
      debuffed = card.debuff and 1 or 0,
    }
  end

  state.jokers = {}
  for i, card in ipairs(G.jokers.cards) do
    state.jokers[i] = {
      center = key_id(card.config.center.key),
      sellable = card:can_sell_card() and 1 or 0,
      sell_cost = card.sell_cost or 0,
      rarity = card.config.center.rarity or 0,
    }
  end

  state.consumables = {}
  for i, card in ipairs(G.consumeables.cards) do
    state.consumables[i] = {
      center = key_id(card.config.center.key),
      set = SET_IDS[card.config.center.set] or 0,
      sellable = card:can_sell_card() and 1 or 0,
    }
  end

  state.shop = {}
  for _, name in ipairs({ 'shop_jokers', 'shop_vouchers', 'shop_booster' }) do
    local area = G[name]
    if area and area.cards then
      for i, card in ipairs(area.cards) do
        state.shop[#state.shop + 1] = {
          area = name, index = i,
          center = key_id(card.config.center.key),
          set = SET_IDS[card.config.center.set] or 0,
          cost = card.cost or 0,
          buyable = ((card.cost or 0) <= G.GAME.dollars
                     and buy_space(card)) and 1 or 0,
        }
      end
    end
  end

  state.pack = {}
  if G.pack_cards and G.pack_cards.cards then
    for i, card in ipairs(G.pack_cards.cards) do
      state.pack[i] = { center = key_id(card.config.center.key),
                        set = SET_IDS[card.config.center.set] or 0 }
    end
  end

  state.hand_levels = {}
  for name, data in pairs(G.GAME.hands) do
    state.hand_levels[name] = { level = data.level, played = data.played,
                                chips = data.chips, mult = data.mult }
  end
  return state
end

-- --------------------------------------------------------------- search

-- Enumerating subsets in-game rather than over the socket: 218 round trips per
-- decision would dominate everything else. Same routine as the headless engine.
local function hand_info(indices)
  local cards = {}
  for _, i in ipairs(indices) do cards[#cards + 1] = G.hand.cards[i] end
  local text, _, _, scoring = G.FUNCS.get_poker_hand_info(cards)
  local level = G.GAME.hands[text]
  local chips, mult = 0, 0
  if level then chips, mult = level.chips, level.mult end
  local card_chips = 0
  for _, card in ipairs(scoring or {}) do
    card_chips = card_chips + ((card.base and card.base.nominal) or 0)
  end
  return text, (chips + card_chips) * mult
end

function BotAPI.best_play()
  local n = #G.hand.cards
  local best, best_score, best_hand = nil, -1, ""
  local idx = {}
  local function recurse(start, depth)
    if depth > 0 then
      local name, score = hand_info(idx)
      -- Tie-break toward fewer cards: a card that does not score is discarded.
      if score > best_score or (score == best_score and best and #idx < #best) then
        best, best_score, best_hand = { unpack(idx) }, score, name
      end
    end
    if depth == 5 then return end
    for i = start, n do
      idx[depth + 1] = i
      recurse(i + 1, depth + 1)
      idx[depth + 1] = nil
    end
  end
  recurse(1, 0)
  return { cards = best or {}, estimate = best_score, hand = best_hand }
end

-- --------------------------------------------------------------- recording
--
-- Records a human playing, so the same actions can be replayed through the
-- bot's own API and the resulting state compared step by step. If the replay
-- diverges, either the action set is incomplete or the state we read is not
-- the state the game is in -- both worth knowing before trusting an agent.
--
-- The hooks wrap the game's own G.FUNCS, so they capture real clicks rather
-- than a parallel notion of what a click means.

local recording = nil        -- nil = not recording

local function fingerprint()
  local jokers = {}
  for i, card in ipairs(G.jokers.cards) do
    jokers[i] = card.config.center.key
  end
  local consumables = {}
  for i, card in ipairs(G.consumeables.cards) do
    consumables[i] = card.config.center.key
  end
  local levels = {}
  for name, data in pairs(G.GAME.hands) do
    if data.level > 1 then levels[name] = data.level end
  end
  return {
    phase = state_name(),
    dollars = G.GAME.dollars,
    -- The round score, compared exactly at every step. If this diverges, the
    -- replay produced a different hand or different joker effects, which is
    -- the failure that matters most.
    chips = G.GAME.chips,
    hands_played = G.GAME.current_round.hands_played,
    last_hand = G.GAME.last_hand_played or "",
    -- Best single hand this run, so a divergence in scoring shows up even
    -- after the round score has been reset.
    best_hand = math.floor(tonumber(G.GAME.round_scores
                   and G.GAME.round_scores.hand and G.GAME.round_scores.hand.amt or 0)),
    ante = G.GAME.round_resets.ante,
    round = G.GAME.round,
    hands_left = G.GAME.current_round.hands_left,
    discards_left = G.GAME.current_round.discards_left,
    blind = (G.GAME.blind and G.GAME.blind.name) or "",
    blind_chips = (G.GAME.blind and G.GAME.blind.chips) or 0,
    hand_size = #G.hand.cards,
    jokers = jokers,
    consumables = consumables,
    hand_levels = levels,
    deck_size = G.playing_cards and #G.playing_cards or 0,
  }
end

BotAPI.fingerprint = fingerprint

local function selected_indices()
  local out = {}
  for i = 1, #G.hand.cards do
    if is_highlighted(i) then out[#out + 1] = i end
  end
  return out
end

local function note(action, params)
  if not recording then return end
  recording[#recording + 1] = {
    n = #recording + 1,
    action = action,
    params = params or {},
    -- State *before* the action; the replay applies the action and then
    -- compares against the next entry's before-state.
    before = fingerprint(),
  }
end

--- Find a card's area and index, so a click can be replayed by position.
local function locate(card)
  for _, name in ipairs({ 'shop_jokers', 'shop_vouchers', 'shop_booster',
                          'jokers', 'consumeables', 'pack_cards', 'hand' }) do
    local area = G[name]
    if area and area.cards then
      for i, other in ipairs(area.cards) do
        if other == card then return name, i end
      end
    end
  end
  return "?", 0
end

local hooked = false

local function install_hooks()
  if hooked then return end
  hooked = true

  local function wrap(name, capture)
    local original = G.FUNCS[name]
    if not original then return end
    G.FUNCS[name] = function(e, ...)
      if recording then
        local ok, params = pcall(capture, e)
        note(name, ok and params or {})
      end
      return original(e, ...)
    end
  end

  wrap('select_blind', function() return { blind = blind_on_deck() } end)
  wrap('skip_blind', function() return { blind = blind_on_deck() } end)
  wrap('play_cards_from_highlighted', function()
    return { cards = selected_indices() } end)
  wrap('discard_cards_from_highlighted', function()
    return { cards = selected_indices() } end)
  wrap('buy_from_shop', function(e)
    local area, index = locate(e.config.ref_table)
    return { area = area, index = index,
             key = e.config.ref_table.config.center.key } end)
  wrap('sell_card', function(e)
    local area, index = locate(e.config.ref_table)
    return { area = area, index = index,
             key = e.config.ref_table.config.center.key } end)
  wrap('use_card', function(e)
    local area, index = locate(e.config.ref_table)
    return { area = area, index = index,
             key = e.config.ref_table.config.center.key,
             targets = selected_indices() } end)
  wrap('reroll_shop', function() return {} end)
  wrap('toggle_shop', function() return {} end)
  wrap('cash_out', function() return {} end)
  wrap('skip_booster', function() return {} end)
end

function BotAPI.start_recording()
  install_hooks()
  recording = {}
  return { recording = true,
           seed = G.GAME.pseudorandom and G.GAME.pseudorandom.seed or "",
           deck = G.GAME.selected_back and G.GAME.selected_back.name or "",
           start = fingerprint() }
end

function BotAPI.stop_recording()
  local log = recording or {}
  recording = nil
  return { entries = #log }
end

--- Fetch the recording in slices: a full run is far more than one line.
function BotAPI.recording(args)
  if not recording then return { recording = false, entries = 0 } end
  local from = tonumber(args and args[1]) or 1
  local count = tonumber(args and args[2]) or 25
  local out = {}
  for i = from, math.min(from + count - 1, #recording) do
    out[#out + 1] = recording[i]
  end
  return { total = #recording, from = from, entries = out }
end

--- The current state fingerprint, for comparing a replay against a recording.
function BotAPI.check()
  return fingerprint()
end

-- --------------------------------------------------------------- actions

--- Run `fn` as a queued event rather than immediately.
---
--- Balatro's screens finish themselves over several frames: the round
--- evaluation is still adding payout rows, the shop is still sliding in. Those
--- pending events hold references to UI the action is about to destroy, so
--- calling directly crashes the game. Events block by default, so queueing puts
--- the action behind whatever is still in flight -- which is what a player's
--- reaction time does for free, and why this is not reachable by hand.
local function queue(fn)
  G.E_MANAGER:add_event(Event({
    trigger = 'immediate',
    func = function() fn(); return true end,
  }))
end

BotAPI.queue = queue
--
-- Actions return immediately. The game animates them over the following
-- frames, and the client polls `state` until `ready` comes back.

function BotAPI.start_run(args)
  local seed = args and args[1]
  if seed == "-" then seed = nil end          -- "no seed" placeholder
  local deck = args and args[2] and args[2]:gsub("_", " ")
  if deck then G.GAME.viewed_back = { name = deck } end

  -- G.FUNCS.start_run queues delete_run and start_run as events, so when this
  -- returns the previous run is still on screen. A client that acts on what it
  -- sees now is acting on the old run: the stale blind-select box is still
  -- there, and select_blind against it silently does nothing.
  --
  -- Mark the restart as pending and clear it from an event queued behind the
  -- game's own, so `run_pending` tells the client when the new run is real.
  BOT_RUN_PENDING = true
  G.FUNCS.start_run(nil, { seed = seed })
  G.E_MANAGER:add_event(Event({
    trigger = 'immediate', no_delete = true,
    func = function() BOT_RUN_PENDING = false; return true end,
  }))
  return { started = true, seed = seed or "random", deck = deck or "Red Deck" }
end

function BotAPI.select_blind()
  -- The real UI exists here, so the game's own callback works as clicked.
  G.FUNCS.select_blind({ config = {
    ref_table = G.P_BLINDS[G.GAME.round_resets.blind_choices[blind_on_deck()]] } })
  return { selected = blind_on_deck() }
end

function BotAPI.skip_blind()
  local on_deck = blind_on_deck()
  local element = G.blind_select_opts and G.blind_select_opts[on_deck:lower()]
  G.FUNCS.skip_blind({ config = { ref_table = { blind = on_deck } },
                       UIBox = element })
  return { skipped = on_deck }
end

function BotAPI.toggle(args)
  local index = tonumber(args[1])
  local card = G.hand.cards[index]
  if not card then return { ok = false, reason = "no card at " .. tostring(index) } end
  if is_highlighted(index) then
    G.hand:remove_from_highlighted(card)
  else
    G.hand:add_to_highlighted(card)
  end
  return { selection = #G.hand.highlighted }
end

function BotAPI.clear()
  G.hand:unhighlight_all()
  return { selection = 0 }
end

-- Guard the empty case: evaluate_play indexes the scoring hand unconditionally,
-- so playing nothing crashes the game (state_events.lua:574). A client should
-- not be able to do that by mistake.
function BotAPI.play()
  if #G.hand.highlighted == 0 then
    error("play with no cards selected", 0)
  end
  if G.STATE ~= G.STATES.SELECTING_HAND then
    error("play outside the hand-selection phase", 0)
  end
  G.FUNCS.play_cards_from_highlighted()
  return { played = true }
end

function BotAPI.discard()
  if #G.hand.highlighted == 0 then
    error("discard with no cards selected", 0)
  end
  if G.GAME.current_round.discards_left <= 0 then
    error("no discards left", 0)
  end
  G.FUNCS.discard_cards_from_highlighted()
  return { discarded = true }
end

function BotAPI.cash_out()
  queue(function() G.FUNCS.cash_out({ config = {} }) end)
  return { cashed_out = true }
end

function BotAPI.buy(args)
  local area, index = args[1], tonumber(args[2])
  local card = G[area] and G[area].cards[index]
  if not card then return { ok = false, reason = "no shop card" } end
  local element = { config = { ref_table = card, id = 'buy' } }
  -- Queued for the same reason as leave_shop: the shop's entry animation may
  -- still have work pending that expects the shop to exist.
  queue(function()
    if area == 'shop_vouchers' or area == 'shop_booster' then
      G.FUNCS.use_card(element, true)
    else
      G.FUNCS.buy_from_shop(element)
    end
  end)
  return { bought = true }
end

function BotAPI.sell(args)
  local area, index = args[1], tonumber(args[2])
  local card = G[area] and G[area].cards[index]
  if not card then return { ok = false, reason = "no card" } end
  if not card:can_sell_card() then
    return { ok = false, reason = "cannot sell (eternal or locked)" }
  end
  G.FUNCS.sell_card({ config = { ref_table = card } })
  return { sold = true }
end

function BotAPI.use_consumable(args)
  local card = G.consumeables.cards[tonumber(args[1])]
  if not card then return { ok = false, reason = "no consumable" } end
  G.FUNCS.use_card({ config = { ref_table = card } }, true)
  return { used = true }
end

function BotAPI.reroll()
  G.FUNCS.reroll_shop({ config = {} })
  G.CONTROLLER.locks.shop_reroll = nil
  return { rerolled = true }
end

function BotAPI.leave_shop()
  -- Queue it rather than calling it directly. update_shop leaves an event
  -- pending that waits for the shop to finish sliding in and then touches
  -- G.shop; closing the shop before that event runs removes G.shop out from
  -- under it and crashes the game. Balatro's events block by default, so an
  -- action queued behind that one simply waits for it -- which is what a
  -- player's reaction time does for free.
  --
  -- toggle_shop also clears its own lock in its final event, so nothing here
  -- should clear it early.
  queue(function() G.FUNCS.toggle_shop({ config = {} }) end)
  return { left = true }
end

function BotAPI.pick_pack(args)
  local card = G.pack_cards and G.pack_cards.cards[tonumber(args[1])]
  if not card then return { ok = false, reason = "no pack card" } end
  queue(function() G.FUNCS.use_card({ config = { ref_table = card } }, true) end)
  return { picked = true }
end

function BotAPI.skip_pack()
  queue(function() G.FUNCS.skip_booster({ config = {} }) end)
  return { skipped = true }
end

function BotAPI.move_joker(args)
  local from, to = tonumber(args[1]), tonumber(args[2])
  local cards = G.jokers.cards
  if not cards[from] then return { ok = false, reason = "no joker" } end
  to = math.max(1, math.min(#cards, to))
  table.insert(cards, to, table.remove(cards, from))
  G.jokers:set_ranks()
  G.jokers:align_cards()
  return { moved = true }
end

return BotAPI
