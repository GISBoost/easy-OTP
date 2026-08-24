# Jak zrobić tę analizę ręcznie, krok po kroku

Ten plik zakłada, że **nie** chcesz odpalać gotowych skryptów, tylko rozumieć i
wpisywać komendy samemu — np. żeby zrobić to dla siódmego miasta, albo zmienić coś
w środku. Dla samego odtworzenia wyniku wystarczy `run_city_pipeline.sh <miasto>`
(patrz `HANDOFF`-owa dokumentacja w `MULTI_CITY_ANALYSIS.md`) — to jest wersja
"rozpisana na krokach", żeby zrozumieć co się dzieje w każdym z nich.

## Krok 0 — czego potrzebujesz zainstalowanego

- **R** (4.6+) — `winget install RProject.R`, potem pakiety do własnej biblioteki
  (domyślna nie ma uprawnień zapisu bez admina):
  ```
  mkdir "C:\Users\<user>\Documents\R\win-library\4.6"
  set R_LIBS_USER=C:\Users\<user>\Documents\R\win-library\4.6
  Rscript -e "options(repos=c(CRAN='https://cloud.r-project.org')); install.packages(c('r5r','rJavaEnv','sf','data.table'), lib=Sys.getenv('R_LIBS_USER'), type='win.binary')"
  ```
- **JDK 21** (r5r tego wymaga — nie systemowa Java, jeśli masz inną wersję):
  ```
  Rscript -e ".libPaths(c(Sys.getenv('R_LIBS_USER'), .libPaths())); rJavaEnv::java_quick_install(version=21)"
  ```
  Zapamiętaj ścieżkę, którą wypisze (`...rJavaEnv/installed/windows/x64/21`) —
  będziesz jej używać jako `JAVA_HOME` w każdej sesji terminala.
- **osmosis** (do przycinania danych OSM) — jeśli masz zainstalowany JOSM, jest w
  `<JOSM>\osmosis\bin\osmosis.bat`. Jeśli nie: https://wiki.openstreetmap.org/wiki/Osmosis
- **QGIS** z wtyczką MCP (do wizualizacji) — już masz, jeśli robiłeś wcześniejsze
  analizy w tym repo.

## Krok 1 — dane wejściowe: skąd co wziąć

| dane | źródło | jak pobrać |
|---|---|---|
| Sieć drogowa (OSM) | Geofabrik, wyciąg wojewódzki | `https://download.geofabrik.de/europe/poland/<wojewodztwo>-latest.osm.pbf` (np. `mazowieckie` dla Warszawy) |
| Zrealizowany GTFS | Release'y `GISBoost/easy-GTFS-RT` | `gh release list --repo GISBoost/easy-GTFS-RT` — szukaj tagu `<miasto>-realized-<data>-phone`, potem `gh release download <tag> --repo GISBoost/easy-GTFS-RT --pattern "*_p50.zip"` |
| Granica miasta / obwody spisowe | Już masz w `tools/ses_income_lodz/<miasto>.gpkg` (warstwa `obwody_spisowe`) | — |
| Populacja 20-29 lat | `docs/gis/ludnosc_nsp_2021.xlsx`, arkusz właściwego województwa | wczytać przez `easy_otp/core/xlsx_reader.py` (patrz Krok 2) |
| Usługi publiczne / budynki uczelni | OpenStreetMap, przez Overpass API | zapytanie `area["name"="<Miasto>"]["admin_level"="6"]["boundary"="administrative"]` + filtr tagów (patrz Krok 4) |

## Krok 2 — przygotowanie sieci OSM (przycięcie do miasta)

Pobrany plik wojewódzki ma 100-300 MB i pokrywa dużo więcej niż samo miasto — r5r
by to policzył, ale zbudowanie sieci trwałoby dużo dłużej niż trzeba. Przytnij:

```bash
# bbox miasta -- weź extent obwody_spisowe (WGS84) + 0.02 stopnia marginesu,
# np. w Pythonie: geopandas.read_file(...).to_crs(4326).total_bounds
"C:\Users\<user>\josm\osmosis\bin\osmosis.bat" \
  --read-pbf file=mazowieckie.osm.pbf \
  --bounding-box left=20.85 bottom=52.10 right=21.27 top=52.37 completeWays=yes \
  --write-pbf file=warszawa.osm.pbf
```

**`completeWays=yes` jest obowiązkowe** — bez tego niektóre drogi/relacje na
granicy bboxa zostają obcięte z wiszącymi referencjami do węzłów, co potrafi
wywalić `setup_r5()` w R (znaleziono na żywo przy Krakowie, `NullPointerException`
przy budowie stref parkingowych). Nie zawsze crashuje od razu — bez tej flagi
możesz dostać pozornie działającą, ale ciut niedokładną sieć przy granicy obszaru.

## Krok 3 — pobranie zrealizowanego GTFS

```bash
"C:\Program Files\GitHub CLI\gh.exe" release list --repo GISBoost/easy-GTFS-RT --limit 50
# znajdź tag typu warszawa-realized-2026-08-22-phone
"C:\Program Files\GitHub CLI\gh.exe" release download warszawa-realized-2026-08-22-phone \
  --repo GISBoost/easy-GTFS-RT --pattern "*_p50.zip" --output warszawa_gtfs.zip
```

P50 = wariant zbudowany z mediany rzeczywistych prędkości (nie z rozkładu na
papierze) — to jest kluczowa różnica względem typowej analizy dostępności.
Umieść `warszawa.osm.pbf` i `warszawa_gtfs.zip` **w tym samym folderze** —
r5r skanuje cały folder i sam znajdzie oba pliki po rozszerzeniu.

## Krok 4 — siatka heksagonalna 500m (w QGIS, przez Python)

Otwórz konsolę Python w QGIS (lub użyj `mcp__qgis__execute_code`, jeśli masz
dostęp do MCP) i wpisz:

```python
import processing
from qgis.core import QgsVectorLayer, QgsVectorFileWriter, QgsCoordinateTransformContext

obwody = QgsVectorLayer("warszawa.gpkg|layername=obwody_spisowe", "obwody", "ogr")
dissolved = processing.run("native:dissolve", {"INPUT": obwody, "FIELD": [], "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]

bbox = dissolved.extent()
extent_str = f"{bbox.xMinimum()},{bbox.xMaximum()},{bbox.yMinimum()},{bbox.yMaximum()} [{obwody.crs().authid()}]"
grid = processing.run("native:creategrid", {
    "TYPE": 4, "EXTENT": extent_str, "HSPACING": 500, "VSPACING": 500,
    "HOVERLAY": 0, "VOVERLAY": 0, "CRS": obwody.crs(), "OUTPUT": "TEMPORARY_OUTPUT",
})["OUTPUT"]

# WAŻNE: extractbylocation (whole-hex), NIE clip -- inaczej heksagony są ucinane
# na granicy i wyglądają inaczej niż reszta serii map w tym projekcie
whole = processing.run("native:extractbylocation", {
    "INPUT": grid, "PREDICATE": [0], "INTERSECT": dissolved, "OUTPUT": "TEMPORARY_OUTPUT",
})["OUTPUT"]

# dodaj pole hex_id (autoincrement), potem zapisz jako GPKG warstwa "hex500"
```

Zapisz jako `warszawa_hex500.gpkg`, warstwa `hex500`. Wyeksportuj centroidy do
CSV (`id, lon, lat`, WGS84) — to będą origins dla r5r.

## Krok 5 — punkty docelowe: usługi i uczelnie (Overpass)

```python
import urllib.request, urllib.parse, json

query = """
[out:json][timeout:180];
area["name"="Warszawa"]["admin_level"="6"]["boundary"="administrative"]->.a;
(
  node["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|library|community_centre)$"](area.a);
  way["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|library|community_centre)$"](area.a);
  node["shop"="supermarket"](area.a);
  way["shop"="supermarket"](area.a);
);
out center tags;
"""
body = urllib.parse.urlencode({"data": query}).encode()
req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=body)
result = json.load(urllib.request.urlopen(req, timeout=200))
```

Dla uczelni: to samo zapytanie, ale `amenity~"^(university|college)$"` +
`building="university"`, potem dopasuj `tags["name"]`/`tags["operator"]` do
nazwy uczelni regexem (np. `politechnik.*warszawsk`). **Uwaga**: Overpass ma
rate-limit — przy wielu zapytaniach pod rząd (kilka miast × 2 zapytania) rób
20-30 sekund przerwy między nimi, inaczej dostaniesz `HTTP 429`.

Zapisz jako CSV: `category/university, lon, lat`. Do r5r potrzebujesz formatu
szerokiego (jedna kolumna 0/1 per kategoria + `total`) — po prostu pivotuj.

## Krok 6 — samo obliczenie w R

To jest sedno — komenda, którą "wpisujesz do R":

```r
# ustaw JAVA_HOME PRZED library(r5r) -- inaczej rJava nie znajdzie właściwej Javy
Sys.setenv(JAVA_HOME = "C:/Users/<user>/AppData/Local/R/cache/R/rJavaEnv/installed/windows/x64/21")
.libPaths(c("C:/Users/<user>/Documents/R/win-library/4.6", .libPaths()))

library(r5r)
library(data.table)
options(java.parameters = "-Xmx4G")

# data_path = folder z warszawa.osm.pbf + warszawa_gtfs.zip razem
r5r_core <- setup_r5(data_path = "warszawa", verbose = FALSE)

origins <- fread("warszawa_hex_origins.csv", colClasses = list(character = "id"))
destinations <- fread("warszawa_service_destinations.csv", colClasses = list(character = "id"))

acc <- accessibility(
  r5r_core,
  origins = origins,
  destinations = destinations,
  opportunities_colnames = c("opp0", "opp1", "opp2", "opp3", "total"),  # nazwy kolumn z Kroku 5
  mode = c("WALK", "TRANSIT"),
  departure_datetime = as.POSIXct("24-08-2026 07:00:00", format = "%d-%m-%Y %H:%M:%S"),  # sprawdź w calendar.txt/calendar_dates.txt że to dzień powszedni z aktywnym serwisem, nie zgaduj!
  time_window = 120,        # okno 07:00-09:00, r5r liczy medianę z próbek co minutę
  max_trip_duration = 90,
  decay_function = "step",  # liczy PROGOWO (ile celów w zasięgu), nie funkcją zanikania
  cutoffs = c(15, 30, 45, 60),
  progress = TRUE
)

fwrite(acc, "warszawa_service_accessibility.csv")
stop_r5(r5r_core)
```

**Co czytać w wyniku**: format długi, jeden wiersz = (`id` originu, `opportunity`,
`cutoff`, `accessibility`). `accessibility` = liczba punktów danej kategorii
osiągalnych w danym progu czasowym — **nie** populacja, **nie** procent, tylko
surowa liczba miejsc. Powtórz ten sam blok z `warszawa_uni_destinations.csv`
zamiast `service_destinations.csv`, żeby policzyć dostępność do uczelni.

## Krok 7 — wczytanie do QGIS i stylizacja

1. **Pivot długiego CSV do szerokiego** (Python/pandas albo Excel — jedna kolumna
   per `{kategoria}_{próg}min`) i zapisanie z powrotem do `hex500.gpkg` jako nowe
   pola atrybutowe (join po `hex_id`).
2. **Wczytaj warstwę** w QGIS: `Warstwa → Dodaj warstwę → Wektorową`, wskaż
   `warszawa_hex500.gpkg`, warstwa `hex500`.
3. **Stylizacja "poziom dostępności"** (jedna zmienna, ciągła): Właściwości
   warstwy → Styl → **Stopniowany** (graduated), pole `total_30min`, rampa
   kolorów **RdYlGn**, 7 klas.
4. **Stylizacja "dominująca uczelnia" (dwuwymiarowa)**: to wymaga ręcznego
   przygotowania — dla każdego heksagonu policz, która uczelnia ma najwyższą
   liczbę budynków w zasięgu (`argmax` po kolumnach `{uczelnia}_30min`), przypisz
   kategorię, i osobno tercyl populacji (`pandas.qcut(pop, 3)`). Zbuduj tabelę
   9-12 kombinacji (uczelnia × tercyl) → kolor (3 rodziny barw, po 3 odcienie
   jasny→ciemny). Zapisz jako pole tekstowe `biv_color` (kod HEX) w atrybutach.
   W QGIS: Właściwości warstwy → Styl → **Kategoryzowany**, pole = wyrażenie
   `"dominant_university" || '_' || "pop_tercile"`, dla każdej kategorii ustaw
   kolor ręcznie na wartość z `biv_color` (albo zbuduj renderer programowo przez
   konsolę Python QGIS — patrz `analyze_universities.py`/warstwa `wszystkie_
   miasta_dostepnosc.qgz` w tym repo jako gotowy przykład).
5. **Eksport mapy**: `Projekt → Zaimportuj/Eksportuj → Eksportuj mapę jako obraz`,
   albo przez konsolę: `QgsMapRendererParallelJob`/`iface.mapCanvas().saveAsImage(...)`.

## Krok 8 — korelacja dochód × dostępność (dekyle)

```python
import pandas as pd, statistics

ses = pd.read_csv("warszawa_hex_ses.csv")           # hex_id, income_index_pln, ...
acc = pd.read_csv("warszawa_service_accessibility_wide.csv")  # hex_id, total_30min, ...
df = ses.merge(acc, on="hex_id").dropna(subset=["income_index_pln"])

df["decyl"] = pd.qcut(df["income_index_pln"], 10, labels=False) + 1
print(df.groupby("decyl")["total_30min"].mean())
print("r =", statistics.correlation(df["income_index_pln"], df["total_30min"]))
```

To wystarczy do samodzielnego odtworzenia całości. Gotowe skrypty w tym folderze
(`export_hex_data.py`, `prepare_destinations.py`, `run_accessibility.R`,
`analyze_services_income.py`, `analyze_universities.py`) robią dokładnie to
samo, tylko sparametryzowane pod dowolne miasto z `cities_config.py`.
