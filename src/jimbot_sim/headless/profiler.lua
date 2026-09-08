-- Sampling profiler for the game's own Lua, using LuaJIT's built-in one.
--
-- Worth having because the simulator is ~98% of training time, and the frame
-- costs 200 microseconds without it being obvious where. Guessing produced one
-- good change (the per-frame card update) and several dead ends.
--
-- Sampling rather than instrumenting: a count hook on every call would change
-- what it measures, and Balatro's inner loops are small functions called
-- constantly.

local profile = require("jit.profile")

local M = { counts = {}, total = 0, running = false }

--- Start sampling. `format` follows LuaJIT's dumpstack: "F" function, "l"
--- line, "p" path. `interval` is milliseconds between samples.
--- `depth` is how many frames of the stack to key on. One says which function
--- is hot; several say who is calling it, which is the question that matters
--- once the hot function is shared library code.
function M.start(format, interval, depth)
  M.counts, M.total, M.running = {}, 0, true
  M.format = format or "F"
  M.depth = depth or 1
  profile.start("i" .. tostring(interval or 1), function(thread, samples, _)
    local key = profile.dumpstack(thread, M.format, M.depth)
    key = key:gsub("%s+$", "")
    if key ~= "" then
      M.counts[key] = (M.counts[key] or 0) + samples
      M.total = M.total + samples
    end
  end)
end

function M.stop()
  if M.running then profile.stop(); M.running = false end
  return M.total
end

--- The hottest entries, as text: share, samples, location.
function M.report(limit)
  local rows = {}
  for key, count in pairs(M.counts) do rows[#rows + 1] = { key, count } end
  table.sort(rows, function(a, b) return a[2] > b[2] end)
  local out = { string.format("%d samples", M.total) }
  for i = 1, math.min(limit or 25, #rows) do
    out[#out + 1] = string.format("%6.2f%%  %7d  %s",
      100 * rows[i][2] / math.max(1, M.total), rows[i][2], rows[i][1])
  end
  return table.concat(out, "\n")
end

return M
