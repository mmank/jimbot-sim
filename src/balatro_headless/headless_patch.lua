-- Fill in the state that only ever gets set by LOVE callbacks we never receive.
--
-- The game establishes some globals in main.lua's love.resize / love.load,
-- which are not modules and so are never required here. Rather than stubbing
-- the readers, we set the same fields main.lua would, so the logic sees a
-- consistent (if invisible) 1280x720 window.

local patch = {}

-- Settings that must be in place *before* Game:start_up() runs.
function patch.pre_boot()
  -- start_up plays the splash screen, which queues an event that calls
  -- G:main_menu() two seconds later -- and that tears down any run started in
  -- the meantime. The game has its own setting for skipping it.
  G.SETTINGS.skip_splash = 'Yes'

  -- A fresh profile with no save file runs the tutorial, which forces specific
  -- shop items, a specific voucher and specific tags (see G.FUNCS.start_tutorial).
  -- Leaving it on would quietly corrupt every run, so mark it complete, exactly
  -- as a save file from a player who finished it would.
  G.SETTINGS.tutorial_complete = true
  G.SETTINGS.tutorial_progress = {
    hold_parts = {},
    completed_parts = {
      small_blind = true, big_blind = true, second_hand = true,
      shop_1 = true, shop_2 = true, consumables = true,
    },
  }
  return patch
end

-- Menu and splash animations queued during boot are irrelevant here, and left
-- in place they fire mid-run. Dropped once, after start_up.
function patch.flush_events()
  for _, queue in pairs(G.E_MANAGER.queues) do
    for i = #queue, 1, -1 do queue[i] = nil end
  end
  return patch
end

function patch.apply(opts)
  opts = opts or {}
  local w = opts.width or 1280
  local h = opts.height or 720

  -- main.lua love.resize()
  G.WINDOWTRANS = G.WINDOWTRANS or {}
  G.WINDOWTRANS.real_window_w = w
  G.WINDOWTRANS.real_window_h = h
  G.CANV_SCALE = 1
  G.AA_CANVAS = nil

  -- love.resize also caches the room's untransformed origin, which the
  -- screenshake maths reads back every frame.
  if G.ROOM and G.ROOM.T then
    G.ROOM_ORIG = G.ROOM_ORIG or {x = G.ROOM.T.x, y = G.ROOM.T.y, r = G.ROOM.T.r}
  end

  -- The cursor feeds the same maths. All four fields must be set together:
  -- the game only fills its defaults when the whole table is absent.
  if G.CONTROLLER then
    G.CONTROLLER.cursor_position = G.CONTROLLER.cursor_position or {x = w / 2, y = h / 2}
  end
  G.ARGS = G.ARGS or {}
  G.ARGS.eased_cursor_pos = {
    x = G.CURSOR and G.CURSOR.T and G.CURSOR.T.x or 0,
    y = G.CURSOR and G.CURSOR.T and G.CURSOR.T.y or 0,
    sx = w / 2,
    sy = h / 2,
  }

  return patch
end

-- Progression: a fresh profile has 45 of 150 jokers locked (Blueprint,
-- Brainstorm, Canio, Chicot among them), half the vouchers, 14 of 15 decks and
-- 7 of 8 stakes. Pool eligibility is gated on `unlocked ~= false`
-- (get_current_pool), and tag eligibility additionally checks `discovered`, so
-- an un-unlocked profile trains the bot on a smaller game than the real one.
--
-- The game's own unlock_card() is no use here: it early-returns on seeded runs
-- and does save-file and notification I/O. Setting the flags directly is what a
-- 100%-completion save file looks like to the pool code.
function patch.unlock_all()
  local unlocked, discovered = 0, 0
  for _, center in pairs(G.P_CENTERS) do
    if center.unlocked == false then center.unlocked = true; unlocked = unlocked + 1 end
    if not center.discovered then center.discovered = true; discovered = discovered + 1 end
    center.alert = nil
  end
  for _, back in pairs(G.P_CENTER_POOLS.Back or {}) do
    back.unlocked = true
    back.discovered = true
  end
  for _, stake in pairs(G.P_STAKES or {}) do stake.unlocked = true end
  for _, blind in pairs(G.P_BLINDS or {}) do blind.discovered = true end
  for _, tag in pairs(G.P_TAGS or {}) do tag.discovered = true end
  if set_discover_tallies then set_discover_tallies() end
  patch.unlock_counts = { unlocked = unlocked, discovered = discovered }
  return patch
end

-- Some G.FUNCS reach straight into blind-select UI objects that only exist
-- once that screen is built. Their logic is small; the UI around it is not.
-- Replacing them is the same treatment already applied to BLIND_SELECT and
-- ROUND_EVAL: keep the rules, drop the presentation.
function patch.override_ui_functions()
  -- The Boss Tag rerolls the boss blind. The stock implementation animates
  -- G.blind_select_opts.boss, which is nil headless, so a Boss Tag crashed the
  -- run. Everything that matters is these four lines.
  G.FUNCS.reroll_boss = function()
    -- Who may reroll, and how often: nobody without a voucher, once an ante
    -- with Director's Cut, any number with Retcon. A Boss Tag rerolls free
    -- and is not subject to any of it. This used to refuse a second reroll
    -- outright, which made Retcon behave like Director's Cut.
    if not G.from_boss_tag then
      local used = G.GAME.used_vouchers or {}
      local allowed = used.v_retcon
          or (used.v_directors_cut and not G.GAME.round_resets.boss_rerolled)
      if not allowed then return end
      if (G.GAME.dollars - (G.GAME.bankrupt_at or 0)) - 10 < 0 then return end
    end
    G.GAME.round_resets.boss_rerolled = true
    if not G.from_boss_tag then ease_dollars(-10) end
    G.from_boss_tag = nil
    G.GAME.round_resets.blind_choices.Boss = get_new_boss()
    for i = 1, #G.GAME.tags do
      if G.GAME.tags[i]:apply_to_run({ type = 'new_blind_choice' }) then break end
    end
  end
  -- The game saves the run after most actions -- buying, cashing out, taking
  -- a blind. save_run culls a copy of the entire game state to do it, 2ms a
  -- call and 3% of wall clock, and headless nothing ever reads the file: the
  -- write itself is a file handler that never runs.
  --
  -- G.F_NO_SAVING is the game's own switch for this, checked on the first line
  -- of save_run, so nothing needs wrapping. bot_api lifts it while taking a
  -- snapshot, which is the one time the save table is actually wanted.
  G.F_NO_SAVING = true

  -- Overlay menus are built and never looked at. The game-over screen is one
  -- per episode at 15.5ms, which at a hundred episodes a run is most of a
  -- second in twenty -- and headless nothing reads it: the run ends because
  -- the state says GAME_OVER, not because anyone clicks a button on it.
  --
  -- The machinery is kept rather than bypassed: G.OVERLAY_MENU is still a real
  -- UIBox that can be removed and tested for, it just has nothing in it. That
  -- matters because code elsewhere checks whether an overlay is up.
  local overlay_menu = G.FUNCS.overlay_menu
  G.FUNCS.overlay_menu = function(args)
    args = args or {}
    args.definition = { n = G.UIT.ROOT,
                        config = { align = "cm", colour = G.C.CLEAR },
                        nodes = {} }
    return overlay_menu(args)
  end

  return patch
end

-- Animation is pure latency for a bot. GAMESPEED is the game's own speed
-- setting (the UI offers 0.5/1/2/4); it scales the TIMERS.TOTAL clock that
-- event delays are measured against, so a large value collapses animation
-- without altering any logic. It is a multiplier on the clock, not a divisor
-- on delays -- setting it absurdly high is safe, setting it low stalls the run.
function patch.fast_forward(speed)
  G.SETTINGS.GAMESPEED = speed or 64
  G.SETTINGS.reduced_motion = true
  return patch
end

return patch
