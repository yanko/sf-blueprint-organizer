# TODO

Ideas parked for later.

## Downloading
- **SCIM megaprints**: satisfactory-calculator.com has a separate "megaprints"
  section (`/en/megaprints/index/index/idU/<id>/user/<name>`) for multi-part
  mega-blueprints. `download` rejects these URLs for now. Needs a look at how a
  megaprint's parts are listed and downloaded, and how to file the parts
  (probably one subcategory per megaprint).
- **Single blueprint URLs**: `download <blueprint page URL> --as <category>` to add
  one blueprint (from either site) to an existing category folder, instead of an
  author's whole list.

## Features
- **Export/import category organization between saves**: players ask for a way to
  keep their blueprint categories when starting a new session (they have to be
  rebuilt by hand). sat_sav_parse already has `--blueprint --export/--import`.
