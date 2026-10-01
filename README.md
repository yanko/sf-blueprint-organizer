# sf-blueprint-organizer

Downloads all blueprints by a satisfactoryblueprints.com author and files them
into an in-game blueprint category (author name) with subcategories, by editing a
copy of your save. Save editing uses
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
python organize.py suggest  mostly_useless        # optional: Claude writes rules/mostly_useless.toml
python organize.py plan     mostly_useless        # preview; edit the rules file and repeat
python organize.py apply    mostly_useless --save "C:\path\My Session_autosave_0_continue.sav"
```

### Grouping rules
`plan`/`apply` use `rules/<author>.toml` if it exists, otherwise `rules.toml`
(written for mostly_useless's "MK3 - <Product> xPM" titles). Each entry is either
a product name (title with the MK/rate/part suffixes stripped) or an exact
blueprint name.

`suggest <author>` sends the blueprint titles plus the description and tags from
each blueprint page (saved by `download` as `meta.json`) to Claude
(`claude-opus-5-5`) and writes `rules/<author>.toml`. Review and edit it before
`apply`; it won't be overwritten unless you pass `--force`. If you downloaded
before `meta.json` existed, re-run `download`: existing `.sbp` files are skipped
and only the metadata is fetched.

`suggest` needs an Anthropic API key (or an `ant auth login` profile):
```
set ANTHROPIC_API_KEY=sk-ant-...        (cmd)
$env:ANTHROPIC_API_KEY="sk-ant-..."     (PowerShell)
```
One run is a single API call over ~75 short titles/descriptions (a few cents).

Applying a second author: pass the save that the first `apply` wrote to `out/`
as `--save` (or copy it somewhere first), so both categories end up in one save.
`apply` never touches your original save. It writes `out/<same name>.sav`, verifies
it (new blueprints present, all other categories unchanged), and stages the
blueprint files flat in `out/blueprints/`.

Then, manually:
1. Copy `out/blueprints/*` into the server's `blueprints/<session name>/` folder.
2. Stop the Satisfactory container.
3. Back up the original save, copy `out/<save>.sav` into `SaveGames/server/`.
4. Start the container and load the save.

## Notes
- Every blueprint is filed under the author's category. If a name is also listed in
  another category (e.g. your own), that listing is left alone and reported.
  Add `--dedupe` to remove those other listings instead (run it from the ORIGINAL
  save; each removal takes ~20s). Re-running on an already-processed save is safe.
- The tool adds a second to the save's timestamp so it sorts as newest; use
  `--same-time` to keep the original.
- Other files written to `out/`: `plan.json`, `import.json`,
  `existing_categories.json`, `after_categories.json`.
- `requests`/`beautifulsoup4` are only used by `download` and `anthropic` only by
  `suggest`; `plan` and `apply` use the standard library plus the vendored parser.