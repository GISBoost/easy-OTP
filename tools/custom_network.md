# Preparing a custom OSM network

`easy-OTP` already accepts any `.osm.pbf` as the **OSM extract** input to
**Run temporal accessibility** and the other Analysis algorithms, and builds
a graph from it. That means modeling a network change — a closed road, a new
footbridge, a changed speed limit — needs no plugin change at all: you edit a
local copy of OSM data and point the plugin at the edited file instead of a
raw Geofabrik extract. This guide walks through three independent ways to
prepare that file, plus how to merge a synthetic network back into a real OSM
extract. Running your own local OTP router is what makes this possible — it's
a native strength of the setup, not a workaround.

---

## Before you start

- **`.osm.pbf` only — no exceptions in this plugin.** OTP 1.5.0 itself can
  read several OSM formats (`.osm`/`.osm.xml`, `.osm.bz2`, `.osm.gz` are also
  listed in its docs), but `easy-OTP`'s **OSM extract** file picker is
  hard-filtered to `.pbf` — a plain `.osm` file can't even be selected,
  regardless of what OTP itself would accept. Whichever option below you
  use, the file you end up feeding into the plugin must be `.osm.pbf`. Plain
  `.osm` is a useful *intermediate* format (JOSM edits it natively, `ogr2osm`
  can output it), but never the final file you point the plugin at.
- **A way needs a `highway=*` tag (or `railway=platform`) to be routable.**
  OTP 1.5.0 only turns ways with a `highway` tag (or platforms tagged
  `railway=platform`) into graph edges. Plain geometry with no tags is
  silently ignored — no edge is built, no error is raised.
- **Connectivity is decided by a shared node, not visual crossing.** Two
  lines that cross on the map but don't share a node at the intersection are
  disconnected as far as OTP is concerned.
- `router_id` is a hash of the input data, so editing your OSM file triggers
  an automatic graph rebuild on the next run. You don't need to clear any
  cache manually.

---

## Option 1 — Small manual edit in JOSM

Use this for point edits: closing a road, drawing a footbridge or crossing,
restricting access on a way.

If you've never used JOSM before, its own
[Introduction page](https://josm.openstreetmap.de/wiki/Introduction) is worth
a skim first — it covers the node/way/tag data model and the editing basics
in more depth than this guide does.

1. Install [JOSM](https://josm.openstreetmap.de/) (free OSM editor, requires
   Java). You don't need an OSM account for the workflow below — a login is
   only required to upload changes to the public server, which you won't be
   doing here.
2. **Install the JOSM `pbf` plugin.** JOSM has no built-in support for
   `.osm.pbf` at all — not for opening it, not for saving it — it's an
   optional plugin. Since `easy-OTP` only accepts `.osm.pbf` (see
   [Before you start](#before-you-start)), install it now: `Edit →
   Preferences` (or `F12`) → `Plugins` tab → click **Download list** to
   refresh → search `pbf` → tick the checkbox next to it → `OK`. Do this
   before step 3 below if you're opening an existing `.osm.pbf` extract —
   without the plugin, `File → Open` won't even recognize the file.
3. Load the data to edit. Either:
   - `File → Open` and select your existing `.osm.pbf` (or `.osm`) file —
     the usual case if you already have an extract (e.g. from easy-OTP's
     data download algorithm or Geofabrik). For a large city extract, crop
     the area first (see the `osmium`/`osmosis` commands under
     [Merging into an existing OSM extract](#merging-into-an-existing-osm-extract))
     so JOSM doesn't stall loading the whole file.
   - Or use JOSM's built-in **Download** button (or `Ctrl+Shift+↓`) to pull
     current data for a small area directly from the OSM server. The
     download dialog restricts you to a small bounding box, so this only
     works if your edit fits inside a small area — handy if you don't have
     a local extract yet and just want to try something quickly.
4. Make your edit:
   - **Removing a road** — select the way, press `Delete`.
   - **New road or footbridge** — draw the way and give it a `highway=*` tag
     (e.g. `footway`, `path`, `residential`) so it's routable.
   - **Closing access** — change access tags (`access=no`, `motor_vehicle=no`,
     etc.) instead of deleting the geometry.
5. Confirm new or edited ways share nodes with the existing network. JOSM
   highlights unconnected endpoints; use its node-merging tool to join them.
   Running JOSM's validator (`Ctrl+Shift+V`) is also worth doing here — it
   flags untagged ways and disconnected roads, the same two mistakes covered
   under [Common pitfalls](#common-pitfalls), even though you won't be
   uploading anywhere.
6. `File → Save As`, set the file type dropdown to **"OSM Server Files PBF
   Compressed (\*.osm.pbf)"**, and check the filename before clicking Save:
   type it as `network.osm.pbf`, not `network.pbf` — per JOSM's own PBF
   plugin documentation, the save only works correctly when the filename
   ends in the full `.osm.pbf`, not just `.pbf`. (`easy-OTP`'s own file
   picker would technically accept a bare `.pbf` name too, but there's no
   reason to fight JOSM's requirement — just use `.osm.pbf` throughout.) If
   you'd rather save as `.osm` first to double check the edit in a
   text-friendly format, that's fine, but it's an intermediate step —
   re-save (or re-export) as `.osm.pbf` before the next step, since a plain
   `.osm` file can't be used as the plugin's **OSM extract** input.
7. Provide the `.osm.pbf` file as the **OSM extract** parameter.

This is a local scenario copy for your own analysis — JOSM only sends
anything to the public OSM server if you explicitly click Upload, which you
should not do with this file.

---

## Option 2 — Draw your own network in QGIS and convert to OSM

Use this when your network exists as a vector layer in QGIS (shapefile,
GeoJSON, GeoPackage) and needs to become an OSM file. This is the most
failure-prone option, so the steps below are literal commands to copy, not a
conceptual overview.

### Preparing the line layer in QGIS

1. Fix the layer's topology: lines must be split and connected at
   intersections (e.g. `Split with lines`, snapping during edits; a snapping
   tolerance matched to your data's scale).
2. Add the attribute columns that will become OSM tags — this is the
   critical step. Every line needs a `highway` value (`residential`,
   `footway`, `path`, `service`, etc.), since a way without one is invisible
   to routing. Add `maxspeed`, `oneway`, `access`, `name` as needed. Column
   names don't have to match OSM tag names exactly — the translation file
   below maps column → tag — but naming columns after the tags directly
   makes the next step trivial.
3. Note the layer's actual CRS before moving on — check the bottom-right
   corner of the QGIS canvas, or `Layer → Properties → Information`. You'll
   need to tell `ogr2osm` exactly this CRS via the `-e` flag below. Getting
   this wrong doesn't produce an error — it silently produces a file that
   opens in JOSM somewhere on the wrong continent (see the warning under
   [Conversion command](#conversion-command)). If you drew the layer by hand
   directly on the canvas without deliberately setting a project CRS first,
   it's very likely still in QGIS's default new-project CRS, **EPSG:4326**
   (plain lat/lon degrees) — not a local projected CRS like EPSG:2180.

### Installing ogr2osm

`ogr2osm` runs outside QGIS, in its own terminal/environment on your machine
(a standalone Python install, e.g. from python.org) — it doesn't touch the
QGIS Python environment and isn't subject to the plugin's "no pip install"
rule, which only governs the Python environment inside QGIS. Don't try to
install it through the QGIS Python console: QGIS bundles its own GDAL tied
to its own QGIS version, and installing a second GDAL into that environment
risks breaking QGIS itself.

- Standard install: `pip install --upgrade ogr2osm` (needs Python 3, GDAL
  with Python bindings, and `lxml`).
- **Windows: this fails on the first try almost every time.** `ogr2osm`
  depends on the `GDAL` package, which has no prebuilt wheel on PyPI for
  Windows, so `pip` falls back to compiling it from source — and fails,
  because that requires the GDAL C/C++ SDK headers, which you don't have
  installed. The error looks like this, and is not a compiler problem to
  chase (installing more Visual Studio components won't fix it):
  ```
  fatal error C1083: Cannot open include file: 'gdal.h': No such file or directory
  ...
  ERROR: Failed building wheel for GDAL
  ```
  The fix is to install a **prebuilt** GDAL wheel first, so `pip` never
  needs to compile anything:
  1. Check your Python version and bitness (`python --version`, or `py --version`)
     — you need a wheel matching it, e.g. Python 3.11 64-bit → `cp311`,
     `win_amd64`.
  2. Find a matching wheel on [Geospatial Wheels releases](https://github.com/cgohlke/geospatial-wheels/releases).
     **The newest release doesn't necessarily cover your Python version** —
     the project only builds for the last few actively maintained CPython
     releases and drops older ones over time (as of this writing, the
     newest release only ships `cp312`/`cp313`/`cp314`). If your version
     isn't in the latest release, check the
     [tags list](https://github.com/cgohlke/geospatial-wheels/tags) and use
     an older release instead — `cp311` (Python 3.11) wheels were last
     published in release `v2025.10.25`.
  3. Install that wheel — `pip install` accepts a direct URL, so there's no
     need to download the file by hand first. For Python 3.11 64-bit:
     ```
     pip install https://github.com/cgohlke/geospatial-wheels/releases/download/v2025.10.25/gdal-3.11.4-cp311-cp311-win_amd64.whl
     ```
     (swap in the filename/version matching your own Python from steps 1–2.)
  4. Now run `pip install ogr2osm` — GDAL is already satisfied, so nothing
     gets compiled.
- **Docker alternative**, if wheel-matching turns out to be too fiddly, or
  your Python version isn't covered by any Geospatial Wheels release at
  all: skip installing Python/GDAL locally entirely.
  ```
  docker run -ti --rm -v <folder>:/app roelderickx/ogr2osm /app/network.gpkg -o /app/network.osm.pbf --pbf
  ```
- Verify the install with `ogr2osm --version`.

### Translation file

**Do you actually need one?** If your QGIS attribute table already has a
column literally named `highway`, with values already spelled the OSM way
(`motorway`, `residential`, `footway`, …), you can skip this whole section
and run `ogr2osm` **without** `-t`. Without a translation file, `ogr2osm`
falls back to what its docs call "identity translation": it "carr[ies] all
tags from the source to the .pbf or .osm output" completely unchanged. A
column named `highway` holding `motorway` becomes the tag `highway=motorway`
automatically — no script involved. This is the case you're describing: if
you already did the tagging yourself in QGIS, identity translation is
exactly enough.

**So what is this script actually for, then?** Two situations where identity
translation isn't enough on its own:

1. **Your column doesn't already speak OSM.** Say your layer has a column
   called `road_type` with numeric codes (`1`, `2`, `3`) instead of a column
   called `highway` with OSM string values. Identity translation would just
   copy that straight through as the tag `road_type=1` — meaningless to OTP,
   which only ever looks for `highway=*`. The script is where you do that
   lookup: read `road_type`, translate `1` → `"motorway"`, and emit it under
   the tag name `highway`.
2. **You want a clean output file, not a dump of every column.** Real GIS
   layers usually carry columns you didn't put there on purpose — `fid`,
   auto-generated IDs, length/area fields, editing metadata. Identity
   translation copies *all* of them into the `.osm` file as tags, verbatim.
   That's harmless for routing (OTP still only reads `highway=*`), but it's
   messy, non-standard OSM data. The script lets you keep only the columns
   you actually want and drop the rest.

A translation file is a Python module that subclasses
`ogr2osm.TranslationBase` and overrides `filter_tags`, which `ogr2osm` calls
once per line, passing in that line's raw attributes as a dict and expecting
the OSM tags dict back:

```python
import ogr2osm

class MyTranslation(ogr2osm.TranslationBase):
    def filter_tags(self, tags):
        # `tags` = the raw attribute dict of one line from the QGIS layer,
        # e.g. {"road_type": "1", "fid": "42", "speed_mph": "30"} — whatever
        # columns your attribute table happens to have.
        out = {}

        # Case 1: translate a non-OSM column/value into a real OSM tag.
        # Replace this dict with whatever your own road_type codes mean.
        road_type_to_highway = {
            "1": "motorway",
            "2": "primary",
            "3": "residential",
        }
        if "road_type" in tags and tags["road_type"] in road_type_to_highway:
            out["highway"] = road_type_to_highway[tags["road_type"]]

        # Case 2: pass a differently-unit'd value through with conversion.
        if "speed_mph" in tags:
            out["maxspeed"] = str(round(float(tags["speed_mph"]) * 1.60934))

        # Case 3: columns that are already correctly named just get copied —
        # this is the same thing identity translation would do for them.
        for tag in ("oneway", "name", "access"):
            if tag in tags:
                out[tag] = tags[tag]

        # Everything else (fid, length, or any other GIS-internal column
        # not listed above) is dropped, since it isn't added to `out`.
        return out
```

Save this as its own file (e.g. `translation.py`), outside the plugin — **it's a standalone script** you **run with** `ogr2osm`, not something QGIS loads. If your case is only "columns already match OSM tag names 1:1" (no renaming, no value lookup, no filtering needed), don't bother writing this file at all — just omit `-t` and let identity translation handle it, as described above.

### Conversion command

open **cmd** inside folder with your geometry and paste command below.

```
ogr2osm network.gpkg -t translation.py -e 2180 -o network.osm.pbf --pbf -f
```

**`2180` is only an example (Poland CS92) — replace it with your own layer's
EPSG code from the previous step, every time.** This is the single most
common way this whole option goes wrong: copying this command as-is when
your layer is actually in a different CRS (very often EPSG:4326, see above).
`ogr2osm` trusts `-e` completely — if you tell it the wrong CRS, it
reprojects your coordinates using the wrong math and writes a perfectly
valid-looking `.osm` file that just happens to describe a location
thousands of kilometers away (a classic symptom: opening the result in JOSM
and finding your network sitting somewhere in the middle of Asia or the
ocean, at plausible-looking but meaningless coordinates). There's no error
message for this — the fix is to open the *source* layer's properties in
QGIS, confirm its real CRS, and use that EPSG code here.

| Flag | What it does |
|---|---|
| `network.gpkg` | Source layer from QGIS. Any GDAL-readable format works; GeoPackage is recommended over shapefile (no field-name-length or type limits). |
| `-t translation.py` | The translation file from the previous step. Omit if you're using identity translation with pre-named columns. |
| `-e 2180` | Explicit source EPSG code — **must match your layer's actual CRS**, not this example. Set this if the layer's CRS metadata isn't reliably read (common when a `.prj` file gets dropped on export, or when a layer was hand-drawn without ever explicitly setting its CRS). `ogr2osm` reprojects to EPSG:4326 automatically, but only if it knows the *correct* source CRS — a wrong `-e` doesn't fail, it silently produces coordinates on the wrong continent (see above); a missing `-e` on a layer with no reliable embedded CRS metadata tends to fail outright or land near 0°,0°. If you're unsure, reproject the layer to a known CRS in QGIS first (`Export → Save Features As…`, pick a CRS explicitly) so there's no ambiguity about what to pass here. |
| `--gis-order` | Add this if the source coordinates use the conventional GIS order (lon, lat / x, y) — the default for most vector layers (shapefile, GeoPackage). Check the current `ogr2osm` README before relying on this, since the flag's exact effect can be non-obvious; a result with swapped lat/lon ("flipped vertically" from the expected location) is the first thing to suspect. |
| `-o network.osm.pbf` | Output path. `ogr2osm` writes exactly whatever filename you give here — it doesn't add or fix extensions for you, so name it `.osm.pbf` yourself. |
| `--pbf` | Write PBF content instead of the default OSM/XML content. **Don't skip this.** Without it, `-o network.osm.pbf` would just be an XML file with a misleading name — and the default output format (plain `.osm`) can't be used as `easy-OTP`'s **OSM extract** input at all (see [Before you start](#before-you-start)). If you want an `.osm` copy too (e.g. to eyeball it as text before converting), run the command twice — once without `--pbf` for inspection, once with it for the file you'll actually load into the plugin. |
| `-f` | Overwrite the output file without prompting. |

**ID safety:** `ogr2osm` doesn't assign positive IDs by default — its
counter starts at `0` and decreases (`0, -1, -2, …`), the same convention
JOSM uses for new, unsubmitted elements. Real OSM/Geofabrik IDs are always
positive, so there's no collision risk and nothing to do manually, as long
as you don't pass `--positive-id`. The one case where you do need to manage
IDs by hand is merging two *separate* `ogr2osm` runs (two different
synthetic layers) into one file — both start counting from `0` by default
and can collide. In that case, use `--saveid file.id` on the first run and
`--idfile file.id` on the second to continue the numbering.

### Verify before continuing

Open the resulting file in JOSM and check visually: is the network in the
right place on the map, and does clicking a way show a `highway=…` tag in
the tag panel? If you generated `network.osm.pbf` directly (with `--pbf`),
opening it in JOSM needs the `pbf` plugin — see the install steps under
[Option 1](#option-1--small-manual-edit-in-josm). If you also generated a
plain `.osm` copy for inspection, that one opens with no extra setup.

If this network needs to connect to the real street network around it (e.g.
footpaths that should meet the sidewalks at the edge of your study area), go
to [Merging into an existing OSM extract](#merging-into-an-existing-osm-extract)
at the end of this guide. If it's meant to be a fully isolated graph (a
closed site, a campus with no connection to the surrounding city), skip
straight to using `network.osm.pbf` as your **OSM extract** parameter.

A network made only of geometry, with no tags, will not work — OTP 1.5.0
only builds edges where `highway=*` (or `railway=platform`) is present. The
translation file or the `highway` column isn't optional decoration; it's a
hard requirement.

---

## Option 3 — Edit tags and speeds without changing geometry

Use this when the geometry is fine and you want to change how OTP treats it
— speed, access, availability. Example: making all motorways route at
100 km/h.

**Recommended: edit the `maxspeed` tag in OSM data.**

1. Open the OSM file in JOSM (or process it with a script using
   `osmium`/`pyosmium`). If your file is already `.osm.pbf`, you need JOSM's
   `pbf` plugin installed first — see the install steps under
   [Option 1](#option-1--small-manual-edit-in-josm); JOSM can't open or save
   `.osm.pbf` without it.
2. Find ways tagged `highway=motorway` (in JOSM: `Search` →
   `highway=motorway`).
3. Set `maxspeed=100` on them (unit is km/h by default).
4. Save and rebuild the graph. If you're saving through `File → Save As`,
   pick the "OSM Server Files PBF Compressed (\*.osm.pbf)" file type and
   double-check the filename ends in `.osm.pbf` before clicking Save (see
   the note under [Option 1](#option-1--small-manual-edit-in-josm)) — the
   change only takes effect once you rebuild the graph on that saved file.

Verify the actual effect afterward (see [Manual verification](#manual-verification)):
there have historically been cases where OSM's `maxspeed` tag was overridden
by OTP's default speed table, so don't assume the edit took effect without
checking a routed trip.

**Limited alternative: `osmWayPropertySet` in `build-config.json`.** In OTP
1.5.0 this key only accepts one of three preset values — `default`
(US/California), `norway`, `uk` — selecting an entire country's rule set for
permissions and speeds, not a per-tag override like "motorway = 100". A
genuinely custom speed table per road type means writing your own
`WayPropertySet` class in Java and recompiling OTP, which is well outside
"just prepare a file". For the "motorways at 100 km/h" goal, editing the
`maxspeed` tag in the OSM data (above) is the practical answer.

---

## Merging into an existing OSM extract
*Waiting for manual check*

### When you actually need this

- **Option 1 (JOSM edits)** — you're editing a file that's already a full
  extract. No merge needed; save the same file.
- **Option 3 (tag edits)** — same, an in-place edit of an existing file. No
  merge needed.
- **Option 2 (QGIS → `ogr2osm`)** — the output is a new, separate file
  containing only your synthetic network. You need this section only if
  that network should connect to the real network around it (a footbridge
  that ties into existing sidewalks, for example). If the network is meant
  to be fully isolated, skip this section entirely and use the synthetic
  file directly as your OSM extract.

### Why this is safe — ID conventions

The only real risk when merging is an ID collision — two different objects
in the merged file sharing an ID. If your synthetic network came from
`ogr2osm` without the `--positive-id` flag (the default, see the conversion
command above), its IDs are `0, -1, -2, …` — negative, in the same
convention OSM uses for new/local objects (the same one JOSM uses for
newly-drawn, unsubmitted elements). Real IDs from Geofabrik/OSM are always
positive. These two ID spaces never overlap, so there's nothing to do
manually, as long as you didn't change the default ID flags in `ogr2osm`.

### Recommended method — osmium sort

[`osmium-tool`](https://osmcode.org/osmium-tool/) (built on `libosmium`, C++,
fast) combines and sorts in a single command.

This requires `conda` (there's no official prebuilt `.exe` for Windows, and
`osmium-tool` isn't on PyPI — `pip install osmium-tool` will not work, since
`pip` only installs Python packages and this is a compiled C++ tool):

```
conda install -c conda-forge osmium-tool
```

**Don't have conda installed?** You don't need the full Anaconda distribution
— [Miniforge](https://github.com/conda-forge/miniforge) is a minimal
installer preconfigured for the `conda-forge` channel used above. Download
[`Miniforge3-Windows-x86_64.exe`](https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe),
run it, then use the "Miniforge Prompt" it adds to your Start menu to run the
`conda install` command above (a plain `cmd`/PowerShell window won't have
`conda` on its `PATH` unless you chose that option during install).

If you'd rather not install a package manager just for this one tool, skip
straight to [Osmosis below](#alternative-method--osmosis) — it only needs
Java, which you already have installed for OTP. WSL users can alternatively
install `osmium-tool` through their Linux distribution's package manager
(e.g. `apt install osmium-tool` on Ubuntu/Debian).

Combine and sort in one step — `osmium sort` reads both files, sorts their
combined contents by type → ID → version (negative IDs before positive, per
the ID convention above), and writes one output file. It does not need a
separate pre-sort step first, unlike `osmium merge`:

```
osmium sort network.osm city.osm.pbf -o merged.osm.pbf
```

Duplicates aren't removed by `osmium sort`, but that doesn't matter here
since the ID spaces don't overlap (see above) — it only matters if your
inputs genuinely contain duplicate objects, which isn't the case for this
workflow.

Provide `merged.osm.pbf` as the **OSM extract** parameter.

### Alternative method — Osmosis

If you'd rather avoid conda or WSL, [Osmosis](https://wiki.openstreetmap.org/wiki/Osmosis)
is a pure-Java tool distributed as a ready-to-run `.zip` — no compilation
needed.

Osmosis requires a newer Java runtime (17+) than the Java 8 that `easy-OTP`
pins for running the OTP engine itself. These are two independent Java
installations on the same machine — Osmosis can use any sufficiently new
Java you have installed (e.g. your system Java), and it does not need to be
the same binary configured in the plugin's settings. This isn't a
contradiction of the Java 8 requirement elsewhere in this project; that pin
only applies to running `otp-1.5.0-shaded.jar`.

1. Download the latest release (a `.zip`) from
   [Osmosis releases](https://github.com/openstreetmap/osmosis/releases/latest)
   and unzip it anywhere.
2. Unlike `osmium sort`, Osmosis needs both inputs pre-sorted before
   merging:

```
osmosis --rx network.osm --sort --wx network.sorted.osm
osmosis --rb city.osm.pbf --sort --wb city.sorted.osm.pbf
osmosis --rx network.sorted.osm --rb city.sorted.osm.pbf --merge --wb merged.osm.pbf
```

`--rx`/`--wx` read/write XML `.osm`; `--rb`/`--wb` read/write `.pbf` — the
input formats can be mixed, Osmosis handles the conversion internally.

### Verify after merging

- Open `merged.osm.pbf` in JOSM and check visually that the synthetic
  network shares nodes with the real network where it's supposed to connect
  (being visually adjacent isn't enough — see the connectivity rule under
  [Before you start](#before-you-start)).
- Build the graph in `easy-OTP` on `merged.osm.pbf` and compare the build
  log's node/edge counts against a build on `city.osm.pbf` alone — the
  difference should match the size of the network you added.

---

## Common pitfalls

- Saving in the wrong format (shapefile/GeoJSON instead of OSM) — OTP builds
  nothing from it.
- Ending up with a plain `.osm` file and trying to use it as the **OSM
  extract** parameter — `easy-OTP`'s file picker only accepts `.osm.pbf`, no
  matter what OTP itself could technically read. `.osm` is fine as an
  intermediate, inspection-only format; always convert or save as
  `.osm.pbf` before the last step (see [Before you start](#before-you-start)).
- Saving from JOSM without the `pbf` plugin installed — JOSM has no native
  `.osm.pbf` support, so `File → Open`/`Save As` won't even offer it until
  you install the plugin (`F12 → Plugins`, search `pbf`).
- A way with no `highway=*` tag — invisible to routing.
- No shared nodes at intersections — an "island" with no connection.
- Wrong coordinate order or CRS in `ogr2osm` — missing/wrong `-e EPSG`, or a
  needed `--gis-order` flag (see the conversion command table above). The
  telltale symptom: the network opens in JOSM at a valid-looking but
  nonsensical location, often far outside your study area entirely (a
  different continent, the ocean). No error is raised — `ogr2osm` just
  reprojected with the wrong source CRS. Fix by checking the source layer's
  actual CRS in QGIS and matching `-e` to it exactly, not copying this
  guide's example value.
- ID collisions when merging — in practice these don't happen if you keep
  `ogr2osm`'s default negative IDs; the real risk is only when merging two
  of your own synthetic files without `--saveid`/`--idfile`.
- A disconnected synthetic network — GTFS stops and origin points won't snap
  to it; snapping only works against a network that's actually connected.

---

## Manual verification

After preparing your file, verify it worked by:

1. Building the graph in `easy-OTP` and checking the build log confirms the
   node/edge count changed the way you expected.
2. Enabling OTP's debug layers in the browser (`?debug_layers=true`) and
   visually confirming added ways are routable, and closed ones aren't.
3. Running one surface with an origin near the edit and comparing the
   resulting travel time against a run on the unedited data — the effect
   should be visible.
4. For Option 3 (speed changes): compute a driving route over the modified
   motorway and check that the travel time implies roughly 100 km/h,
   confirming the `maxspeed` edit actually took effect.
5. As a topology sanity check: confirm a way with no shared node to the rest
   of the network is unreachable in a routed trip — this proves connectivity
   is decided by the node, not the way the lines look on a map.

---

## Sources

- [JOSM Introduction (editor basics: nodes, ways, tags, download/upload workflow)](https://josm.openstreetmap.de/wiki/Introduction)
- [JOSM PBF plugin (required to open/save .osm.pbf in JOSM)](https://wiki.openstreetmap.org/wiki/JOSM/Plugins/PBF)
- [OpenTripPlanner for R — Introduction (rOpenSci vignette)](https://docs.ropensci.org/opentripplanner/articles/opentripplanner.html)
- [Same vignette, CRAN mirror](https://cran.r-project.org/web/packages/opentripplanner/vignettes/opentripplanner.html)
- [Marcus Young's OTP tutorial repository](https://github.com/marcusyoung/otp-tutorial)
- [OTP 1.5.0 Configuration docs](https://docs.opentripplanner.org/en/v1.5.0/Configuration/)
- [OTP Basic Tutorial (accepted OSM formats, osmium/osmosis/osmconvert)](https://docs.opentripplanner.org/en/latest/Basic-Tutorial/)
- [GraphBuilder / WayPropertySet wiki](https://github.com/opentripplanner/OpenTripPlanner/wiki/graphbuilder)
- [OTP issue #996 — historical `maxspeed` handling](https://github.com/opentripplanner/OpenTripPlanner/issues/996)
- [`ogr2osm` README](https://github.com/roelderickx/ogr2osm)
- [Geospatial Wheels (prebuilt GDAL wheels for Windows)](https://github.com/cgohlke/geospatial-wheels)
- [`osmium sort` documentation](https://docs.osmcode.org/osmium/latest/osmium-sort.html)
- [`osmium merge` documentation](https://docs.osmcode.org/osmium/latest/osmium-merge.html)
- [`osmium-tool` on conda-forge](https://anaconda.org/conda-forge/osmium-tool)
- [Osmosis wiki](https://wiki.openstreetmap.org/wiki/Osmosis)
- [Osmosis releases](https://github.com/openstreetmap/osmosis/releases/latest)
- [Osmosis Detailed Usage wiki](https://wiki.openstreetmap.org/wiki/Osmosis/Detailed_Usage)
- [Import a shapefile — tool comparison wiki](https://wiki.openstreetmap.org/wiki/Software_comparison/Import_a_shapefile)
