<h1 align="center">packrat</h1>

<p align="center">
  <strong>Batch-redeem Pokémon TCG Live code cards, unattended.</strong><br>
  Feed it a spreadsheet of codes and walk away — it drives the macOS game client,
  reads each result off the screen, and keeps a durable record of what it redeemed.
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/platform-macOS-black">
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="tests" src="https://img.shields.io/badge/tests-59%20passing-brightgreen">
  <img alt="license" src="https://img.shields.io/badge/license-private-lightgrey">
</p>

---

## What it does

You have a pile of code cards from booster packs. Redeeming them by hand means
typing 13 characters, clicking, waiting, and repeating — for hours.

`packrat` does it for you:

```console
$ packrat run
3193 pending; batch=10
…VQPQJ  success            SCANNED
…HT4W   success            SCANNED
…KKKR2  success            SCANNED
collected (last stage: done)
```

It has redeemed **4,000 codes in a single unattended session** at roughly five
seconds each.

| | |
|---|---|
| **Resumable** | Progress is written after every batch. Kill it anytime; restart continues where it stopped. |
| **Never double-redeems** | Every code's outcome is recorded, and already-redeemed codes are detected and skipped. |
| **Self-healing** | Recovers from stray clicks, stuck reward pop-ups, and the client wandering to another screen. |
| **Fails loud** | An unrecognised message halts the run. A code is never marked redeemed on a guess. |
| **Codes stay private** | Masked in output by default; the real file lives outside the repo. |

## Why it drives the game and not the website

The official web redeemer is protected by **reCAPTCHA Enterprise**. It is
invisible and score-based — there is no puzzle to solve. Every code costs one
verification, the score decays with volume, and it eventually pins to the floor.
Measured on a real run:

| Hour (UTC) | Redeemed | Captcha rejections |
|---|---|---|
| 18:00 | 140 | 2 |
| 23:00 | 120 | 1 |
| **00:00** | **400** | **33** |
| 01:00 | 46 | 36 |
| 02:00 | **0** | 2 |

After ~440 verifications in one hour it stopped working entirely, and retrying
made it worse — each failed attempt is another signal that the client is a bot.

**The macOS game client has no reCAPTCHA on any redemption path.** That is the
whole reason this tool exists.

## Requirements

Install these before anything else:

| Requirement | Why | How |
|---|---|---|
| **macOS** | Uses Quartz, Vision, and CGEvent. Will not run anywhere else. | — |
| **Pokémon TCG Live** | The app being driven. Install it and sign in. | [pokemon.com/tcgl](https://www.pokemon.com/us/pokemon-trading-card-game-live) |
| **Python 3.11+** | — | `brew install python` |
| **uv** | Dependency and script runner. | `brew install uv` |

Python dependencies (`typer`, `rich`, and the `pyobjc` frameworks for Quartz,
Vision, Cocoa and ApplicationServices) are installed automatically by `uv sync`.

### Two macOS permissions — a human must grant these

Both apply to **the terminal you run `packrat` from**, not to the game. They
cannot be granted from the command line: macOS requires a person to click.

Open **System Settings → Privacy & Security**, then enable your terminal under:

1. **Screen & System Audio Recording** — lets `packrat` see the game window.
   **Quit and reopen the terminal afterwards**, or it will not take effect.
   Without this, screen capture silently returns nothing.
2. **Accessibility** — lets `packrat` click and type.

If your terminal is not listed, add it with the **+** button.

Check both at once — run this before your first redemption:

```console
$ packrat doctor
permissions
  screen_recording   GRANTED
  accessibility      GRANTED
client
  window             GRANTED  2560x1440 at (0,0)
  redeem screen      GRANTED  layout matches

Ready. Run: packrat run --limit 10
```

`doctor` exits non-zero until everything is ready, so it is safe to gate on.

## Install

```bash
git clone <your-remote> packrat && cd packrat
uv sync --extra dev
```

> [!NOTE]
> **Setting this up with an AI agent?** Everything except the two permissions
> above can be automated. Point the agent at this file and have it work through
> the checklist below, stopping at step 3 to ask you to click.
>
> 1. `uv sync --extra dev`
> 2. `uv run pytest` — 59 tests, no game required
> 3. **Human step:** grant the two permissions, restart the terminal
> 4. `packrat doctor` — repeat until it exits 0
> 5. `packrat import <csv>` then `packrat status`
> 6. **Human step:** open the game to Shop → Redeem, windowed on the active Space
> 7. `packrat run --limit 10`, confirm the CSV updated, then `packrat run`
>
> Every command is non-interactive and exits non-zero on failure. `packrat run`
> is safe to re-run: it skips anything already redeemed.

## Use it

**1 — Load your codes.** Export your spreadsheet as CSV. Only a `Code` column is
required; header rows and blank spacers are ignored.

```bash
packrat import ~/Downloads/codes.csv
packrat status
```

**2 — Open the game** to **Shop → Redeem**, windowed on the Space you are looking
at (not fullscreen on another desktop).

**3 — Redeem.**

```bash
packrat run --limit 10                 # try a few first
packrat run                            # everything pending
packrat run --set "Black Bolt"         # one set only
packrat run --log ~/Desktop/done.txt   # record full codes to a file
./run-until-done.sh                    # auto-resume across interruptions
```

> [!WARNING]
> **Your Mac is unusable while this runs.** `packrat` re-focuses the game before
> every code — about every five seconds. This is deliberate: clicks are sent to
> screen coordinates, so without it a click would land in whatever window you
> switched to. Stop anytime with `pkill -f 'packrat run'`; nothing is lost.

### CSV format

```csv
Code,Set,Batch,Date,Redeemed,Status,Detail
ABCDEFGHIJKLM,Black Bolt,1/400,2026-01-01,FALSE,,
```

`packrat` fills in `Redeemed`, `Status`, and `Detail`. Rows whose `Code` is not a
13-character `[A-Z0-9]` string are skipped, so spreadsheet section headers pass
through harmlessly.

## Commands

| Command | What it does |
|---|---|
| `packrat doctor` | Verify permissions and that the client is ready |
| `packrat import FILE` | Copy a spreadsheet export into the local working CSV |
| `packrat run` | Redeem pending codes |
| `packrat status` | Pending vs redeemed, broken down by set |
| `packrat scrub` | Replace stored codes with one-way hashes |
| `packrat where` | Print local data paths |

## How it works

The client is a Unity app. It renders its own interface and exposes **no
accessibility tree**, so there are no buttons to query — everything is done on
pixels.

```
   ┌──────────────┐   capture    ┌─────────────┐   OCR     ┌──────────────┐
   │ game window  │ ───────────► │  CGImage    │ ────────► │ text + boxes │
   └──────────────┘  CGWindowList└─────────────┘  Vision   └──────┬───────┘
          ▲                                                       │
          │                  click / paste                        │
          └────────────────── CGEvent ◄──────────────── locate targets
```

For each code: clear the field, paste, submit, re-capture, read the status
label, classify. Then every ten codes, collect the rewards.

Codes are written back as redeemed only **after** collection succeeds, because
collection is what finalises a redemption. If it fails, the whole batch stays
unmarked and is retried rather than optimistically recorded.

### What the client tells us

| On-screen status | Meaning | Recorded as |
|---|---|---|
| `SCANNED` | Redeemed just now | `success` ✅ |
| `YOU HAVE ALREADY REDEEMED THAT CODE.` | Account already had it | `already_redeemed` ✅ |
| `THAT CODE IS NOT VALID.` | Bad or mistyped code | `invalid` |
| `ALREADY IN THE LIST` | Duplicate, still uncollected | `in_list` |

Anything else stops the run. If a game update reworded a message, `packrat`
halts rather than silently marking codes redeemed.

## Your codes stay yours

Redemption codes are bearer tokens — anyone who reads one can redeem it.

- The working CSV lives in `~/.local/share/packrat/`, **never** in the repo.
- `.gitignore` excludes `*.csv`, `*.txt` and `*.log`, allowing only the
  fabricated `codes.example.csv`.
- Console output and run journals **mask codes** (`…JKLM`) unless you pass
  `--print-codes`.
- `--log` is the one place real codes are written, and only to a path you name.

### Scrub when you are done

While codes are pending the CSV must hold them in the clear — it is the work
queue. Once everything is redeemed, that plaintext is worth stealing and worth
nothing to you:

```console
$ packrat scrub
Will hash 4000 codes in ~/.local/share/packrat/codes.csv
and rewrite 7 run journals.
This cannot be undone.
Continue? [y/N]: y
Scrubbed 4000 codes and 3194 journal entries.
```

Codes become SHA-256 fingerprints. Set, status and counts survive — `packrat
status` reads exactly as before — but nothing redeemable is left on disk. You
can still recognise a code later by hashing it again:

```bash
printf '%s' 'YOURCODEHERE' | shasum -a 256
```

`scrub` refuses to run while any code is still pending, so it cannot destroy a
live queue.

## Development

```bash
uv run pytest        # 59 tests
uv run ruff check .
```

Tests cover code parsing, the CSV store, and the driver's pure logic — status
classification and the screen geometry — by injecting OCR boxes, so they need
neither the game nor a screen capture. macOS-only tests skip automatically
elsewhere.

## Caveats

- Automating the client may conflict with Pokémon's terms of service. This
  redeems codes you already own; it does not obtain, generate, or guess codes,
  and it bypasses no authentication or anti-bot control.
- Each product has a soft redemption cap (around 400). Beyond it, codes return a
  small amount of in-game currency instead of a pack.
- Coordinates are calibrated against a 2560×1440 client and scale to other sizes,
  but a substantial UI redesign would need recalibrating. `packrat` fails loudly
  if the layout does not match.
