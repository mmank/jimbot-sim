-- The bot_api command surface, implemented over the headless engine.
--
-- The observation half of bot_api runs here unchanged -- `state` and
-- `fingerprint` are required straight from the mod, so a replay compares the
-- real game and the engine with literally the same code. The action half
-- cannot be shared: the game's own G.FUNCS callbacks are guarded on UIBoxes
-- that the headless engine deliberately never builds, so calling
-- G.FUNCS.select_blind here silently does nothing. headless_api reimplements
-- those, and this maps the mod's command names onto them.
--
-- Commands take the mod's argument convention -- a table of strings, args[1],
-- args[2] -- so the Python client cannot tell the two apart.

local BOT = require("bot_api")
local api = require("headless_api")

local H = {}

local function num(value, default)
  return tonumber(value) or default
end

-- -- observation: the mod's own, verbatim ---------------------------------

H.state = BOT.state
H.fingerprint = BOT.fingerprint
H.check = BOT.check
H.best_play = BOT.best_play

function H.hello()
  return { game = "Balatro", protocol = 1, headless = 1 }
end

-- -- run lifecycle ---------------------------------------------------------

function H.start_run(args)
  local result = BOT.start_run(args)
  -- start_run is queued as an event, exactly as in the real game; pump until
  -- the new run is actually up rather than returning into the old one.
  api.pump_until(function()
    return not BOT_RUN_PENDING and G.STATE == G.STATES.BLIND_SELECT
  end, 2000)
  return result
end

-- -- blinds ----------------------------------------------------------------

function H.select_blind() return { selected = api.select_blind() } end
function H.skip_blind()   return { skipped = api.skip_blind() } end

-- -- hand ------------------------------------------------------------------

function H.toggle(args) return { toggled = api.toggle(num(args[1], 0)) } end
function H.clear()      api.clear_highlights(); return { cleared = true } end

function H.play()
  -- play_selected, not play(indices): the client has already made the
  -- selection through toggle, and re-highlighting would undo a deliberate
  -- order.
  api.play_selected()
  return { played = true }
end

function H.discard()
  api.discard_selected()
  return { discarded = true }
end

function H.sort_hand(args)
  api.sort_hand(args[1] == "suit" and "suit" or "rank")
  return { sorted = args[1] }
end

-- -- consumables and packs -------------------------------------------------

function H.use_consumable(args)
  local card = G.consumeables.cards[num(args[1], 0)]
  if not card then error("no consumable at index " .. tostring(args[1]), 0) end
  -- Deliberately not api.use_consumable: it clears the highlight when given no
  -- targets, which would throw away the selection the client just made.
  if not card:can_use_consumeable() then
    error("cannot use " .. tostring(card.config.center.key) ..
          " right now (" .. #G.hand.highlighted .. " selected)", 0)
  end
  G.FUNCS.use_card({ config = { ref_table = card } }, true)
  api.pump(240)
  return { used = true }
end

function H.pick_pack(args)
  api.pick_pack(num(args[1], 0))
  return { picked = true }
end

function H.skip_pack() api.skip_pack(); return { skipped = true } end

-- -- shop ------------------------------------------------------------------

function H.buy(args)
  api.buy(args[1], num(args[2], 0))
  return { bought = true }
end

function H.buy_and_use(args)
  api.buy_and_use(args[1], num(args[2], 0))
  return { bought = true }
end

function H.sell(args)
  local area, index = args[1], num(args[2], 0)
  local card = G[area] and G[area].cards[index]
  if not card then
    error("no card at " .. tostring(area) .. "[" .. tostring(index) .. "]", 0)
  end
  if card.ability.eternal then
    error("cannot sell " .. tostring(card.config.center.key) ..
          ": it is eternal", 0)
  end
  api.sell(area, index)
  return { sold = true }
end

H.set_money = BOT.set_money

function H.reroll()     api.reroll();     return { rerolled = true } end
function H.leave_shop() api.leave_shop(); return { left = true } end

-- -- ordering --------------------------------------------------------------

-- Everything else comes from the mod as-is. Inheriting rather than listing is
-- deliberate: a command added to bot_api for the real game should exist here
-- the moment it is written, and only the ones that genuinely cannot work
-- headless -- the UI-guarded callbacks overridden above -- should need saying
-- twice. Reordering, querying and the recording hooks are all pure table work
-- and run here unchanged.
for name, fn in pairs(BOT) do
  if type(fn) == "function" and H[name] == nil then
    H[name] = fn
  end
end

return H
