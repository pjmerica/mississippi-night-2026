"""Mississippi election night 2026: county benchmarks + shapes for index.html. Run once (the inputs don't change
on the night; index.html fetches the live count from NYT in the browser).

Same method as polling-agg's State election results/scripts/county_benchmarks.py and the data desk:
  1. Turnout weight: the county's share of the 2024 statewide vote for Senate (Mississippi had no statewide race in
     2022, and its governor runs in odd years, which this archive doesn't hold).
  2. Lean: the county's two-party Democratic share in the 2024 presidential race plus the seat's most
     comparable race (the 2020 Senate race: Hyde-Smith, the same incumbent, against Espy), averaged on the
     log-odds scale, then re-centred so the state is tied (L = the county's share at a tied statewide result).
  3. Expected tonight: one log-odds shift for every county (uniform swing), solved so the turnout-weighted
     two-party share lands on the margin model's 2026 margin (docs/forecast_data.js), divided by a 0.97
     two-party share as live_project.tracking does.

    py build.py      -> ms_bench.js, ms_geo.js
"""
import json
import math
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PA = r"C:\Users\pjmer\Documents\Polling Agg\Polling agg and Prediction markets"
NYT = os.path.join(PA, "State election results", "data", "raw", "nyt_all")
sys.path.insert(0, os.path.join(PA, "scripts"))
from build_map_geo import arcs_of, ring  # noqa: E402  (us-atlas TopoJSON decoding, as the desk's county map)

TWO = 0.97
ST = "28"
TURNOUT = ("2024 Senate", "2024/mississippi-us-senate.json")
RACES = {
    "sen": dict(label="Senate", office="U.S. Senate", fc="2026-SEN-MS",
                bases=[("2024 President", "2024/mississippi-president.json"), ("2020 Senate", "2020/mississippi-senate.json")]),
}


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def expit(x):
    return 1 / (1 + math.exp(-x))


def solve(L, w, target):
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if sum(w[k] * expit(L[k] + mid) for k in w) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def counties(path):
    """{fips: dict(name, d, r, total)} from a NYT results file (2022/2024 page format, or 2020's "data" format)."""
    d = json.load(open(os.path.join(NYT, path), encoding="utf-8"))
    if "data" in d:
        r = d["data"]["races"][0]
        pty = {c["candidate_key"]: c["party_id"] for c in r["candidates"]}
        return {c["fips"]: dict(name=c["name"], d=sum(v for k, v in c["results"].items() if pty.get(k) == "democrat"),
                                r=sum(v for k, v in c["results"].items() if pty.get(k) == "republican"),
                                total=sum(c["results"].values())) for c in r["counties"]}
    r = [x for x in d["races"] if x.get("reporting_units")][0]
    pty = {k: v["party"]["nyt_id"] for k, v in r["candidate_metadata"].items()}
    out = {}
    for u in r["reporting_units"]:
        if u.get("level") != "county":
            continue
        f = ST + str(u["fips_county"]).zfill(3)
        c = {x["nyt_id"]: (x.get("votes") or {}).get("total", 0) for x in u["candidates"]}
        out[f] = dict(name=u["name"], d=sum(v for k, v in c.items() if pty.get(k) == "DEM"),
                      r=sum(v for k, v in c.items() if pty.get(k) == "GOP"), total=sum(c.values()))
    return out


def model_margin(fc_id):
    s = open(os.path.join(PA, "docs", "forecast_data.js"), encoding="utf-8").read()
    i = s.index('"' + fc_id + '"')
    seg = s[i:s.index("]", i)]
    dm = rm = None
    for m in re.finditer(r'"name":"([^"]+)","party":"(\w+)","win":([\d.]+)[^}]*?"margin":(-?[\d.]+)', seg):
        name, party, win, mar = m.groups()
        if party == "DEM" and dm is None:
            dm = (name, float(win), float(mar))
        if party == "REP" and rm is None:
            rm = (name, float(win), float(mar))
    return dm, rm


def main():
    turn = counties(TURNOUT[1])
    out = {}
    for key, cfg in RACES.items():
        bases = [(lab, counties(p)) for lab, p in cfg["bases"]]
        keys = [f for f in turn if turn[f]["total"] > 0 and all(b[f]["d"] + b[f]["r"] > 0 for _, b in bases if f in b)
                and all(f in b for _, b in bases)]
        tot = sum(turn[f]["total"] for f in keys)
        w = {f: turn[f]["total"] / tot for f in keys}
        raw = {f: sum(logit(b[f]["d"] / (b[f]["d"] + b[f]["r"])) for _, b in bases) / len(bases) for f in keys}
        s_tie = solve(raw, w, 0.5)
        L = {f: raw[f] + s_tie for f in keys}
        dm, rm = model_margin(cfg["fc"])
        m0 = dm[2]                                  # the Democrat's margin, points
        s_mod = solve(L, w, 0.5 + (m0 / TWO) / 200)
        hist = {lab: {f: round(100 * (b[f]["d"] - b[f]["r"]) / max(1, b[f]["d"] + b[f]["r"]), 1) for f in keys}
                for lab, b in bases}
        out[key] = dict(
            label=cfg["label"], office=cfg["office"], turnout=TURNOUT[0], model=dict(d=dm[0], r=rm[0], d_margin=m0, d_win=dm[1]),
            bases=[lab for lab, _ in cfg["bases"]],
            counties=[dict(f=f, n=turn[f]["name"], w=round(w[f], 5), L=round(L[f], 4), tie=round(expit(L[f]), 4),
                           exp=round(expit(L[f] + s_mod), 4), vt=turn[f]["total"],
                           past={lab: hist[lab][f] for lab in hist}) for f in sorted(keys)])
        print(f"{key}: {len(keys)} counties, model {dm[0]} {m0:+.2f}, shift {s_mod:+.3f}")
    with open(os.path.join(HERE, "ms_bench.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("window.MS_BENCH = " + json.dumps(out, separators=(",", ":")) + ";\n")

    url = "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-albers-10m.json"
    topo = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mississippi-night build"}), timeout=60).read())
    arcs = arcs_of(topo)
    polys = lambda g: [[ring(arcs, r) for r in p] for p in (g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]])]
    flip = lambda rg: [[round(x, 2), round(-y, 2)] for x, y in rg]     # y up for deck.gl, as the desk's prepGeo
    geo = dict(counties={}, outline=[], neighbours=[])
    for g in topo["objects"]["counties"]["geometries"]:
        if str(g["id"]).startswith(ST):
            rings = [flip(rg) for p in polys(g) for rg in p[:1]]
            geo["counties"][str(g["id"])] = rings
    for g in topo["objects"]["states"]["geometries"]:
        if g["id"] in (ST, "22", "05", "47", "01"):
            for p in polys(g):
                (geo["outline"] if g["id"] == ST else geo["neighbours"]).append(flip(p[0]))
    with open(os.path.join(HERE, "ms_geo.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("window.MS_GEO = " + json.dumps(geo, separators=(",", ":")) + ";\n")
    print("geo:", len(geo["counties"]), "counties")


if __name__ == "__main__":
    main()
