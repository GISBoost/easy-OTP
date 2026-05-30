# easy-OTP

A QGIS processing plugin that automates temporal accessibility analysis for public
transport using OpenTripPlanner 1.5.0. For each minute of the analysis time window
the plugin generates one travel-time surface, counts how many minutes each hexagonal
grid cell is within your travel-time threshold, and classifies cells into four
service-time categories consistent with the academic literature.

Requires **QGIS 3.40 LTR** or newer. No R, no GRASS, no `pip install`.

---

## Download

Download the latest release ZIP from GitHub:

**[easy-OTP Releases → https://github.com/GISBoost/easy-OTP/releases/latest](https://github.com/GISBoost/easy-OTP/releases/latest)**

Download `easy_otp-0.1.0.zip` from the Assets section. This is the correctly
structured plugin ZIP — do **not** use the auto-generated "Source code" archives
on the same page, as those have the wrong directory layout for QGIS.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| QGIS | 3.40 LTR+ | Plugin uses the bundled Python and GDAL |
| Java | **8 (1.8)** | Portable distribution recommended — see below |
| OpenTripPlanner jar | **1.5.0** | `otp-1.5.0-shaded.jar` — see below |
| OSM extract | any | `.osm.pbf` covering your study area |
| GTFS feed(s) | any valid | One or more `.zip` archives in a folder |

---

## Getting Java 8

OTP 1.5.0 requires **exactly Java 8**. Use a portable (no-installer) build so you
can keep it separate from any other Java on your machine.

1. Go to **Eclipse Temurin releases**:
   `https://adoptium.net/temurin/releases/?version=8`
2. Select your OS, architecture (x64), package type **zip** (Windows) or
   **tar.gz** (Linux/Mac), version **JRE** (a full JDK works too).
3. Unpack the archive. Recommended: keep it together with your OTP jar and
   analysis data in one dedicated folder, e.g.:
   - Windows: `C:\otp\java\`
   - Linux/Mac: `~/otp/java/`
4. Note the path to the `java` executable:
   - Windows: `C:\otp\java\bin\java.exe`
   - Linux/Mac: `~/otp/java/bin/java`

You will enter this path in the **Java 8 binary** parameter of the plugin.

---

## Getting otp-1.5.0-shaded.jar

1. Open Maven Central search:
   `https://central.sonatype.com/artifact/org.opentripplanner/otp/1.5.0`
2. Download the file named `otp-1.5.0-shaded.jar` (classifier `shaded`,
   ~47 MB). Do **not** download the plain `otp-1.5.0.jar` — it is not
   executable on its own.
3. Save it in your OTP folder alongside Java and your analysis data, e.g.
   `C:\otp\otp-1.5.0-shaded.jar`.

> **Tip — keep everything in one folder.** The recommended layout is:
> ```
> C:\otp\
> ├── java\                   ← unpacked portable Java 8 JRE
> ├── otp-1.5.0-shaded.jar    ← OTP executable
> ├── city.osm.pbf            ← OSM extract
> ├── gtfs\                   ← GTFS feeds
> └── work\                   ← working directory for the plugin
> ```
> This way all OTP-related files are in one place and easy to back up or move.

You will enter this path in the **OpenTripPlanner 1.5.0 jar** parameter.

---

## Plugin Installation

1. Download `easy_otp-0.1.0.zip` from the [Releases page](https://github.com/GISBoost/easy-OTP/releases/latest).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the downloaded `easy_otp-0.1.0.zip` and click **Install Plugin**.
4. After installation, **Plugins → easy-OTP → Enable** (if not enabled
   automatically).
5. The algorithms appear in **Processing Toolbox** under the **easy-OTP** group.

---

## Configuration

Open any easy-OTP algorithm in the Processing Toolbox. The key parameters to
configure once are in the **Advanced** section:

| Parameter | What to enter |
|---|---|
| **Java 8 binary** | Full path to `java.exe` / `java` from your portable Java 8 install |
| **OpenTripPlanner 1.5.0 jar** | Full path to `otp-1.5.0-shaded.jar` |
| **Working directory** | An empty folder where the plugin can write surfaces, graph cache, and logs |
| **OTP server port** | Default `8801`; change only if that port is taken |
| **OTP heap for graph build** | Default `2G`; increase to `4G` for large cities |
| **OTP heap for analyst server** | Default `4G`; increase if OTP crashes during surface generation |

Run **Diagnostics → Test OTP server** first to verify Java and jar are found
correctly before attempting a full run.

---

## Example Run

```
Input data
├── wroclaw.osm.pbf        ← OSM extract of the study area
└── gtfs/
    └── wroclaw_gtfs.zip   ← GTFS feed

Working directory
└── work/                  ← empty folder; plugin writes everything here
```

1. Open **Processing Toolbox → easy-OTP → Analysis → Run temporal accessibility**.
2. Fill in the required parameters:
   - **OSM extract**: `wroclaw.osm.pbf`
   - **GTFS folder**: `gtfs/`
   - **Origin point**: click the map to pick a central location (e.g. the main
     railway station)
   - **Analysis date**: a weekday date covered by your GTFS feed
   - **Window start / end**: `06:00` / `22:00`
   - **Sampling interval**: `1 min` (or `15 min` for a quick test)
   - **Travel-time threshold**: `30` minutes
   - **Working directory**: `work/`
3. In **Advanced**, enter the Java and jar paths configured above.
4. Enable **Generate hex grid** (or supply your own polygon grid layer).
5. Click **Run**.

The plugin will:
- Build the OTP graph (first run only; subsequent runs use the cache).
- Start the OTP analyst server.
- Generate one travel-time surface per minute.
- Count pixels below the threshold.
- Run zonal statistics on the hex grid.
- Classify cells into four service-time categories.
- Load the result into QGIS with the built-in style applied.

A full 1-minute window run (961 surfaces) on Wrocław-sized data takes roughly
20–25 minutes. A 15-minute window run (65 surfaces) takes about 2 minutes.

---

## Running Unit Tests

The following tests do **not** require a running OTP server or QGIS:

```bash
python -m pytest easy_otp/test/test_time_utils.py -v
python -m pytest easy_otp/test/test_classification.py -v
```

The raster-counting tests require GDAL and must run inside the QGIS Python
interpreter (e.g. from the QGIS Python console or `python-qgis`):

```bash
python -m pytest easy_otp/test/test_raster_processing.py -v
```

---

## Troubleshooting

### "OTP 1.5.0 requires Java 8; detected version X"
Your `Java 8 binary` parameter points to a different Java version. Follow the
[Getting Java 8](#getting-java-8) section and update the parameter.

### "OTP 1.5.0 jar not found at: ..."
The path in `OpenTripPlanner 1.5.0 jar` is wrong or the file has not been
downloaded. Follow the [Getting otp-1.5.0-shaded.jar](#getting-otp-150-shaded-jar)
section.

### "Port 8801 is held by a non-OTP process"
Something else is using port 8801. Either stop that service or change the
**OTP server port** parameter (e.g. to `8802`).

### OTP graph build fails
Check the build log at `<working directory>/otp_build_<hash>.log`. Common causes:
- **Wrong Java version** — verify with *Test OTP server*.
- **Corrupt GTFS** — OTP logs a specific error; re-download the feed.
- **Not enough memory** — increase `OTP heap for graph build` to `4G` or `8G`.

### All surfaces return "unreachable"
Run **Diagnostics → Test OTP server** and check the router diagnostic printed at
the start of the run:
- `hasTransit = False` → the GTFS was not loaded; check the build log.
- `transitServiceStarts / transitServiceEnds` does not cover `ANALYSIS_DATE` →
  use a date within your GTFS service range.
- `Origin (lat, lon)` outside the router polygon bbox → pick an origin point
  inside the OSM coverage area.

### Java process left running after QGIS crash
On Windows, open Task Manager and end any `java.exe` processes manually.
Under normal circumstances (including user-initiated Cancel) the plugin
terminates the OTP process automatically.
