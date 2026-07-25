#!/usr/bin/env python3
"""
usage-bar brain — prints a menu-bar title + dropdown describing how close you
are to your Codex and Claude usage limits. The Swift shell renders this output;
`osascript` fires notifications when an enforced limit crosses a threshold.

Everything is read from your own machine; nothing leaves it.

Data sources
  Codex weekly : exact — newest ~/.codex/sessions/.../rollout-*.jsonl
                 `token_count` event, which Codex writes with real rate_limits.
  Codex 5h     : estimate — reconstructed from rollout token deltas (trailing 5h
                 vs your busiest 5h), since Codex no longer reports a 5h window.
  Claude 5h    : estimate — via `ccusage`: the active 5h block vs your peak block.

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
    h, m = secs // 3600, (secs % 3600) // 60
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


# ---- Codex 5h (estimate) --------------------------------------------------
# Codex now only reports a weekly window (secondary/5h comes back null), so we
# reconstruct the 5h burn ourselves: sum per-turn token deltas from the rollout
# logs over the trailing 5h, expressed as a % of your peak 5h bucket.
def codex_5h_estimate(window_secs=5 * 3600, days=10):
    roots = os.path.expanduser("~/.codex/sessions")
    files = glob.glob(os.path.join(roots, "**", "rollout-*.jsonl"), recursive=True)
    now = time.time()
    cutoff = now - days * 86400
    events = []  # (epoch, delta_tokens)
    for path in files:
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        try:
            with open(path, "r", errors="replace") as fh:
                for line in fh:
                    if '"token_count"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = d.get("payload") or {}
                    if payload.get("type") != "token_count":
                        continue
                    ep = _iso_epoch(d.get("timestamp"))
                    if ep is None or ep < cutoff:
                        continue
                    last = (payload.get("info") or {}).get("last_token_usage") or {}
                    events.append((ep, last.get("total_tokens") or 0))
        except OSError:
            continue
    if not events:
        return None
    current = sum(dl for ep, dl in events if ep >= now - window_secs)
    buckets = {}
    for ep, dl in events:
        buckets.setdefault(int(ep // window_secs), 0)
        buckets[int(ep // window_secs)] += dl
    cur_b = int(now // window_secs)
    past = [v for b, v in buckets.items() if b != cur_b]
    cap = max(max(past, default=0), 1)
    return {"pct": round(current / cap * 100)}


# ---- Claude (estimate via ccusage) ----------------------------------------
def read_claude():
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

    # ---- Codex windows: exact weekly, plus 5h (exact if Codex reports it,
    #      otherwise our own trailing-5h estimate) ----
    cdx_windows = list(cdx.get("windows", []))
    if not any(w.get("label") == "5h" for w in cdx_windows):
        est = codex_5h_estimate()
        if est is not None:
            cdx_windows.append({"label": "5h", "pct": est["pct"],
                                "resets": None, "est": True})
    cdx_windows.sort(key=lambda w: 0 if w["label"] == "5h" else 1)  # 5h first

    claude_ok = cld.get("ok") and not cld.get("empty")

    # ---- notifications: only ENFORCED limits alert (exact Codex windows +
    #      Claude 5h). The estimates are display-only, so no false alarms. ----
    notify_items = [(f"codex-{w['label']}", f"Codex {disp(w['label'])}", w["pct"])
                    for w in cdx_windows if not w.get("est")]
    if claude_ok:
        notify_items.append(("claude-5h", "Claude 5h", cld["pct"]))
    maybe_notify(notify_items)

    # ---- menu-bar title: each tool's 5h window (fast, session-level, and
    #      consistent). COLOR reflects the worst of ALL windows so a low weekly
    #      still turns the dot orange/red at a glance. ----
    def window_pct(windows, label):
        return next((w["pct"] for w in windows if w["label"] == label), None)

    cdx_5h = window_pct(cdx_windows, "5h")
    cdx_pct = cdx_5h if cdx_5h is not None else window_pct(cdx_windows, "wk")
    cdx_worst = max([w["pct"] for w in cdx_windows], default=None)
    cld_pct = cld["pct"] if claude_ok else None   # 0 == idle (window full)

    def left(pct):                       # battery-style: how much headroom remains
        return max(0, 100 - pct)

    # Menu-bar: a clean status dot PER TOOL, coloured by that tool's worst window
    # (Swift tints each "●" from the `dots=` param), then the % left. Grey = no data.
    def dot_hex(pct):
        return "#9aa0a6" if pct is None else color_for(pct)

    def seg(name, num_pct):
        return f"● {name}:{'—' if num_pct is None else left(num_pct)}"

    dots = f"{dot_hex(cdx_worst)},{dot_hex(cld_pct)}"
    print(f"{seg('Cdx', cdx_pct)}  {seg('Cld', cld_pct)} | dots={dots}")
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
        tail = ("idle — 5h window full" if cld.get("idle")
                else (fmt_reset(cld.get("resets")) or "trailing 5h"))
        print(wrow(cld["pct"], "5h", True, tail))
        exp = cld.get("expected_pct")
        if exp is not None:
            print(f"  you usually use ~{exp}% of a 5h block | color=#888 size=11")
        else:
            print("  estimate, vs your peak 5h block | color=#888 size=11")
    else:
        print("  ccusage unavailable (npm i -g ccusage) | color=#888 size=11")

    print("---")
    print(f"warn at {100 - WARN}% left   ·   alert at {100 - ALERT}% left | color=#888 size=11")


if __name__ == "__main__":
    main()
