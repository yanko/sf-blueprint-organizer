# sf-blueprint-organizer

Downloads all blueprints by an author from
[satisfactoryblueprints.com](https://satisfactoryblueprints.com) or
[satisfactory-calculator.com](https://satisfactory-calculator.com/en/blueprints) and
files them into an in-game blueprint category (the author's name) with
subcategories, by editing a **copy** of your save.

- **download** an author's whole blueprint list, with each page's description and tags
- **suggest** a grouping into subcategories with an AI (any OpenAI-compatible API, or
  by hand through any AI chat)
- **plan** to preview the grouping, **apply** to write it into a copy of your save,
  verified, plus the blueprint files staged for your game or dedicated server

Save editing uses [GreyHak/sat_sav_parse](https://github.com/GreyHak/sat_sav_parse),
which is fetched into `vendor/` on the first `apply`.

> **Game version:** the save parser supports **Satisfactory 1.2.x**. A game update
> can change the save format and break `apply` until the parser is updated. `apply`
> verifies the save it writes and never touches your original, but keep a backup
> before loading an edited save.

## Requirements
- Python 3.11 or newer
- git (used to fetch the save parser on the first `apply`)

## Setup (one time)
```
git clone https://github.com/yanko/sf-blueprint-organizer.git
cd sf-blueprint-organizer
```
Windows:
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
macOS / Linux:
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Activate the venv in each new terminal before running the tool. When you're done
with the project, `deactivate` and delete `.venv`; nothing is installed system-wide.
(Inside an activated venv, `python` works on every OS.)

## Quick start
```
python organize.py download scorpi            # or an author page URL, see below
python organize.py suggest  scorpi            # optional: an AI writes rules/scorpi.toml
python organize.py plan     scorpi            # preview; edit the rules file and repeat
python organize.py apply    scorpi --save "C:\path\My Session_autosave_0_continue.sav"
```
Every command has `-h` for all its options, e.g. `python organize.py apply -h`.

## Commands

### `download AUTHOR_OR_URL`
| site | what to pass |
|---|---|
| satisfactoryblueprints.com | the author name (`scorpi`) or their page `https://satisfactoryblueprints.com/author/scorpi` |
| satisfactory-calculator.com | the author's blueprint page, e.g. `https://satisfactory-calculator.com/en/blueprints/index/index/idU/101521/user/Legitim` (click the author's name on any of their blueprints; any language prefix works) |

The author's name becomes the folder `downloads/<author>/` and the in-game
category, and is what you pass to `suggest`, `plan` and `apply`.

| option | effect |
|---|---|
| `--as "Some Name"` | file the blueprints under another name/category (useful if two sites have authors with the same name) |
| `--include-modded` | also download blueprints that need mods. By default they're skipped (satisfactory-calculator.com's "Modded" category, satisfactoryblueprints.com's "Modded Content: Yes") |
| `--delay SECONDS` | pause between requests (default 0.5) |

Each blueprint folder also gets a `meta.json`: description, tags/categories and,
from satisfactory-calculator.com, the list of buildings used. `suggest` sends this
to the AI. Re-running `download` refreshes `meta.json` and only fetches missing
files, so it's cheap. It never deletes anything: blueprints you downloaded earlier
with `--include-modded` stay in the folder (and get filed) until you delete them.

### `suggest AUTHOR`
Sends the blueprint names plus their description, tags and building list to an AI
and writes `rules/<author>.toml`. Review and edit it before `apply`; it isn't
overwritten unless you pass `--force`. If you downloaded before `meta.json`
existed, re-run `download` first to fetch it.

The AI's answer is checked before anything is written: names it made up are
dropped, repeats are ignored, and any blueprint it left out goes to `Other`.

**With an API (any OpenAI-compatible provider).** Defaults live in
[ai.toml](ai.toml): OpenRouter with `anthropic/claude-opus-5.5`. Keys are read
from an environment variable, never from a file:
```
$env:OPENROUTER_API_KEY="sk-or-..."     (Windows PowerShell)
set OPENROUTER_API_KEY=sk-or-...        (Windows cmd)
export OPENROUTER_API_KEY="sk-or-..."   (macOS / Linux: bash, zsh)
python organize.py suggest scorpi
```
These last for the current terminal only. Use the variable name for your provider
(`OPENAI_API_KEY`, or your `--api-key-env` name) in the same way. Override per run:
```
python organize.py suggest scorpi --model openai/gpt-6.1-sol          # another OpenRouter model
python organize.py suggest scorpi --provider openai --model <id>      # OPENAI_API_KEY
python organize.py suggest scorpi --provider ollama --model <id>      # local, no key
python organize.py suggest scorpi --provider custom --base-url https://host/v1 --api-key-env MY_KEY --model <id>
```
| provider     | key variable         | endpoint                       |
|--------------|----------------------|--------------------------------|
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| `openai`     | `OPENAI_API_KEY`     | `https://api.openai.com/v1`    |
| `ollama`     | none                 | `http://localhost:11434/v1`    |
| `custom`     | `--api-key-env`      | `--base-url`                   |

It asks for JSON-schema output first and falls back to plain JSON for models that
don't support it; if the answer is malformed it asks once more. That's usually one
request (at most four). Cost scales with the number of blueprints: 74 blueprints
was about 16k input tokens, roughly $0.10 with Claude Opus on OpenRouter.

**By hand (any AI chat, no key):**
```
python organize.py suggest scorpi --prompt-only            # writes out/suggest_prompt_scorpi.md
# paste that file into any AI chat, save its JSON reply as reply.json
python organize.py suggest scorpi --from-file reply.json
```
The reply may be bare JSON or JSON inside a ``` code block.

### `plan AUTHOR`
Prints the grouping `apply` would use and writes `out/plan.json`. Changes nothing
else. Blueprints that match no rule are listed so you can add them.

### `apply AUTHOR --save PATH`
Adds the category to a copy of your save, written to `out/<same file name>`, then
verifies it: every new blueprint is present and every other category is unchanged.
It also stages the blueprint files flat in `out/blueprints/`. Your original save is
never touched.

| option | effect |
|---|---|
| `--icon N` | category icon id (default: `icon` in the rules file) |
| `--same-time` | keep the save's timestamp (by default one second is added so it sorts as newest) |
| `--dedupe` | also remove these blueprints from any other category that lists them (run it from the original save; each removal takes ~20 s) |

Without `--dedupe`, blueprints also listed in another category (e.g. your own) are
left there and reported. Re-running `apply` on a save that already has the
category only adds what's missing.

Then, manually (example for a dedicated server):
1. Copy `out/blueprints/*` into the server's `blueprints/<session name>/` folder.
   The folder name must match the session name exactly.
2. Stop the server.
3. Back up the original save, then copy `out/<save>.sav` into `SaveGames/server/`.
4. Start the server and load the save.

For single-player, the blueprint folder is
`%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\blueprints\<session name>\`.

**Adding a second author:** pass the save that the first `apply` wrote as `--save`,
so both categories end up in one save. Copy it out of `out/` first, since every
`apply` rewrites that folder:
```
copy "out\My Session_autosave_0_continue.sav" after_first.sav      (Windows)
cp "out/My Session_autosave_0_continue.sav" after_first.sav        (macOS / Linux)
python organize.py apply Legitim --save after_first.sav
```

## Grouping rules
`plan` and `apply` use `rules/<author>.toml` if it exists, otherwise `rules.toml`
(written for mostly_useless's "MK3 - <Product> xPM" titles).
```toml
[settings]
icon = 351            # category icon id
fallback = "Other"    # subcategory for blueprints that match nothing

[subcategories]
"Frames & Plating" = ["Smart Plating", "Modular Frame"]   # product names...
"Ammo" = ["Rifle ammo", "Nobelisk"]                       # ...or exact blueprint names
```
- Each entry is either an exact blueprint name or a product name (the title with
  the `MK3 -` prefix, `xPM` rate, `(1of2)` part and `ALT`/`v2` suffixes stripped).
  Matching ignores case.
- **Subcategories** appear in the game in the order they're listed in the file.
  **Blueprints inside a subcategory** are always sorted by name (`2` before `10`),
  whatever order the file lists them in.
- When `apply` adds to a category that already exists in the save, new
  subcategories go after the existing ones.
- `suggest` copies `icon` and `fallback` from `rules.toml`.

## Files the tool writes
| path | what |
|---|---|
| `downloads/<author>/<title>/` | `<title>.sbp`, `<title>.sbpcfg`, `meta.json` |
| `rules/<author>.toml` | grouping written by `suggest` (yours to edit) |
| `out/<save>.sav` | the edited copy of your save |
| `out/blueprints/` | blueprint files staged for the game/server |
| `out/plan.json`, `out/import.json` | the grouping, and what `apply` added |
| `out/existing_categories.json`, `out/after_categories.json` | the save's categories before and after `apply` |
| `out/suggest_prompt_<author>.md` | the prompt from `suggest --prompt-only` |

`downloads/`, `out/`, `vendor/`, `.venv/` and `*.sav` are git-ignored.

## Be kind to the blueprint sites
Both sites are free, community-run and have no public API. The tool waits between
requests (`--delay`, default 0.5 s) and skips files you already have. Please don't
lower the delay or download the same authors over and over, and remember the
blueprints belong to their authors: don't re-upload them as your own.

## Development
Everything lives in [organize.py](organize.py), in sections:

| section | contents |
|---|---|
| download | `sanitize`, one adapter class per site, `make_source`, `cmd_download` (shared loop: listing pages, detail pages, retries, files, `meta.json`) |
| grouping | `scan_downloads`, `load_rules`, `product_of`, `build_groups`, `make_plan`, `cmd_plan` |
| suggest | prompt, `PROVIDERS`, `resolve_ai`, `ask_model`, `parse_suggestion`, `write_rules_file`, `cmd_suggest` |
| apply | `ensure_parser`, `run_parser` (calls `vendor/sat_sav_parse/sav_cli.py` as a subprocess), `cmd_apply` |

Third-party packages are imported inside the command that needs them
(`requests`/`beautifulsoup4` for `download`, `openai` for `suggest`), so `plan` and
`apply` run on the standard library alone.

**Adding a blueprint site:** write a class like `SatisfactoryCalculator` with
- `site` (host name) and `author` (set in `__init__` from the URL)
- `listing_url(page)`: one page of the author's list; asking past the last page
  must return no new ids, because that's how the loop knows it's done
- `ids_on(soup)`: blueprint ids on a listing page
- `detail_url(id)` and `parse_detail(soup, id)`, which returns `title`,
  `description`, `tags`, `details`, `buildings`, `modded`, `sbp_url`, `cfg_url`

Then add it to the tuple in `make_source`. Use the English version of the site so
labels match.

**Branches:** work on `dev`; `main` only receives merges:
```
git checkout main && git merge --no-ff dev && git checkout dev
```

**Testing:** there's no automated test suite yet. Before merging, check:
1. `python organize.py plan mostly_useless` (or any author you have) still groups
   the same as before.
2. `download` on a small author (satisfactory-calculator.com user `Legitim` has
   two blueprints), then look at `meta.json`.
3. `suggest <author> --prompt-only`, then `--from-file` with a hand-written reply,
   including a made-up name and a missing name. No API key needed.
4. `apply` against a copy of a save: the built-in verification must pass.

Line endings are LF (enforced by `.gitattributes`).

## License
[MIT](LICENSE). The save parser (GreyHak/sat_sav_parse) is GPL-3.0; it is not part
of this repository and is downloaded separately on first use.

## Credits
- Built by Yanko with the help of [Claude](https://claude.com/claude-code)
  (Anthropic's AI, via Claude Code), which helped design, write and test the tool.
- Save editing by [GreyHak/sat_sav_parse](https://github.com/GreyHak/sat_sav_parse).
- Blueprints come from [satisfactoryblueprints.com](https://satisfactoryblueprints.com)
  and [satisfactory-calculator.com](https://satisfactory-calculator.com/en/blueprints)
  and belong to their authors.
