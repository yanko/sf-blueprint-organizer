#!/usr/bin/env python3
"""
Satisfactory blueprint organizer.

  python organize.py download mostly_useless
  python organize.py download https://satisfactory-calculator.com/en/blueprints/index/index/idU/101521/user/Legitim
  python organize.py suggest  mostly_useless
  python organize.py plan     mostly_useless
  python organize.py apply    mostly_useless --save "My Session_autosave_0_continue.sav"

download  Fetch every blueprint by an author from satisfactoryblueprints.com (author
          name or URL) or satisfactory-calculator.com (author page URL) into
          downloads/<author>/<Title>/<Title>.sbp + .sbpcfg, plus meta.json with the
          page's description, tags and (SCIM) building list. Modded blueprints are
          skipped unless --include-modded; --as NAME files them under another name.
suggest   Ask an AI (any OpenAI-compatible API, e.g. OpenRouter; see ai.toml) to group
          the downloaded blueprints and write rules/<author>.toml. Or use --prompt-only /
          --from-file to go through any AI chat by hand. Review/edit, then run plan.
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
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import quote, unquote, unquote_plus, urljoin, urlparse

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor" / "sat_sav_parse"
DOWNLOADS = ROOT / "downloads"
OUT = ROOT / "out"
RULES_FILE = ROOT / "rules.toml"
RULES_DIR = ROOT / "rules"
AI_CONFIG = ROOT / "ai.toml"
PARSER_REPO = "https://github.com/GreyHak/sat_sav_parse.git"
SITE = "https://satisfactoryblueprints.com"
SCIM = "https://satisfactory-calculator.com"
BP_LINK_RE = re.compile(r"/blueprint/([0-9a-f\-]{36})/?$")
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# --------------------------------------------------------------------------- download
def sanitize(name: str) -> str:
    name = INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    return name or "untitled"


def text_lines(el):
    """Element text with blank lines dropped, or None."""
    if el is None:
        return None
    return "\n".join(l.strip() for l in el.get_text("\n").splitlines() if l.strip()) or None


class SatisfactoryBlueprints:
    """satisfactoryblueprints.com: an author name, or https://satisfactoryblueprints.com/author/<name>."""
    site = "satisfactoryblueprints.com"

    def __init__(self, ref):
        if "://" in ref:
            m = re.search(r"/author/([^/?#]+)", ref)
            if not m:
                sys.exit(f"Expected an author page like {SITE}/author/<name>, got {ref}")
            ref = unquote(m.group(1))
        self.author = ref

    def listing_url(self, page):
        return f"{SITE}/author/{quote(self.author)}?page={page}"

    def ids_on(self, soup):
        return [m.group(1) for a in soup.find_all("a", href=True) if (m := BP_LINK_RE.search(a["href"]))]

    def detail_url(self, bp_id):
        return f"{SITE}/blueprint/{bp_id}"

    def parse_detail(self, soup, bp_id):
        h2 = soup.find("h2")
        title = h2.get_text(strip=True) if h2 else None
        details, tags = {}, []
        dl = soup.find("dl")
        if dl:
            for dt in dl.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                details[dt.get_text(strip=True)] = dd.get_text(" ", strip=True) if dd else None
            badges = dl.find_next_sibling("div")
            if badges:
                tags = [s.get_text(strip=True) for s in badges.find_all("span") if s.get_text(strip=True)]
        return {
            "title": title,
            "description": text_lines(h2.find_next_sibling("p")) if h2 else None,
            "tags": tags,
            "details": details,
            "buildings": {},
            "modded": (details.get("Modded Content") or "").strip().lower() == "yes",
            "sbp_url": f"{SITE}/blueprint/download/{bp_id}",
            "cfg_url": f"{SITE}/blueprint/config/{bp_id}",
        }


class SatisfactoryCalculator:
    """satisfactory-calculator.com (SCIM): .../blueprints/index/index/idU/<id>/user/<name>."""
    site = "satisfactory-calculator.com"
    DETAILS = ("Items count", "Build version", "Author", "File size", "Created on", "Last updated on")

    def __init__(self, ref):
        m = re.search(r"/blueprints/index/index/idU/(\d+)/user/([^/?#]+)", ref)
        if not m:
            sys.exit("Expected a SCIM author page like "
                     f"{SCIM}/en/blueprints/index/index/idU/<id>/user/<name>, got {ref}\n"
                     "(single blueprints and megaprints are not supported yet, see TODO.md)")
        self.author = unquote_plus(m.group(2))
        # always the English site, so the labels parse_detail looks for match
        self.base = f"{SCIM}/en/blueprints/index/index/idU/{m.group(1)}/user/{m.group(2)}"

    def listing_url(self, page):
        return self.base if page == 1 else f"{self.base}/p/{page}"

    def ids_on(self, soup):
        return [m.group(1) for a in soup.find_all("a", href=True)
                if (m := re.search(r"/blueprints/index/details/id/(\d+)", a["href"]))]

    def detail_url(self, bp_id):
        return f"{SCIM}/en/blueprints/index/details/id/{bp_id}"

    def parse_detail(self, soup, bp_id):
        page_title = soup.title.get_text(strip=True) if soup.title else ""
        title = page_title.split(" / Blueprints / ")[0].strip() if " / Blueprints / " in page_title else None
        details, tags = {}, []
        for li in soup.select("ul.list-group-flush.mt-3 > li"):
            spans = li.find_all("span", recursive=False)
            if len(spans) < 2:
                continue
            label = spans[0].get_text(" ", strip=True)
            if label == "Categories":
                tags = [b.get_text(strip=True) for b in spans[1].select(".badge")]
            elif label in self.DETAILS:
                details[label] = spans[1].get_text(" ", strip=True)
        buildings = {}
        for li in soup.select("#tab-breakdown li"):
            a, count = li.find("a"), li.select_one("span.float-right")
            if a and count:
                buildings[a.get_text(strip=True)] = int(re.sub(r"\D", "", count.get_text()) or 0)
        sbp = soup.find("a", href=re.compile(r"/blueprints/index/download/id/\d+"))
        cfg = soup.find("a", href=re.compile(r"/blueprints/index/download-cfg/id/\d+"))
        return {
            "title": title,
            "description": text_lines(soup.select_one(".media-body blockquote")),
            "tags": tags,
            "details": details,
            "buildings": buildings,
            "modded": "Modded" in tags,
            "sbp_url": urljoin(SCIM, sbp["href"]) if sbp else f"{SCIM}/en/blueprints/index/download/id/{bp_id}",
            "cfg_url": urljoin(SCIM, cfg["href"]) if cfg else f"{SCIM}/en/blueprints/index/download-cfg/id/{bp_id}",
        }


def make_source(ref):
    """Pick the site adapter from a bare author name (satisfactoryblueprints.com) or a URL."""
    if "://" not in ref:
        return SatisfactoryBlueprints(ref)
    host = urlparse(ref).netloc.lower().removeprefix("www.")
    for cls in (SatisfactoryBlueprints, SatisfactoryCalculator):
        if host == cls.site:
            return cls(ref)
    sys.exit(f"Unsupported site '{host}'. Supported: satisfactoryblueprints.com, satisfactory-calculator.com")


def cmd_download(args):
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("Missing packages. Run: pip install requests beautifulsoup4")

    src = make_source(args.source)
    author = args.as_name or src.author
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
    print(f"{src.site}: author '{src.author}'" + (f", filed as '{author}'" if author != src.author else ""))
    ids, seen, page = [], set(), 1
    while True:
        soup = BeautifulSoup(get(src.listing_url(page)).text, "html.parser")
        new = [i for i in dict.fromkeys(src.ids_on(soup)) if i not in seen]
        seen.update(new)
        ids += new
        print(f"Page {page}: {len(new)} new blueprints")
        if not new:
            break
        page += 1
        time.sleep(args.delay)
    if not ids:
        sys.exit("No blueprints found. Check the author name / URL.")
    print(f"Found {len(ids)} blueprints.\n")

    author_dir = DOWNLOADS / sanitize(author)
    author_dir.mkdir(parents=True, exist_ok=True)
    used, failures, modded = set(), [], []

    def fetch(url, dest):
        if dest.exists() and dest.stat().st_size > 0:
            print(f"    skip (exists): {dest.name}")
            return
        r = get(url)
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"got HTML instead of a file from {url}")
        dest.write_bytes(r.content)
        print(f"    saved: {dest.name} ({len(r.content)} bytes)")

    for i, bp_id in enumerate(ids, 1):
        try:
            url = src.detail_url(bp_id)
            bp = src.parse_detail(BeautifulSoup(get(url).text, "html.parser"), bp_id)
            title = bp["title"] or str(bp_id)
            print(f"[{i}/{len(ids)}] {title}")
            if bp["modded"] and not args.include_modded:
                print("    skip (modded; use --include-modded to download it)")
                modded.append(title)
                time.sleep(args.delay)
                continue
            name = sanitize(title)
            if name.lower() in used:
                name = f"{name} [{str(bp_id)[:8]}]"
            used.add(name.lower())
            folder = author_dir / name
            folder.mkdir(exist_ok=True)
            meta = {"source": src.site, "id": bp_id, "url": url,
                    **{k: v for k, v in bp.items() if k not in ("sbp_url", "cfg_url")}}
            meta["title"] = title
            (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            time.sleep(args.delay)
            fetch(bp["sbp_url"], folder / f"{name}.sbp")
            time.sleep(args.delay)
            fetch(bp["cfg_url"], folder / f"{name}.sbpcfg")
            time.sleep(args.delay)
        except Exception as e:
            print(f"    FAILED: {e}")
            failures.append((bp_id, str(e)))
    done = len(ids) - len(failures) - len(modded)
    print(f"\nDone. {done}/{len(ids)} downloaded into {author_dir.relative_to(ROOT)}"
          + (f", {len(modded)} modded skipped" if modded else "") + ".")
    for bp_id, err in failures:
        print(f"  failed {bp_id}: {err}")
    print(f"Next: python organize.py suggest \"{author}\"   (or plan \"{author}\")")


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
  Use the descriptions, tags and (when given) building counts as evidence; titles
  can be misleading.
- Subcategory names are short (at most ~24 characters) and descriptive.
- List subcategories in a sensible order (e.g. game progression), most useful first.

Reply with only a JSON object of this shape, no other text:
{"subcategories": [{"name": "<subcategory>", "blueprints": ["<exact blueprint name>", ...]}, ...]}"""

SUGGEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subcategories"],
    "properties": {"subcategories": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "blueprints"],
        "properties": {"name": {"type": "string"},
                       "blueprints": {"type": "array", "items": {"type": "string"}}},
    }}},
}

# OpenAI-compatible endpoints. key_env None = no key needed.
PROVIDERS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "openai": {"base_url": None, "key_env": "OPENAI_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},
    "custom": {"base_url": None, "key_env": None},
}


def suggest_items(author):
    """Blueprint names plus tags/description from meta.json, as sent to the model."""
    found = scan_downloads(author)
    items, no_meta = [], []
    for name, (sbp, _cfg) in found.items():
        entry = {"name": name}
        meta_file = sbp.parent / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            entry["tags"] = meta.get("tags") or []
            entry["description"] = meta.get("description") or ""
            if meta.get("buildings"):
                entry["buildings"] = meta["buildings"]
        else:
            no_meta.append(name)
        items.append(entry)
    if no_meta:
        print(f"WARNING: {len(no_meta)} blueprint(s) have no meta.json; grouping them by title only.")
        print(f"         Re-run `download {author}` to fetch descriptions (existing files are skipped).\n")
    return found, items


def suggest_user_prompt(author, items):
    return f"Author: {author}\nBlueprints ({len(items)}):\n" + json.dumps(items, indent=1, ensure_ascii=False)


def parse_suggestion(text):
    """Model reply -> [(subcategory, [names])]. Accepts bare JSON or JSON inside a ``` block."""
    if not text or "{" not in text:
        raise ValueError("no JSON object found in the reply")
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from None
    subs = data.get("subcategories") if isinstance(data, dict) else None
    if not isinstance(subs, list):
        raise ValueError('expected an object with a "subcategories" list')
    out = []
    for i, sub in enumerate(subs):
        if not (isinstance(sub, dict) and isinstance(sub.get("name"), str)
                and isinstance(sub.get("blueprints"), list)
                and all(isinstance(b, str) for b in sub["blueprints"])):
            raise ValueError(f'subcategories[{i}] must be {{"name": string, "blueprints": [strings]}}')
        out.append((sub["name"], sub["blueprints"]))
    return out


def resolve_ai(args):
    """Provider settings from ai.toml, overridden by command-line flags."""
    cfg = {}
    if AI_CONFIG.exists():
        with open(AI_CONFIG, "rb") as f:
            cfg = tomllib.load(f).get("ai", {})
    provider = args.provider or cfg.get("provider", "openrouter")
    if provider not in PROVIDERS:
        sys.exit(f"Unknown provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")
    preset = PROVIDERS[provider]
    model = args.model or cfg.get("model")
    base_url = args.base_url or cfg.get("base_url") or preset["base_url"]
    key_env = args.api_key_env or cfg.get("api_key_env") or preset["key_env"]
    if not model:
        sys.exit(f"No model set. Pass --model or set `model` in {AI_CONFIG.name}.")
    if provider == "custom" and not base_url:
        sys.exit("Provider 'custom' needs --base-url (or base_url in ai.toml).")
    api_key = "none"  # local servers ignore it, but the client requires a value
    if key_env:
        api_key = os.environ.get(key_env)
        if not api_key:
            sys.exit(f"Set {key_env} to your {provider} API key (see README.md), "
                     f"or use --prompt-only to paste the prompt into any AI chat.")
    return provider, model, base_url, api_key, key_env


def ask_model(args, author, items):
    try:
        import openai
    except ImportError:
        sys.exit("Missing packages. Run: pip install -r requirements.txt")
    provider, model, base_url, api_key, key_env = resolve_ai(args)
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=600)
    messages = [{"role": "system", "content": SUGGEST_SYSTEM},
                {"role": "user", "content": suggest_user_prompt(author, items)}]
    # Strictest format first; fall back for providers/models that don't support it.
    formats = [{"type": "json_schema",
                "json_schema": {"name": "blueprint_groups", "strict": True, "schema": SUGGEST_SCHEMA}},
               {"type": "json_object"},
               None]
    print(f"Asking {model} via {provider} to group {len(items)} blueprints (can take a minute)...")

    def call(fmt):
        extra = {"response_format": fmt} if fmt else {}
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **extra)
        except openai.AuthenticationError:
            sys.exit(f"{provider} rejected the API key" + (f" in {key_env}." if key_env else "."))
        except openai.APIConnectionError as e:
            sys.exit(f"Could not reach {base_url or provider}: {e}")
        if not resp.choices:
            sys.exit(f"{provider} returned no answer; try again.")
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            sys.exit("The model's answer was cut off (too long); try again or another model.")
        if not choice.message.content:
            sys.exit(f"The model returned no text ({getattr(choice.message, 'refusal', None) or choice.finish_reason}).")
        if resp.usage:
            print(f"  ({resp.usage.prompt_tokens} in / {resp.usage.completion_tokens} out tokens)")
        return choice.message.content

    for fmt in formats:
        try:
            text = call(fmt)
            break
        except openai.BadRequestError as e:
            if fmt is None:
                sys.exit(f"{provider} error: {e.message}")
            print(f"  {provider} rejected response_format={fmt['type']}; retrying with a simpler format")
        except openai.APIStatusError as e:
            sys.exit(f"{provider} error {e.status_code}: {e.message}")
    try:
        return parse_suggestion(text), model
    except ValueError as e:
        print(f"  answer was not usable ({e}); asking once more")
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That answer was not usable: {e}. Reply with only the corrected JSON."}]
        try:
            return parse_suggestion(call(fmt)), model
        except ValueError as e:
            sys.exit(f"The model's answer was still not usable: {e}")
        except openai.APIStatusError as e:
            sys.exit(f"{provider} error {e.status_code}: {e.message}")


def write_rules_file(path, author, groups, icon, fallback, source):
    q = json.dumps  # JSON string escapes are valid TOML basic strings
    lines = [
        f"# Generated by `organize.py suggest {author}` from {source} on {datetime.date.today()}.",
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
    found, items = suggest_items(args.author)
    if args.prompt_only:
        OUT.mkdir(exist_ok=True)
        prompt_file = OUT / f"suggest_prompt_{sanitize(args.author)}.md"
        prompt_file.write_text(SUGGEST_SYSTEM + "\n\n" + suggest_user_prompt(args.author, items) + "\n",
                               encoding="utf-8")
        print(f"Wrote {prompt_file}\nPaste it into any AI chat, save the JSON reply to a file, then run:\n"
              f"  python organize.py suggest {args.author} --from-file <reply file>")
        return

    dest = author_rules_file(args.author)
    if dest.exists() and not args.force:
        sys.exit(f"{dest.relative_to(ROOT)} already exists. Edit it, or pass --force to regenerate it.")
    if args.from_file:
        try:
            suggestion = parse_suggestion(Path(args.from_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            sys.exit(f"Could not use {args.from_file}: {e}")
        source = f"{Path(args.from_file).name} (manual)"
    else:
        suggestion, source = ask_model(args, args.author, items)

    defaults = load_rules(RULES_FILE)
    by_lower = {n.lower(): n for n in found}
    groups, seen, unknown, dupes = {}, set(), [], []
    for sub_name, blueprints in suggestion:
        sub_name = sub_name.strip() or defaults["fallback"]
        for bp in blueprints:
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

    write_rules_file(dest, args.author, groups, defaults["icon"], defaults["fallback"], source)
    print(f"Wrote {dest.relative_to(ROOT)}. Review it, then run `plan {args.author}`.\n")
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
    d.add_argument("source", metavar="AUTHOR_OR_URL",
                   help="satisfactoryblueprints.com author name, or an author page URL from "
                        "satisfactoryblueprints.com or satisfactory-calculator.com")
    d.add_argument("--as", dest="as_name", metavar="NAME",
                   help="file under this name (= in-game category) instead of the author's name")
    d.add_argument("--include-modded", action="store_true", help="also download blueprints that need mods")
    d.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("suggest", help="ask an AI to write rules/<author>.toml")
    s.add_argument("author")
    s.add_argument("--force", action="store_true", help="overwrite an existing rules/<author>.toml")
    s.add_argument("--provider", choices=list(PROVIDERS), help="AI provider (default from ai.toml)")
    s.add_argument("--model", help="model id, e.g. anthropic/claude-opus-5.5 (default from ai.toml)")
    s.add_argument("--base-url", help="API base URL (required for --provider custom)")
    s.add_argument("--api-key-env", help="environment variable holding the API key")
    manual = s.add_mutually_exclusive_group()
    manual.add_argument("--prompt-only", action="store_true",
                        help="just write the prompt to out/ to paste into any AI chat")
    manual.add_argument("--from-file", metavar="REPLY", help="use a saved AI reply instead of calling an API")
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