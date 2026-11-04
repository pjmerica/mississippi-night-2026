"""Mississippi Senate live: the desk's county projector + Kalshi/Polymarket books, written to the orphan branch `live`.

index.html reads live.json from that branch (Kalshi's API refuses cross-site browser requests, so prices can't be
fetched by the page itself). Every pass (30 s):
  - NYT's Mississippi results page -> counted votes per county.
  - county_project.project (a copy of polling-agg's projector v1, unchanged) on the county leans in ms_bench.js,
    starting from our margin model (Colom -5.54, sd 6.1 as the desk's MODEL_MARGIN_SD) -> projected final margin,
    its sd and P(Colom wins). Also the count-only margin (no starting point), as the desk's "implied".
  - Kalshi books (SENATEMS-26-D / -R) and Polymarket's (Colom / Hyde-Smith tokens): the cheapest way to
    buy each side (YES on that side or NO on the other), with taker fees (utils/fees.py formulas).
  - Edge after fee = our chance - ask - fee; FLIP as the desk's is_flip (45%+, edge 5c+, ask 40c or less, 20%+ counted).

    py night.py --minutes 340 [--state DIR]     (DIR: a clone of the `live` branch; omit to write ./live.json only)
"""
import argparse
import json
import math
import os
import subprocess
import time
import urllib.request

import county_project

HERE = os.path.dirname(os.path.abspath(__file__))
NYT = "https://static01.nyt.com/elections-assets/pages/data/2026-11-03/results-mississippi.json"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
K_SERIES, K_EVENT = "SENATEMS", "SENATEMS-26"
K_MKT = {"D": "SENATEMS-26-D", "R": "SENATEMS-26-R"}
P_SLUG = "mississippi-senate-election-winner"
P_NAME = {"D": "Colom", "R": "Hyde-Smith"}
MODEL_SD = 6.1
PASS = 30
FLIP_MIN_P, FLIP_MIN_EDGE, FLIP_MAX_ASK, FLIP_MIN_PCT = 0.45, 0.05, 0.40, 20.0
UA = {"User-Agent": "mississippi-night-2026 (github.com/pjmerica/mississippi-night-2026)"}


def get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def bench():
    s = open(os.path.join(HERE, "ms_bench.js"), encoding="utf-8").read()
    b = json.loads(s[s.index("=") + 1:].strip().rstrip(";"))["sen"]
    return b, {c["f"]: c["L"] for c in b["counties"]}, {c["f"]: c["w"] for c in b["counties"]}


def nyt_count():
    d = get(NYT + "?t=" + str(int(time.time())))
    r = next(x for x in d["races"] if x["office"] == "U.S. Senate")
    pty = {k: v["party"]["nyt_id"] for k, v in r["candidate_metadata"].items()}
    units, st = {}, None
    for u in r["reporting_units"]:
        dv = rv = tot = 0
        for c in u["candidates"]:
            v = (c.get("votes") or {}).get("total") or 0
            tot += v
            dv += v if pty.get(c["nyt_id"]) == "DEM" else 0
            rv += v if pty.get(c["nyt_id"]) == "GOP" else 0
        rec = dict(d=dv, r=rv, total=tot, expected=u.get("total_expected_vote") or 0, eevp=u.get("eevp") or 0)
        if u["level"] == "state":
            st = rec
        elif u["level"] == "county":
            units["28" + str(u["fips_county"]).zfill(3)] = rec
    return units, st, r.get("updated_at")


def kalshi_fee_mult():
    try:
        return float(get(f"{KALSHI}/series/{K_SERIES}")["series"].get("fee_multiplier", 1))
    except Exception:
        return 1.0


def kalshi_books():
    """{side: dict(bid, ask, ask_size, how)}: the cheapest buy of each side - YES on its market, or NO on the other's
    (a NO bid at p is a YES ask at 1 - p; buying NO on the other market at 1 - its YES bid)."""
    top = {}
    for side, t in K_MKT.items():
        ob = get(f"{KALSHI}/markets/{t}/orderbook?depth=5")["orderbook_fp"]
        yes = sorted(((float(p), float(s)) for p, s in ob.get("yes_dollars") or []), reverse=True)
        no = sorted(((float(p), float(s)) for p, s in ob.get("no_dollars") or []), reverse=True)
        top[side] = dict(yes_bid=yes[0] if yes else None, no_bid=no[0] if no else None)
    out = {}
    for side, other in (("D", "R"), ("R", "D")):
        opts = []
        if top[side]["no_bid"]:
            opts.append((round(1 - top[side]["no_bid"][0], 4), top[side]["no_bid"][1], f"YES {K_MKT[side]}"))
        if top[other]["yes_bid"]:
            opts.append((round(1 - top[other]["yes_bid"][0], 4), top[other]["yes_bid"][1], f"NO {K_MKT[other]}"))
        best = min(opts) if opts else None
        out[side] = dict(bid=top[side]["yes_bid"][0] if top[side]["yes_bid"] else None,
                         ask=best[0] if best else None, ask_size=best[1] if best else None, how=best[2] if best else None)
    return out


def poly_setup():
    e = get(f"{GAMMA}/events?slug={P_SLUG}")[0]
    toks, rate = {}, 0.0
    for m in e["markets"]:
        for side, nm in P_NAME.items():
            if nm.lower() in (m.get("groupItemTitle") or "").lower():
                ids = json.loads(m["clobTokenIds"])
                toks[side] = dict(yes=ids[0], no=ids[1])
                fs = m.get("feeSchedule") or {}
                if m.get("feesEnabled") and fs.get("rate") is not None:
                    rate = float(fs["rate"])
    return toks, rate


def poly_books(toks):
    def best(tok):
        b = get(f"{CLOB}/book?token_id={tok}")
        asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks") or [])
        bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids") or []), reverse=True)
        return (asks[0] if asks else None), (bids[0] if bids else None)
    raw = {side: dict(yes=best(t["yes"]), no=best(t["no"])) for side, t in toks.items()}
    out = {}
    for side, other in (("D", "R"), ("R", "D")):
        opts = []
        if raw[side]["yes"][0]:
            opts.append((raw[side]["yes"][0][0], raw[side]["yes"][0][1], f"YES {P_NAME[side]}"))
        if other in raw and raw[other]["no"][0]:
            opts.append((raw[other]["no"][0][0], raw[other]["no"][0][1], f"NO {P_NAME[other]}"))
        b = min(opts) if opts else None
        bid = raw[side]["yes"][1]
        out[side] = dict(bid=bid[0] if bid else None, ask=b[0] if b else None, ask_size=b[1] if b else None,
                         how=b[2] if b else None)
    return out


def fee(venue, p, kmult, prate):
    return 0.07 * kmult * p * (1 - p) if venue == "kalshi" else prate * p * (1 - p)


def r_(x, n=4):
    return None if x is None or (isinstance(x, float) and x != x) else round(x, n)


def project(units, st, model, L, w):
    if not st or not st["total"]:
        return None, None
    v1 = county_project.project(units, L, w, m0=(model, MODEL_SD))
    if not v1:
        return None, None
    implied = sd = None
    if v1["n_counties"]:
        s, se, ts = v1["count_shift"], v1["count_shift_se"], v1["two_share"]
        implied = county_project.shift_margin(L, w, s, ts)
        sd = (county_project.shift_margin(L, w, s + se, ts) - county_project.shift_margin(L, w, s - se, ts)) / 2
    return v1, (implied, sd)


def publish(state, data):
    path = os.path.join(state or HERE, "live.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(path + ".tmp", path)
    if not state:
        return
    g = lambda *a, check=True: subprocess.run(["git", "-C", state, *a], check=check, capture_output=True, text=True)
    g("add", "-A")
    msg = "live " + data["generated"]
    if g("rev-parse", "--verify", "HEAD", check=False).returncode == 0:
        g("commit", "--amend", "-q", "-m", msg)
    else:
        g("commit", "-q", "-m", msg)
    r = g("push", "-q", "--force", "origin", "HEAD:live", check=False)
    if r.returncode:
        print("push failed:", r.stderr.strip()[:300])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=340)
    ap.add_argument("--state", default=None)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    b, L, w = bench()
    model = b["model"]["d_margin"]
    kmult = kalshi_fee_mult()
    try:
        toks, prate = poly_setup()
    except Exception as e:
        print("polymarket setup:", e)
        toks, prate = {}, 0.0
    hist = []
    old = os.path.join(a.state or HERE, "live.json")
    if os.path.exists(old):
        try:
            hist = json.load(open(old, encoding="utf-8")).get("hist", [])
        except Exception:
            pass
    end = time.time() + 60 * a.minutes
    kal = poly = None
    count = dict(units={}, st=None, updated=None)
    errors = {}
    while True:
        t0 = time.time()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            u, st, upd = nyt_count()
            count = dict(units=u, st=st, updated=upd)
            errors.pop("nyt", None)
        except Exception as e:
            errors["nyt"] = f"{now} {e}"
        for name, fn in (("kalshi", kalshi_books), ("polymarket", lambda: poly_books(toks) if toks else None)):
            try:
                v = fn()
                if name == "kalshi":
                    kal = v
                else:
                    poly = v
                errors.pop(name, None)
            except Exception as e:
                errors[name] = f"{now} {e}"
        v1, imp = project(count["units"], count["st"], model, L, w)
        st = count["st"]
        p = v1["p_win"] if v1 else None
        trades = []
        for venue, bk in (("kalshi", kal), ("polymarket", poly)):
            for side in ("D", "R"):
                s = (bk or {}).get(side) or {}
                if p is None or not s.get("ask"):
                    continue
                prob = p if side == "D" else 1 - p
                f = fee(venue, s["ask"], kmult, prate)
                trades.append(dict(venue=venue, side=side, ask=s["ask"], fee=r_(f), edge=r_(prob - s["ask"] - f),
                                   how=s["how"], size=s["ask_size"]))
        best = max(trades, key=lambda x: x["edge"]) if trades else None
        pct = st["eevp"] if st else 0
        flip = bool(best and best["edge"] + best["ask"] >= FLIP_MIN_P and best["edge"] >= FLIP_MIN_EDGE
                    and best["ask"] <= FLIP_MAX_ASK and pct >= FLIP_MIN_PCT)
        margin = 100 * (st["d"] - st["r"]) / st["total"] if st and st["total"] else None
        kd = (kal or {}).get("D") or {}
        hist.append(dict(t=now, pct=r_(pct, 1), m=r_(margin, 2), mu=r_(v1["mu"], 2) if v1 else None,
                         sd=r_(v1["sd"], 2) if v1 else None, p=r_(p), imp=r_(imp[0], 2) if imp and imp[0] is not None else None,
                         kb=kd.get("bid"), ka=kd.get("ask"),
                         pm=r_(((poly or {}).get("D") or {}).get("ask"))))
        hist = hist[-900:]
        data = dict(generated=now, race="MS-Sen", d=b["model"]["d"], r=b["model"]["r"], model=dict(margin=model, sd=MODEL_SD),
                    nyt=dict(pct=r_(pct, 1), margin=r_(margin, 2), votes=st["total"] if st else 0, updated=count["updated"]),
                    v1=dict(mu=r_(v1["mu"], 2), sd=r_(v1["sd"], 2), p=r_(p), shift=r_(v1["shift"]),
                            counted=r_(v1["counted_pct"], 1), remaining=round(v1["remaining"]), n=v1["n_counties"]) if v1 else None,
                    implied=dict(mu=r_(imp[0], 2), sd=r_(imp[1], 2)) if imp and imp[0] is not None else None,
                    kalshi=kal, polymarket=poly, fees=dict(kalshi_mult=kmult, poly_rate=prate),
                    trades=trades, best=best, flip=flip, errors=errors, hist=hist)
        try:
            publish(a.state, data)
        except Exception as e:
            print("publish:", e)
        print(now, f"pct {pct} v1 {data['v1'] and data['v1']['mu']} p {p} kalshi D {kd.get('bid')}/{kd.get('ask')}",
              f"best {best and (best['venue'], best['side'], best['edge'])}", flush=True)
        if a.once or time.time() > end:
            break
        time.sleep(max(1, PASS - (time.time() - t0)))


if __name__ == "__main__":
    main()
