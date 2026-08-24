# Handoff: warstwa SES (dochód szacowany) dla 6 miast — jak to zrobiono, jak odtworzyć

**Zobacz najpierw `METHODOLOGY.md`** (co i dlaczego liczymy, wzory, ograniczenia, źródła).
Ten plik opisuje **jak dokładnie** to zrobiono technicznie — żeby dało się odtworzyć albo
dodać kolejne miasto.

## 1. Wynik końcowy

6 plików GPKG w tym folderze, każdy z 2 warstwami:

```
lodz.gpkg      → obwody_spisowe (3854 obiektów), obwody_glosowania (283)
krakow.gpkg    → obwody_spisowe (4614),          obwody_glosowania (412)
warszawa.gpkg  → obwody_spisowe (8849),           obwody_glosowania (805)
poznan.gpkg    → obwody_spisowe (2715),           obwody_glosowania (258)
gdansk.gpkg    → obwody_spisowe (2497),           obwody_glosowania (202)
szczecin.gpkg  → obwody_spisowe (1935),           obwody_glosowania (207)
```

`obwody_spisowe` = warstwa wynikowa (do dalszych analiz — łącz po tym). `obwody_glosowania`
= referencja/audyt (domyślnie ukryta w projekcie QGIS).

Od 2026-08-22 `obwody_spisowe` ma też pola struktury rodzin/gospodarstw domowych
(`fam_*`, `hh_*` — patrz METHODOLOGY.md §4a i §3.4 niżej).

Projekt QGIS ze wszystkimi 6 miastami wczytanymi i wystylowanymi (RdYlGn, kwantyle 7 klas na
`income_index_pln`): **`docs/gis/lodz_ses_dochod.qgz`**.

## 2. Tabela weryfikacyjna (żeby ufać danym bez ponownego liczenia)

| Miasto | Obwody spisowe | Populacja (suma) | Populacja oficjalna GUS | Zgodność | Obwody głosowania | Rozbieżność vs oficjalny PKW (głosy) |
|---|---|---|---|---|---|---|
| Łódź | 3854 | 669 995 | 670 642 | 99.9% | 283 | nie liczono (dane z ArcGIS geometrii Łodzi, nie z tileset) |
| Kraków | 4614 | 800 653 | 800 653 | dokładna | 412 | nie liczono |
| Warszawa | 8849 | 1 860 281 | 1 860 281 | dokładna | 805 | **0 na 805** |
| Poznań | 2715 | 546 859 | 546 859 (546.9 tys. wg poznan.stat.gov.pl) | dokładna | 258 | **0 na 258** |
| Gdańsk | 2497 | 486 022 | 486 022 | dokładna | 202 | **0 na 202** |
| Szczecin | 1935 | 396 168 | 396 168 | dokładna | 207 | **0 na 207** |

Sposób weryfikacji populacji: suma pola `population` w `obwody_spisowe` porównana z sumą
delegatur/dzielnic w arkuszu GUS (kolumna "Ogółem", wiersz "miasto na prawach powiatu").
Sposób weryfikacji głosów (tylko Warszawa/Poznań/Gdańsk/Szczecin, dane z `wybory.it`): każdy
rekord dopasowany po (teryt, numer_obwodu) do osobno pobranego oficjalnego CSV PKW
(`{miasto}_wyniki_listy.csv` — **usunięty w ramach porządków**, patrz §5; do ponownego
pobrania: sekcja 4 poniżej), porównane pole po polu.

## 3. Pipeline krok po kroku (jak to faktycznie powstało)

### 3.1 Dla Łodzi i Krakowa (oficjalna geometria z portali miejskich)

1. Filtr warstwy nationwide `docs/gis/SU_BREC_2021_OBW/SU_BREC_2021_OBW.shp` po polu `GMINA`
   (kody TERYT ustalone przez wyszukanie miasta w arkuszu GUS — **nigdy nie zgadywać**, patrz
   METHODOLOGY.md §5.1) → `native:saveselectedfeatures`.
2. `extract_population_generic.py <rows_wojewodztwo.json> <out.csv>` — wyciąga ludność z
   arkusza GUS per obwód spisowy. `<rows_wojewodztwo.json>` = zrzut arkusza przez
   `easy_otp/core/xlsx_reader.py` (uruchamiane interpreterem Pythona z instalacji QGIS, bo tylko
   tam jest zainstalowany `openpyxl` — patrz §4.1).
3. `native:joinattributestable` (populacja → geometria po polu `OBWOD`, `METHOD=1`,
   `DISCARD_NONMATCHING=false`).
4. Pobranie granic obwodów głosowania z ArcGIS REST danego miasta (query `?f=geojson&outSR=4326`),
   `native:fixgeometries` (poligony z ArcGIS bywają invalid).
5. `compute_precinct_income.py` / `compute_precinct_income_generic.py <wyniki_listy.csv> <out.csv>`
   — liczy `income_index_pln` z oficjalnego CSV PKW (patrz METHODOLOGY.md Krok 1–2).
6. Join CSV dochodowego → geometria obwodów głosowania (po numerze obwodu, **bez ograniczania
   `FIELDS_TO_COPY`** albo z dokładnie zachowaną kolejnością pól — patrz METHODOLOGY.md §5.2).
7. `native:centroids` na obwodach spisowych → `native:reprojectlayer` do EPSG:4326 → **koniecznie
   zweryfikować `get_layer_extent()` względem znanego realnego bboxa miasta przed spatial joinem**
   (patrz METHODOLOGY.md §5.1 — tak wykryto pomyłkę Piotrków/Łódź).
8. `native:joinattributesbylocation` (centroidy obwodów spisowych × poligony obwodów głosowania,
   `PREDICATE=intersects`) → `native:joinattributestable` z powrotem do pełnej geometrii
   obwodów spisowych.
9. Weryfikacja: suma `population`, liczba NULL w `income_index_pln`, ręczne sprawdzenie 1–2
   rekordów względem źródłowego CSV.

### 3.2 Dla Warszawy, Poznania, Gdańska, Szczecina (kafle `wybory.it`)

Różnica względem 3.1: geometria + wyniki głosowania **razem**, z kafli wektorowych, nie z
osobnych źródeł.

1. `fetch_tiles_mbtiles.py <xmin> <ymin> <xmax> <ymax> <zoom=14> <out.mbtiles>` — pobiera kafle
   MVT bezpośrednio przez HTTP z `https://wybory.it/api/martin/parl_2023/{z}/{x}/{y}` i zapisuje
   poprawny MBTiles (metadane TileJSON wpisane ręcznie w skrypcie, bo Martin ich nie eksponuje
   w standardowym formacie MBTiles). **Wymaga nagłówka `User-Agent` udającego przeglądarkę**
   (domyślny UA Pythona bywa blokowany, HTTP 403).
2. `ogr2ogr -f GeoJSON out.geojson in.mbtiles parl_2023 -t_srs EPSG:4326 -oo ZOOM_LEVEL=14`
   (ogr2ogr z instalacji QGIS, np. `C:\Program Files\QGIS 3.44.11\bin\ogr2ogr.exe`).
3. Filtr GeoJSON po polu `teryt` (Python, w pamięci) → tylko obwody należące do miasta.
4. **Dissolve po (teryt, number)** — kafle dają fragmenty per-tile, trzeba scalić z powrotem w
   całe poligony (`native:dissolve`).
5. `compute_income_from_tileset.py <dissolved.geojson> <out.csv>` — liczy `income_index_pln`
   **z pola `total`, NIE `all_votes`** (patrz METHODOLOGY.md §5.4 — to była realna pomyłka
   wykryta przez krzyżową weryfikację). Skrypt sam sprawdza, że dokładnie 5 kolumn komitetów
   dopasowało się do głównych partii (`assert n_major == 5`) — jeśli PKW zmieni nazwy komitetów
   w przyszłych wyborach, skrypt rzuci błędem zamiast po cichu liczyć źle.
6. Reszta identyczna jak 3.1 (join do geometrii, centroidy, reprojekcja, weryfikacja extentu,
   spatial join, join z powrotem, weryfikacja).

**Nie używać `native:downloadvectortiles`** — crashuje QGIS (access violation, potwierdzone
raportem crashu). `fetch_tiles_mbtiles.py` to zamiennik.

### 3.3 Konsolidacja do jednego GPKG per miasto

```
ogr2ogr -f GPKG miasto.gpkg  {miasto}_ses_final.gpkg           {warstwa} -nln obwody_spisowe
ogr2ogr -f GPKG -update miasto.gpkg  {precinct_geom_fixed}.gpkg {warstwa} -nln obwody_glosowania
```

Weryfikacja niezależna od QGIS (Python + `sqlite3`, bo GPKG to SQLite): porównanie sumy
`population` i liczby rekordów w `obwody_glosowania` z tabelą w §2 **przed** usunięciem plików
pośrednich.

### 3.4 Dodanie struktury rodzin/gospodarstw domowych (2026-08-22)

Niezależny od 3.1–3.3 krok, dodany po konsolidacji do 6 `.gpkg`. Wejście: 4 pliki xlsx GUS NSP2021
w `docs/gis/` (nationwide, flat, patrz METHODOLOGY.md §4a).

```
py extract_family_household_stats.py "../../docs/gis/rodziny_w_rejonach_i_obwodach_wg_typow_nsp2021.xlsx" ./stats family_types
py extract_family_household_stats.py "../../docs/gis/gospodarstwa_w_rejonach_i_obwodach_wg_skladu_rodzinnego_nsp2021_2.xlsx" ./stats hh_composition
py extract_family_household_stats.py "../../docs/gis/rodziny_w_rejonach_i_obwodach_wg_liczby_dzieci_nsp2021.xlsx" ./stats children_count
py extract_family_household_stats.py "../../docs/gis/gospodarstwa_w_rejonach_i_obwodach_wg_liczby_osob_nsp2021.xlsx" ./stats hh_size

for c in lodz krakow warszawa poznan gdansk szczecin; do py join_family_household_stats.py $c; done
```

`extract_family_household_stats.py` filtruje po znanych kodach `GMINA` (z `cities_teryt.md`) —
**każdy z 4 plików czytany raz dla wszystkich 6 miast naraz** (skanowanie całego pliku 30MB/~1M
wierszy jest tanie tylko raz, nie 6×). `join_family_household_stats.py <miasto>` liczy pola
pochodne (`fam_*`, `hh_*`) i zapisuje je do `{miasto}.gpkg` przez surowy `sqlite3` (nie przez
`native:joinattributestable`/QGIS) — **wymaga wczytania `mod_spatialite.dll`** (patrz Sekcja 6,
GPKG-trigger gotcha). Skrypt korzysta z `openpyxl`, więc uruchamiany interpreterem QGIS
(`C:\Program Files\QGIS 3.22.16\apps\Python39\python3.exe`), nie `py` — patrz §4.1.

Po joinie: **przeładować projekt QGIS** (`load_project`), zanim spróbujesz czytać nowe pola przez
`get_layer_features` — QGIS cache'uje listę pól warstwy przy wczytaniu, a pliki `.gpkg` zostały
zmienione poza QGIS (bezpośrednio przez `sqlite3`).

Folder `stats/` (24 CSV pośrednich, ~5.6MB) usunięty po weryfikacji i joinie — w pełni
odtwarzalny z powyższych 2 komend (źródłowe xlsx zostają w `docs/gis/`, niewersjonowane).

### 3.5 Uzupełnienie `pis_proc` dla Łodzi/Krakowa + analiza korelacji i przestrzenna (2026-08-22)

`obwody_glosowania` dla Łodzi/Krakowa nie miało zachowanych surowych pól partyjnych (tylko
`valid_votes`/`income_index_pln` — patrz §3.1 krok 5). Odtworzone przez `backfill_pis_share.py`:
pobiera ponownie `wyniki_gl_na_listy_po_obwodach_sejm_csv.zip` z
`https://danewyborcze.kbw.gov.pl/dane/2023/sejmsenat/wyniki_gl_na_listy_po_obwodach_sejm_csv.zip`
(link znaleziony przez grep `.zip`/`.csv` na stronie indeksu, nie zgadywany), filtruje po
`TERYT Gminy` (kol. 2 = "106101" Łódź / "126101" Kraków — **6-cyfrowy kod KBW, inny niż
7-cyfrowy `GMINA` GUS**, patrz `cities_teryt.md`), liczy `pis_proc = pis_votes/total_valid*100`
i zapisuje bezpośrednio do `obwody_glosowania.pis_proc` oraz `obwody_spisowe.pis_proc` (join po
`precinct_nr`/`Nr komisji`, przez `sqlite3` + `mod_spatialite.dll` jak w §3.4). Wynik: 100%
dopasowania w obu miastach (322/283 i 454/412 obwodów głosowania w pliku PKW vs. w warstwie —
więcej w PKW bo obejmuje też obwody odrębne/zamknięte nieujęte w geometrii miejskiej, bez wpływu
na trafienia). Plik CSV (7.2MB) i zip usunięte po użyciu — link do ponownego pobrania wyżej.

**Analiza korelacji** (`analyze_correlations.py`, aspatial, Pearson) i **analiza przestrzenna**
(`spatial_analysis.py`, globalny wskaźnik I Morana, wagi k-NN k=8 row-standardized, test
permutacyjny 299 permutacji) — obie **tylko do odczytu**, nie modyfikują `.gpkg`. Wymagają
`geopandas`/`numpy`/`scikit-learn` — już zainstalowane w systemowym `py` (sprawdzone przed
użyciem, zero nowych `pip install`). Pełne wyniki i wnioski: patrz podsumowanie w rozmowie
2026-08-22 (nie skopiowane 1:1 do tego pliku — uruchom skrypty ponownie, żeby odtworzyć liczby).

## 4. Jak dodać kolejne miasto

### 4.1 Ludność (GUS)

```
# Zrzut arkusza (interpreterem z instalacji QGIS — tam jest openpyxl):
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "C:\Program Files\QGIS 3.22.16\apps\Python39\python3.exe" \
  easy_otp/core/xlsx_reader.py '{"path": "docs/gis/ludnosc_nsp_2021.xlsx", "sheet": "<Województwo>"}' \
  > rows_<Województwo>.json

# Ustal kod(y) TERYT miasta — wyszukaj nazwę miasta w kolumnie "Powiat" arkusza, sprawdź czy
# miasto jest dzielone na delegatury/dzielnice (jak Łódź/Warszawa/Kraków/Poznań) czy nie
# (jak Gdańsk/Szczecin). NIGDY nie zgaduj kodu — sprawdź i zweryfikuj sumą populacji.

PYTHONUTF8=1 py extract_population_generic.py rows_<Województwo>.json <miasto>_population.csv
```

### 4.2 Geometria obwodów głosowania — dwie ścieżki

**A) Miasto ma własny portal GIS** (ArcGIS Web AppBuilder — szukaj `<miasto> mapa obwody
wyborcze` → znajdź URL `.../portal/apps/webappviewer/index.html?id=<APP_ID>` → fetch
`<portal>/sharing/rest/content/items/<APP_ID>/data?f=json` → znajdź `map.itemId` → fetch
`<portal>/sharing/rest/content/items/<webmap_id>/data?f=json` → znajdź `operationalLayers` z
tytułem zawierającym "Obwody"/"wyborcze" → URL `.../MapServer/<n>`):

```
curl "<MapServer_URL>/<n>/query?where=1%3D1&outFields=*&f=geojson&outSR=4326" -o obwody.geojson
```

Potem osobno pobrać wyniki głosowania z `danewyborcze.kbw.gov.pl` (patrz METHODOLOGY.md §7),
przefiltrować po TERYT gminy (6-cyfrowy kod, **inny format niż 7-cyfrowy `GMINA` w geometrii
GUS** — sprawdź w pliku `obwody_glosowania_csv.zip`, kolumna "TERYT gminy").

**B) Miasto NIE ma własnego portalu** (sprawdzone: Warszawa, Poznań, Gdańsk, Szczecin) — użyj
`wybory.it`:

```
py fetch_tiles_mbtiles.py <xmin> <ymin> <xmax> <ymax> 14 <miasto>_raw.mbtiles
ogr2ogr -f GeoJSON <miasto>_precincts.geojson <miasto>_raw.mbtiles parl_2023 -t_srs EPSG:4326
# filtruj po "teryt", dissolve po (teryt, number), compute_income_from_tileset.py
```

Bbox miasta: pobierz z geometrii GUS (`GMINA` filter → `boundingBox()` w EPSG:4326, +0.01°
marginesu) — nie zgaduj współrzędnych ręcznie.

### 4.3 Reszta pipeline'u — identyczna jak §3.1 kroki 7–9, potem konsolidacja §3.3.

## 5. Co zostało usunięte (porządki) i dlaczego to bezpieczne

W trakcie pracy powstało ~110 plików pośrednich per miasto (surowe kafle `.mbtiles`, GeoJSON
fragmentów, kolejne kroki `native:joinattributestable`/`centroids`/`reprojectlayer`, CSV z
oficjalnymi wynikami PKW per miasto). **Wszystkie usunięte** po konsolidacji i weryfikacji
(§3.3) — były to jednorazowe kroki pośrednie, nie dane źródłowe. Dane źródłowe (GUS Excel,
GUS shapefile, PKW CSV nationwide) nie były w tym folderze — są w `docs/gis/` (GUS) i trzeba
je pobrać ponownie z linków w METHODOLOGY.md §7 (PKW), jeśli potrzebne (np. do rozszerzenia na
kolejne miasto ścieżką A z §4.2, albo do powtórnej weryfikacji głosów dla Warszawy/Poznania/
Gdańska/Szczecina).

Zachowane: 6 plików `{miasto}.gpkg`, 8 skryptów Python, `cities_teryt.md` (tabela kodów TERYT
dla wszystkich 6 miast — przydatna przy dodawaniu 7. miasta jako wzór), `METHODOLOGY.md`,
ten plik, `NEXT_AGENT_BRIEF.md`. Folder `stats/` (pośrednie CSV z §3.4) usunięty tego samego dnia
po joinie do `.gpkg` — odtwarzalny w ~1 min z `extract_family_household_stats.py` + źródłowe xlsx.

## 6. Znane pułapki techniczne (żeby nie wpaść drugi raz)

- **QGIS crashował dwa razy** podczas tej pracy (raz na `native:downloadvectortiles`, raz z
  nieustaloną przyczyną przy równoległym `execute_code`). Po każdym crashu projekt trzeba
  załadować na nowo (`load_project`) **przed** kolejnym `save_project` — inaczej zapiszesz pusty/
  niepełny projekt nad dobrym (to się raz zdarzyło, naprawione, ale bądź czujny).
- Pliki `.gpkg-shm`/`.gpkg-wal` obok `.gpkg` = niezacommitowane zmiany SQLite w trybie WAL
  (zwykle po edycji w QGIS). Przed usunięciem/kopiowaniem `.gpkg` sprawdź czy te pliki istnieją.
- `ogr2ogr`/`ogrinfo` z instalacji QGIS: `C:\Program Files\QGIS 3.44.11\bin\`. Bash czasem
  zwraca `exit code 1` z opóźnieniem/bez błędu przy pierwszym sprawdzeniu pliku output —
  filesystem sync lag, nie prawdziwy błąd (sprawdzone: plik pojawiał się po chwili).
- Python w tym repo: `py`, nie `python` (konwencja z CLAUDE.md). Dla operacji wymagających
  `openpyxl` — interpreter z instalacji QGIS (patrz §4.1), bo `py` (systemowy) go nie ma.
- **`UPDATE`/`ALTER TABLE` na `.gpkg` przez goły Python `sqlite3` rzuca `no such function:
  ST_IsEmpty`.** GPKG ma triggery (walidacja geometrii) wołające funkcje Spatialite. Napraw:
  `conn.enable_load_extension(True); conn.load_extension(r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll")`
  przed pierwszym `UPDATE`/`ALTER`. Zweryfikowane bezpieczne dla zmian tylko-atrybutowych
  (nie dotyka geometrii/FID/`gpkg_contents`); nie testowane dla zmian geometrii tą drogą.
- Po zmianie `.gpkg` przez `sqlite3` z pominięciem QGIS: **przeładować projekt** (`load_project`)
  zanim odczytasz nowe pola przez MCP — QGIS cache'uje schemat warstwy przy wczytaniu.
