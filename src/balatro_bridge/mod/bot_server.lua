-- In-game bot server.
--
-- Injected into a modified *copy* of Balatro (see scripts/build_modded_game.py);
-- the Steam install is never touched. The game renders normally, and this
-- listens on a socket so the Python agent can drive it and you can watch.
--
-- Protocol: one request per line, plain text `<id> <cmd> [args]`; one reply per
-- line, JSON. Commands are trivially parseable so the Lua side needs only a
-- JSON encoder, not a parser -- both ends are written here, so there is no
-- reason to pay for a Lua JSON parser we would then have to trust.

local BotServer = {}

local HOST = "127.0.0.1"
local PORT = 34143

local socket_ok, socket = pcall(require, "socket")

local server, client, inbuf = nil, nil, ""

-- --------------------------------------------------------------- logging

local log_lines = {}

local function log(message)
  log_lines[#log_lines + 1] = message
  -- Written to the save directory, which is writable without extra privileges
  -- and is where a mod is expected to keep its state.
  local ok = pcall(function()
    love.filesystem.write("bot_server.log", table.concat(log_lines, "\n") .. "\n")
  end)
  return ok
end

BotServer.log = log

-- --------------------------------------------------------------- JSON out

local function escape(str)
  return (str:gsub('[%c"\\]', function(c)
    local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n',
                  ['\r'] = '\\r', ['\t'] = '\\t' }
    return map[c] or string.format('\\u%04x', c:byte())
  end))
end

local function encode(value)
  local kind = type(value)
  if value == nil then return "null" end
  if kind == "boolean" then return tostring(value) end
  if kind == "number" then
    -- JSON has no inf/nan; clamp rather than emit something unparseable.
    if value ~= value then return "0" end
    if value == math.huge then return "1e999" end
    if value == -math.huge then return "-1e999" end
    return string.format("%.14g", value)
  end
  if kind == "string" then return '"' .. escape(value) .. '"' end
  if kind ~= "table" then return '"<' .. kind .. '>"' end

  -- Array if keys are 1..n, object otherwise.
  local n = 0
  for _ in pairs(value) do n = n + 1 end
  local is_array = n == #value
  local parts = {}
  if is_array then
    for i = 1, #value do parts[#parts + 1] = encode(value[i]) end
    return "[" .. table.concat(parts, ",") .. "]"
  end
  for k, v in pairs(value) do
    parts[#parts + 1] = '"' .. escape(tostring(k)) .. '":' .. encode(v)
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

BotServer.encode = encode

-- --------------------------------------------------------------- commands

BotServer.handlers = {}

function BotServer.handlers.hello()
  return {
    game = "Balatro",
    protocol = 1,
    love = string.format("%d.%d.%d", love.getVersion()),
    socket = socket_ok and "yes" or "no",
    screenmode = (G and G.SETTINGS and G.SETTINGS.WINDOW
                  and G.SETTINGS.WINDOW.screenmode) or "?",
    fullscreen = (love.window.getFullscreen and love.window.getFullscreen())
                 and 1 or 0,
  }
end

function BotServer.handlers.ping()
  return { pong = true, time = love.timer and love.timer.getTime() or 0 }
end

-- Everything the agent actually drives lives in bot_api; the server is only
-- transport. Each handler is wrapped by dispatch's pcall, so a bad command
-- returns an error to the client instead of taking the game down.
local api_ok, BotAPI = pcall(require, "bot_api")
if api_ok then
  BotServer.api = BotAPI
  for name, fn in pairs(BotAPI) do
    if type(fn) == "function" then
      BotServer.handlers[name] = fn
    end
  end
else
  log("bot_api failed to load: " .. tostring(BotAPI))
end

-- --------------------------------------------------------------- transport

function BotServer.start()
  if not socket_ok then
    log("FATAL: luasocket unavailable; cannot serve")
    return false
  end
  local err
  server, err = socket.bind(HOST, PORT)
  if not server then
    log("FATAL: bind " .. HOST .. ":" .. PORT .. " failed: " .. tostring(err))
    return false
  end
  server:settimeout(0)
  log("listening on " .. HOST .. ":" .. PORT)
  return true
end

local function reply(id, ok, payload, err)
  if not client then return end
  local body = { id = id, ok = ok }
  if payload ~= nil then body.result = payload end
  if err ~= nil then body.error = err end
  client:send(encode(body) .. "\n")
end

local function dispatch(line)
  -- `<id> <cmd> [arg ...]`
  local id, cmd, rest = line:match("^(%S+)%s+(%S+)%s*(.*)$")
  if not id then
    reply(0, false, nil, "malformed request: " .. line:sub(1, 60))
    return
  end
  local handler = BotServer.handlers[cmd]
  if not handler then
    reply(tonumber(id) or 0, false, nil, "unknown command: " .. cmd)
    return
  end
  local args = {}
  for token in rest:gmatch("%S+") do args[#args + 1] = token end
  local ok, result = pcall(handler, args)
  if ok then
    reply(tonumber(id) or 0, true, result)
  else
    log("command '" .. cmd .. "' errored: " .. tostring(result))
    reply(tonumber(id) or 0, false, nil, tostring(result))
  end
end

-- Profile setup has to wait for G:start_up(), which runs in love.load -- i.e.
-- after main.lua, where this module is installed. G.P_CENTERS does not exist
-- yet at install time, so configure runs on the first frame that has it.
local configured = false

local function configure_once()
  if configured or not (G and G.P_CENTERS and G.SETTINGS) then return end
  configured = true
  if not (BotServer.api and BotServer.api.configure) then return end
  local ok, changed = pcall(BotServer.api.configure)
  log(ok and ("configured: tutorial_skipped=" .. tostring(changed.tutorial)
              .. " unlocked=" .. tostring(changed.unlocked)
              .. " gamespeed=" .. tostring(changed.gamespeed)
              .. " screenmode=" .. tostring(changed.screenmode))
           or ("configure failed: " .. tostring(changed)))
end

--- Poll the socket. Called once per frame from love.update.
function BotServer.poll()
  configure_once()
  if not server then return end

  if not client then
    local incoming = server:accept()
    if incoming then
      client = incoming
      client:settimeout(0)
      inbuf = ""
      log("client connected")
    end
    return
  end

  while true do
    local line, err, partial = client:receive("*l")
    if line then
      dispatch(line)
    elseif err == "timeout" then
      if partial and #partial > 0 then inbuf = inbuf .. partial end
      return
    else
      log("client disconnected: " .. tostring(err))
      client:close()
      client = nil
      return
    end
  end
end

--- Wrap love.update so the server is polled every frame. Called from main.lua
--- after love.update has been defined.
function BotServer.install()
  log("bot_server installing")

  -- Settings that must land before Game:start_up() runs in love.load. This
  -- module is required at the end of main.lua, which is still early enough.
  if G and G.SETTINGS then
    -- The game's own tutorial switch. tutorial_controller honours it by
    -- setting tutorial_complete and clearing tutorial_progress itself, which
    -- is better than fabricating a progress table: Game:update calls that
    -- controller every frame while the tutorial is incomplete, and it indexes
    -- tutorial_progress unconditionally. Set here, before start_up, because
    -- the game's update runs before any of our per-frame code.
    G.F_SKIP_TUTORIAL = true

    -- F_SKIP_TUTORIAL alone is not enough. It is only read by
    -- tutorial_controller, which runs in Game:update -- but Game:main_menu is
    -- called from start_up during love.load, before any update, and line 1531
    -- indexes G.SETTINGS.tutorial_progress unconditionally:
    --
    --   if (not tutorial_complete) and tutorial_progress.completed_parts[...]
    --
    -- On a fresh save tutorial_progress is nil, so that crashes before any
    -- per-frame code can run. Setting tutorial_complete makes the `and`
    -- short-circuit, so the nil is never indexed.
    G.SETTINGS.tutorial_complete = true
    G.SETTINGS.tutorial_progress = nil

    -- Belt and braces: start_up reloads settings.jkr, which can put
    -- tutorial_complete back to false with tutorial_progress still nil. Guard
    -- the call site itself so the order of settings loading cannot matter.
    local original_main_menu = Game.main_menu
    Game.main_menu = function(self, ...)
      G.SETTINGS.tutorial_complete = true
      G.SETTINGS.tutorial_progress = nil
      G.F_SKIP_TUTORIAL = true
      return original_main_menu(self, ...)
    end

    -- Silence the music. A bot-driven game is something you leave running in
    -- a window while working, and the soundtrack loops. Game sounds are left
    -- alone -- they mark what the agent is doing, which is useful to hear.
    --
    -- Override without rebuilding: BALATRO_BOT_MUSIC=on in the environment.
    G.SETTINGS.SOUND = G.SETTINGS.SOUND or {}
    if os.getenv("BALATRO_BOT_MUSIC") ~= "on" then
      G.SETTINGS.SOUND.music_volume = 0
    end

    -- The splash is ~7 seconds of logo animation before the menu appears.
    G.SETTINGS.skip_splash = "Yes"
    -- 4 is the maximum the options screen offers; it shortens animation only.
    G.SETTINGS.GAMESPEED = 4

    -- Windowed by default: a bot-driven game you want to watch alongside a
    -- terminal should not own the whole screen. Game:init_window copies this
    -- into QUEUED_CHANGE before calling apply_window_changes, and that path
    -- sizes the window at 80% of the desktop.
    --
    -- Override without rebuilding: BALATRO_BOT_SCREENMODE=Borderless (or
    -- Fullscreen) in the environment the game is launched from.
    local mode = os.getenv("BALATRO_BOT_SCREENMODE") or "Windowed"
    if mode ~= "Windowed" and mode ~= "Fullscreen" and mode ~= "Borderless" then
      log("unknown BALATRO_BOT_SCREENMODE " .. tostring(mode) .. ", using Windowed")
      mode = "Windowed"
    end
    G.SETTINGS.WINDOW = G.SETTINGS.WINDOW or {}
    G.SETTINGS.WINDOW.screenmode = mode

    log("pre-boot: splash skipped, gamespeed 4, music "
        .. tostring(G.SETTINGS.SOUND.music_volume) .. ", screenmode " .. mode)
  end
  if not BotServer.start() then return false end
  -- An error anywhere in the game drops LOVE into its error screen and stops
  -- calling love.update -- which looks from outside exactly like the socket
  -- hanging. Log it so the cause is visible instead of guessed at.
  local previous_handler = love.errorhandler or love.errhand
  local function record(message)
    log("GAME ERROR: " .. tostring(message))
    log(debug.traceback("", 2))
    if previous_handler then return previous_handler(message) end
  end
  love.errorhandler = record
  love.errhand = record

  local original = love.update
  love.update = function(dt)
    -- Before the game's own update, not after: Game:update calls
    -- tutorial_controller on its first frame, so configuring afterwards is
    -- already too late.
    configure_once()
    if original then
      local ok, err = pcall(original, dt)
      if not ok then
        log("UPDATE ERROR: " .. tostring(err))
        error(err, 0)     -- still surface it; do not silently limp along
      end
    end
    local ok, err = pcall(BotServer.poll)
    if not ok then log("poll error: " .. tostring(err)) end
  end
  log("bot_server installed")
  return true
end

return BotServer
