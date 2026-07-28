#!/usr/bin/env python3
"""
usage-bar brain — prints a menu-bar title + dropdown describing how close you
are to your Codex and Claude usage limits. The Swift shell renders this output;
`osascript` fires notifications when an enforced limit crosses a threshold.

Everything is read from your own machine; nothing leaves it.

Data sources
  Codex weekly    : exact — newest ~/.codex/sessions/.../rollout-*.jsonl
                    `token_count` event, which Codex writes with real rate_limits.
  Codex 5h        : estimate — reconstructed from rollout token deltas (trailing
                    5h vs your busiest 5h), since Codex no longer reports a 5h.
  Claude 5h/weekly: exact — the OAuth /usage endpoint (same data Claude Code's
                    /usage screen shows), including per-model limits. Falls back
                    to a `ccusage` estimate if the endpoint is unavailable.

Percentages are shown as "% left" (battery-style). Estimates are marked "~".
Thresholds: WARN at USED >= 80% (<=20% left), ALERT at USED >= 90% (<=10% left).
"""
import calendar
import glob
import json
import os
import shutil
import subprocess
import time
import urllib.request

# ---- config ---------------------------------------------------------------
WARN = 80          # % used -> orange + first notification (20% left)
ALERT = 90         # % used -> red + louder notification (10% left)
APP = "usage-bar"
# ccusage path: preferred from the LaunchAgent (install.sh resolves it at install
# time, when your full shell PATH is available), else search PATH, else by name.
CCUSAGE = (os.environ.get("USAGE_BAR_CCUSAGE")
           or shutil.which("ccusage")
           or "ccusage")
STATE = os.path.expanduser(f"~/.local/state/{APP}/notify-state.json")

# ---------------------------------------------------------------------------


def level(pct):
    if pct is None:
        return 0
    if pct >= ALERT:
        return 2
    if pct >= WARN:
        return 1
    return 0


def color_for(pct):
    # tuned for contrast: vivid green, amber, softer red (reads on light + dark)
    return {2: "#EA5455", 1: "#FF9F43", 0: "#28C76F"}[level(pct)]


def fmt_reset(epoch):
    if not epoch:
        return ""
    secs = int(epoch - time.time())
    if secs <= 0:
        return "resets now"
    d, rem = divmod(secs, 86400)
    h, m = rem // 3600, (rem % 3600) // 60
    if d:
        return f"resets in {d}d {h}h"
    return f"resets in {h}h{m:02d}m" if h else f"resets in {m}m"


# ---- Codex weekly (exact) -------------------------------------------------
def read_codex():
    roots = os.path.expanduser("~/.codex/sessions")
    files = glob.glob(os.path.join(roots, "**", "rollout-*.jsonl"), recursive=True)
    if not files:
        return {"ok": False, "windows": []}
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    for path in files[:8]:  # newest first; stop at first file with a snapshot
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 200_000))  # last ~200KB is plenty
                lines = fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            rl = obj.get("rate_limits") or obj.get("payload", {}).get("rate_limits")
            if not rl or not rl.get("primary"):
                continue
            windows = []
            for key, label in (("secondary", "5h"), ("primary", "wk")):
                w = rl.get(key)
                if not w:
                    continue
                wm = w.get("window_minutes") or 0
                lbl = "5h" if wm and wm <= 360 else ("wk" if wm else label)
                windows.append({
                    "label": lbl,
                    "pct": round(w.get("used_percent") or 0.0),
                    "resets": w.get("resets_at"),
                })
            return {"ok": True, "plan": rl.get("plan_type"), "windows": windows}
    return {"ok": False, "windows": []}


def _iso_epoch(s):
    """ISO-8601 UTC string (e.g. 2026-07-24T00:49:00.344Z) -> epoch seconds."""
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


# ---- Claude (exact via the OAuth usage endpoint; ccusage as fallback) ------
USAGE_CACHE = os.path.expanduser(f"~/.local/state/{APP}/usage-api.json")


def _claude_token():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5).stdout
        d = json.loads(out)
        return (d.get("claudeAiOauth") or d).get("accessToken")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def read_claude_api():
    """Exact usage from the same endpoint Claude Code's /usage screen reads.
    Cached ~2 min. On a failed refresh it keeps serving the last good response
    for up to 20 min (so a token/network blip doesn't drop us to the estimate
    and silently lose per-model limits). Returns None only with no usable data."""
    cached, mtime = None, 0
    try:
        cached = json.load(open(USAGE_CACHE))
        mtime = os.path.getmtime(USAGE_CACHE)
    except (OSError, ValueError):
        cached = None
    age = (time.time() - mtime) if cached is not None else None
    data = cached if (cached is not None and age < 110) else None
    if data is None:
        fetched = None
        tok = _claude_token()
        if tok:
            req = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage",
                headers={"Authorization": f"Bearer {tok}",
                         "anthropic-beta": "oauth-2025-04-20",
                         "Content-Type": "application/json",
                         "User-Agent": "usage-bar (oauth, cli)"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    fetched = json.loads(r.read().decode())
                os.makedirs(os.path.dirname(USAGE_CACHE), exist_ok=True)
                json.dump(fetched, open(USAGE_CACHE, "w"))
            except Exception:
                fetched = None
        if fetched is not None:
            data = fetched
        elif cached is not None and age < 1200:   # sticky: last good, <20 min old
            data = cached
        else:
            return None
    windows = []
    for key, label in (("five_hour", "5h"), ("seven_day", "wk")):
        b = data.get(key) or {}
        if b.get("utilization") is not None:
            windows.append({"label": label, "pct": round(b["utilization"]),
                            "resets": _iso_epoch(b.get("resets_at"))})
    scoped = []
    for lim in data.get("limits") or []:
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if model and lim.get("group") == "weekly":
            scoped.append({"name": model, "pct": round(lim.get("percent") or 0),
                           "resets": _iso_epoch(lim.get("resets_at")),
                           "critical": lim.get("severity") == "critical"})
    return {"ok": True, "exact": True, "windows": windows, "scoped": scoped} if windows else None


def read_claude():
    api = read_claude_api()
    if api is not None:
        return api
    c = read_claude_ccusage()
    if not c.get("ok"):
        return {"ok": False}
    return {"ok": True, "exact": False, "scoped": [],
            "expected_pct": c.get("expected_pct"), "idle": c.get("idle", False),
            "windows": [{"label": "5h", "pct": c.get("pct", 0), "resets": c.get("resets")}]}


# ---- Claude usage estimate via ccusage (fallback) --------------------------
def read_claude_ccusage():
    try:
        # launchd gives us a minimal PATH; ccusage is a node script and needs
        # node on PATH, so inject the ccusage/node bin dir.
        env = dict(os.environ)
        ccusage_dir = os.path.dirname(CCUSAGE)
        if ccusage_dir:
            env["PATH"] = ccusage_dir + ":" + env.get("PATH", "")
        out = subprocess.run(
            [CCUSAGE, "blocks", "--json"],
            capture_output=True, text=True, timeout=25, env=env,
        ).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"ok": False}
    blocks = data.get("blocks", [])
    now = time.time()
    real = [b for b in blocks if not b.get("isGap")]
    if not real:                              # Claude present but unused -> full
        return {"ok": True, "idle": True, "pct": 0,
                "expected_pct": None, "resets": None, "models": []}
    active = next((b for b in real if b.get("isActive")), None)

    # --- 5h window: active block vs historical peak block. When IDLE (no active
    #     block) you've used ~none of the window, so it reads as full (0% used). ---
    past = [b.get("totalTokens", 0) for b in real if not b.get("isActive")]
    cap = max(max(past, default=0), (active or {}).get("totalTokens", 0), 1)
    now_tok = active.get("totalTokens", 0) if active else 0
    end_epoch = _iso_epoch(active.get("endTime")) if active else None

    # --- "your usual": average 5h-block utilisation over the last 7 days ---
    recent = [b.get("totalTokens", 0) for b in real
              if not b.get("isActive")
              and (_iso_epoch(b.get("startTime")) or 0) >= now - 7 * 86400]
    expected_pct = round(sum(recent) / len(recent) / cap * 100) if recent else None

    return {
        "ok": True,
        "idle": active is None,
        "pct": round(now_tok / cap * 100),
        "expected_pct": expected_pct,
        "resets": end_epoch,
        "models": active.get("models", []) if active else [],
    }


# ---- notifications --------------------------------------------------------
def notify(title, msg, loud):
    sound = "Sosumi" if loud else "Ping"
    script = f'display notification "{msg}" with title "{title}" sound name "{sound}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def maybe_notify(items):
    # items: list of (key, pretty_name, pct). Fire only when a window crosses UP
    # into a new band, so you get one banner per threshold, not one per minute.
    try:
        prev = json.load(open(STATE))
    except (OSError, ValueError):
        prev = {}
    new = {}
    for key, name, pct in items:
        lv = level(pct)
        new[key] = lv
        if lv > prev.get(key, 0):
            if lv == 2:
                notify("⚠️ Usage limit critical", f"{name}: {max(0, 100 - pct)}% left", True)
            elif lv == 1:
                notify("Usage limit warning", f"{name}: {max(0, 100 - pct)}% left", False)
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(new, open(STATE, "w"))
    except OSError:
        pass


# ---- render ---------------------------------------------------------------
def main():
    try:  # heartbeat so the installer/app can confirm the brain is running
        hb = os.path.expanduser(f"~/.local/state/{APP}/heartbeat")
        os.makedirs(os.path.dirname(hb), exist_ok=True)
        open(hb, "w").write(str(int(time.time())))
    except OSError:
        pass
    cdx = read_codex()
    cld = read_claude()

    def ring(pct):   # 5-cell meter of remaining headroom; Swift tints the cells
        filled = min(5, max(0, round(max(0, 100 - pct) / 100 * 5)))
        return "▰" * filled + "▱" * (5 - filled)

    def disp(label):
        return "Weekly" if label == "wk" else "5h"

    # ---- Codex windows: only what Codex actually enforces (weekly; plus a real
    #      5h only if Codex reports one). We no longer fabricate a 5h estimate —
    #      Codex is weekly-only for most plans now, so a fake 5h would mislead. ----
    cdx_windows = list(cdx.get("windows", []))
    cdx_windows.sort(key=lambda w: 0 if w["label"] == "5h" else 1)  # 5h first if any

    claude_ok = bool(cld.get("ok") and cld.get("windows"))
    cld_windows = cld.get("windows", []) if claude_ok else []
    cld_scoped = cld.get("scoped", []) if claude_ok else []
    cld_exact = cld.get("exact", False)

    # ---- notifications: enforced Codex windows + all exact Claude limits
    #      (5h, weekly, and per-model scoped like Fable). Rough estimates don't. ----
    notify_items = [(f"codex-{w['label']}", f"Codex {disp(w['label'])}", w["pct"])
                    for w in cdx_windows if not w.get("est")]
    if claude_ok:
        for w in cld_windows:
            if cld_exact:
                notify_items.append((f"claude-{w['label']}", f"Claude {disp(w['label'])}", w["pct"]))
        if not cld_exact:
            notify_items.append(("claude-5h", "Claude 5h", cld_windows[0]["pct"]))
        for s in cld_scoped:
            notify_items.append((f"claude-{s['name']}", f"Claude {s['name']}", s["pct"]))
    maybe_notify(notify_items)

    def left(pct):                       # battery-style: how much headroom remains
        return max(0, 100 - pct)

    # ---- menu-bar title: per tool, show the TIGHTEST window (least headroom) with
    #      a cadence tag (5h / wk), so a weekly number is never mistaken for a
    #      fast-resetting one. Per-model scoped limits show only in the dropdown. ----
    def tightest(windows):
        return max(windows, key=lambda w: w["pct"], default=None)

    def tag(label):
        return "wk" if label == "wk" else "5h"

    def dot_hex(pct):
        return "#9aa0a6" if pct is None else color_for(pct)

    def seg(name, w):
        return f"● {name}:—" if w is None else f"● {name}:{left(w['pct'])} {tag(w['label'])}"

    cdx_t = tightest(cdx_windows)
    # Claude menu-bar = its 5h (hourly) window; weekly + per-model are in the
    # dropdown (weekly still notifies). Codex has no 5h, so it stays weekly.
    cld_t = next((w for w in cld_windows if w["label"] == "5h"), None) or tightest(cld_windows)
    dots = f"{dot_hex(cdx_t['pct'] if cdx_t else None)},{dot_hex(cld_t['pct'] if cld_t else None)}"
    print(f"{seg('Cdx', cdx_t)}  {seg('Cld', cld_t)} | dots={dots}")
    print("---")

    def wrow(pct, label, est, tail):     # dropdown data row
        return (f"  {ring(pct)}  {label}{' ~' if est else ''}   {left(pct)}% left   ·   {tail}"
                f" | ring={color_for(pct)}")

    # Codex section
    plan = f" · {cdx.get('plan')}" if cdx.get("plan") else ""
    print(f"Codex{plan} |")
    if cdx_windows:
        for w in cdx_windows:
            tail = (fmt_reset(w["resets"]) if w.get("resets")
                    else ("trailing 5h" if w["label"] == "5h" else "trailing 7d"))
            print(wrow(w["pct"], disp(w["label"]), w.get("est", False), tail))
    else:
        print("  no rollout data yet — run codex once | color=#888 size=11")

    print("---")
    # Claude section
    print("Claude |")
    if claude_ok:
        for w in cld_windows:
            if cld_exact:
                tail = fmt_reset(w["resets"])
            elif cld.get("idle"):
                tail = "idle — 5h window full"
            else:
                tail = fmt_reset(w["resets"]) or "trailing 5h"
            print(wrow(w["pct"], disp(w["label"]), not cld_exact, tail))
        for s in cld_scoped:
            r = fmt_reset(s["resets"])
            crit = s.get("critical") or left(s["pct"]) == 0
            tail = (f"maxed · {r}" if r else "maxed") if crit else r
            print(wrow(s["pct"], s["name"], False, tail))
        if not cld_exact:
            exp = cld.get("expected_pct")
            note = (f"you usually use ~{exp}% of a 5h block" if exp is not None
                    else "estimate, vs your peak 5h block")
            print(f"  {note} | color=#888 size=11")
    else:
        print("  Claude usage unavailable | color=#888 size=11")

    print("---")
    print(f"warn at {100 - WARN}% left   ·   alert at {100 - ALERT}% left | color=#888 size=11")


if __name__ == "__main__":
    main()
