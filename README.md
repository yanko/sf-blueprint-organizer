# sf-blueprint-organizer

Downloads all blueprints by an author from
[satisfactoryblueprints.com](https://satisfactoryblueprints.com) or
[satisfactory-calculator.com](https://satisfactory-calculator.com/en/blueprints) and
files them into an in-game blueprint category (author name) with subcategories, by
editing a copy of your save. Save editing uses
[GreyHak/sat_sav_parse](https://github.com/GreyHak/sat_sav_parse) (GPL-3.0,
supports Satisfactory 1.2.x), fetched into `vendor/` on first `apply`.

## Setup (Windows, one time)
Requires Python 3.11+ and git.
```
cd sf-blueprint-organizer
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
Activate the venv (`.venv\Scripts\activate`) in each new terminal before running
the tool. When you're done with the project, `deactivate` and delete `.venv\`;
nothing is installed system-wide.

On Linux/macOS: `python3 -m venv .venv && source .venv/bin/activate`.

## Use
```
python organize.py download mostly_useless
python organize.py suggest  mostly_useless        # optional: an AI writes rules/mostly_useless.toml
python organize.py plan     mostly_useless        # preview; edit the rules file and repeat
python organize.py apply    mostly_useless --save "C:\path\My Session_autosave_0_continue.sav"
```

### Where `download` can get blueprints
| site | what to pass |
|---|---|
| satisfactoryblueprints.com | the author name (`scorpi`) or their page `https://satisfactoryblueprints.com/author/scorpi` |
| satisfactory-calculator.com | the author's blueprint page, e.g. `https://satisfactory-calculator.com/en/blueprints/index/index/idU/101521/user/Legitim` (click the author's name on any of their blueprints; any language prefix works) |

The author name from the site becomes the folder `downloads/<author>/` and the
in-game category, and is what you pass to `suggest`/`plan`/`apply`. Options:
- `--as "Some Name"` files the blueprints under another name/category instead
  (useful if two sites have authors with the same name).
- Blueprints that need mods are skipped (satisfactory-calculator.com's "Modded"
  category, satisfactoryblueprints.com's "Modded Content: Yes"); add
  `--include-modded` to download them anyway.

Each blueprint folder gets a `meta.json` (description, tags/categories, and on
satisfactory-calculator.com the list of buildings used) that `suggest` sends to the
AI. Re-running `download` only refreshes `meta.json` and fetches missing files.
See [TODO.md](TODO.md) for what's not supported yet.

`apply` never touches your original save. It writes `out/<same name>.sav`, verifies
it (new blueprints present, all other categories unchanged), and stages the
blueprint files flat in `out/blueprints/`.

Then, manually:
1. Copy `out/blueprints/*` into the server's `blueprints/<session name>/` folder.
2. Stop the Satisfactory container.
3. Back up the original save, copy `out/<save>.sav` into `SaveGames/server/`.
4. Start the container and load the save.

Applying a second author: pass the save that the first `apply` wrote to `out/`
as `--save` (copy it out of `out/` first, since every `apply` rewrites that folder),
so both categories end up in one save.

## Grouping rules
`plan`/`apply` use `rules/<author>.toml` if it exists, otherwise `rules.toml`
(written for mostly_useless's "MK3 - <Product> xPM" titles). Each entry is either
a product name (title with the MK/rate/part suffixes stripped) or an exact
blueprint name. Order in the file = order in game.

## AI-suggested rules (`suggest`)
`suggest <author>` sends the blueprint titles plus the description and tags from
each blueprint page (saved by `download` as `meta.json`) to an AI and writes
`rules/<author>.toml`. Review and edit it before `apply`; it won't be overwritten
unless you pass `--force`. If you downloaded before `meta.json` existed, re-run
`download`: existing `.sbp` files are skipped and only the metadata is fetched.

The AI's answer is checked before anything is written: names it made up are
dropped, repeats are ignored, and any blueprint it left out goes to `Other`.

### With an API (any OpenAI-compatible provider)
Defaults live in [ai.toml](ai.toml) (OpenRouter + `anthropic/claude-opus-5.5`).
Keys are read from an environment variable, never from a file:
```
$env:OPENROUTER_API_KEY="sk-or-..."     (Windows PowerShell)
set OPENROUTER_API_KEY=sk-or-...        (Windows cmd)
export OPENROUTER_API_KEY="sk-or-..."   (macOS / Linux: bash, zsh)
python organize.py suggest scorpi
```
These last for the current terminal only. Use the variable name for your provider
(`OPENAI_API_KEY`, or your `--api-key-env` name) in the same way.
Override per run with flags:
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

The tool asks for JSON-schema output first and falls back to plain JSON for
models that don't support it; if the answer is malformed it asks once more.
One run is a single request over ~75 short titles/descriptions (cents on
OpenRouter with a Claude model).

### By hand (any AI chat, no key)
```
python organize.py suggest scorpi --prompt-only            # writes out/suggest_prompt_scorpi.md
# paste that file into any AI chat, save its JSON reply as reply.json
python organize.py suggest scorpi --from-file reply.json
```
The reply may be bare JSON or JSON inside a ``` code block.

## Notes
- Every blueprint is filed under the author's category. If a name is also listed in
  another category (e.g. your own), that listing is left alone and reported.
  Add `--dedupe` to remove those other listings instead (run it from the ORIGINAL
  save; each removal takes ~20s). Re-running on an already-processed save is safe.
- The tool adds a second to the save's timestamp so it sorts as newest; use
  `--same-time` to keep the original.
- Other files written to `out/`: `plan.json`, `import.json`,
  `existing_categories.json`, `after_categories.json`.
- `requests`/`beautifulsoup4` are only used by `download` and `openai` (a generic
  client for OpenAI-compatible APIs) only by `suggest`; `plan` and `apply` use the
  standard library plus the vendored parser.

## Credits
- Built by Yanko with the help of [Claude](https://claude.com/claude-code)
  (Anthropic's AI, via Claude Code), which helped design, write and test the tool.
- Save editing by [GreyHak/sat_sav_parse](https://github.com/GreyHak/sat_sav_parse).
- Blueprints come from [satisfactoryblueprints.com](https://satisfactoryblueprints.com)
  and [satisfactory-calculator.com](https://satisfactory-calculator.com/en/blueprints)
  and belong to their authors.