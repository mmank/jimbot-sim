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
