-- The game-facing half of the bridge, running inside the real Balatro.
--
-- `state` is the single observation for this project. The bridge polls it, the
-- replay compares it, and the learning environment encodes it -- one producer,
-- running unchanged in both the real game and the headless engine, so a policy
-- trained against the engine sees exactly what it will see when it drives the
-- real thing.
--
-- It used to be one of two: the engine had its own api.env_state() of nearly
-- the same shape. They drifted, quietly, in the places nobody compared -- the
-- per-card flags. env_state reported every consumable as usable, because the
-- expression was `(card.check_use and not card:check_use()) and 1 or 1`, whose
-- arms are both 1, and a policy trained on that spent Deaths on one selected
-- card for nothing.
--
-- The difference between the two runtimes is timing, not structure: in the
-- real game actions are asynchronous because it is animating them, so the
-- client polls this until the phase is actionable again rather than blocking.

local BotAPI = {}

local KEY_INDEX, KEY_ORDER
local TAG_INDEX, TAG_ORDER
local BLIND_INDEX, BLIND_ORDER

-- Editions and seals are a large part of what a card is worth -- polychrome is
-- x1.5 on the whole hand, negative is a whole extra joker slot, a gold seal is
-- $3 every time the card is played -- and neither was in the observation at
-- all. Ordered explicitly rather than by sorting, so the ids are stable if the
-- game ever adds one.
local EDITION_IDS = { foil = 1, holo = 2, polychrome = 3, negative = 4 }
local SEAL_IDS = { Gold = 1, Red = 2, Blue = 3, Purple = 4 }

-- Enhancements, in the order the client expects. Index 0 is a plain card.
local ENHANCEMENT_IDS = {
  c_base = 0, m_bonus = 1, m_mult = 2, m_wild = 3, m_glass = 4,
  m_steel = 5, m_stone = 6, m_gold = 7, m_lucky = 8,
}

--- can_use_consumeable, safe on a card the game has not updated yet.
---
--- The gate compares #G.hand.highlighted against ability.consumeable.mod_num,
--- and mod_num is not set when the card is built -- Card:update derives it
--- from max_highlighted, once a frame. So a card created this frame has none,
--- and the comparison is against nil. Reading the state of a freshly dealt
--- pack raised "attempt to compare number with nil" from inside the
--- observation, which is the one place that must never throw.
---
--- Derived here with the game's own formula rather than guarded around,
--- because the answer is not "unusable" -- it is the value the game is about
--- to fill in anyway.
local function can_use(card)
  local con = card.ability and card.ability.consumeable
  if con and con.max_highlighted and not con.mod_num then
    con.mod_num = math.min(5, con.max_highlighted)
  end

  -- The other things can_use_consumeable reads that Card:update computes.
  --
  -- Three cards are gated on a list of eligible jokers rebuilt every frame
  -- inside Card:update -- next to the sprite flipping and the focus handling.
  -- Skip those updates and can_use_consumeable calls next(nil) and takes the
  -- run down. Derived here instead, with the game's own condition, so the
  -- answer does not depend on how recently a frame ran.
  local name = card.ability and card.ability.name
  if name == 'The Wheel of Fortune' or name == 'Ectoplasm' or name == 'Hex' then
    local eligible = {}
    for _, joker in pairs(G.jokers.cards) do
      if joker.ability.set == 'Joker' and (not joker.edition) then
        eligible[#eligible + 1] = joker
      end
    end
    if name == 'The Wheel of Fortune' then
      card.eligible_strength_jokers = eligible
    else
      card.eligible_editionless_jokers = eligible
    end
  end

  return card:can_use_consumeable()
end

--- Whether a card on offer inside a pack can actually be taken.
---
--- The game's own rule, from G.FUNCS.can_select_card in
--- button_callbacks.lua:2112. That function does not refuse anything -- it
--- decides whether the card is given a `use_card` button at all, and when the
--- answer is no it sets the button to nil so there is nothing to click. Which
--- is why nothing inside G.FUNCS.use_card checks: in normal play the gate is
--- the button's existence, and use_card is unreachable without it.
---
--- BOT.pick_pack calls use_card directly, so the gate was never consulted.
--- The mod reported a joker as takeable with a full row and then took it,
--- and a policy played on into a run holding six jokers in five slots -- a
--- state the game cannot otherwise reach. Seen in play at ante four, from a
--- Mega Buffoon.
---
--- A consumable is used the instant it is taken, so it faces the use gate
--- instead of a room one. A playing card always goes to the deck. A joker
--- needs a free slot unless it is Negative, which takes none.
---
--- Module level, and deliberately: a helper defined inside `state()` below
--- its own use is nil when called, and booting the engine does not catch it
--- because nothing calls state() until something asks for an observation.
local function pack_takeable(card)
  if card.ability.consumeable then return can_use(card) end
  if card.ability.set ~= "Joker" then return true end
  if card.edition and card.edition.negative then return true end
  return #G.jokers.cards < G.jokers.config.card_limit
end

local function edition_id(card)
  local e = card.edition
  return (e and e.type and EDITION_IDS[e.type]) or 0
end

local function seal_id(card)
  return (card.seal and SEAL_IDS[card.seal]) or 0
end

local function key_index()
  if KEY_INDEX then return KEY_INDEX end
  KEY_INDEX, KEY_ORDER = {}, {}
  for key in pairs(G.P_CENTERS) do KEY_ORDER[#KEY_ORDER + 1] = key end
  table.sort(KEY_ORDER)
  for i, key in ipairs(KEY_ORDER) do KEY_INDEX[key] = i end
  return KEY_INDEX
end

local function key_id(key) return key and key_index()[key] or 0 end

local function tag_index()
  if TAG_INDEX then return TAG_INDEX end
  TAG_INDEX, TAG_ORDER = {}, {}
  for key in pairs(G.P_TAGS) do TAG_ORDER[#TAG_ORDER + 1] = key end
  table.sort(TAG_ORDER)
  for i, key in ipairs(TAG_ORDER) do TAG_INDEX[key] = i end
  return TAG_INDEX
end

local function tag_id(key) return (key and tag_index()[key]) or 0 end

-- The tags whose reward is a booster pack.
PACK_TAGS = {
  tag_charm = true,     -- Arcana
  tag_meteor = true,    -- Celestial
  tag_ethereal = true,  -- Spectral
  tag_standard = true,  -- Standard
  tag_buffoon = true,   -- Buffoon
}

--- Blinds have their own table too, G.P_BLINDS, so key_id cannot see them --
--- the same way it could not see tags. Asking it for a boss returned 0 for
--- every boss there is, which reads as "no boss" rather than as an error.
local function blind_index()
  if BLIND_INDEX then return BLIND_INDEX end
  BLIND_INDEX, BLIND_ORDER = {}, {}
  for key in pairs(G.P_BLINDS) do BLIND_ORDER[#BLIND_ORDER + 1] = key end
  table.sort(BLIND_ORDER)
  for i, key in ipairs(BLIND_ORDER) do BLIND_INDEX[key] = i end
  return BLIND_INDEX
end

local function blind_id(key) return (key and blind_index()[key]) or 0 end

--- The blind vocabulary, so the client can size a one-hot over it.
function BotAPI.blind_list()
  blind_index()
  return table.concat(BLIND_ORDER, ",")
end

--- The tag vocabulary, so the client can size a one-hot over it. Tags are in
--- G.P_TAGS, a different table from P_CENTERS -- which is why asking key_id
--- for a tag quietly returned 0 for every tag there is.
function BotAPI.tag_list()
  tag_index()
  return table.concat(TAG_ORDER, ",")
end

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
-- Where each scaling joker keeps the number that is actually its worth.
--
-- There is no rule to infer this from. `card.ability` carries every field the
-- game ever uses, defaulted -- a Seltzer reports mult=0 and x_mult=1 as well
-- as the extra=10 that is its real counter -- so anything that picked the
-- first plausible field would confidently read the wrong one and never say
-- so. The table is explicit, and anything relying on it should check it
-- against src/jimbot_sim/jokers.py rather than trust it.
--
-- A joker absent from the table has no counter and reports zero: most do not
-- scale, and Hiker, Matador, Triboulet and Bootstraps compute their effect
-- from elsewhere rather than accumulating it.
local COUNTER_FIELD = {
  j_caino              = { "caino_xmult" },
  j_selzer             = { "extra" },
  j_ice_cream          = { "extra.chips" },
  j_runner             = { "extra.chips" },
  j_castle             = { "extra.chips" },
  j_wee                = { "extra.chips" },
  j_square             = { "extra.chips" },
  j_rocket             = { "extra.dollars" },
  j_turtle_bean        = { "extra.h_size" },
  j_yorick             = { "extra.xmult", "extra.discards" },
  j_popcorn            = { "mult" },
  j_trousers           = { "mult" },
  j_ride_the_bus       = { "mult" },
  j_green_joker        = { "mult" },
  j_red_card           = { "mult" },
  j_ceremonial         = { "mult" },
  j_flash              = { "mult" },
  j_obelisk            = { "x_mult" },
  -- Steel Joker is deliberately absent. Its X Mult is recomputed from the
  -- steel cards in the deck rather than accumulated, so the engine's x_mult
  -- is a derived total where every other entry here is a stored counter.
  -- Reporting it would have the two backends disagree from the moment the
  -- joker is bought. What drives it is already visible in the deck block.
  j_campfire           = { "x_mult" },
  j_constellation      = { "x_mult" },
  j_madness            = { "x_mult" },
  j_hit_the_road       = { "x_mult" },
  j_glass              = { "x_mult" },
  j_lucky_cat          = { "x_mult" },
  j_vampire            = { "x_mult" },
  j_hologram           = { "x_mult" },
  j_ramen              = { "x_mult" },
}

local function ability_number(ability, path)
  if not path then return 0 end
  local head, tail = path:match("^([^.]+)%.(.+)$")
  if head then
    local nested = ability[head]
    if type(nested) ~= "table" then return 0 end
    return tonumber(nested[tail]) or 0
  end
  return tonumber(ability[path]) or 0
end

-- The three stickers, for a row that may hold a voucher or a pack rather than
-- a joker. Spelled out rather than omitted so every row has the same shape.
-- The shop is where they matter most: the stickers are rolled when the shop
-- stocks the joker, not when it is bought, and a rental is priced at $1
-- however expensive the joker -- so the shelf shows a bargain and says nothing
-- about the $3 a round behind it.
-- One of G.GAME.current_round's per-run card nominations, as the same id the
-- card rows use. The game stores them as words -- {suit = "Spades", rank =
-- "Ace"} -- and RANK_IDS and SUIT_IDS are already keyed that way, so the two
-- backends meet on the number rather than on the spelling.
local function idol_part(which, field, ids)
  local round = G.GAME and G.GAME.current_round
  local card = round and round[which]
  return (card and ids[card[field]]) or 0
end

local function stickers(card)
  local a = card.ability or {}
  return a.eternal and 1 or 0, a.perishable and 1 or 0, a.rental and 1 or 0
end

local function joker_counters(card)
  local fields = COUNTER_FIELD[card.config.center.key]
  if not fields then return 0, 0 end
  return ability_number(card.ability, fields[1]),
         ability_number(card.ability, fields[2])
end


local function state_name()
  if not STATE_NAMES then
    STATE_NAMES = {}
    for name, value in pairs(G.STATES) do STATE_NAMES[value] = name end
  end
  return STATE_NAMES[G.STATE] or ("STATE_" .. tostring(G.STATE))
end

local PACK_STATES
--- Whether a screen the client needs is available to act on.
---
--- Every readiness signal here is really "has the game built this UIBox yet",
--- because in the real game the callbacks are guarded on exactly that. The
--- headless engine never builds them and does not animate, so there is nothing
--- to wait for: the phase alone says whether an action is legal. Without this,
--- every wait in the client times out against the engine.
local function screen_up(box)
  return BOT_HEADLESS or box ~= nil
end

--- Which sort buttons have been used on the hand currently held.
---
--- Tracked here rather than in the client so that every client agrees without
--- keeping its own copy: the training environment and a policy driving the
--- real game ask the same question and must get the same answer.
---
--- Keyed on the hand's *contents*, not its order -- sorting permutes the cards
--- without changing which ones are held, so the key survives a sort and resets
--- on anything that redraws: playing, discarding, a Death, a pack's targets.
local SORT_STATE = { key = nil, rank = false, suit = false, toggles = 0 }

--- How much the jokers have been rearranged since the set last changed.
---
--- Joker order is scoring order, so rearranging is a real move -- but only
--- until they are arranged. Left ungated it was two thirds of every episode:
--- 333 swaps a run against 9 hands played. Keyed on which jokers are held, so
--- the budget refreshes exactly when the decision comes back, which is when
--- one is bought or sold.
local JOKER_STATE = { key = nil, swaps = 0 }

local function joker_state()
  local keys = {}
  for _, card in ipairs((G.jokers and G.jokers.cards) or {}) do
    keys[#keys + 1] = card.config.center.key
  end
  table.sort(keys)
  local key = table.concat(keys, ",")
  if key ~= JOKER_STATE.key then
    JOKER_STATE.key, JOKER_STATE.swaps = key, 0
  end
  return JOKER_STATE
end

local function sort_state()
  local ids = {}
  for _, card in ipairs((G.hand and G.hand.cards) or {}) do
    ids[#ids + 1] = card.sort_id
  end
  table.sort(ids)
  local key = table.concat(ids, ",")
  if key ~= SORT_STATE.key then
    SORT_STATE.key, SORT_STATE.rank, SORT_STATE.suit = key, false, false
    SORT_STATE.toggles = 0
  end
  return SORT_STATE
end

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
  if G.STATE ~= G.STATES.ROUND_EVAL then return false end
  if BOT_HEADLESS then
    -- No button to look for, so use what the button waits behind. Defeating a
    -- blind ends in set_blind(nil, nil, true), which clears its name and chip
    -- requirement; until that has run, the round is still finishing, and the
    -- engine would cash out a round the real game had not finished paying.
    -- Also wait for the evaluation itself. evaluate_round is what asks each
    -- tag whether it pays -- an Investment Tag's $25 is settled and the tag
    -- consumed there -- so comparing before it has run shows a tag the real
    -- game had already spent.
    return G.round_eval ~= nil
        and not (G.GAME.blind and (G.GAME.blind.name or "") ~= "")
  end
  if not G.round_eval then return false end
  for _, box in pairs((G.I and G.I.UIBOX) or {}) do
    -- Must belong to *this* round's evaluation. A button left in the registry
    -- by the previous round makes the scan succeed immediately, so cash_out
    -- fires before this round's rows have been added -- and any money still
    -- to come, such as an Investment Tag's $25, is simply never counted.
    if box.config and box.config.major == G.round_eval
        and box.get_UIE_by_ID and box:get_UIE_by_ID('cash_out_button') then
      return true
    end
  end
  return false
end

--- A card identity that is stable across runs of the same seed.
---
--- card.sort_id is a process-global counter, so a second run of the same seed
--- gives the same cards different ids -- offset by however many cards were
--- created before. The deck is built in a deterministic order per seed, so
--- subtracting the run's lowest deck id cancels that offset and yields the
--- same identity every time.
-- sort_id is a process-global counter, so the same card has a different id in
-- every run; subtracting the run's lowest id makes ids comparable across runs.
--
-- That lowest id has to be pinned when the run starts, not recomputed. Cards
-- are created and destroyed mid-run -- glass shattering, Immolate, DNA,
-- Familiar, a Death converting one card into another -- and if the card
-- holding the minimum is destroyed, a recomputed base jumps and *every* id
-- shifts with it, turning one destroyed card into a whole-deck divergence.
-- Cards created later get ids above the base, which is what we want: they read
-- as new cards, because they are.
local pinned_base = nil
local reset_ids   -- defined below, used by start_run

local function deck_base()
  if pinned_base then return pinned_base end
  local base = nil
  for _, card in ipairs(G.playing_cards or {}) do
    if not base or card.sort_id < base then base = card.sort_id end
  end
  -- Pin only once the run is real. start_run queues the old run's teardown, so
  -- for a few frames G.playing_cards still holds the *previous* deck; pinning
  -- against that offsets every id in the new run.
  if base and not BOT_RUN_PENDING and G.STAGE == G.STAGES.RUN then
    pinned_base = base
  end
  return base or 0
end

local function card_uid(card, base)
  return card.sort_id - (base or deck_base())
end

BotAPI.card_uid = card_uid

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

  -- Window mode has to be set here rather than pre-boot: Game:start_up loads
  -- settings.jkr from the profile, which overwrites anything set before it.
  -- apply_window_changes reads QUEUED_CHANGE for the sizing branch, so both
  -- fields are set, and it is applied live.
  local mode = os.getenv("BALATRO_BOT_SCREENMODE") or "Windowed"
  if mode ~= "Windowed" and mode ~= "Fullscreen" and mode ~= "Borderless" then
    mode = "Windowed"
  end
  if G.SETTINGS.WINDOW and G.SETTINGS.WINDOW.screenmode ~= mode then
    G.SETTINGS.WINDOW.screenmode = mode
    G.SETTINGS.QUEUED_CHANGE = G.SETTINGS.QUEUED_CHANGE or {}
    G.SETTINGS.QUEUED_CHANGE.screenmode = mode
    if G.FUNCS.apply_window_changes then
      pcall(G.FUNCS.apply_window_changes)
    end
    -- Persist it, so the next launch starts in this mode from the first frame
    -- rather than flipping after boot.
    pcall(function() G:save_settings() end)
  end
  changed.screenmode = mode

  -- Music off. Here rather than pre-boot for the same reason as the window
  -- mode: Game:start_up loads settings.jkr and replaces G.SETTINGS wholesale,
  -- so anything written before it is discarded -- which is why setting it
  -- early looked right in the log and still played.
  --
  -- The mixer reads G.SETTINGS.SOUND live every frame (G.ARGS.play_sound
  -- .sound_settings is a reference to it), so changing the value is enough;
  -- nothing needs to be restarted. Game sounds are left alone: they mark what
  -- the agent is doing, which is useful to hear.
  --
  -- Override without rebuilding: BALATRO_BOT_MUSIC=on in the environment.
  G.SETTINGS.SOUND = G.SETTINGS.SOUND or {}
  if os.getenv("BALATRO_BOT_MUSIC") == "on" then
    changed.music = G.SETTINGS.SOUND.music_volume
  else
    G.SETTINGS.SOUND.music_volume = 0
    changed.music = 0
  end

  -- The tutorial forces specific shop items, a specific voucher and specific
  -- tags (see G.FUNCS.start_tutorial), so leaving it on quietly corrupts runs.
  -- G.F_SKIP_TUTORIAL is the game's own switch; tutorial_controller acts on it.
  G.F_SKIP_TUTORIAL = true
  if not G.SETTINGS.tutorial_complete then
    G.SETTINGS.tutorial_complete = true
    G.SETTINGS.tutorial_progress = nil
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

--- Forget the pinned base, so the next run pins its own.
function reset_ids()
  pinned_base = nil
end

local function selected_indices()
  local out = {}
  for i = 1, #G.hand.cards do
    if is_highlighted(i) then out[#out + 1] = i end
  end
  return out
end

--- The sort_ids of the selected cards, so a replay can pick the same cards
--- even if the hand is in a different order.
local function selected_ids()
  local out, base = {}, deck_base()
  for i = 1, #G.hand.cards do
    if is_highlighted(i) then
      out[#out + 1] = card_uid(G.hand.cards[i], base)
    end
  end
  return out
end

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
    -- How many cards may be selected at once. Not always five: a Serpent-style
    -- hand or a joker can change it, so it cannot be a constant on the client.
    highlight_limit = in_run and (G.hand.config.highlighted_limit or 5) or 0,
    -- Each sort button is worth one press per hand: pressing it again does
    -- nothing, and an agent with nothing better to do will press it forever.
    sorted_rank = (in_run and sort_state().rank) and 1 or 0,
    sorted_suit = (in_run and sort_state().suit) and 1 or 0,
    -- How much picking has been done at this hand. Selecting five of eight
    -- takes five; an agent with nothing better to do takes hundreds, which is
    -- how episodes reached 636 steps while achieving nothing.
    toggles_used = (in_run and sort_state().toggles) or 0,
    joker_swaps_used = (in_run and joker_state().swaps) or 0,
    boss = (blind and blind.boss) and 1 or 0,
    skippable = (in_run and blind_on_deck() ~= "Boss") and 1 or 0,
    -- The tag on offer for the blind on deck. Skipping is a trade -- no money
    -- and no chips, in exchange for this -- so it has to be visible before the
    -- decision, not after.
    -- The run info screen's blind list: what each blind of this ante asks for,
    -- what it pays, whether it is still to come -- and which boss it is.
    --
    -- A player checks this constantly, because the boss decides what to build
    -- for: The Wall wants twice the score, The Needle gives one hand, The
    -- Water takes the discards. Knowing it while there is still a shop to
    -- spend in is most of preparing for it, and none of it was visible.
    blinds = (function()
      local out = {}
      if not in_run then return out end
      local resets = G.GAME.round_resets
      for i, kind in ipairs({ "Small", "Big", "Boss" }) do
        local key = resets.blind_choices and resets.blind_choices[kind]
        local proto = key and G.P_BLINDS[key]
        local state = (resets.blind_states and resets.blind_states[kind]) or ""
        out[i] = {
          kind = kind,
          blind = blind_id(key),
          -- The requirement, in chips, as the run info screen states it.
          chips = (proto and math.floor(
            get_blind_amount(resets.ante) * (proto.mult or 1)
            * G.GAME.starting_params.ante_scaling)) or 0,
          reward = (proto and proto.dollars) or 0,
          -- Upcoming, current, defeated, skipped or hidden.
          defeated = (state == "Defeated") and 1 or 0,
          skipped = (state == "Skipped") and 1 or 0,
          current = (state == "Current") and 1 or 0,
        }
      end
      return out
    end)(),
    offered_tag = in_run and tag_id(
      G.GAME.round_resets.blind_tags[blind_on_deck()]) or 0,
    -- Whether skipping would hand over a tag that opens a booster pack.
    -- Those five build their pack from a card belonging to no area, and the
    -- headless engine opens it empty -- so a policy that skips for one gets a
    -- pack with nothing in it, which is not a game anybody plays. Named by
    -- the game's own keys rather than inferred from the tag's config, since
    -- every one of them shares the same trigger type as tags that are fine.
    offered_tag_opens_pack = (in_run and PACK_TAGS[
      G.GAME.round_resets.blind_tags[blind_on_deck()] or ""] and 1) or 0,
    -- "The shop has dealt", not "the joker row is non-empty". Buying the
    -- last joker leaves a shop that is still perfectly usable -- vouchers,
    -- packs, rerolling, leaving -- but under the old reading it was never
    -- ready again, so a driver waiting for it waited forever.
    shop_ready = (G.STATE == G.STATES.SHOP and (
                    (G.shop_jokers and #G.shop_jokers.cards > 0) or
                    (G.shop_vouchers and #G.shop_vouchers.cards > 0) or
                    (G.shop_booster and #G.shop_booster.cards > 0))) and 1 or 0,
    stop_use = in_run and (G.GAME.STOP_USE or 0) or 0,
    -- The biggest single hand scored so far this run. The game keeps it as a
    -- high score -- check_and_set_high_score only ever raises it -- and clears
    -- it when a run starts, so it is a per-run measure of how strong the build
    -- has become, which nothing else in the state says.
    best_hand = (in_run and G.GAME.round_scores and G.GAME.round_scores.hand
                 and math.floor(G.GAME.round_scores.hand.amt or 0)) or 0,
    -- A bought playing card lands in neither tray -- the Magic Trick voucher
    -- puts them in the shop and buy_from_shop sends them to the deck -- so
    -- this is the only place its arrival is visible.
    deck_size = (in_run and G.playing_cards and #G.playing_cards) or 0,
    -- What the whole deck is made of, which the game shows on its deck view:
    -- ranks, suits, enhancements, seals and editions across every card owned,
    -- not just the eight in hand.
    --
    -- Without it the agent cannot tell whether it is building something -- six
    -- steel kings, a suit it has been fixing toward -- or just holding
    -- fifty-two cards. Deck fixing is most of strong play and every signal for
    -- it was invisible.
    deck_cards = (function()
      local ranks, suits = {}, {}
      local enhancements, seals, editions = {}, {}, {}
      local extra_total, extra_any, extra_cards = 0, 0, 0
      for i = 1, 13 do ranks[i] = 0 end
      for i = 1, 4 do suits[i] = 0 end
      for i = 0, 8 do enhancements[i + 1] = 0 end
      for i = 0, 4 do seals[i + 1] = 0 end
      for i = 0, 4 do editions[i + 1] = 0 end
      if in_run then
        for _, card in ipairs(G.playing_cards or {}) do
          local rank = card.base and RANK_IDS[card.base.value]
          local suit = card.base and SUIT_IDS[card.base.suit]
          if rank then ranks[rank] = ranks[rank] + 1 end
          if suit then suits[suit] = suits[suit] + 1 end
          local key = card.config.center.key
          local slot = ENHANCEMENT_IDS[key] or 0
          enhancements[slot + 1] = enhancements[slot + 1] + 1
          local seal = seal_id(card)
          seals[seal + 1] = seals[seal + 1] + 1
          local edition = edition_id(card)
          editions[edition + 1] = editions[edition + 1] + 1
          local perma = (card.ability and card.ability.perma_bonus) or 0
          extra_total = extra_total + perma
          if perma > 0 then extra_any = extra_any + 1 end
          extra_cards = extra_cards + 1
        end
      end
      return { ranks = ranks, suits = suits, enhancements = enhancements,
               seals = seals, editions = editions,
               -- How far the pumping has got, and how much of the deck it
               -- has reached. The two come apart badly when one card has
               -- been hit twenty times and the rest not at all.
               extra_chips_mean = extra_cards > 0
                 and (extra_total / extra_cards) or 0,
               extra_chips_share = extra_cards > 0
                 and (extra_any / extra_cards) or 0 }
    end)(),
    -- Redeemed vouchers. A voucher joins no tray and does not touch the deck,
    -- so this is the only visible consequence of buying one. The real game
    -- happened to be caught by state_name instead -- use_card flips the state
    -- to PLAY_TAROT while it redeems -- but the engine completes the whole
    -- action before anything can look, so that transient is never seen.
    vouchers = (function()
      local out = {}
      if not in_run then return out end
      for key in pairs(G.GAME.used_vouchers or {}) do out[#out + 1] = key end
      -- Sorted so the list is the same on both sides: `pairs` has no order
      -- and the simulator sorts its own.
      table.sort(out)
      return out
    end)(),
    -- The four values the run holds on behalf of a joker: The Idol scores a
    -- rank and a suit re-rolled every ante, Ancient Joker a suit, Mail-In
    -- Rebate a rank every round, Castle a suit. Without these, holding any of
    -- the four is holding a joker whose effect cannot be known. Zero means
    -- not rolled yet, which is a real state before the first ante.
    idol_rank = in_run and idol_part("idol_card", "rank", RANK_IDS) or 0,
    idol_suit = in_run and idol_part("idol_card", "suit", SUIT_IDS) or 0,
    ancient_suit = in_run and idol_part("ancient_card", "suit", SUIT_IDS) or 0,
    mail_rank = in_run and idol_part("mail_card", "rank", RANK_IDS) or 0,
    castle_suit = in_run and idol_part("castle_card", "suit", SUIT_IDS) or 0,
    -- Which stake and deck the run is on. Stake decides whether jokers can
    -- come out eternal, perishable or rental, so it changes what is legal to
    -- do with them.
    stake = in_run and (G.GAME.stake or 1) or 0,
    deck = (in_run and G.GAME.selected_back and G.GAME.selected_back.name)
           or "",
    -- Why the last deferred use gave up, if it did. The use happens inside an
    -- event, long after the command was answered, so this is the only channel
    -- back to the client.
    last_refusal = BOT_LAST_REFUSAL or "",
    -- Which positions are highlighted, so the client can confirm a selection
    -- took rather than assume it did.
    selected = in_run and selected_indices() or {},
    -- What the selected cards make, exactly as the game shows a player while
    -- they are choosing: the hand's name, its level, and the chips and mult it
    -- would score before jokers. This is on screen in the real game; an agent
    -- without it is choosing five cards blindfolded to what they add up to.
    selected_hand = (function()
      if not in_run or #G.hand.highlighted == 0 then
        return { name = "", level = 0, chips = 0, mult = 0, cards = 0,
                 estimate = 0 }
      end
      local cards = {}
      for _, card in ipairs(G.hand.highlighted) do cards[#cards + 1] = card end
      local ok, text, _, _, scoring = pcall(G.FUNCS.get_poker_hand_info, cards)
      if not ok or not text then
        return { name = "", level = 0, chips = 0, mult = 0, cards = 0,
                 estimate = 0 }
      end
      local level = G.GAME.hands[text]
      local base_chips = (level and level.chips) or 0
      local mult = (level and level.mult) or 0
      -- The cards' own chips, which only the scoring ones contribute: two pair
      -- played with a fifth card scores four of the five.
      local card_chips = 0
      for _, card in ipairs(scoring or {}) do
        card_chips = card_chips + ((card.base and card.base.nominal) or 0)
      end
      return {
        name = text,
        level = (level and level.level) or 0,
        chips = base_chips + card_chips,
        mult = mult,
        cards = #(scoring or {}),
      }
    end)(),
    -- How many cards the hand holds once dealing finishes. An Arcana or
    -- Spectral pack deals a hand to target and it arrives over several frames;
    -- acting on a partial hand targets the wrong cards.
    hand_limit = in_run and (G.hand.config.card_limit or 0) or 0,
    -- The game's own "not now": the first guard in can_use_consumeable. A
    -- consumable used while the previous one is still resolving is refused,
    -- and the redraw clears the highlight, so wait on this before selecting.
    busy = ((in_run and not BOT_HEADLESS
                    and ((G.play and #G.play.cards > 0)
                         or G.CONTROLLER.locked
                         or (G.GAME.STOP_USE and G.GAME.STOP_USE > 0)
                         -- can_use_consumeable's *other* guard: these three
                         -- states mean a hand or a tarot is mid-resolution, so
                         -- the next consumable is refused until it finishes.
                         or G.STATE == G.STATES.PLAY_TAROT
                         or G.STATE == G.STATES.HAND_PLAYED
                         or G.STATE == G.STATES.DRAW_TO_HAND))
            and 1 or 0),
    blind_on_deck = in_run and blind_on_deck() or "",
    run_pending = BOT_RUN_PENDING and 1 or 0,
    -- The Investment Tag pays only when the blind just set up was a boss
    -- (tag.lua checks G.GAME.last_blind.boss during round evaluation).
    last_blind_boss = (in_run and G.GAME.last_blind
                       and G.GAME.last_blind.boss) and 1 or 0,
    tags = (function()
      local out = {}
      for i, tag in ipairs((in_run and G.GAME.tags) or {}) do out[i] = tag.key end
      return out
    end)(),
    -- The client uses this to know whether it may act at all: mid-animation
    -- the game is in a transient state and inputs are ignored.
    -- G.FUNCS.select_blind is guarded by `if G.blind_select then`, so calling
    -- it before that UIBox exists silently does nothing. The blind select
    -- phase is only actionable once the screen is actually up.
    -- Each of these callbacks is guarded by `if <uibox> then`, so calling one
    -- before its screen is up silently does nothing.
    blind_select_up = screen_up(G.blind_select) and 1 or 0,
    shop_up = screen_up(G.shop) and 1 or 0,
    -- Diagnostic: alert_no_space sets this lock and shows "No space!" over the
    -- joker area. A state read must never raise it -- if this is ever 1 after
    -- polling, a read-only query has side effects again.
    no_space_lock = (G.CONTROLLER and G.CONTROLLER.locks
                     and G.CONTROLLER.locks.no_space) and 1 or 0,
    -- update_shop queues an event that waits for the shop to finish sliding in
    -- (`math.abs(G.shop.T.y - G.shop.VT.y) < 3`). Leaving before that event
    -- runs removes G.shop out from under it and crashes the game -- reachable
    -- only because a bot can act faster than a player physically can.
    shop_settled = (BOT_HEADLESS or (G.shop ~= nil and G.shop.T and G.shop.VT
                    and math.abs(G.shop.T.y - G.shop.VT.y) < 3)) and 1 or 0,
    -- The shop's UIBox finishes animating before its cards are dealt, so
    -- shop_settled alone hands the client an empty shop -- and a buy against
    -- an index that does not exist yet does nothing. The main row always
    -- stocks, so its contents are the real signal.
    shop_stocked = (G.shop_jokers ~= nil and G.shop_jokers.cards ~= nil
                    and #G.shop_jokers.cards > 0) and 1 or 0,
    round_eval_up = screen_up(G.round_eval) and 1 or 0,
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
              or (G.STATE == G.STATES.BLIND_SELECT and screen_up(G.blind_select))
              -- Deliberately not requiring shop_stocked: a row bought out is
              -- legitimately empty, and the shop is still perfectly usable
              -- (reroll, leave). Stockedness is a precondition for buying, not
              -- for the shop being actionable.
              or (G.STATE == G.STATES.SHOP and (BOT_HEADLESS
                  or (G.shop ~= nil and G.shop.T and G.shop.VT
                      and math.abs(G.shop.T.y - G.shop.VT.y) < 3)))
              or (G.STATE == G.STATES.ROUND_EVAL and BotAPI.cash_out_ready())
              or G.STATE == G.STATES.GAME_OVER
              or in_pack())) and 1 or 0,
  }
  if not in_run then return state end

  state.hand = {}
  local base = deck_base()
  for i, card in ipairs(G.hand.cards) do
    state.hand[i] = {
      -- Identity rather than position, so a recording survives the player
      -- reordering their hand. Normalised against the run's deck so it is the
      -- same value in a replay of the same seed.
      id = card_uid(card, base),
      rank = (card.base and RANK_IDS[card.base.value]) or 0,
      suit = (card.base and SUIT_IDS[card.base.suit]) or 0,
      center = key_id(card.config.center.key),
      chips = (card.base and card.base.nominal) or 0,
      -- What Hiker and friends have added to this card for good. Beside the
      -- nominal, not folded into it: reporting the bonus *instead* of the
      -- nominal is exactly the bug the nominal was introduced to fix, and
      -- swapping which of the two is visible is not showing both.
      extra_chips = (card.ability and card.ability.perma_bonus) or 0,
      highlighted = is_highlighted(i) and 1 or 0,
      debuffed = card.debuff and 1 or 0,
      edition = edition_id(card),
      seal = seal_id(card),
    }
  end

  state.jokers = {}
  for i, card in ipairs(G.jokers.cards) do
    local counter, secondary = joker_counters(card)
    state.jokers[i] = {
      id = card_uid(card, base),
      center = key_id(card.config.center.key),
      sellable = card:can_sell_card() and 1 or 0,
      sell_cost = card.sell_cost or 0,
      rarity = card.config.center.rarity or 0,
      -- Negative is worth seeing for a reason beyond scoring: it is an extra
      -- joker slot, so it changes how many jokers can be held at all.
      edition = edition_id(card),
      -- Stickers from the higher stakes. They change what is legal or wise:
      -- eternal cannot be sold or destroyed, perishable expires after five
      -- rounds, rental charges $3 every round.
      eternal = card.ability.eternal and 1 or 0,
      perishable = card.ability.perishable and 1 or 0,
      perish_tally = card.ability.perish_tally or 0,
      rental = card.ability.rental and 1 or 0,
      -- Switched off -- by a boss, or by a perishable running out. It scores
      -- nothing while still sitting in the row looking healthy, so without
      -- this the observation says the board is stronger than it is.
      debuffed = card.debuff and 1 or 0,
      -- What it has grown to, and the second number the few that keep two
      -- use. See COUNTER_FIELD.
      counter = counter,
      secondary = secondary,
      -- Hands played since it joined the row: what the jokers that count
      -- hands measure from, which is not the start of the run.
      hands_held = (G.GAME.hands_played or 0)
        - (card.ability.hands_played_at_create or 0),
    }
  end

  state.consumables = {}
  for i, card in ipairs(G.consumeables.cards) do
    state.consumables[i] = {
      center = key_id(card.config.center.key),
      set = SET_IDS[card.config.center.set] or 0,
      sellable = card:can_sell_card() and 1 or 0,
      edition = edition_id(card),
      -- The game's own gate. A Death with one card selected is not usable,
      -- and using it anyway spends it for nothing -- or, in the real game,
      -- crashes on highlighted[2].
      usable = can_use(card) and 1 or 0,
    }
  end

  state.shop = {}
  for _, name in ipairs({ 'shop_jokers', 'shop_vouchers', 'shop_booster' }) do
    local area = G[name]
    if area and area.cards then
      for i, card in ipairs(area.cards) do
        local eternal, perishable, rental = stickers(card)
        state.shop[#state.shop + 1] = {
          area = name, index = i,
          eternal = eternal, perishable = perishable, rental = rental,
          center = key_id(card.config.center.key),
          set = SET_IDS[card.config.center.set] or 0,
          cost = card.cost or 0,
          edition = edition_id(card),
          seal = seal_id(card),
          -- check_for_buy_space answers for a card that is about to be
          -- *stored*: a joker needs a joker slot, a consumable a consumable
          -- one. A booster pack is neither. It is opened, and what comes out
          -- of it is what needs room -- which is checked when a card is
          -- picked, not when the pack is bought.
          --
          -- Asking it about a pack therefore returned false for every pack
          -- in every shop, so `buyable` was zero, so the mask never offered
          -- the buy, so no policy trained here has ever opened one. Packs
          -- are most of where jokers and planets come from.
          buyable = ((card.cost or 0) <= G.GAME.dollars
                     and (card.config.center.set == 'Booster'
                          or buy_space(card))) and 1 or 0,
          -- The shop's second button. Legal where plain buying is not: the
          -- card is used rather than stored, so it needs no free slot.
          buy_and_usable = (card.ability.consumeable
                            and (card.cost or 0) <= G.GAME.dollars
                            and can_use(card)) and 1 or 0,
        }
      end
    end
  end

  state.pack = {}
  if G.pack_cards and G.pack_cards.cards then
    for i, card in ipairs(G.pack_cards.cards) do
      local eternal, perishable, rental = stickers(card)
      state.pack[i] = { center = key_id(card.config.center.key),
                        set = SET_IDS[card.config.center.set] or 0,
                        edition = edition_id(card),
                        seal = seal_id(card),
                        eternal = eternal, perishable = perishable,
                        rental = rental,
                        -- "Anything else is always takeable" was wrong: a
                        -- joker needs a free slot. See pack_takeable.
                        usable = pack_takeable(card) and 1 or 0 }
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
  -- The hand in order. Card order is player-visible and player-controlled
  -- (dragging, and the sort buttons), and it affects which card scores first,
  -- so a replay that ignored it would not be reproducing the same game.
  -- Tags held. Without these, a tag that failed to apply is invisible until
  -- its payout goes missing much later -- an Investment Tag not granted shows
  -- up as $25 unaccounted for, 29 actions downstream.
  local tags = {}
  for i, tag in ipairs(G.GAME.tags or {}) do tags[i] = tag.key end

  local hand_ids = {}
  local base = deck_base()
  for i, card in ipairs(G.hand.cards) do hand_ids[i] = card_uid(card, base) end

  -- Joker order is not decoration: effects resolve left to right, so XMult
  -- after +Mult scores differently from the reverse. Dragging jokers is a
  -- real move and has to be reproduced, not just noticed.
  local joker_ids = {}
  for i, card in ipairs(G.jokers.cards) do
    joker_ids[i] = card_uid(card, base)
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
    -- How many cards the hand should hold once dealing finishes. An Arcana or
    -- Spectral pack deals a hand to target, and it arrives over several
    -- frames; acting on a partial hand targets the wrong cards.
    hand_limit = G.hand.config.card_limit or 0,
    -- What is highlighted right now, so the client can confirm a selection
    -- took rather than assume it did.
    selected = selected_indices(),
    selected_ids = selected_ids(),
    -- The game's own "not now" signal, the first guard in can_use_consumeable
    -- and can_sell_card. A consumable used while the previous one is still
    -- resolving is refused, so this is what to wait on between them.
    busy = (((G.play and #G.play.cards > 0)
             or G.CONTROLLER.locked
             or (G.GAME.STOP_USE and G.GAME.STOP_USE > 0)) and 1 or 0),
    jokers = jokers,
    consumables = consumables,
    hand_levels = levels,
    deck_size = G.playing_cards and #G.playing_cards or 0,
    hand_ids = hand_ids,
    tags = tags,
    joker_ids = joker_ids,
    skips = G.GAME.skips or 0,
  }
end

BotAPI.fingerprint = fingerprint

local function note(action, params, before)
  if not recording then return end
  recording[#recording + 1] = {
    n = #recording + 1,
    action = action,
    params = params or {},
    -- State *before* the action; the replay applies the action and then
    -- compares against the next entry's before-state. Taken by the caller
    -- when the action is one that only gets written down once it is known to
    -- have happened -- by then the state has moved on.
    before = before or fingerprint(),
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
      if not recording then return original(e, ...) end

      -- The extra arguments matter: the game calls some of these functions
      -- itself and says so in them. Passing only `e` hides that.
      local ok, params = pcall(capture, e, ...)
      -- A capture returning false declines: the click is already covered by
      -- another entry, and recording it twice would make the replay do it
      -- twice.
      local wanted = (not ok) or params ~= false
      -- The state has to be read now, before the action changes it, even
      -- though the entry may not be written until afterwards.
      local before = wanted and fingerprint() or nil

      local result = original(e, ...)

      -- The game refuses some clicks and says so by returning false --
      -- buy_from_shop does it when there is no room for the card. The button
      -- was pressed, so the hook fires, but nothing happened: no money left
      -- the bankroll and no card moved. Writing that down would make a replay
      -- wait for a consequence that never comes, which is exactly how a
      -- recording of five jokers and a refused sixth used to stall.
      if wanted and result ~= false then
        note(name, ok and params or {}, before)
      end
      return result
    end
  end

  wrap('select_blind', function() return { blind = blind_on_deck() } end)
  wrap('skip_blind', function() return { blind = blind_on_deck() } end)
  wrap('play_cards_from_highlighted', function()
    return { cards = selected_indices(), card_ids = selected_ids() } end)
  -- The Hook discards two random cards after every hand played, and it does
  -- so by calling this function with hook = true. That is the game acting,
  -- not the player: no discard button exists while a hand is resolving, and
  -- the discard does not cost the player one of theirs.
  --
  -- Recording it would be worse than noise. Replaying it discards two *more*,
  -- on top of the two the engine takes unprompted, and every hand after that
  -- comes off a different deck.
  wrap('discard_cards_from_highlighted', function(e, hook)
    if hook then return false end
    return { cards = selected_indices(), card_ids = selected_ids() } end)
  -- The sort buttons reorder the hand, which changes what every later index
  -- means and which card scores first.
  wrap('sort_hand_value', function() return { by = "rank" } end)
  wrap('sort_hand_suit', function() return { by = "suit" } end)
  -- The shop's second button on a consumable buys and uses it in one click.
  -- The game routes it through buy_from_shop with id 'buy_and_use', and
  -- buy_from_shop then calls use_card itself -- so the click is one action,
  -- and recording the inner use_card as well would replay it as two.
  wrap('buy_from_shop', function(e)
    local area, index = locate(e.config.ref_table)
    return { area = area, index = index,
             buy_and_use = (e.config.id == 'buy_and_use') and 1 or 0,
             key = e.config.ref_table.config.center.key } end)
  wrap('sell_card', function(e)
    local area, index = locate(e.config.ref_table)
    return { area = area, index = index,
             key = e.config.ref_table.config.center.key } end)
  wrap('use_card', function(e)
    -- buy_from_shop passes its own element straight through, so the inner use
    -- still carries the id. Skip it: the buy_from_shop entry covers both, and
    -- by now the card has left the shop, so there is no area to record anyway.
    if e.config and e.config.id == 'buy_and_use' then return false end
    local area, index = locate(e.config.ref_table)
    return { area = area, index = index,
             key = e.config.ref_table.config.center.key,
             targets = selected_indices() } end)
  wrap('reroll_shop', function() return {} end)
  wrap('toggle_shop', function() return {} end)
  wrap('cash_out', function() return {} end)
  wrap('skip_booster', function() return {} end)
  -- Rerolling the boss blind. It is a button like any other and it costs ten
  -- dollars, and it was not being recorded at all: a recording where the
  -- player pressed it diverges from that point on, in the engine's own replay
  -- as much as in the simulator's, because the ten dollars and the new boss
  -- appear from nowhere. Director's Cut allows one a shop, Retcon any number.
  wrap('reroll_boss', function()
    -- A Boss Tag rerolls through this same function, and that is the game
    -- acting rather than the player. It says so with G.from_boss_tag, so
    -- decline: the tag is already in the recording and replaying both would
    -- reroll twice and charge for one of them.
    if G.from_boss_tag then return false end
    return { boss = G.GAME.round_resets.blind_choices
                    and G.GAME.round_resets.blind_choices.Boss or "?" }
  end)
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
  -- Stake 1-8 (White, Red, Green, Black, Blue, Purple, Orange, Gold). Higher
  -- stakes add the modifiers worth testing against: eternal, perishable,
  -- rental jokers.
  local stake = args and args[3] and tonumber(args[3]) or nil

  -- Card ids are normalised against this run's lowest sort_id; forget the
  -- previous run's.
  reset_ids()

  -- G.FUNCS.start_run queues delete_run and start_run as events, so when this
  -- returns the previous run is still on screen. A client that acts on what it
  -- sees now is acting on the old run: the stale blind-select box is still
  -- there, and select_blind against it silently does nothing.
  --
  -- Mark the restart as pending and clear it from an event queued behind the
  -- game's own, so `run_pending` tells the client when the new run is real.
  BOT_RUN_PENDING = true
  G.FUNCS.start_run(nil, { seed = seed, stake = stake })
  G.E_MANAGER:add_event(Event({
    trigger = 'immediate', no_delete = true,
    func = function() BOT_RUN_PENDING = false; return true end,
  }))
  return { started = true, seed = seed or "random", deck = deck or "Red Deck",
           stake = stake or 1 }
end

function BotAPI.select_blind()
  -- The real UI exists here, so the game's own callback works as clicked.
  G.FUNCS.select_blind({ config = {
    ref_table = G.P_BLINDS[G.GAME.round_resets.blind_choices[blind_on_deck()]] } })
  return { selected = blind_on_deck() }
end

function BotAPI.reroll_boss()
  -- The Director's Cut / Retcon button. Ten dollars and a new boss.
  --
  -- Gated on the state rather than on G.blind_select. The headless engine
  -- replaces the blind select screen with its own entry function and never
  -- builds that UIBox, so testing for it refuses a press the game would
  -- allow -- the same trap as the booster pack that would not deal until it
  -- had slid into view.
  if G.STATE ~= G.STATES.BLIND_SELECT then
    error("cannot reroll the boss: not on the blind select screen (state "
          .. tostring(G.STATE) .. ")", 0)
  end
  local before = G.GAME.round_resets.blind_choices
                 and G.GAME.round_resets.blind_choices.Boss
  G.FUNCS.reroll_boss({})
  return { was = before,
           now = G.GAME.round_resets.blind_choices
                 and G.GAME.round_resets.blind_choices.Boss }
end

function BotAPI.skip_blind()
  local on_deck = blind_on_deck()

  -- G.FUNCS.skip_blind does everything that matters -- granting the tag and
  -- advancing the blind states -- inside `if _tag then`, where _tag comes from
  -- e.UIBox:get_UIE_by_ID('tag_container'). Hand it anything without that
  -- element and it silently skips the blind without giving the tag, which
  -- shows up much later as missing money.
  local element = G.blind_select_opts and G.blind_select_opts[on_deck:lower()]
  if not element or not element.get_UIE_by_ID
      or not element:get_UIE_by_ID('tag_container') then
    error("cannot skip: no tag_container for the " .. on_deck ..
          " blind (is the blind select screen up?)", 0)
  end

  local before = #(G.GAME.tags or {})
  G.FUNCS.skip_blind({ config = { ref_table = { blind = on_deck } },
                       UIBox = element })
  G.CONTROLLER.locks.skip_blind = nil
  return { skipped = on_deck, tags_before = before }
end

function BotAPI.toggle(args)
  local index = tonumber(args[1])
  local card = G.hand.cards[index]
  if not card then return { ok = false, reason = "no card at " .. tostring(index) } end
  local state = sort_state()
  if is_highlighted(index) then
    G.hand:remove_from_highlighted(card)
  else
    G.hand:add_to_highlighted(card)
  end
  state.toggles = state.toggles + 1
  return { selection = #G.hand.highlighted, toggles = state.toggles }
end

--- Select cards by identity rather than position.
function BotAPI.toggle_id(args)
  local want = tonumber(args[1])
  local base = deck_base()
  for i, card in ipairs(G.hand.cards) do
    if card_uid(card, base) == want then return BotAPI.toggle({ i }) end
  end
  error("no card with id " .. tostring(want) .. " in hand", 0)
end

--- The game's own sort buttons.
function BotAPI.sort_hand(args)
  local state = sort_state()
  if args and args[1] == "suit" then
    G.FUNCS.sort_hand_suit({ config = {} })
    state.suit = true
  else
    G.FUNCS.sort_hand_value({ config = {} })
    state.rank = true
  end
  return { sorted = (args and args[1]) or "rank" }
end

--- Put the hand into an explicit order, given as sort_ids.
---
--- Dragging a card is not a G.FUNCS call, so it cannot be hooked -- but it is
--- observable in the resulting order, and reproducible by setting that order
--- directly. This is what lets a replay follow a hand the player rearranged
--- by hand.
--- Put the jokers into an explicit order, given as ids.
---
--- Like dragging a card in hand, dragging a joker is not a G.FUNCS call and
--- cannot be hooked -- but the order it produces is observable and can be set
--- directly, which is what lets a replay reproduce it.
function BotAPI.set_joker_order(args)
  local wanted = {}
  for _, token in ipairs(args or {}) do wanted[#wanted + 1] = tonumber(token) end
  local by_id, base = {}, deck_base()
  for _, card in ipairs(G.jokers.cards) do by_id[card_uid(card, base)] = card end
  local ordered = {}
  for _, id in ipairs(wanted) do
    if by_id[id] then
      ordered[#ordered + 1] = by_id[id]
      by_id[id] = nil
    end
  end
  for _, card in ipairs(G.jokers.cards) do
    if by_id[card_uid(card, base)] then ordered[#ordered + 1] = card end
  end
  if #ordered ~= #G.jokers.cards then
    error("joker order must cover every joker", 0)
  end
  for i = 1, #ordered do G.jokers.cards[i] = ordered[i] end
  G.jokers:set_ranks()
  G.jokers:align_cards()
  return { ordered = #ordered }
end

function BotAPI.set_hand_order(args)
  local wanted = {}
  for _, token in ipairs(args or {}) do
    wanted[#wanted + 1] = tonumber(token)
  end
  local by_id, base = {}, deck_base()
  for _, card in ipairs(G.hand.cards) do by_id[card_uid(card, base)] = card end
  local ordered = {}
  for _, id in ipairs(wanted) do
    if by_id[id] then
      ordered[#ordered + 1] = by_id[id]
      by_id[id] = nil
    end
  end
  -- Anything not named keeps its relative position at the end.
  for _, card in ipairs(G.hand.cards) do
    if by_id[card_uid(card, base)] then ordered[#ordered + 1] = card end
  end
  if #ordered ~= #G.hand.cards then
    error("hand order must cover every card", 0)
  end
  for i = 1, #ordered do G.hand.cards[i] = ordered[i] end
  G.hand:set_ranks()
  G.hand:align_cards()
  return { ordered = #ordered }
end

--- Move one card left in the hand.
---
--- Hand order is scoring order -- a card area is sorted by x position, and
--- align_cards derives x from the array -- so this is not cosmetic. With a
--- +4 mult card and a x1.5 card in the same played hand, taking them in one
--- order scores 288 and the other 224.
---
--- Left-swaps rather than a from/to pair, for the same reason the joker
--- version does: it keeps the action space linear in hand size while still
--- reaching every permutation.
function BotAPI.swap_card_left(args)
  local index = tonumber(args and args[1])
  if not index or index <= 1 or not G.hand.cards[index] then
    return { swapped = false }
  end
  local card = table.remove(G.hand.cards, index)
  table.insert(G.hand.cards, index - 1, card)
  G.hand:set_ranks()
  G.hand:align_cards()
  return { swapped = true }
end

function BotAPI.clear()
  G.hand:unhighlight_all()
  return { selection = 0 }
end

-- Guard the empty case: evaluate_play indexes the scoring hand unconditionally,
-- so playing nothing crashes the game (state_events.lua:574). A client should
-- not be able to do that by mistake.
--- Bring the hand's positions into line with its order before acting on it.
---
--- A played hand scores left to right on *screen*: play_cards_from_highlighted
--- sorts the highlighted cards by `T.x` (state_events.lua:463), and everything
--- order-sensitive follows from that -- Hanging Chad retriggers `G.play.cards[1]`,
--- and so do Sock and Buskin, Seltzer and the rest.
---
--- Screen order and list order are the same thing once `align_cards` has run:
--- it writes each card's x from its index and then re-sorts the list by x
--- (cardarea.lua:463). Between a draw and the next alignment they are not. The
--- state reports `G.hand.cards`, the client and the simulator both take that
--- for the play order, and the engine took the stale x -- which on one seed
--- put a freshly drawn Ace first in the list and last on screen, so the engine
--- retriggered a 5 for 260 chips where the simulator retriggered the Ace for
--- 308.
---
--- A player never sees this: clicking takes longer than the animation. A bot
--- acts in the same frame, so it does what a player cannot, and this is the
--- settled state a player would have been looking at.
local function settle_hand()
  if G.hand then G.hand:align_cards() end
end

function BotAPI.play()
  if #G.hand.highlighted == 0 then
    error("play with no cards selected", 0)
  end
  if G.STATE ~= G.STATES.SELECTING_HAND then
    error("play outside the hand-selection phase", 0)
  end
  settle_hand()
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
  settle_hand()
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
  if not card then
    error("no shop card at " .. tostring(area) .. "[" .. tostring(index) .. "]", 0)
  end
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
  if not card then
    error("no card at " .. tostring(area) .. "[" .. tostring(index) .. "]", 0)
  end
  -- Distinguish "never" from "not yet". can_sell_card returns false for both
  -- an eternal joker and a game that is merely mid-animation, and reporting
  -- the first when it is the second sends you looking in the wrong place.
  if card.ability.eternal then
    error("cannot sell " .. tostring(card.config.center.key) ..
          ": it is eternal", 0)
  end
  local left = 120
  local function attempt()
    if not card:can_sell_card() then
      left = left - 1
      if left <= 0 then
        BOT_LAST_REFUSAL = "cannot sell " .. tostring(card.config.center.key) ..
            ": stop_use=" .. tostring(G.GAME.STOP_USE) ..
            " locked=" .. tostring(G.CONTROLLER.locked) ..
            " area=" .. tostring(card.area and card.area.config.type)
        return true
      end
      G.E_MANAGER:add_event(Event({
        trigger = 'after', delay = 0.1, blocking = false,
        no_delete = true, func = attempt }))
      return true
    end
    G.FUNCS.sell_card({ config = { ref_table = card } })
    return true
  end
  BOT_LAST_REFUSAL = nil
  queue(attempt)
  return { sold = true }
end

--- Why can_use_consumeable said no. It has a dozen guards and none of them are
--- visible from outside, so report their values rather than the verdict.
local function why_unusable(card)
  local con = card.ability.consumeable
  return "cannot use " .. tostring(card.config.center.key) ..
      " right now: state=" .. tostring(state_name()) ..
      " highlighted=" .. #G.hand.highlighted ..
      " hand=" .. #G.hand.cards ..
      " mod_num=" .. tostring(con.mod_num) ..
      " min=" .. tostring(con.min_highlighted) ..
      " max=" .. tostring(con.max_highlighted) ..
      " stop_use=" .. tostring(G.GAME.STOP_USE) ..
      " locked=" .. tostring(G.CONTROLLER.locked) ..
      " locks=" .. (function()
        local held = {}
        for name, on in pairs(G.CONTROLLER.locks or {}) do
          if on then held[#held + 1] = tostring(name) end
        end
        return #held > 0 and table.concat(held, ",") or "none"
      end)() ..
      " consumables=" .. #G.consumeables.cards ..
      "/" .. tostring(G.consumeables.config.card_limit)
end

--- Use a consumable once the game will actually accept it.
---
--- The check has to happen where the use happens. Checking at request time and
--- using later is what crashed the game: Death indexes G.hand.highlighted[1]
--- and [2] unconditionally, so a highlight cleared in between -- by the
--- previous consumable finishing, or by a pack still dealing its hand -- turns
--- into an index of nil. Retrying for a second also absorbs the common case,
--- where the only problem was that the previous use had not finished.
local function use_when_usable(card, frames)
  local left = frames or 120
  BOT_LAST_REFUSAL = nil
  local function attempt()
    if card.ability and card.ability.consumeable
        and not card:can_use_consumeable() then
      left = left - 1
      if left <= 0 then
        BOT_LAST_REFUSAL = why_unusable(card)
        return true                      -- give up; the client's wait reports it
      end
      -- 'after' with a delay, not 'immediate': immediate events run inside
      -- the same update, so an immediate retry loop spends its whole budget
      -- in one frame without any of the in-flight work getting to advance.
      G.E_MANAGER:add_event(Event({
        trigger = 'after', delay = 0.1, blocking = false,
        no_delete = true, func = attempt }))
      return true
    end
    G.FUNCS.use_card({ config = { ref_table = card } }, true)
    return true
  end
  queue(attempt)
end

function BotAPI.use_consumable(args)
  local card = G.consumeables.cards[tonumber(args[1])]
  if not card then
    error("no consumable at index " .. tostring(args[1]), 0)
  end
  if card.ability and card.ability.consumeable then
    use_when_usable(card)
  else
    queue(function() G.FUNCS.use_card({ config = { ref_table = card } }, true) end)
  end
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
  -- Errors, not a quiet { ok = false } payload: the protocol's own ok flag is
  -- what the client checks, so a refusal returned in the body is invisible.
  if not card then
    error("no pack card at index " .. tostring(args[1]) ..
          " (pack holds " ..
          tostring(G.pack_cards and #G.pack_cards.cards or 0) .. ")", 0)
  end
  -- A tarot taken from an Arcana pack is used the moment it is taken, so it
  -- goes through the same gate as one used from the consumable slots.
  if card.ability and card.ability.consumeable then
    if not card:can_use_consumeable() and #G.hand.cards == 0 then
      error(why_unusable(card), 0)       -- no hand at all: never going to work
    end
    use_when_usable(card)
  else
    -- Checked here as well as reported in the state, because use_card cannot
    -- refuse. If the mask is ever wrong again the run should stop with a
    -- readable error rather than carry on from a position the game has no
    -- way to represent.
    if not pack_takeable(card) then
      error("no room for that joker: " .. tostring(#G.jokers.cards) .. " of " ..
            tostring(G.jokers.config.card_limit) .. " slots filled", 0)
    end
    queue(function() G.FUNCS.use_card({ config = { ref_table = card } }, true) end)
  end
  return { picked = true }
end

--- Set the bankroll outright. A sandbox lever, not a game action: it makes
--- setting up a situation to record quick, instead of grinding to it.
function BotAPI.set_money(args)
  local amount = tonumber(args[1])
  if not amount then error("set_money needs a number", 0) end
  G.GAME.dollars = amount
  return { dollars = G.GAME.dollars }
end

--- Buy a consumable and use it in the same click, the shop's second button.
--- Distinct from buy: the card never reaches the consumable slots, so it works
--- with them full, and the effect lands immediately.
function BotAPI.buy_and_use(args)
  local area, index = args[1], tonumber(args[2])
  local card = G[area] and G[area].cards[index]
  if not card then
    error("no shop card at " .. tostring(area) .. "[" .. tostring(index) .. "]", 0)
  end
  queue(function()
    G.FUNCS.buy_from_shop({ config = { ref_table = card, id = 'buy_and_use' } })
  end)
  return { bought = true, key = card.config.center.key }
end

--- Freeze the run as the game's own save format, returned as a string.
---
--- This is what the game writes when it saves a run in progress, built by
--- save_run into G.ARGS.save_run: card areas, jokers, consumables, the deck,
--- tags, G.GAME and the blind. The write itself is a file handler that never
--- runs headless, so calling it just leaves the table in memory for us.
---
--- The point is training that does not only ever see ante one. Play to a
--- position once, keep it, and start episodes from there.
function BotAPI.snapshot_run()
  if G.STAGE ~= G.STAGES.RUN then error("not in a run", 0) end
  -- Headless the engine turns saving off wholesale (G.F_NO_SAVING), since the
  -- game writes a save after most actions and nothing here reads it. This is
  -- the one place the save table is genuinely wanted, so lift it just here.
  local no_saving = G.F_NO_SAVING
  G.F_NO_SAVING = false
  local ok, err = pcall(save_run)
  G.F_NO_SAVING = no_saving
  if not ok then error("save_run failed: " .. tostring(err), 0) end
  if not G.ARGS.save_run then error("the game produced no save table", 0) end

  -- Drop the menu's card areas. save_run stores every CardArea hanging off G,
  -- including the title screen's, and the loader complains once per area it
  -- cannot find -- which headless it never can, since it builds no menu. They
  -- have nothing to do with the run.
  local areas = G.ARGS.save_run.cardAreas
  if areas then
    for key in pairs(areas) do
      if key:match("^title") then areas[key] = nil end
    end
  end
  return STR_PACK(G.ARGS.save_run)
end

--- Start a run from a snapshot taken by snapshot_run.
---
--- Not over the socket: the payload is tens of kilobytes with newlines in it,
--- and the protocol is one line per request. Called in-process, which is where
--- training runs anyway.
function BotAPI.restore_run(packed)
  if type(packed) ~= "string" then
    error("restore_run wants the string snapshot_run returned", 0)
  end
  local saved = STR_UNPACK(packed)
  if type(saved) ~= "table" then error("snapshot did not unpack", 0) end
  if G.STAGE == G.STAGES.RUN then G:delete_run() end

  -- Build the shop's card areas if the snapshot has them. They are created in
  -- G.UIDEF.shop(), a UI function the engine never runs, so without this the
  -- loader finds nothing to load them into and drops the shelves -- which
  -- makes a shop position impossible to restore, and the shop is where the
  -- interesting decisions are.
  local areas = saved.cardAreas or {}
  if areas.shop_jokers and not G.shop_jokers then
    G.shop_jokers = CardArea(0, 0, 1, 1,
      { card_limit = 4, type = 'shop', highlight_limit = 1 })
  end
  if areas.shop_vouchers and not G.shop_vouchers then
    G.shop_vouchers = CardArea(0, 0, 1, 1,
      { card_limit = 2, type = 'shop', highlight_limit = 1 })
  end
  if areas.shop_booster and not G.shop_booster then
    G.shop_booster = CardArea(0, 0, 1, 1,
      { card_limit = 2, type = 'shop', highlight_limit = 1 })
  end

  BOT_RUN_PENDING = true
  G:start_run({ savetext = saved })
  BOT_RUN_PENDING = false
  return { restored = true, ante = G.GAME.round_resets.ante }
end

function BotAPI.skip_pack()
  queue(function() G.FUNCS.skip_booster({ config = {} }) end)
  return { skipped = true }
end

function BotAPI.move_joker(args)
  joker_state().swaps = joker_state().swaps + 1
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
