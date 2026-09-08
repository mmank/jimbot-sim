-- A no-op stand-in for the LOVE2D API.
--
-- Balatro's game logic barely touches love directly (card.lua has one call,
-- blind/cardarea/tag/state_events have none) -- the coupling is the object
-- hierarchy and the event queue, not the API. So everything graphical becomes a
-- chainable no-op, and only the handful of calls whose return value the logic
-- actually reads are implemented for real.

local stub = {}

-- Any unknown field is a callable that returns another auto-stub, so long
-- chains like love.graphics.newCanvas():renderTo(...) never crash.
local function auto(name)
  local t = {}
  local mt = {}
  mt.__index = function(_, key)
    local child = auto(name .. "." .. tostring(key))
    rawset(t, key, child)
    return child
  end
  mt.__call = function(_, ...) return auto(name .. "()") end
  mt.__tostring = function() return "<stub " .. name .. ">" end
  return setmetatable(t, mt)
end

stub.auto = auto

function stub.install(opts)
  opts = opts or {}
  local love = auto("love")

  love.getVersion = function() return 11, 5, 0, "Mysterious Mysteries" end
  love._version = "11.5"

  love.system = auto("love.system")
  love.system.getOS = function() return opts.os or "Windows" end

  -- Timing: the event manager reads real time to advance animations. A
  -- monotonic counter we control keeps runs deterministic and lets us skip
  -- animation delays entirely.
  local clock = 0
  love.timer = auto("love.timer")
  love.timer.getTime = function() return clock end
  love.timer.step = function() return 1 / 60 end
  love.timer.getDelta = function() return 1 / 60 end
  love.timer.getFPS = function() return 60 end
  love.timer.sleep = function() end
  stub.advance = function(dt) clock = clock + (dt or 1 / 60) end
  stub.now = function() return clock end

  -- Randomness must come from the game's own seeded RNG, not love's.
  love.math = auto("love.math")
  love.math.random = math.random
  love.math.randomseed = math.randomseed
  love.math.newRandomGenerator = function()
    local g = {}
    function g:random(a, b)
      if a and b then return math.random(a, b) elseif a then return math.random(a) end
      return math.random()
    end
    function g:setSeed() end
    function g:getSeed() return 0 end
    return g
  end

  -- Graphics: dimensions are read for layout maths, so return plausible ones.
  love.graphics = auto("love.graphics")
  love.graphics.getWidth = function() return opts.width or 1280 end
  love.graphics.getHeight = function() return opts.height or 720 end
  love.graphics.getDimensions = function()
    return opts.width or 1280, opts.height or 720
  end
  love.graphics.getDPIScale = function() return 1 end
  love.graphics.isActive = function() return false end
  love.graphics.newImage = function() return stub.image() end
  love.graphics.newCanvas = function() return stub.image() end
  love.graphics.newQuad = function() return auto("quad") end
  love.graphics.newShader = function() return auto("shader") end
  love.graphics.newFont = function() return stub.font() end
  love.graphics.newSpriteBatch = function() return auto("spritebatch") end
  love.graphics.getFont = function() return stub.font() end

  love.window = auto("love.window")
  love.window.getMode = function()
    return opts.width or 1280, opts.height or 720, {}
  end
  love.window.getFullscreenModes = function()
    return { { width = 1280, height = 720 } }
  end
  love.window.getDesktopDimensions = function() return 1920, 1080 end
  love.window.setMode = function() return true end
  love.window.hasFocus = function() return false end

  -- Real reads, rooted at the extracted game tree: the game loads its
  -- localization and other data through love.filesystem, and that data feeds
  -- the item prototypes, so it cannot be stubbed away.
  local root = opts.root
  local function resolve(path)
    if not root or not path then return nil end
    return root .. "/" .. tostring(path)
  end

  love.filesystem = auto("love.filesystem")
  love.filesystem.getInfo = function(path)
    local full = resolve(path)
    if not full then return nil end
    local f = io.open(full, "rb")
    if not f then return nil end
    f:close()
    return { type = "file" }
  end
  love.filesystem.read = function(path)
    local full = resolve(path)
    if not full then return nil, "headless: no root configured" end
    local f = io.open(full, "rb")
    if not f then return nil, "headless: missing " .. tostring(path) end
    local data = f:read("*a")
    f:close()
    return data, #data
  end
  love.filesystem.getSourceBaseDirectory = function() return root or "." end
  love.filesystem.getDirectoryItems = function() return {} end
  love.filesystem.write = function() return true end
  love.filesystem.remove = function() return true end
  love.filesystem.createDirectory = function() return true end
  love.filesystem.getSaveDirectory = function() return "." end
  love.filesystem.setIdentity = function() end
  love.filesystem.isFused = function() return false end

  love.event = auto("love.event")
  love.event.pump = function() end
  love.event.poll = function() return function() return nil end end
  love.event.quit = function() end

  love.thread = auto("love.thread")
  love.thread.getChannel = function()
    return { push = function() end, pop = function() return nil end,
             supply = function() end, demand = function() return nil end,
             getCount = function() return 0 end }
  end
  love.thread.newThread = function()
    return { start = function() end, isRunning = function() return false end }
  end

  love.audio = auto("love.audio")
  love.audio.newSource = function() return stub.source() end
  love.audio.play = function() end
  love.audio.stop = function() end

  love.mouse = auto("love.mouse")
  love.mouse.getPosition = function() return 0, 0 end
  love.mouse.isDown = function() return false end
  love.mouse.setVisible = function() end

  love.keyboard = auto("love.keyboard")
  love.keyboard.isDown = function() return false end

  love.joystick = auto("love.joystick")
  love.joystick.getJoysticks = function() return {} end

  love.arg = auto("love.arg")
  love.arg.parseGameArguments = function() return {} end

  return love
end

function stub.image()
  local img = stub.auto("image")
  img.getWidth = function() return 1 end
  img.getHeight = function() return 1 end
  img.getDimensions = function() return 1, 1 end
  img.setFilter = function() end
  img.renderTo = function(_, fn) if type(fn) == "function" then fn() end end
  img.release = function() end
  return img
end

function stub.font()
  local font = stub.auto("font")
  font.getWidth = function(_, s) return #tostring(s or "") * 8 end
  font.getHeight = function() return 12 end
  font.setFilter = function() end
  return font
end

function stub.source()
  local src = stub.auto("source")
  src.play = function() end
  src.stop = function() end
  src.setVolume = function() end
  src.setPitch = function() end
  src.setLooping = function() end
  src.isPlaying = function() return false end
  src.clone = function() return stub.source() end
  return src
end

return stub
