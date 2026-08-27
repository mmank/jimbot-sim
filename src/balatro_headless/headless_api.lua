-- Player actions, with the UI theatre removed.
--
-- Balatro's button callbacks mix two things: game logic, and the animation that
-- presents it. G.FUNCS.select_blind is ~40 lines, of which three matter
-- (set the blind, mark it current, call new_round()); the rest moves prompt
-- boxes around. This module calls the logic and skips the presentation.
--
-- Where a value is normally computed inside a UI builder -- blind_on_deck is
-- derived in create_UIBox_blind_select -- the derivation is reproduced here
-- verbatim rather than guessed at.

local api = {}

local DT = 1 / 60

-- Game:update dispatches to a per-state update function, and those carry real
-- logic: update_draw_to_hand deals cards, update_hand_played scores them,
-- update_round_eval pays out. This mirrors that dispatch block verbatim,
-- leaving out only the surrounding animation bookkeeping.
function api.state_dispatch(dt)
  local S = G.STATES
  if G.STATE == S.SELECTING_HAND then
    if (not G.hand.cards[1]) and G.deck.cards[1] then
      G.STATE = S.DRAW_TO_HAND
      G.STATE_COMPLETE = false
    else
      G:update_selecting_hand(dt)
    end
  end
  if G.STATE == S.SHOP           then G:update_shop(dt) end
  if G.STATE == S.PLAY_TAROT     then G:update_play_tarot(dt) end
  if G.STATE == S.HAND_PLAYED    then G:update_hand_played(dt) end
  if G.STATE == S.DRAW_TO_HAND   then G:update_draw_to_hand(dt) end
  if G.STATE == S.NEW_ROUND      then G:update_new_round(dt) end
  -- BLIND_SELECT's real update builds the blind-select screen; its only logic
  -- is applying pending tags, which api.enter_blind_select does instead.
  if G.STATE == S.BLIND_SELECT   then api.enter_blind_select() end
  if G.STATE == S.ROUND_EVAL     then G:update_round_eval(dt) end
  if G.STATE == S.TAROT_PACK     then G:update_arcana_pack(dt) end
  if G.STATE == S.SPECTRAL_PACK  then G:update_spectral_pack(dt) end
  if G.STATE == S.STANDARD_PACK  then G:update_standard_pack(dt) end
  if G.STATE == S.BUFFOON_PACK   then G:update_buffoon_pack(dt) end
  if G.STATE == S.PLANET_PACK    then G:update_celestial_pack(dt) end
  if G.STATE == S.GAME_OVER      then G:update_game_over(dt) end
end

-- The event manager is where the game's logic actually happens: scoring,
-- drawing, joker triggers are all queued Events. Card areas are pumped too so
-- card state settles. Game:update is deliberately not called -- it drives the
-- HUD, which does not exist here.
function api.pump(frames)
  for _ = 1, (frames or 120) do
    LOVE_STUB.advance(DT)
    -- Frame-scoped values Game:update would set; the event manager and the
    -- card areas read them back.
    G.real_dt = DT
    G.MAJORS, G.MINORS = 0, 0
    G.FRAMES.MOVE = G.FRAMES.MOVE + 1

    -- Game:update's clock. Event delays are measured against TIMERS.TOTAL,
    -- which runs at dt*SPEEDFACTOR, while the event manager itself is stepped
    -- with the unscaled real dt. Raising GAMESPEED therefore fast-forwards
    -- animation without touching any game logic. (Delays written as
    -- `0.06*GAMESPEED` are deliberately immune -- they scale with the clock.)
    if G.STATE == G.STATES.HAND_PLAYED or G.STATE == G.STATES.NEW_ROUND then
      G.ACC = math.min((G.ACC or 0) + DT * 0.2 * G.SETTINGS.GAMESPEED, 16)
    else
      G.ACC = 0
    end
    G.SPEEDFACTOR = (G.STAGE == G.STAGES.RUN and not G.SETTINGS.paused) and
                    G.SETTINGS.GAMESPEED or 1
    G.SPEEDFACTOR = G.SPEEDFACTOR + math.max(0, math.abs(G.ACC) - 2)

    G.TIMERS.REAL = G.TIMERS.REAL + DT
    G.TIMERS.TOTAL = G.TIMERS.TOTAL + DT * G.SPEEDFACTOR
    G.E_MANAGER:update(DT)
    api.state_dispatch(DT)
    for _, area in ipairs({ G.hand, G.deck, G.play, G.discard, G.jokers,
                            G.consumeables, G.shop_jokers, G.shop_booster,
                            G.shop_vouchers, G.pack_cards }) do
      if area then area:update(DT) end
    end
  end
end

-- Pump until a predicate holds, so callers wait on the game reaching a state
-- rather than on a frame count they had to guess.
function api.pump_until(predicate, max_frames)
  max_frames = max_frames or 3000
  for i = 1, max_frames do
    if predicate() then return true, i end
    api.pump(1)
  end
  return predicate(), max_frames
end

-- The logic tail of Game:update_blind_select, without the UIBox: pending tags
-- fire here (Investment pays out, Charm opens a pack, Voucher stocks the shop),
-- so skipping the state entirely would silently drop them.
function api.enter_blind_select()
  if G.STATE_COMPLETE then return end
  G.STATE_COMPLETE = true
  if G.buttons then G.buttons:remove(); G.buttons = nil end
  if G.shop then G.shop:remove(); G.shop = nil end
  for i = 1, #G.GAME.tags do
    G.GAME.tags[i]:apply_to_run({ type = 'immediate' })
  end
  for i = 1, #G.GAME.tags do
    if G.GAME.tags[i]:apply_to_run({ type = 'new_blind_choice' }) then break end
  end
end

function api.blind_on_deck()
  local states = G.GAME.round_resets.blind_states
  local function done(s) return s == 'Defeated' or s == 'Skipped' or s == 'Hide' end
  return (not done(states.Small) and 'Small')
      or (not done(states.Big) and 'Big')
      or 'Boss'
end

function api.current_blind_key()
  local on_deck = api.blind_on_deck()
  return G.GAME.round_resets.blind_choices[on_deck]
end

-- G.FUNCS.select_blind, minus the prompt boxes.
function api.select_blind()
  local on_deck = api.blind_on_deck()
  G.GAME.blind_on_deck = on_deck
  G.GAME.facing_blind = true
  ease_round(1)
  G.GAME.round_resets.blind = G.P_BLINDS[G.GAME.round_resets.blind_choices[on_deck]]
  G.GAME.round_resets.blind_states[on_deck] = 'Current'
  new_round()
  api.pump_until(function()
    return G.STATE == G.STATES.SELECTING_HAND and #G.hand.cards > 0
  end)
  return api.blind_on_deck()
end

-- G.FUNCS.skip_blind, minus the tag animation. The tag itself is real.
function api.skip_blind()
  local on_deck = api.blind_on_deck()
  G.GAME.blind_on_deck = on_deck
  local tag = G.GAME.round_resets.blind_tags[on_deck]
  if tag then
    add_tag(Tag(tag))
  end
  G.GAME.round_resets.blind_states[on_deck] = 'Skipped'
  G.GAME.round_resets.blind_states[on_deck == 'Small' and 'Big' or 'Boss'] = 'Select'
  G.GAME.blind_on_deck = on_deck == 'Small' and 'Big' or 'Boss'
  api.pump(30)
  return G.GAME.blind_on_deck
end

-- Selection is normally done by clicking; highlight_card does it directly.
function api.clear_highlights()
  if G.hand then G.hand:unhighlight_all() end
end

function api.highlight(indices)
  api.clear_highlights()
  for _, i in ipairs(indices) do
    local card = G.hand.cards[i]
    if not card then error("no card at hand index " .. tostring(i)) end
    G.hand:add_to_highlighted(card, true)
  end
  return #G.hand.highlighted
end

-- Scoring is asynchronous: play_cards_from_highlighted only queues the work.
-- Two traps here. Waiting on G.STATE alone is wrong, because the state is
-- still SELECTING_HAND at the moment of the call. Waiting for the event queue
-- to *drain* is also wrong -- card-movement easing events are non-blocking and
-- effectively never finish, so that spins forever. Wait instead for the game to
-- be playable again: the counter has ticked, and either the hand is refilled or
-- the round has moved on.

-- States in which the run has left the hand behind and the caller must act.
local HANDED_OFF = nil
local function handed_off()
  HANDED_OFF = HANDED_OFF or {
    [G.STATES.GAME_OVER] = true, [G.STATES.ROUND_EVAL] = true,
    [G.STATES.NEW_ROUND] = true, [G.STATES.SHOP] = true,
    [G.STATES.TAROT_PACK] = true, [G.STATES.PLANET_PACK] = true,
    [G.STATES.SPECTRAL_PACK] = true, [G.STATES.STANDARD_PACK] = true,
    [G.STATES.BUFFOON_PACK] = true, [G.STATES.BLIND_SELECT] = true,
  }
  return HANDED_OFF
end

function api.playable()
  return (G.STATE == G.STATES.SELECTING_HAND and #G.hand.cards > 0)
      or handed_off()[G.STATE] or false
end

function api.play(indices)
  api.highlight(indices)
  local before = G.GAME.current_round.hands_played
  G.FUNCS.play_cards_from_highlighted()
  api.pump_until(function()
    return G.GAME.current_round.hands_played > before and api.playable()
  end)
  return G.GAME.chips
end

function api.discard(indices)
  api.highlight(indices)
  local before = G.GAME.current_round.discards_used
  G.FUNCS.discard_cards_from_highlighted()
  api.pump_until(function()
    return G.GAME.current_round.discards_used > before and api.playable()
  end)
  return G.GAME.current_round.discards_left
end

return api
