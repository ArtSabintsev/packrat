# ptcgl-redeem

Private CLI that batch-redeems **your** Pokémon TCG Live booster codes through the official site, [redeem.tcg.pokemon.com](https://redeem.tcg.pokemon.com/en-us/).

This is a personal tool. Automating that page may violate Pokémon's terms. It talks to the real redeem UI in a real browser (Brave/Chrome) — it does not bypass reCAPTCHA, Imperva, or login.

## Why this exists

[AidanHarveyNelson/pokemon_tcg_redeem](https://github.com/AidanHarveyNelson/pokemon_tcg_redeem) is a 2023 Selenium script. It does not work against the current site, and it is the wrong shape even if it did.

What is actually wrong with it:

| Problem | Reality |
|---|---|
| Dead since June 2023 | Site is now a Vite/React SPA behind Imperva + reCAPTCHA Enterprise. Login is Pokémon Trainer Central OAuth (`access.pokemon.com/oauth2`), not a local email form that stays put. |
| Password on argv | `pipenv run main.py <user> <pass>` leaks into shell history and `ps`. 2FA is not handled. |
| Fragile selectors | `RedeemModule_loadingWrapper__2mZ-x` is a CSS-module hash. It rotated. |
| Wrong code format | Samples are hyphenated PTCGO leftovers (`276-BLW2-ZVD-ZD6`). Current TCG Live pack codes are 13-character `[A-Z0-9]`. |
| No resume | 4,000 codes at 10 per redeem click is hours. A crash starts from zero. |
| No source of truth | A `codes.txt` in the repo. Codes belong in a sheet/CSV that is **not** git. |
| Bugs | Class name `PokeonTCGClient`. `--codes` typed as `list` (argparse eats characters). `input.text` on an `<input>` is always empty. Unbounded recursion on `StaleElementReferenceException`. |

The current site still verifies/redeems in chunks of 10. The backend is:

```
POST https://api.us-east-1.studio-prod.pokemon.com/commerce/v1/external/webccr/verify
POST https://api.us-east-1.studio-prod.pokemon.com/commerce/v1/external/webccr/redeem
Authorization: basic <a_token cookie>
```

reCAPTCHA Enterprise (`action: submit`) is attached client-side. Calling that API from a script without the browser token fails. So this tool drives a **real local browser** over CDP, types into `input#code`, clicks `[data-testid=verify-code-button]` / `[data-testid=button-redeem]`, and classifies the XHR JSON.

Headless Playwright Chromium gets scored as a bot. Use Brave or Chrome with a persistent profile.

## Setup

Needs macOS, Python 3.11+, [uv](https://docs.astral.sh/uv/), and **Brave** (preferred) or Google Chrome.

```bash
cd ptcgl-redeem
uv sync --extra dev
```

Export the [TCG Codes](https://docs.google.com/spreadsheets/d/1ATNNKYtzBdQQu2xyWXcoCPdM5DrNh3hJAFrVkE9CQyA) sheet as CSV (`File → Download → CSV`), then:

```bash
uv run ptcgl-redeem import ~/Downloads/TCG\ Codes.csv
uv run ptcgl-redeem status
```

The working copy lives at `~/.local/share/ptcgl-redeem/codes.csv`. It is outside the repo. Do not copy real codes into this tree.

## Usage

```bash
# Once: sign in (2FA included). Session sticks to ~/.local/state/ptcgl-redeem/browser-profile
uv run ptcgl-redeem login

# Smoke-test 5 codes without consuming them
uv run ptcgl-redeem run --dry-run --limit 5

# Redeem one set
uv run ptcgl-redeem run --set "Destined Rivals"

# Redeem everything still pending
uv run ptcgl-redeem run
```

Each code is written back to the CSV immediately (`Redeemed` / `Status` / `Detail`), so Ctrl-C is safe. Stdout prints only the last four characters of a code.

Stop conditions: reCAPTCHA block, fatal UI change, or an indeterminate redeem (verify succeeded, Redeem click did not confirm). Those last codes are **not** marked redeemed — check in-game before retrying.

At ~3 seconds per verify plus a redeem click every 10 codes, ~3,900 pending codes is on the order of 3–4 hours. Use `--set` / `--limit` and leave it running.

## CSV format

```
Code,Set,Batch,Date,Redeemed,Status,Detail
```

- Real codes: 12–16 alphanumeric, or the old `XXX-XXXX-XXX-XXX` form.
- Rows whose `Code` is a section header (`Email 1 — Destined Rivals (100/400)`) or blank are kept but ignored.
- `Redeemed=TRUE` or `Status` in `{success, redeemed, rejected}` is skipped.

## What this is not

- Not an in-game OCR clicker.
- Not a recaptcha solver.
- Not a public product. Private repo, personal codes, your account.
