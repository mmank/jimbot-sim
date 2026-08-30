"""Dump every joker the game defines, with its text and config.

Written against the engine rather than a wiki: the localisation strings and the
config numbers are what the game actually scores with, so a joker implemented
from this cannot be built on a half-remembered description.

    python ops/joker_catalogue.py            # everything the simulator lacks
    python ops/joker_catalogue.py --all      # all 150
"""

import argparse
import json
import sys

sys.path.insert(0, "src")

from balatro.jokers import REGISTRY                      # noqa: E402
from balatro_headless.runtime import HeadlessBalatro     # noqa: E402

QUERY = """(function()
  local out = {}
  for k, v in pairs(G.P_CENTERS) do
    if v.set == "Joker" then
      local loc = G.localization.descriptions.Joker[k]
      if loc then
        local txt = {}
        for _, line in ipairs(loc.text or {}) do txt[#txt+1] = line end
        local cfg = {}
        for ck, cv in pairs(v.config or {}) do
          if type(cv) ~= "table" then
            cfg[#cfg+1] = ck .. "=" .. tostring(cv)
          else
            for k2, v2 in pairs(cv) do
              cfg[#cfg+1] = ck .. "." .. k2 .. "=" .. tostring(v2)
            end
          end
        end
        out[#out+1] = table.concat({
          k, loc.name, tostring(v.rarity or 1), tostring(v.cost or 0),
          table.concat(txt, " | "), table.concat(cfg, " ")}, "\\t")
      end
    end
  end
  return table.concat(out, "\\n") end)()"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", metavar="PATH")
    args = parser.parse_args()

    engine = HeadlessBalatro().boot()
    rows = []
    for line in engine.eval(QUERY).split("\n"):
        if not line.strip():
            continue
        key, name, rarity, cost, text, config = line.split("\t")
        rows.append({"key": key, "name": name, "rarity": int(float(rarity)),
                     "cost": int(float(cost)), "text": text, "config": config})

    if not args.all:
        rows = [r for r in rows if r["name"] not in REGISTRY]
    rows.sort(key=lambda r: (r["rarity"], r["name"]))

    print("implemented %d of 150, listing %d" % (len(REGISTRY), len(rows)))
    for row in rows:
        print("\n%-24s r%d  $%d  [%s]" % (row["name"], row["rarity"],
                                          row["cost"], row["key"]))
        print("    %s" % row["text"])
        if row["config"]:
            print("    config: %s" % row["config"])
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(rows, handle, indent=1)


if __name__ == "__main__":
    main()
