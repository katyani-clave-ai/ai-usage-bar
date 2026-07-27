# usage-bar

A tiny macOS **menu-bar indicator for your Codex and Claude usage limits** — so you can glance up and see how much you have left instead of hunting through each tool. It reads your own local usage files, colors green → orange → red as you approach a limit, and pops a notification before you hit the wall.

```
● Cdx:63   ● Cld:30        ← always visible: % LEFT, one tinted dot per tool
──────────────
Codex · prolite
  ▰▰▰▱▱  5h ~     63% left   ·  resets in 46m
  ▰▰▰▰▰  Weekly   96% left   ·  resets in 5d 6h
Claude
  ▰▰▱▱▱  5h       30% left   ·  resets in 42m
  ▰▰▱▱▱  Weekly   44% left   ·  resets in 1d 20h
  ▱▱▱▱▱  Fable     0% left   ·  maxed · resets in 1d 20h
──────────────
warn at 20% left   ·   alert at 10% left
```

The menu bar shows a small status dot per tool (green → amber → red) with the % left; the dropdown breaks each tool into its windows with a 5-cell meter.

## What it shows, and how much to trust each number

Numbers are shown as **% left** (like a battery — high/green is good).

| Window | Source | Accuracy |
|---|---|---|
| **Codex Weekly** | exact, straight from Codex's own `rate_limits` in its local session logs | 💯 exact |
| **Codex 5h** `~` | reconstructed from your rollout token logs (Codex stopped reporting a 5h window) | estimate |
| **Claude 5h / Weekly** | exact, from Claude's own usage API (the same data Claude Code's `/usage` screen shows) | 💯 exact |
| **Claude per-model** (e.g. Fable) | exact, from that same API — shows when a specific model's weekly limit is maxed while others still have room | 💯 exact |

`~` marks the one estimate (Codex 5h). Warnings fire at 20% left, critical at 10% left, for every enforced limit (Codex windows, Claude 5h/weekly, and any maxed per-model limit).

**Where the numbers come from.** Codex writes its real `used_percent` into its session logs, so those are read straight off disk. Claude is read from the OAuth usage endpoint (`/api/oauth/usage`) — the exact source Claude Code's own `/usage` screen uses — via the token Claude Code already stores in your macOS Keychain. That request goes only to your own Anthropic account; nothing is sent anywhere else. If the endpoint is unavailable, it falls back to a rougher `ccusage` estimate (marked `~`).

## Requirements

- **macOS** with **Xcode Command Line Tools** (`xcode-select --install`) for `swiftc`.
- **[Codex CLI](https://developers.openai.com/codex/cli)** — recent enough that it writes `rate_limits` into its session logs (older versions show blank Codex numbers).
- **[Claude Code](https://www.anthropic.com/claude-code)** — signed in. Its stored OAuth token (macOS Keychain) is read (read-only) to fetch your usage. Optional: **[ccusage](https://github.com/ryoppippi/ccusage)** (`npm i -g ccusage`) as a fallback if the usage endpoint is unavailable.

It uses whatever Codex/Claude you already have installed. No accounts or config to set up.

## Install

```sh
git clone https://github.com/<you>/ai-usage-bar.git
cd ai-usage-bar
./install.sh
```

The installer detects `ccusage`/`node`, compiles the app to `~/.local/bin/usage-bar`, installs a LaunchAgent (so it runs at login and restarts itself), and starts it. Look top-right for `🟢 Cdx:… Cld:…`. The first notification may ask for permission.

> **Note on the notch:** the title is compact so it survives a crowded menu bar, but if you have a lot of menu-bar apps you may want a manager like [Ice](https://github.com/jordanbaird/Ice) to pin it.

## Uninstall

```sh
./uninstall.sh
```

Removes the app, LaunchAgent, and state. Leaves `ccusage` in place.

## Configuration

Thresholds live at the top of `src/usage_brain.py`:

```python
WARN  = 80   # % used -> orange + first notification (20% left)
ALERT = 90   # % used -> red + louder notification  (10% left)
```

Edit, then re-run `./install.sh` (or copy the file to `~/.local/share/usage-bar/` and `launchctl kickstart -k gui/$(id -u)/com.usage-bar.agent`).

## How it works

A ~60-line Swift shell (`NSStatusItem`, no Dock icon) runs a Python "brain" every 60s and renders its output. The brain:

- reads Codex's newest `~/.codex/sessions/**/rollout-*.jsonl` for the exact weekly `rate_limits`, and reconstructs a trailing-5h estimate from per-turn token deltas;
- calls Claude's `/api/oauth/usage` endpoint (token from the Keychain, cached ~2 min) for exact 5h, weekly, and per-model limits, falling back to `ccusage` if that fails;
- fires notifications via `osascript` only when an enforced limit crosses a threshold (once per crossing).

## Limitations

- The **Codex 5h** number is an **estimate** (calibrated against your own busiest 5h) — a "how close am I, roughly" signal. Everything else is exact.
- The Claude usage endpoint is **undocumented** — it's the one Claude Code itself uses, but Anthropic could change it, at which point the app falls back to the `ccusage` estimate.
- Depends on Codex/Claude local formats and that endpoint; a future change to either could require an update.
- Because notifications fire through `osascript`, the banner's sender label reads as *Script Editor* rather than *usage-bar* (avoiding that needs a signed `.app` bundle).

## License

MIT — see [LICENSE](LICENSE).
