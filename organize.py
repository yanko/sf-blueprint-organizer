#!/usr/bin/env python3
"""
Satisfactory blueprint organizer.

  python organize.py download mostly_useless
  python organize.py suggest  mostly_useless
  python organize.py plan     mostly_useless
  python organize.py apply    mostly_useless --save "My Session_autosave_0_continue.sav"

download  Fetch every blueprint by an author from satisfactoryblueprints.com into
          downloads/<author>/<Title>/<Title>.sbp + .sbpcfg, plus meta.json with the
          page's description and tags.
suggest   Ask Claude to group the downloaded blueprints and write rules/<author>.toml
          (needs ANTHROPIC_API_KEY). Review/edit that file, then run plan.
plan      Scan downloads/, group blueprints into subcategories using rules/<author>.toml
          (or rules.toml if there is none), print the result and write out/plan.json.
          Touches nothing else.
apply     Add category <author> with those subcategories to a COPY of your save
          (written to out/<same file name>) using GreyHak/sat_sav_parse, and stage
          the blueprint files flat in out/blueprints/ for you to copy to the game.

All blueprints are filed under the author's category. If a name is also listed in
another category, that listing is left alone (reported) unless you pass --dedupe.

Setup (see README.md): create a venv, activate it, then pip install -r requirements.txt
Requires Python 3.11+ (tomllib) and git (to fetch the save parser on first apply).
"""
import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor" / "sat_sav_parse"
DOWNLOADS = ROOT / "downloads"
OUT = ROOT / "out"
RULES_FILE = ROOT / "rules.toml"
RULES_DIR = ROOT / "rules"
SUGGEST_MODEL = "claude-opus-5-5"
PARSER_REPO = "https://github.com/GreyHak/sat_sav_parse.git"
SITE = "https://satisfactoryblueprints.com"
BP_LINK_RE = re.compile(r"/blueprint/([0-9a-f\-]{36})/?$")
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# --------------------------------------------------------------------------- download
def sanitize(name: str) -> str:
    name = INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    return name or "untitled"


def page_meta(soup, uuid, title):
    """Description, tags (category is one of them) and the details list of a blueprint page."""
    h2 = soup.find("h2")
    p = h2.find_next_sibling("p") if h2 else None
    description = None
    if p:
        description = "\n".join(l.strip() for l in p.get_text().splitlines() if l.strip()) or None
    details, tags = {}, []
    dl = soup.find("dl")
    if dl:
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            details[dt.get_text(strip=True)] = dd.get_text(" ", strip=True) if dd else None
        badges = dl.find_next_sibling("div")
        if badges:
            tags = [s.get_text(strip=True) for s in badges.find_all("span") if s.get_text(strip=True)]
    return {"uuid": uuid, "url": f"{SITE}/blueprint/{uuid}", "title": title,
            "description": description, "tags": tags, "details": details}


def cmd_download(args):
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("Missing packages. Run: pip install requests beautifulsoup4")

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (blueprint-organizer script)"

    def get(url, retries=3):
        for attempt in range(1, retries + 1):
            try:
                r = session.get(url, timeout=30)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                if attempt == retries:
                    raise
                print(f"  retry {attempt}/{retries - 1}: {e}")
                time.sleep(2 * attempt)

    # 1. collect blueprint ids from the author's paginated listing
    uuids, seen, page = [], set(), 1
    while True:
        soup = BeautifulSoup(get(f"{SITE}/author/{args.author}?page={page}").text, "html.parser")
        new = 0
        for a in soup.find_all("a", href=True):
            m = BP_LINK_RE.search(a["href"])
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                uuids.append(m.group(1))
                new += 1
        print(f"Page {page}: {new} new blueprints")
        if new == 0:
            break
        page += 1
        time.sleep(args.delay)
    if not uuids:
        sys.exit("No blueprints found. Check the author name.")
    print(f"Found {len(uuids)} blueprints.\n")

    author_dir = DOWNLOADS / sanitize(args.author)
    author_dir.mkdir(parents=True, exist_ok=True)
    used, failures = set(), []

    def fetch(url, dest):
        if dest.exists() and dest.stat().st_size > 0:
            print(f"    skip (exists): {dest.name}")
            return
        r = get(url)
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"got HTML instead of a file from {url}")
        dest.write_bytes(r.content)
        print(f"    saved: {dest.name} ({len(r.content)} bytes)")

    for i, uuid in enumerate(uuids, 1):
        try:
            soup = BeautifulSoup(get(f"{SITE}/blueprint/{uuid}").text, "html.parser")
            h2 = soup.find("h2")
            title = h2.get_text(strip=True) if h2 else uuid
            name = sanitize(title)
            if name.lower() in used:
                name = f"{name} [{uuid[:8]}]"
            used.add(name.lower())
            print(f"[{i}/{len(uuids)}] {title}")
            folder = author_dir / name
            folder.mkdir(exist_ok=True)
            (folder / "meta.json").write_text(
                json.dumps(page_meta(soup, uuid, title), indent=2, ensure_ascii=False), encoding="utf-8"
            )
            time.sleep(args.delay)
            fetch(f"{SITE}/blueprint/download/{uuid}", folder / f"{name}.sbp")
            time.sleep(args.delay)
            fetch(f"{SITE}/blueprint/config/{uuid}", folder / f"{name}.sbpcfg")
            time.sleep(args.delay)
        except Exception as e:
            print(f"    FAILED: {e}")
            failures.append((uuid, str(e)))
    print(f"\nDone. {len(uuids) - len(failures)}/{len(uuids)} succeeded.")
    for uuid, err in failures:
        print(f"  failed {uuid}: {err}")


# --------------------------------------------------------------------------- grouping
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def scan_downloads(author: str):
    """Blueprint names = file stems of <author>/<folder>/<name>.sbp (must have .sbpcfg too)."""
    author_dir = DOWNLOADS / sanitize(author)
    if not author_dir.is_dir():
        sys.exit(f"{author_dir} not found. Run `download {author}` first.")
    found = {}
    for folder in sorted(p for p in author_dir.iterdir() if p.is_dir()):
        for sbp in folder.glob("*.sbp"):
            cfg = sbp.with_suffix(".sbpcfg")
            if not cfg.exists():
                print(f"WARNING: {sbp.name} has no .sbpcfg, skipping")
                continue
            found[sbp.stem] = (sbp, cfg)
    if not found:
        sys.exit(f"No .sbp files found under {author_dir}")
    return found


def author_rules_file(author: str) -> Path:
    return RULES_DIR / f"{sanitize(author)}.toml"


def load_rules(path: Path = RULES_FILE):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    settings = data.get("settings", {})
    return {
        "icon": int(settings.get("icon", 351)),
        "fallback": settings.get("fallback", "Other"),
        "subcategories": data.get("subcategories", {}),
    }


def product_of(name: str) -> str:
    """'MK3 - Turbo Motor 3.75PM (4of4-ALT)' -> 'Turbo Motor'."""
    s = re.sub(r"^\s*MK\d+\s*-\s*", "", name, flags=re.I)
    m = re.search(r"\s\d+(?:\.\d+)?\s*PM\b", s, flags=re.I)
    if m:
        s = s[: m.start()]
    s = re.sub(r"\s*\(\d+of\d+[^)]*\)", "", s)
    s = re.sub(r"\s+(ALT\d*|v\d+|TWICE)$", "", s, flags=re.I)
    return s.strip()


def build_groups(names, rules):
    lookup = {}
    for sub, products in rules["subcategories"].items():
        for p in products:
            lookup[p.lower()] = sub
    groups = {sub: [] for sub in rules["subcategories"]}
    fallback_hits = []
    for n in names:
        sub = lookup.get(n.lower()) or lookup.get(product_of(n).lower())
        if sub is None:
            sub = rules["fallback"]
            fallback_hits.append(n)
            groups.setdefault(sub, [])
        groups[sub].append(n)
    groups = {k: sorted(v, key=natural_key) for k, v in groups.items() if v}
    return groups, fallback_hits


def print_plan(author, groups, fallback_hits, rules):
    total = sum(len(v) for v in groups.values())
    print(f"Category: {author}  ({total} blueprints, {len(groups)} subcategories)")
    for sub, items in groups.items():
        print(f"\n  {sub}  [{len(items)}]")
        for n in items:
            print(f"    {n}")
    if fallback_hits:
        print(f"\n{len(fallback_hits)} blueprint(s) matched no rule and went to '{rules['fallback']}'.")
        print("Add their product names to rules.toml:")
        for n in fallback_hits:
            print(f"  '{product_of(n)}'   <- {n}")


def make_plan(author):
    path = author_rules_file(author)
    if not path.exists():
        path = RULES_FILE
    print(f"Using rules from {path.relative_to(ROOT)}\n")
    rules = load_rules(path)
    found = scan_downloads(author)
    groups, fallback_hits = build_groups(list(found), rules)
    return rules, found, groups, fallback_hits


def cmd_plan(args):
    rules, found, groups, fallback_hits = make_plan(args.author)
    print_plan(args.author, groups, fallback_hits, rules)
    OUT.mkdir(exist_ok=True)
    (OUT / "plan.json").write_text(
        json.dumps({args.author: {"Icon": rules["icon"], "Subcategories": groups}}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {OUT / 'plan.json'}")


# --------------------------------------------------------------------------- suggest
SUGGEST_SYSTEM = """\
You organize Satisfactory blueprints into the in-game blueprint menu. All blueprints
below are by one author and go into one category named after them; your job is to
split that category into subcategories a player can browse easily.

Rules:
- Put every blueprint in exactly one subcategory, using its name exactly as given.
- Aim for roughly 5-12 subcategories. Avoid one-item subcategories unless the item
  truly fits nowhere else, and avoid a catch-all "Other"/"Misc" bucket.
- Keep multi-part sets together (e.g. "Middle tier production 3_8", "(1of3)"; names
  show "/" as "_"). Keep variants ("extra production", "X2 production", "ALT", "v2")
  in the same subcategory as their base blueprint.
- Prefer groupings a player thinks in: what the blueprint produces or its role
  (e.g. ammo, fuel/power, nuclear, tiered production sets, oil, space elevator parts).
  Use the descriptions and tags as evidence; titles can be misleading.
- Subcategory names are short (at most ~24 characters) and descriptive.
- List subcategories in a sensible order (e.g. game progression), most useful first."""


def write_rules_file(path, author, groups, icon, fallback):
    q = json.dumps  # JSON string escapes are valid TOML basic strings
    lines = [
        f"# Generated by `organize.py suggest {author}` with {SUGGEST_MODEL} on {datetime.date.today()}.",
        "# Entries are exact blueprint names (product names like in rules.toml also work).",
        "# Edit freely; order here = order in game. Re-run suggest with --force to regenerate.",
        "",
        "[settings]",
        f"icon = {icon}",
        f"fallback = {q(fallback)}",
        "",
        "[subcategories]",
    ]
    for sub, names in groups.items():
        lines.append(f"{q(sub)} = [")
        lines += [f"    {q(n)}," for n in names]
        lines.append("]")
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(path, "rb") as f:
        tomllib.load(f)  # fail loudly if we ever write something unparsable


def cmd_suggest(args):
    try:
        import anthropic
        from pydantic import BaseModel
    except ImportError:
        sys.exit("Missing packages. Run: pip install -r requirements.txt")

    dest = author_rules_file(args.author)
    if dest.exists() and not args.force:
        sys.exit(f"{dest.relative_to(ROOT)} already exists. Edit it, or pass --force to regenerate it.")
    found = scan_downloads(args.author)
    defaults = load_rules(RULES_FILE)

    items, no_meta = [], []
    for name, (sbp, _cfg) in found.items():
        entry = {"name": name}
        meta_file = sbp.parent / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            entry["tags"] = meta.get("tags") or []
            entry["description"] = meta.get("description") or ""
        else:
            no_meta.append(name)
        items.append(entry)
    if no_meta:
        print(f"WARNING: {len(no_meta)} blueprint(s) have no meta.json; grouping them by title only.")
        print(f"         Re-run `download {args.author}` to fetch descriptions (existing files are skipped).\n")

    class Subcategory(BaseModel):
        name: str
        blueprints: list[str]

    class Suggestion(BaseModel):
        subcategories: list[Subcategory]

    prompt = (f"Author: {args.author}\nBlueprints ({len(items)}):\n"
              + json.dumps(items, indent=1, ensure_ascii=False))
    print(f"Asking {SUGGEST_MODEL} to group {len(items)} blueprints (can take a minute)...")
    try:
        resp = anthropic.Anthropic().beta.messages.parse(
            model=SUGGEST_MODEL,
            max_tokens=16000,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SUGGEST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Suggestion,
        )
    except anthropic.AuthenticationError:
        sys.exit("Claude API rejected the credentials. Set ANTHROPIC_API_KEY (see README.md).")
    except anthropic.APIStatusError as e:
        sys.exit(f"Claude API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        sys.exit(f"Could not reach the Claude API: {e}")
    except TypeError as e:  # raised by the SDK when no credentials are configured at all
        sys.exit(f"No Claude API credentials found ({e}). Set ANTHROPIC_API_KEY (see README.md).")
    if resp.stop_reason == "refusal":
        sys.exit("Claude declined the request; try again or write the rules file by hand.")
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        sys.exit(f"Claude's answer was incomplete (stop_reason={resp.stop_reason}); try again.")

    by_lower = {n.lower(): n for n in found}
    groups, seen, unknown, dupes = {}, set(), [], []
    for sub in resp.parsed_output.subcategories:
        sub_name = sub.name.strip() or defaults["fallback"]
        for bp in sub.blueprints:
            real = by_lower.get(bp.strip().lower())
            if real is None:
                unknown.append(bp)
            elif real in seen:
                dupes.append(real)
            else:
                seen.add(real)
                groups.setdefault(sub_name, []).append(real)
    missing = [n for n in found if n not in seen]
    if missing:
        groups.setdefault(defaults["fallback"], []).extend(missing)
    for label, names in (("ignored unknown name", unknown), ("ignored repeat of", dupes),
                         (f"not placed, put in '{defaults['fallback']}':", missing)):
        for n in names:
            print(f"  {label} {n}")

    write_rules_file(dest, args.author, groups, defaults["icon"], defaults["fallback"])
    print(f"Wrote {dest.relative_to(ROOT)} ({resp.usage.input_tokens} in / {resp.usage.output_tokens} out tokens). "
          f"Review it, then run `plan {args.author}`.\n")
    rules, _found, plan_groups, fallback_hits = make_plan(args.author)
    print_plan(args.author, plan_groups, fallback_hits, rules)


# --------------------------------------------------------------------------- apply
def ensure_parser():
    if (VENDOR / "sav_cli.py").exists():
        return
    print("Fetching save parser (GreyHak/sat_sav_parse)...")
    VENDOR.parent.mkdir(exist_ok=True)
    try:
        subprocess.run(["git", "clone", "--depth", "1", PARSER_REPO, str(VENDOR)], check=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit(
            f"Could not run git. Download {PARSER_REPO[:-4]}/archive/refs/heads/main.zip, "
            f"unzip it, and put the contents in {VENDOR}"
        )


def run_parser(*cli_args):
    r = subprocess.run(
        [sys.executable, "sav_cli.py", *map(str, cli_args)],
        cwd=VENDOR, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        sys.exit(f"Save parser failed ({' '.join(map(str, cli_args[:2]))}):\n{r.stdout}\n{r.stderr}")
    return r.stdout


def export_categories(save: Path, dest: Path) -> dict:
    run_parser("--blueprint", "--export", save, dest)
    return json.loads(dest.read_text(encoding="utf-8"))


def cmd_apply(args):
    save = Path(args.save).expanduser().resolve()
    if not save.is_file():
        sys.exit(f"Save not found: {save}")
    rules, found, groups, fallback_hits = make_plan(args.author)
    icon = args.icon if args.icon is not None else rules["icon"]
    ensure_parser()
    OUT.mkdir(exist_ok=True)

    print("Reading existing categories from the save (takes ~20s)...")
    existing = export_categories(save, OUT / "existing_categories.json")
    own = {
        bp
        for v in [existing.get(args.author, {"Subcategories": {}})]
        for bps in v["Subcategories"].values()
        for bp in bps
    }
    elsewhere = {}  # blueprint name -> [(category, subcategory)] in OTHER categories
    for cat, v in existing.items():
        if cat == args.author:
            continue
        for sub, bps in v["Subcategories"].items():
            for bp in bps:
                elsewhere.setdefault(bp, []).append((cat, sub))

    to_add = {}
    for sub, items in groups.items():
        keep = [n for n in items if n not in own]
        if keep:
            to_add[sub] = keep
    all_names = [n for items in groups.values() for n in items]
    dupes = sorted(n for n in all_names if n in elsewhere)
    removals = [(n, c, sb) for n in dupes for (c, sb) in elsewhere[n]] if args.dedupe else []

    if not to_add and not removals:
        print("Nothing to do: everything is already filed under this category.")
        return

    out_save = OUT / save.name
    current = save
    if to_add:
        import_file = OUT / "import.json"
        import_file.write_text(
            json.dumps({args.author: {"Icon": icon, "Subcategories": to_add}}, indent=2),
            encoding="utf-8",
        )
        extra = ["--same-time"] if args.same_time else []
        print(f"Writing modified save to {out_save} ...")
        run_parser("--blueprint", "--import", save, import_file, out_save, *extra)
        current = out_save

    for i, (name, cat, sub) in enumerate(removals, 1):
        print(f"Removing duplicate from '{cat} > {sub}' ({i}/{len(removals)}): {name}")
        step = OUT / "_step.sav"
        run_parser("--blueprint", "--remove-blueprint", cat, sub, name, current, step, "--same-time")
        shutil.move(str(step), str(out_save))
        current = out_save

    print("Verifying the new save...")
    after = export_categories(out_save, OUT / "after_categories.json")
    problems = []
    got = after.get(args.author, {}).get("Subcategories", {})
    for sub, items in to_add.items():
        missing = [n for n in items if n not in got.get(sub, [])]
        if missing:
            problems.append(f"{sub}: missing {missing}")
    expected = {c: {"Icon": v["Icon"], "Subcategories": {sb: list(b) for sb, b in v["Subcategories"].items()}}
                for c, v in existing.items() if c != args.author}
    for name, cat, sub in removals:
        expected[cat]["Subcategories"][sub].remove(name)
    for cat, v in expected.items():
        if after.get(cat) != v:
            problems.append(f"category '{cat}' differs from expectation")
    if problems:
        sys.exit("VERIFY FAILED, do not use the save in out/:\n  " + "\n  ".join(problems))

    stage = OUT / "blueprints"
    stage.mkdir(exist_ok=True)
    for sbp, cfg in found.values():
        shutil.copy2(sbp, stage / sbp.name)
        shutil.copy2(cfg, stage / cfg.name)

    added = sum(len(v) for v in to_add.values())
    print(f"\nOK. Added {added} blueprints to category '{args.author}' in {len(to_add)} subcategories "
          f"({len(own)} were already there).")
    if dupes and not args.dedupe:
        print(f"{len(dupes)} of them are also listed in other categories (left in place):")
        for n in dupes:
            print(f"  {n}   (also in " + ", ".join(f"{c} > {s}" for c, s in elsewhere[n]) + ")")
        print("If duplicates cause trouble in game, re-run apply from the ORIGINAL save with --dedupe.")
    elif removals:
        print(f"Removed {len(removals)} duplicate listing(s) from other categories:")
        for n, c, sb in removals:
            print(f"  {n}   (was in {c} > {sb})")
    if fallback_hits:
        print(f"{len(fallback_hits)} blueprint(s) went to '{rules['fallback']}' (see `plan`).")
    print(f"\nNext steps (manual):\n"
          f"  1. Copy everything in {stage} into the server's  blueprints/<session name>/  folder.\n"
          f"  2. Stop the Satisfactory container.\n"
          f"  3. Copy {out_save} into the server's SaveGames/server/ folder (keep a backup of the original).\n"
          f"  4. Start the container and load the save.")


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="download all blueprints by an author")
    d.add_argument("author")
    d.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("suggest", help="ask Claude to write rules/<author>.toml")
    s.add_argument("author")
    s.add_argument("--force", action="store_true", help="overwrite an existing rules/<author>.toml")
    s.set_defaults(fn=cmd_suggest)

    p = sub.add_parser("plan", help="preview grouping (changes nothing)")
    p.add_argument("author")
    p.set_defaults(fn=cmd_plan)

    a = sub.add_parser("apply", help="write modified save copy + staged blueprint files")
    a.add_argument("author")
    a.add_argument("--save", required=True, help="path to the original .sav")
    a.add_argument("--icon", type=int, default=None, help="category icon id (default from rules.toml)")
    a.add_argument("--same-time", action="store_true", help="keep the save's original timestamp")
    a.add_argument("--dedupe", action="store_true",
                   help="also remove these blueprints from any OTHER category that lists them")
    a.set_defaults(fn=cmd_apply)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()