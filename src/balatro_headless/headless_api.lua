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

-- See api.pump: animation-only, but game code may read card state.
api.update_card_areas = true

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
  if G.STATE == S.ROUND_EVAL     then api.enter_round_eval() end
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
    -- Card areas are animation: they ease cards toward their target positions.
    -- The logical transfers (draw_card, emplace, remove_card) happen in events,
    -- not here. Updating them is ~60% of a frame's cost during scoring, so it
    -- is optional -- but only switch it off if the whole suite still passes,
    -- since some game code does read card state.
    if api.update_card_areas then
      for _, area in ipairs({ G.hand, G.deck, G.play, G.discard, G.jokers,
                              G.consumeables, G.shop_jokers, G.shop_booster,
                              G.shop_vouchers, G.pack_cards }) do
        -- Leaving the shop removes its areas, but the globals linger a frame;
        -- a removed area has no cards table and updating it throws.
        if area and area.cards then area:update(DT) end
      end
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

-- Game:update_round_eval builds the cash-out box, then waits for it to finish
-- sliding into place before calling evaluate_round(). Headless, the box never
-- moves, so that wait never ends and the blind is never paid out. Build the box
-- for real (add_round_eval_row measures against it, and ease_dollars pokes the
-- HUD, so both must exist), then snap it into position and pay out.
function api.enter_round_eval()
  if G.STATE_COMPLETE then return end
  G.STATE_COMPLETE = true
  G.GAME.facing_blind = nil
  if G.buttons then G.buttons:remove(); G.buttons = nil end
  if not G.round_eval then
    G.round_eval = UIBox{
      definition = create_UIBox_round_evaluation(),
      config = {align = "bm", offset = {x = 0, y = G.ROOM.T.y + 19},
                major = G.hand, bond = 'Weak'},
    }
  end
  G.round_eval.alignment.offset.y = -7.8
  G.FUNCS.evaluate_round()
end

-- G.FUNCS.cash_out expects the button element it was clicked from.
function api.cash_out()
  api.pump_until(function() return G.round_eval ~= nil end, 600)
  local owed = G.GAME.current_round.dollars or 0
  local before = G.GAME.dollars
  G.FUNCS.cash_out({ config = {} })
  -- ease_dollars queues the payment rather than applying it, so waiting only
  -- for the SHOP state reads the money before it lands.
  api.pump_until(function()
    return G.STATE == G.STATES.SHOP and (owed == 0 or G.GAME.dollars ~= before)
  end, 3000)
  -- The shop's cards are dealt asynchronously after the state flips, so
  -- returning on the state alone hands the caller an empty shop.
  api.pump_until(api.shop_ready, 400)
  api.pump(30)
  return G.GAME.dollars
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

-- Enumerating the 218 subsets of an 8-card hand is done here rather than in
-- Python: each hand_info call crosses the language boundary, and 218 crossings
-- per decision is far more expensive than the search itself.
local function subsets(n, max_size, fn)
  local idx = {}
  local function recurse(start, depth)
    if depth > 0 then fn(idx) end
    if depth == max_size then return end
    for i = start, n do
      idx[depth + 1] = i
      recurse(i + 1, depth + 1)
      idx[depth + 1] = nil
    end
  end
  recurse(1, 0)
end

function api.best_play(max_size)
  max_size = max_size or 5
  local best, best_score = nil, -1
  subsets(#G.hand.cards, max_size, function(idx)
    local info = api.hand_info(idx)
    -- Tie-break toward fewer cards: playing a card that does not score just
    -- discards it, which matters for hands the deck still needs.
    if info.estimate > best_score or
       (info.estimate == best_score and best and #idx < #best) then
      best_score = info.estimate
      best = { unpack(idx) }
    end
  end)
  return best, best_score
end

-- Discard the cards the best play does not use, worst-first. Keeping the
-- best play's cards is a simple heuristic but a sound one: it never throws
-- away the hand it is building toward.
function api.best_discard(max_size)
  max_size = max_size or 5
  local keep_list = api.best_play(5)
  local keep = {}
  for _, i in ipairs(keep_list or {}) do keep[i] = true end
  local junk = {}
  for i = 1, #G.hand.cards do
    if not keep[i] then junk[#junk + 1] = { i, G.hand.cards[i].base.nominal or 0 } end
  end
  table.sort(junk, function(a, b) return a[2] < b[2] end)
  local out = {}
  for i = 1, math.min(max_size, #junk) do out[#out + 1] = junk[i][1] end
  table.sort(out)
  return out
end

-- ---------------------------------------------------------------- packs

local PACK_STATES = nil
function api.in_pack()
  PACK_STATES = PACK_STATES or {
    [G.STATES.TAROT_PACK] = true, [G.STATES.PLANET_PACK] = true,
    [G.STATES.SPECTRAL_PACK] = true, [G.STATES.STANDARD_PACK] = true,
    [G.STATES.BUFFOON_PACK] = true,
  }
  return PACK_STATES[G.STATE] or false
end

-- Card:open() generates the pack's cards immediately but only emplaces them
-- once the pack area has animated into view: card.lua gates the emplace on
-- `G.pack_cards.VT.y < G.ROOM.T.h`, and returns nil otherwise, so the event
-- retries every frame forever. Headless the area never moves, so the pack
-- stays permanently empty. Snapping it into view lets the game's own event
-- place its own cards.
function api.settle_pack()
  if not api.in_pack() then return 0 end
  if G.pack_cards and G.pack_cards.VT.y >= G.ROOM.T.h then
    G.pack_cards.T.y = G.ROOM.T.h - 2
    G.pack_cards.VT.y = G.pack_cards.T.y
  end
  api.pump_until(function()
    return G.pack_cards and G.pack_cards.cards and #G.pack_cards.cards > 0
  end, 900)
  return (G.pack_cards and G.pack_cards.cards and #G.pack_cards.cards) or 0
end

function api.pack_contents()
  api.settle_pack()
  local out = {}
  if G.pack_cards and G.pack_cards.cards then
    for i, card in ipairs(G.pack_cards.cards) do
      out[#out + 1] = {
        index = i,
        key = card.config.center.key,
        set = card.config.center.set,
      }
    end
  end
  return out
end

function api.skip_pack()
  G.FUNCS.skip_booster({ config = {} })
  api.pump_until(function() return not api.in_pack() end, 2000)
  api.pump(30)
  return G.STATE
end

-- Taking from a pack is the same "use this card" path as playing a consumable.
function api.pick_pack(index)
  local card = G.pack_cards and G.pack_cards.cards and G.pack_cards.cards[index]
  if not card then error('no pack card at index ' .. tostring(index)) end
  G.FUNCS.use_card({ config = { ref_table = card } }, true)
  api.pump(120)
  return G.STATE
end

-- ---------------------------------------------------------------- inspection

-- Non-destructive: asks the game what a candidate selection would be scored as,
-- without playing it. This is what a policy uses to choose a hand.
function api.hand_info(indices)
  local cards = {}
  for _, i in ipairs(indices) do
    cards[#cards + 1] = G.hand.cards[i]
  end
  local text, _, _, scoring = G.FUNCS.get_poker_hand_info(cards)
  local level = G.GAME.hands[text]
  local chips, mult = 0, 0
  if level then chips, mult = level.chips, level.mult end
  local card_chips = 0
  for _, card in ipairs(scoring or {}) do
    card_chips = card_chips + (card.base and card.base.nominal or 0)
  end
  return {
    hand = text,
    level = level and level.level or 0,
    base_chips = chips,
    base_mult = mult,
    card_chips = card_chips,
    scoring = #(scoring or {}),
    -- Jokers are not accounted for, so this is a ranking signal, not a score.
    estimate = (chips + card_chips) * mult,
  }
end

function api.hand_cards()
  local out = {}
  for i, card in ipairs(G.hand.cards) do
    out[#out + 1] = {
      index = i,
      rank = card.base.value,
      suit = card.base.suit,
      nominal = card.base.nominal,
      id = card:get_id(),
      enhancement = card.config.center.key,
      debuffed = card.debuff and true or false,
    }
  end
  return out
end

function api.jokers()
  local out = {}
  for i, card in ipairs(G.jokers.cards) do
    out[#out + 1] = { index = i, key = card.config.center.key,
                      sell_cost = card.sell_cost,
                      rarity = card.config.center.rarity,
                      eternal = card.ability.eternal and true or false,
                      sellable = card:can_sell_card() and true or false }
  end
  return out
end

-- One flat snapshot, cheap to cross the Lua/Python boundary once per decision.
function api.snapshot()
  local blind = G.GAME.blind
  return {
    state = G.STATE,
    state_name = api.state_name(),
    ante = G.GAME.round_resets.ante,
    round = G.GAME.round,
    dollars = G.GAME.dollars,
    chips = G.GAME.chips,
    blind_name = blind and blind.name or '',
    blind_chips = (blind and blind.chips) or 0,
    blind_on_deck = api.blind_on_deck(),
    -- Both of the ante's skip rewards, which the real game shows on the blind
    -- select screen -- the skip decision is uninformed without them.
    tag_small = G.GAME.round_resets.blind_tags.Small or '',
    tag_big = G.GAME.round_resets.blind_tags.Big or '',
    offered_tag = G.GAME.round_resets.blind_tags[api.blind_on_deck()] or '',
    skippable = api.blind_on_deck() ~= 'Boss',
    hands_left = G.GAME.current_round.hands_left,
    discards_left = G.GAME.current_round.discards_left,
    hand_size = #G.hand.cards,
    joker_count = #G.jokers.cards,
    joker_limit = G.jokers.config.card_limit,
    consumable_count = #G.consumeables.cards,
    consumable_limit = G.consumeables.config.card_limit,
    reroll_cost = G.GAME.current_round.reroll_cost or 0,
    won = G.GAME.won and true or false,
  }
end

local STATE_NAMES = nil
function api.state_name()
  if not STATE_NAMES then
    STATE_NAMES = {}
    for name, value in pairs(G.STATES) do STATE_NAMES[value] = name end
  end
  return STATE_NAMES[G.STATE] or ('STATE_' .. tostring(G.STATE))
end

function api.consumables()
  local out = {}
  for i, card in ipairs(G.consumeables.cards) do
    out[#out + 1] = { index = i, key = card.config.center.key,
                      set = card.config.center.set,
                      sell_cost = card.sell_cost,
                      sellable = card:can_sell_card() and true or false }
  end
  return out
end

-- Targeted consumables (most tarots) read G.hand.highlighted, exactly as they
-- do when a player selects cards and clicks Use. Untargeted ones ignore it.
function api.use_consumable(index, card_indices)
  local card = G.consumeables.cards[index]
  if not card then error('no consumable at index ' .. tostring(index)) end
  if card_indices and #card_indices > 0 then
    api.highlight(card_indices)
  else
    api.clear_highlights()
  end
  G.FUNCS.use_card({ config = { ref_table = card } }, true)
  api.pump_until(function()
    for _, c in ipairs(G.consumeables.cards) do if c == card then return false end end
    return true
  end, 600)
  api.pump(60)
  return G.STATE
end

-- ---------------------------------------------------------------- ordering

-- Joker order is not cosmetic: effects resolve left to right, so XMult after
-- +Mult scores differently from the reverse. Dragging is a UI affordance over
-- this array, which is the same one the scoring loop walks.
function api.move_joker(from, to)
  local cards = G.jokers.cards
  if not cards[from] then error('no joker at index ' .. tostring(from)) end
  to = math.max(1, math.min(#cards, to))
  local card = table.remove(cards, from)
  table.insert(cards, to, card)
  G.jokers:set_ranks()
  G.jokers:align_cards()
  return api.jokers()
end

function api.reorder_jokers(order)
  local cards = G.jokers.cards
  if #order ~= #cards then
    error('reorder needs ' .. #cards .. ' indices, got ' .. #order)
  end
  local seen, reordered = {}, {}
  for _, i in ipairs(order) do
    if not cards[i] then error('no joker at index ' .. tostring(i)) end
    if seen[i] then error('duplicate index ' .. tostring(i) .. ' in reorder') end
    seen[i] = true
    reordered[#reordered + 1] = cards[i]
  end
  for i = 1, #cards do cards[i] = reordered[i] end
  G.jokers:set_ranks()
  G.jokers:align_cards()
  return api.jokers()
end

-- The game's own sort buttons.
function api.sort_hand(by)
  if by == 'suit' then
    G.FUNCS.sort_hand_suit({ config = {} })
  else
    G.FUNCS.sort_hand_value({ config = {} })
  end
  api.pump(30)
  return api.hand_cards()
end

function api.move_consumable(from, to)
  local cards = G.consumeables.cards
  if not cards[from] then error('no consumable at index ' .. tostring(from)) end
  to = math.max(1, math.min(#cards, to))
  table.insert(cards, to, table.remove(cards, from))
  G.consumeables:set_ranks()
  G.consumeables:align_cards()
  return api.consumables()
end

-- ---------------------------------------------------------------- shop
-- The shop's buttons pass the clicked UI element to their callback; all any of
-- them actually read is e.config, so a table with the right fields stands in.

local SHOP_AREAS = { 'shop_jokers', 'shop_booster', 'shop_vouchers' }

-- The shop always stocks its main row; empty means the cards have not landed.
function api.shop_ready()
  return G.STATE == G.STATES.SHOP
     and G.shop_jokers ~= nil and G.shop_jokers.cards ~= nil
     and #G.shop_jokers.cards > 0
end

function api.shop_contents()
  local out = {}
  for _, name in ipairs(SHOP_AREAS) do
    local area = G[name]
    if area then
      for i, card in ipairs(area.cards) do
        out[#out + 1] = {
          area = name,
          index = i,
          key = card.config.center.key,
          name = card.ability and card.ability.name or card.config.center.key,
          set = card.config.center.set,
          rarity = card.config.center.rarity or 0,
          cost = card.cost,
          affordable = (card.cost or 0) <= G.GAME.dollars,
          -- The game's own space check: joker slots for jokers, consumable
          -- slots for consumables and for the packs that yield them.
          buyable = ((card.cost or 0) <= G.GAME.dollars)
                    and (G.FUNCS.check_for_buy_space(card) and true or false),
        }
      end
    end
  end
  return out
end

function api.shop_card(area, index)
  local a = G[area]
  local card = a and a.cards[index]
  if not card then error('no shop card at ' .. tostring(area) .. '[' .. tostring(index) .. ']') end
  return card
end

function api.can_buy(area, index)
  local card = api.shop_card(area, index)
  if (card.cost or 0) > G.GAME.dollars then return false end
  return G.FUNCS.check_for_buy_space(card) and true or false
end

-- The shop's three rows use three different callbacks, and calling the wrong
-- one fails silently: buy_from_shop on a voucher or a booster pack simply does
-- nothing, which is why they never appeared to be purchasable.
function api.buy(area, index)
  local card = api.shop_card(area, index)
  local before = G.GAME.dollars
  local element = { config = { ref_table = card, id = 'buy' } }

  -- The shop's three rows are three different actions. Only the joker row
  -- goes through buy_from_shop; vouchers and packs go through use_card, which
  -- removes the card from the shop (button_callbacks: card.area:remove_card)
  -- *before* calling Card:redeem() or Card:open().
  --
  -- Calling those Card methods directly, as this used to, redeems or opens
  -- without ever taking the card off the shelf -- so the same voucher could be
  -- bought again and again. That was observable: with a large bankroll a policy
  -- redeemed Hieroglyph 98 times and drove the ante to -99.
  if area == 'shop_vouchers' or area == 'shop_booster' then
    G.FUNCS.use_card(element, true)
  else
    G.FUNCS.buy_from_shop(element)
  end

  api.pump_until(function()
    return G.GAME.dollars ~= before or card.area ~= G[area] or api.in_pack()
  end, 900)
  api.pump(60)
  if api.in_pack() then api.settle_pack() end
  return G.GAME.dollars
end

function api.reroll_cost()
  return G.GAME.current_round.reroll_cost or 0
end

function api.can_reroll()
  return api.reroll_cost() <= G.GAME.dollars
end

function api.reroll()
  local before = G.GAME.dollars
  G.FUNCS.reroll_shop({ config = {} })
  api.pump_until(function() return G.GAME.dollars ~= before end, 600)
  api.pump(60)
  G.CONTROLLER.locks.shop_reroll = nil
  return G.GAME.dollars
end

function api.can_sell(area, index)
  local a = G[area or 'jokers']
  local card = a and a.cards[index]
  if not card then return false end
  -- The game's own gate. G.FUNCS.sell_card does not check it -- only the UI
  -- does, via can_sell_card -- so calling sell directly would happily sell an
  -- Eternal joker, which the real game forbids.
  return card:can_sell_card() and true or false
end

function api.sell(area, index)
  local a = G[area or 'jokers']
  local card = a and a.cards[index]
  if not card then error('no card to sell at ' .. tostring(area) .. '[' .. tostring(index) .. ']') end
  if not card:can_sell_card() then
    error('cannot sell ' .. tostring(card.config.center.key) ..
          ' (eternal or otherwise locked)')
  end
  local before = G.GAME.dollars
  G.FUNCS.sell_card({ config = { ref_table = card } })
  api.pump_until(function() return G.GAME.dollars ~= before end, 600)
  api.pump(30)
  return G.GAME.dollars
end

function api.leave_shop()
  G.FUNCS.toggle_shop({ config = {} })
  api.pump_until(function() return G.STATE == G.STATES.BLIND_SELECT end, 3000)
  G.CONTROLLER.locks.toggle_shop = nil
  return G.STATE
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

-- ---------------------------------------------------------------- RL surface
--
-- One call per environment step. Everything an agent needs -- observation and
-- action legality -- comes back in a single table, because each crossing of the
-- Lua/Python boundary costs far more than the work behind it.
--
-- Card selection is G.hand.highlighted, the game's own mechanism, rather than a
-- parallel bookkeeping layer that could drift out of sync with it.

local KEY_INDEX, KEY_ORDER = nil, nil

local function key_index()
  if KEY_INDEX then return KEY_INDEX end
  KEY_INDEX, KEY_ORDER = {}, {}
  for key, center in pairs(G.P_CENTERS) do
    KEY_ORDER[#KEY_ORDER + 1] = key
  end
  table.sort(KEY_ORDER)
  for i, key in ipairs(KEY_ORDER) do KEY_INDEX[key] = i end
  return KEY_INDEX
end

function api.key_count()
  key_index()
  return #KEY_ORDER
end

-- The full centre vocabulary, in the same order as key_id, so Python can build
-- its one-hot tables once at construction instead of shipping strings.
function api.key_list()
  key_index()
  local out = {}
  for i, key in ipairs(KEY_ORDER) do
    out[i] = key .. '|' .. tostring(G.P_CENTERS[key].set or '')
  end
  return table.concat(out, ',')
end

-- Stable id for a centre (joker, consumable, enhancement...) so Python can
-- one-hot it without shipping strings every step.
function api.key_id(key)
  return key and key_index()[key] or 0
end

local SET_IDS = { Joker = 1, Tarot = 2, Planet = 3, Spectral = 4, Voucher = 5,
                  Booster = 6, Default = 7, Enhanced = 8 }

local RANK_IDS = { ['2']=1, ['3']=2, ['4']=3, ['5']=4, ['6']=5, ['7']=6,
                   ['8']=7, ['9']=8, ['10']=9, ['Jack']=10, ['Queen']=11,
                   ['King']=12, ['Ace']=13 }
local SUIT_IDS = { Spades = 1, Hearts = 2, Clubs = 3, Diamonds = 4 }

function api.is_highlighted(index)
  local card = G.hand.cards[index]
  if not card then return false end
  for _, c in ipairs(G.hand.highlighted) do
    if c == card then return true end
  end
  return false
end

-- Toggle a card in or out of the selection, exactly as clicking it does.
function api.toggle(index)
  local card = G.hand.cards[index]
  if not card then return false end
  if api.is_highlighted(index) then
    G.hand:remove_from_highlighted(card)
  else
    if #G.hand.highlighted >= (G.hand.config.highlighted_limit or 5) then
      return false
    end
    G.hand:add_to_highlighted(card, true)
  end
  return true
end

function api.selection()
  local out = {}
  for i = 1, #G.hand.cards do
    if api.is_highlighted(i) then out[#out + 1] = i end
  end
  return out
end

-- Play or discard whatever is currently selected.
function api.play_selected()
  local before = G.GAME.current_round.hands_played
  G.FUNCS.play_cards_from_highlighted()
  api.pump_until(function()
    return G.GAME.current_round.hands_played > before and api.playable()
  end)
  return G.GAME.chips
end

function api.discard_selected()
  local before = G.GAME.current_round.discards_used
  G.FUNCS.discard_cards_from_highlighted()
  api.pump_until(function()
    return G.GAME.current_round.discards_used > before and api.playable()
  end)
  return G.GAME.current_round.discards_left
end

-- Swap a joker with its left neighbour. Repeated swaps reach any ordering,
-- which keeps the action space linear in joker count rather than quadratic.
function api.swap_joker_left(index)
  if index <= 1 or not G.jokers.cards[index] then return false end
  api.move_joker(index, index - 1)
  return true
end

local function card_row(card, highlighted)
  return {
    rank = (card.base and RANK_IDS[card.base.value]) or 0,
    suit = (card.base and SUIT_IDS[card.base.suit]) or 0,
    center = api.key_id(card.config.center.key),
    edition = card.edition and api.key_id(card.edition.key or '') or 0,
    seal = card.seal and api.key_id('seal_' .. tostring(card.seal)) or 0,
    chips = (card.base and card.base.nominal) or 0,
    highlighted = highlighted and 1 or 0,
    debuffed = card.debuff and 1 or 0,
  }
end

function api.env_state()
  local blind = G.GAME.blind
  local state = {
    state = G.STATE,
    state_name = api.state_name(),
    ante = G.GAME.round_resets.ante,
    round = G.GAME.round,
    dollars = G.GAME.dollars,
    chips = G.GAME.chips,
    blind_chips = (blind and blind.chips) or 0,
    blind_name = (blind and blind.name) or '',
    boss = (blind and blind.boss) and 1 or 0,
    hands_left = G.GAME.current_round.hands_left,
    discards_left = G.GAME.current_round.discards_left,
    joker_limit = G.jokers.config.card_limit,
    consumable_limit = G.consumeables.config.card_limit,
    reroll_cost = G.GAME.current_round.reroll_cost or 0,
    won = G.GAME.won and 1 or 0,
    in_pack = api.in_pack() and 1 or 0,
    shop_ready = api.shop_ready() and 1 or 0,
    skippable = (api.blind_on_deck() ~= 'Boss') and 1 or 0,
    offered_tag = api.key_id(G.GAME.round_resets.blind_tags[api.blind_on_deck()] or ''),
    selection_size = #G.hand.highlighted,
    highlight_limit = G.hand.config.highlighted_limit or 5,
  }

  state.hand = {}
  for i, card in ipairs(G.hand.cards) do
    state.hand[i] = card_row(card, api.is_highlighted(i))
  end

  state.jokers = {}
  for i, card in ipairs(G.jokers.cards) do
    state.jokers[i] = {
      center = api.key_id(card.config.center.key),
      sellable = card:can_sell_card() and 1 or 0,
      sell_cost = card.sell_cost or 0,
      rarity = card.config.center.rarity or 0,
    }
  end

  state.consumables = {}
  for i, card in ipairs(G.consumeables.cards) do
    state.consumables[i] = {
      center = api.key_id(card.config.center.key),
      set = SET_IDS[card.config.center.set] or 0,
      sellable = card:can_sell_card() and 1 or 0,
      usable = (card.check_use and not card:check_use()) and 1 or 1,
    }
  end

  state.shop = {}
  for _, name in ipairs({ 'shop_jokers', 'shop_vouchers', 'shop_booster' }) do
    local area = G[name]
    if area and area.cards then
      for i, card in ipairs(area.cards) do
        state.shop[#state.shop + 1] = {
          area = name,
          index = i,
          center = api.key_id(card.config.center.key),
          set = SET_IDS[card.config.center.set] or 0,
          cost = card.cost or 0,
          buyable = ((card.cost or 0) <= G.GAME.dollars
                     and G.FUNCS.check_for_buy_space(card)) and 1 or 0,
        }
      end
    end
  end

  state.pack = {}
  if G.pack_cards and G.pack_cards.cards then
    for i, card in ipairs(G.pack_cards.cards) do
      state.pack[i] = {
        center = api.key_id(card.config.center.key),
        set = SET_IDS[card.config.center.set] or 0,
      }
    end
  end

  state.hand_levels = {}
  for name, data in pairs(G.GAME.hands) do
    state.hand_levels[name] = { level = data.level, played = data.played,
                                chips = data.chips, mult = data.mult }
  end

  return state
end

return api
