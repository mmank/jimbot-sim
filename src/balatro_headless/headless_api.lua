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
-- Updating every card every frame is what Game:update does, and it is not
-- only animation: Card:update derives consumeable.mod_num, which
-- can_use_consumeable compares against. bot_api's can_use now derives that on
-- demand, so this is here for anything else per-frame card logic does -- and
-- it is a large share of a frame, so it is worth being able to measure
-- without.
api.update_cards = false

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

    -- Booster packs deal their cards behind a *layout* check. Card:open
    -- creates them, and a second event only puts them on the table once
    -- `G.pack_cards.VT.y < G.ROOM.T.h` -- once the pack has slid into view.
    -- The position comes from UIBox alignment, which needs a renderer, so
    -- headless the pack sits below the room forever and the cards are never
    -- emplaced: the pack opens empty and the run wedges. It only bit packs
    -- opened from a tag, because those align to the hand on the blind select
    -- screen rather than in the shop, and that is fifteen units lower.
    --
    -- Supplying the one number the game's logic reads is enough. Nothing
    -- else looks at where the pack is.
    if G.pack_cards and G.ROOM and G.pack_cards.VT.y >= G.ROOM.T.h then
      G.pack_cards.T.y = G.ROOM.T.h - 1
      G.pack_cards.VT.y = G.ROOM.T.h - 1
    end

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
        if area and area.cards then
          area:update(DT)
          -- And the cards themselves. Game:update walks G.MOVEABLES and calls
          -- update on every one, cards included; skipping that here is not
          -- just skipping animation, because Card:update carries logic. It is
          -- where consumeable.mod_num is derived from max_highlighted, and
          -- can_use_consumeable compares against mod_num -- so without this
          -- every targeting tarot in the engine raised "attempt to compare
          -- number with nil" the moment the game asked whether it was usable.
          -- The small areas always update, whatever api.update_cards says,
          -- because Card:update is where several cards keep the number they
          -- are about to pay out. It is not animation.
          --
          -- Swashbuckler recomputes its mult from the other jokers' sell
          -- value there; skipping it left a lone one paying its center
          -- default of +1 instead of +0. Temperance is the same shape and
          -- worse: its payout *is* ability.money, recomputed per frame, so a
          -- Temperance used without an update pays nothing at all. Hex,
          -- Ectoplasm and The Wheel of Fortune sit in the same block, and
          -- those live in the consumable slots or in an open pack rather
          -- than the joker row.
          --
          -- Together these hold at most a handful of cards, so this costs
          -- nothing next to the fifty-two in the deck, which is what
          -- api.update_cards is actually for.
          -- Every area except the big piles updates, whatever
          -- api.update_cards says. The optimisation was only ever about the
          -- fifty-two cards sitting in the deck and the discard; the small
          -- areas hold a handful between them and their cards carry real
          -- state.
          --
          -- Naming the small ones instead was wrong twice. Jokers were
          -- exempted first, for Swashbuckler; then consumables and open
          -- packs, for a Temperance used out of a pack; and a Temperance
          -- bought with the shop's buy-and-use button is still sitting in the
          -- shop when it resolves, so that missed it too. The rule now says
          -- what it is for rather than listing what has bitten so far.
          local bulk = (area == G.deck) or (area == G.hand)
              or (area == G.discard)
          if api.update_cards or not bulk then
            for _, card in ipairs(area.cards) do
              card:update(DT * G.SPEEDFACTOR)
            end
          end
        end
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

--- What create_UIBox_blind_choice settles while it builds the blind select
--- screen: which poker hand an Orbital Tag would level.
---
--- It is decided in UI construction, and the engine never constructs that UI,
--- so it was never decided here. An Orbital Tag created afterwards has a nil
--- orbital_hand, and applying it reads G.GAME.hands[nil] -- which is not a
--- misbehaving agent but a dead worker, and with it the whole run.
---
--- Same blinds and same order as the real game, which builds a choice for each
--- blind it is not hiding, so the draws come off the seed stream in step.
local function ensure_orbital_choices()
  if not (G.GAME and G.GAME.round_resets and G.GAME.hands) then return end
  local ante = G.GAME.round_resets.ante
  G.GAME.orbital_choices = G.GAME.orbital_choices or {}
  G.GAME.orbital_choices[ante] = G.GAME.orbital_choices[ante] or {}
  for _, kind in ipairs({ 'Small', 'Big', 'Boss' }) do
    local states = G.GAME.round_resets.blind_states or {}
    if states[kind] ~= 'Hide' and not G.GAME.orbital_choices[ante][kind] then
      local hands = {}
      for name, data in pairs(G.GAME.hands) do
        if data.visible then hands[#hands + 1] = name end
      end
      if #hands > 0 then
        G.GAME.orbital_choices[ante][kind] =
          pseudorandom_element(hands, pseudoseed('orbital'))
      end
    end
  end
end

--- Give an Orbital Tag a hand if it never got one.
---
--- set_ability picks the hand when the tag is created, out of the table the
--- blind-select UI fills in. Deciding that table earlier is not enough on its
--- own: a tag created in a window where it was not yet populated -- or for a
--- blind_type with no entry -- keeps a nil hand for the rest of its life, and
--- applying it indexes G.GAME.hands[nil] and takes the worker with it. So it
--- is also repaired where it is used, which is the only place the damage
--- actually shows.
local function repair_orbital_tags()
  if not (G.GAME and G.GAME.tags and G.GAME.hands) then return end
  for _, tag in ipairs(G.GAME.tags) do
    local hand = tag.ability and tag.ability.orbital_hand
    if tag.name == 'Orbital Tag' and not (hand and G.GAME.hands[hand]) then
      local ante = G.GAME.round_resets.ante
      local chosen = G.GAME.orbital_choices
        and G.GAME.orbital_choices[ante]
        and G.GAME.orbital_choices[ante][tag.ability.blind_type]
      if not (chosen and G.GAME.hands[chosen]) then
        local hands = {}
        for name, data in pairs(G.GAME.hands) do
          if data.visible then hands[#hands + 1] = name end
        end
        chosen = #hands > 0 and pseudorandom_element(hands, pseudoseed('orbital'))
          or nil
      end
      tag.ability.orbital_hand = chosen
    end
  end
end

-- The logic tail of Game:update_blind_select, without the UIBox: pending tags
-- fire here (Investment pays out, Charm opens a pack, Voucher stocks the shop),
-- so skipping the state entirely would silently drop them.
function api.enter_blind_select()
  if G.STATE_COMPLETE then return end
  G.STATE_COMPLETE = true
  ensure_orbital_choices()
  repair_orbital_tags()
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
  -- Clear last round's total before this round's rows are built.
  --
  -- current_round.dollars is written by add_round_eval_row and never reset,
  -- so between rounds it holds whatever the previous one paid. cash_out pays
  -- exactly that field, and waiting for it to be non-zero is satisfied
  -- instantly from round two on -- by the wrong number. Measured on one
  -- seed: a round whose rows read blind1=4, hands=1, bottom=5 paid 7,
  -- because 7 was the round before. Clearing it here rather than in cash_out
  -- so that the wait is for a value this round actually produced; clearing
  -- it there would throw away rows that had already been built.
  G.GAME.current_round.dollars = 0
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

  -- Take the spent tags out. evaluate_round asks each tag whether it pays and
  -- marks the ones that did as triggered, but removing them is the tail of an
  -- animation: Tag:yep hides self.HUD_tag and the chain ends in Tag:remove().
  -- That HUD element is never built here, so an Investment Tag paid its $25
  -- and then stayed in the list forever -- visible to a policy as a reward it
  -- could collect again.
  for i = #G.GAME.tags, 1, -1 do
    if G.GAME.tags[i].triggered then table.remove(G.GAME.tags, i) end
  end
end

-- G.FUNCS.cash_out expects the button element it was clicked from.
function api.cash_out()
  -- Both have to be true. The state says the round is over; G.round_eval is
  -- the cash-out screen, and G.FUNCS.cash_out reads what to pay from it -- so
  -- calling before it exists moves the game to the shop and pays nothing.
  api.pump_until(function()
    return G.STATE == G.STATES.ROUND_EVAL and G.round_eval ~= nil
  end, 600)
  -- The screen existing is not the same as the screen being filled in. Its
  -- rows -- the blind's reward, the interest, the unspent hands -- are added
  -- by queued events, and the *total* only lands in current_round.dollars
  -- when the cash-out row among them runs. G.FUNCS.cash_out pays whatever
  -- that field holds at the moment it is pressed.
  --
  -- Pressing on `round_eval ~= nil` therefore paid zero, every round, for
  -- the whole life of this environment: owed read 0 before the rows arrived,
  -- so the guard below could not fire either, and the field was filled in
  -- immediately afterwards with the amount nobody received. A run reached
  -- its first shop on the deck's starting stake alone.
  --
  -- enter_round_eval clears the field before the rows are built, so this
  -- waits for a total this round produced rather than the last one's.
  api.pump_until(function()
    return (G.GAME.current_round.dollars or 0) > 0
  end, 600)
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

  -- Say so when the payout did not arrive. It used to pass quietly: the run
  -- carried on into a shop with the blind's reward simply not paid, and a
  -- policy trained on money got a reward that sometimes did not come. A
  -- reward that silently goes missing is worse than one that fails loudly.
  if owed > 0 and G.GAME.dollars == before then
    error('cash_out did not pay the ' .. tostring(owed) ..
          ' owed for the round', 0)
  end
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

  -- Counted before the tag is handed over, which is the game's own order in
  -- G.FUNCS.skip_blind and the reason a Speed Tag pays for its own skip.
  -- Nothing here incremented it, so Throwback -- X0.25 Mult per blind
  -- skipped -- read zero for the whole of every run in this environment.
  G.GAME.skips = (G.GAME.skips or 0) + 1
  for i = 1, #G.jokers.cards do
    G.jokers.cards[i]:calculate_joker({ skip_blind = true })
  end

  local tag = G.GAME.round_resets.blind_tags[on_deck]
  if tag then
    add_tag(Tag(tag))
  end

  -- The tags that pay the instant a blind is skipped: Economy doubles the
  -- bankroll, Handy and Garbage pay per hand and per unspent discard, Speed
  -- per skip, Top-up makes two jokers, Orbital levels a hand three times.
  -- The game fires this loop in skip_blind itself; only the new_blind_choice
  -- one below was copied, so all six did nothing at all here -- a skip
  -- handed over a tag that was then never applied.
  for i = 1, #G.GAME.tags do
    G.GAME.tags[i]:apply_to_run({ type = 'immediate' })
  end
  G.GAME.round_resets.blind_states[on_deck] = 'Skipped'
  G.GAME.round_resets.blind_states[on_deck == 'Small' and 'Big' or 'Boss'] = 'Select'
  G.GAME.blind_on_deck = on_deck == 'Small' and 'Big' or 'Boss'

  -- Skipping puts a new blind on the table, and some tags act on exactly
  -- that: a Boss Tag rerolls the boss the moment the next choice appears,
  -- then removes itself. The game fires this from the UI that builds the
  -- choice screen, which is not built here, so without it a Boss Tag is taken
  -- and then sits in the run for ever -- present in the tag list, never
  -- spent, and rerolling nothing.
  --
  -- This is the game's own apply_to_run, not a reimplementation of what the
  -- tag does; the loop is copied from the three places the game calls it.
  G.E_MANAGER:add_event(Event({
    blocking = false, trigger = 'after', delay = 0.5,
    func = function()
      for i = 1, #G.GAME.tags do
        if G.GAME.tags[i]:apply_to_run({ type = 'new_blind_choice' }) then
          break
        end
      end
      return true
    end
  }))

  api.pump(120)
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
    -- Whether the shop has its cards yet, not merely whether it is open.
    -- Its absence here is why the scripted runner carried its own wait for
    -- this; the shared phase logic asks every state the same question.
    shop_ready = api.shop_ready() and true or false,
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
--- Sort the hand, through bot_api so the once-per-hand bookkeeping happens.
---
--- This used to call G.FUNCS directly, which sorted correctly and left the
--- flag that says the button has been used unset -- so the engine offered the
--- sort forever while the real game offered it once. Two implementations of
--- one action is exactly the split that keeps producing these.
function api.sort_hand(by)
  require("bot_api").sort_hand({ by == 'suit' and 'suit' or 'rank' })
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

-- ---------------------------------------------------------------- shop
-- The shop's buttons pass the clicked UI element to their callback; all any of
-- them actually read is e.config, so a table with the right fields stands in.

local SHOP_AREAS = { 'shop_jokers', 'shop_booster', 'shop_vouchers' }

-- The shop always stocks its main row; empty means the cards have not landed.
--- Whether the shop has finished dealing -- not whether jokers remain.
---
--- Buying the last joker leaves a shop that still sells vouchers and packs and
--- can be rerolled or left. Reading "ready" as "the joker row has cards" meant
--- such a shop was never ready again, and a driver waiting on it hung.
function api.shop_ready()
  if G.STATE ~= G.STATES.SHOP then return false end
  for _, name in ipairs({ 'shop_jokers', 'shop_vouchers', 'shop_booster' }) do
    local area = G[name]
    if area and area.cards and #area.cards > 0 then return true end
  end
  return false
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
          -- A booster is opened rather than stored, so buy_space -- which
          -- asks whether there is a slot to put it in -- is the wrong
          -- question and always answered no. See the note in bot_api.lua.
          buyable = ((card.cost or 0) <= G.GAME.dollars)
                    and (card.config.center.set == 'Booster'
                         or buy_space(card)),
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
  return buy_space(card)
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

--- Buy a consumable and use it in one click -- the shop's second button.
---
--- Not a buy followed by a use. buy_from_shop with id 'buy_and_use' skips both
--- the space check and the emplace, so this works with the consumable slots
--- full, and the card never occupies a slot at all. The engine had no way to
--- express this at all, which meant a policy trained here could not take an
--- action the shop plainly offers.
function api.buy_and_use(area, index, card_indices)
  local card = api.shop_card(area, index)
  if not card then error('no shop card at ' .. tostring(area) .. '[' .. tostring(index) .. ']') end
  if not card.ability.consumeable then
    error('buy_and_use is for consumables; ' ..
          tostring(card.config.center.key) .. ' is not one')
  end
  if card_indices and #card_indices > 0 then
    api.highlight(card_indices)
  else
    api.clear_highlights()
  end
  local before = G.GAME.dollars
  G.FUNCS.buy_from_shop({ config = { ref_table = card, id = 'buy_and_use' } })
  api.pump_until(function()
    return G.GAME.dollars ~= before or card.area ~= G[area]
  end, 900)
  api.pump(60)
  return G.GAME.dollars
end

--- Whether the shop's buy-and-use button would be offered for this card. The
--- game's own gate: affordable, a consumable, and usable right now.
function api.can_buy_and_use(area, index)
  local card = api.shop_card(area, index)
  if not card or not card.ability.consumeable then return false end
  if (card.cost or 0) > G.GAME.dollars then return false end
  return card:can_use_consumeable() and true or false
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
--- Select or deselect a card, through bot_api so the per-hand tally happens.
---
--- Reimplementing it here is what made sort_hand disagree with the real game;
--- the same would happen to the toggle budget.
function api.toggle(index)
  local card = G.hand.cards[index]
  if not card then return false end
  if not api.is_highlighted(index)
      and #G.hand.highlighted >= (G.hand.config.highlighted_limit or 5) then
    return false
  end
  require("bot_api").toggle({ index })
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
--- Move a joker one place left, through bot_api so the per-set budget counts.
---
--- Reimplementing it here is what let sort_hand and toggle disagree with the
--- real game; the same would happen to this.
function api.swap_joker_left(index)
  if index <= 1 or not G.jokers.cards[index] then return false end
  require("bot_api").move_joker({ index, index - 1 })
  api.pump(30)
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

--- The observation lives in bot_api now, so that the engine and the real game
--- produce it with the same code. This used to be a second implementation of
--- nearly the same table, and the halves drifted where nobody compared them.
function api.env_state()
  return require("bot_api").state()
end

return api
