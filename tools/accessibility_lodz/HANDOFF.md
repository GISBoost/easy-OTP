# Handoff: dostępność transportowa Łodzi (r5r) — jak to zrobiono, jak odtworzyć

Rozszerzenie `tools/ses_income_lodz/` o wymiar dostępności transportowej: dla każdego
obwodu spisowego (`lodz.gpkg` z `ses_income_lodz/`) liczymy dostępność czasową (walk+transit,
r5r) do usług publicznych (edukacja/zdrowie/kultura/sklepy spożywcze, OSM), na
**zrealizowanym** GTFS (Family A, `easy-GTFS-RT`) z konkretnego, rzeczywiście
nagranego dnia — nie na statycznym rozkładzie.

## 1. Wynik końcowy

`lodz_accessibility.gpkg` (2 warstwy): `obwody_spisowe` (3854, geometria +
pola SES z `ses_income_lodz/lodz.gpkg` + 20 pól dostępności: `{education,health,
culture,groceries,total}_{15,30,45,60}min` = liczba punktów usług osiągalnych w
danym progu czasowym), `poi_services` (1328 punktów OSM, pole `category`).

Projekt QGIS wystylowany: `lodz_dostepnosc.qgz`. Renderowane mapy:
`map_accessibility_total_30min.png`, `map_income_index.png`.

`lodz_accessibility_wide.csv` — ten sam wynik w formacie płaskim (do dalszej analizy
poza QGIS), z dołączonym `income_index_pln`/`population`/`fam_pct_matki_samotne`.

## 2. Kluczowy wynik (patrz też podsumowanie w rozmowie 2026-08-22)

Dostępność jest **silnie monocentryczna** (promieniście maleje od centrum Łodzi,
r5r/mapa) — praktycznie w całości wyjaśniona odległością/gęstością, nie dochodem.
Korelacja `income_index_pln` × dostępność jest wszędzie słaba (|r|<0.13) i **zmienia
znak z progiem czasowym** (ujemna przy 15 min, dodatnia przy 60 min) — biedniejsze/
gęściej zaludnione obwody centrum mają *lepszą* dostępność krótkoterminową, nie gorszą.
`%matek samotnych` koreluje dodatnio z dostępnością (r=+0.38 przy 30 min) z tego samego
powodu (centrum = gęstsza zabudowa, więcej gospodarstw jednorodzicielskich, więcej usług
w zasięgu spaceru+tramwaju). To odwraca naiwną hipotezę "biedny = gorszy dostęp" dla
Łodzi — zobacz pełne wnioski w rozmowie, nie kopiowane 1:1 tutaj.

## 3. Pipeline krok po kroku

### 3.1 Instalacja (jednorazowa, per maszyna)

```
winget install --id RProject.R -e
# R zainstalowany do C:\Program Files\R\R-4.6.1\ ale bez uprawnień zapisu do
# library systemowej -- pakiety trzeba instalować do R_LIBS_USER:
mkdir -p "C:/Users/<user>/Documents/R/win-library/4.6"
export R_LIBS_USER="C:/Users/<user>/Documents/R/win-library/4.6"
Rscript -e '.libPaths(c(Sys.getenv("R_LIBS_USER"), .libPaths()));
  options(repos=c(CRAN="https://cloud.r-project.org"));
  install.packages(c("r5r","rJavaEnv","sf","data.table"),
    lib=Sys.getenv("R_LIBS_USER"), type="win.binary")'
```

r5r wymaga **JDK 21** (nie systemowej Javy — tu zainstalowana Java 25, i osobno
JDK8 dla OTP/QGIS pluginu, żadna z nich nie pasuje). `rJavaEnv::java_quick_install(
version=21)` ściąga scoped Amazon Corretto 21 (~200MB) i **symlinkuje go tylko do
tego katalogu projektu** przez `.Rprofile` (`JAVA_HOME`/`PATH` ustawiane per-sesja R,
zero zmian systemowego środowiska) — bezpieczne, nie koliduje z niczym innym.

### 3.2 Dane wejściowe

```
# Sieć drogowa (OSM) — reużyta z tools/family_a_reconstruction/graphs/*/lodz.osm.pbf
cp ../family_a_reconstruction/graphs/0035572f/lodz.osm.pbf ./network_data/

# GTFS zrealizowany (Family A, dzień faktycznie nagrany) — najnowszy release na dzień
# analizy, p50 = mediana skorygowanego rozkładu (p85 = wariant bardziej pesymistyczny)
"C:\Program Files\GitHub CLI\gh.exe" release list --repo GISBoost/easy-GTFS-RT | grep lodz
"C:\Program Files\GitHub CLI\gh.exe" release download <tag> --repo GISBoost/easy-GTFS-RT \
  --pattern "*_p50.zip"
cp lodz_realized_<data>_p50.zip network_data/lodz_gtfs.zip

# Miejsca docelowe: usługi publiczne z OSM (Overpass) -- fallback zamiast miejsc pracy,
# patrz sekcja 4 (dlaczego REGON odrzucony na razie)
py fetch_osm_services.py lodz_services.csv
py prepare_destinations.py lodz_services.csv lodz_destinations.csv

# Punkty startowe: centroidy obwodów spisowych, reprojekcja EPSG:2180 -> EPSG:4326
# (lodz.gpkg z ses_income_lodz/ jest w PUWG 1992, r5r chce WGS84)
py export_origins.py lodz_origins.csv
```

### 3.3 Sieć + dostępność (r5r)

```
export R_LIBS_USER="..."
Rscript run_accessibility.R
```

`build_network()` buduje graf R5 (pbf+GTFS) do `network_data/network.dat` (~180MB,
cache — kolejne uruchomienia `setup_r5()` w tym folderze są natychmiastowe, nie
rebuildują). `accessibility()`: `mode=c("WALK","TRANSIT")`, `departure_datetime`
ustawiony na **konkretny dzień nagrania** (nie dowolny dzień w przyszłości — GTFS
zrealizowany ma unikalne `service_id` per okres, patrz Sekcja 5), `departure_datetime
= 07:00`, `time_window = 120` (r5r próbkuje odjazdy co minutę w oknie 07:00–09:00,
poranny szczyt — 2h, nie 1h, żeby nie zależeć od pojedynczej minuty odjazdu),
`cutoffs=c(15,30,45,60)` min, `decay_function="step"` (liczy punkty osiągalne w
progu, nie ważoną funkcję rozpadu — prostsze do interpretacji: "ile szkół/przychodni
w zasięgu X minut").

### 3.4 Join do GPKG + wizualizacja

```
py analyze_accessibility.py   # pivot long->wide + korelacje z income/single-motherhood
py join_accessibility.py      # zapisuje 20 pól do lodz_accessibility.gpkg (sqlite3+mod_spatialite,
                               # ten sam gotcha co w ses_income_lodz -- ST_IsEmpty bez rozszerzenia)
```

Potem QGIS MCP: `create_new_project` → `add_vector_layer` (obwody_spisowe + poi_services)
→ `set_layer_style` (graduated, RdYlGn, 7 klas) → `render_map`.

## 4. Dlaczego OSM, nie REGON (miejsca pracy)

Docelowo chcieliśmy dostępności do **miejsc pracy**, nie usług publicznych — ale
REGON (rejestr podmiotów gospodarki narodowej, GUS) nie ma gotowego bulk-downloadu z
adresami + liczbą pracujących bez rejestracji/klucza API (`regon_bir@stat.gov.pl`), a
przetworzenie API BIR1 (lookup per-podmiot, nie masowy eksport) + geokodowanie
dziesiątek tysięcy adresów to osobny, kilkugodzinny projekt o niepewnym wyniku.
**Zdecydowano (2026-08-22, z Michałem)**: OSM usługi publiczne jako pierwszy,
szybki wynik; REGON jako możliwy stretch goal później, nie blokuje tego pipeline'u.

## 4a. Rozszerzenie (2026-08-22, później tego samego dnia): wykresy, populacja z dostępem, heksagony 500m

Trzy dodatki na prośbę Michała, w tej kolejności:

1. **Okno odjazdu poprawione na 07:00–09:00** (poranny szczyt, 2h) zamiast błędnego 08:00–09:00
   — `run_accessibility.R` §3.3, `departure_datetime`/`time_window`. Wyniki praktycznie bez zmian
   (3. miejsce po przecinku) — wzorzec przestrzenny dominuje nad wyborem dokładnej minuty w oknie.
2. **Wykresy matplotlib** (`plot_correlations.py`, styl `transit_charts`: samowyjaśniające
   tytuły, PNG+CSV) w `out/`: `lodz_H6_correlation_bars.png` (r per kategoria×próg), `lodz_H6_
   income_scatter.png` (hexbin dochód×dostępność), `lodz_H6_distance_scatter.png` (hexbin
   odległość-od-centrum×dostępność — r=−0.71, prawdziwy sterownik, dochód tylko r=+0.12).
3. **Populacja z dostępem pasywnym** (`compute_population_coverage.py`) — druga metryka, różna
   od "liczby POI w zasięgu": czy obwód ma **choć jedną** placówkę danej kategorii w progu
   (`has_access_*`), zsumowana populacja takich obwodów / populacja całkowita. Pełny opis każdej
   kolumny w **`COLUMNS.md`** (czytaj to przed użyciem danych — łatwo pomylić "liczbę POI" z
   "populacją objętą dostępem", to dwie różne rzeczy w tym samym pliku).
4. **Siatka heksagonalna 500m** zamiast obwodów spisowych — metoda z Kroku 1–2 skilla
   `qgis-hex-atlas-map` (`native:creategrid` TYPE=4, `native:extractbylocation` PREDICATE=
   intersects, **whole-hex, nie clip**), granica miasta = dissolve `obwody_spisowe` (nie OSM/
   QuickOSM — mamy już dokładniejszą granicę z GUS). 1479 heksagonów. Powód: obwody spisowe są
   mikroskopijne w centrum i ogromne na granicy miasta (MAUP) — jednolity rozmiar komórki daje
   czytelniejszą mapę (patrz `map_accessibility_hex500_total_30min.png` vs `map_accessibility_
   total_30min.png`) i nieco silniejszy, czystszy sygnał korelacji (income vs total_60min:
   r=+0.12 na obwodach, r=+0.28 na heksagonach — MAUP faktycznie tłumił sygnał).
   - `export_hex_origins.py`: centroidy heksagonów → nowe origins dla r5r (`lodz_hex_origins.csv`);
     SES (dochód/populacja/%matek) zagregowane **przez centroid obwodu wpadający w heksagon**
     (point-in-polygon, `gpd.sjoin`), populacja-ważona średnia dochodu. **Ograniczenie**: tylko
     646 z 1479 heksagonów ma dopasowany SES (peryferyjne heksagony leżą wewnątrz jednego
     ogromnego obwodu, którego centroid wpada w inny heksagon) — do poprawy przez area-weighted
     overlay, jeśli SES na heksagonach ma być użyty poważnie, nie tylko jako sprawdzian.
   - `run_accessibility_hex.R`: identyczne wywołanie `accessibility()`, origins podmienione,
     **sieć reużyta z cache'u** (`network_data/network.dat`, bez rebuildu).
   - `join_hex_results.py`: pivot + join SES + `has_access_*` + zapis do `lodz_hex500.gpkg`
     (warstwa `hex500`) — ten sam wzorzec `sqlite3`+`mod_spatialite` co wcześniej.

## 5. Znane pułapki

- **`r5r` ostrzega "Less than 20% of the transit services in the GTFS are running
  on the selected departure date"** — sprawdzone, **to nie błąd**: ten konkretny
  feed `easy-GTFS-RT` ma 9 różnych `service_id` (3 warianty rozkładu × 3 okresy
  ważności) skumulowanych w jednym pliku obejmującym cały rok, ale tylko 1 z nich
  (~9893 kursów, typowy dzień powszedni) jest aktywny na wybrany dzień — ostrzeżenie
  liczy proporcję względem WSZYSTKICH `service_id` w pliku, nie względem realnego
  ruchu tego dnia. Zweryfikowane: liczba kursów (9893) jest sensowna dla Łodzi w dzień
  powszedni, zgodna z release notes (283520 obserwacji dopasowanych tego dnia).
- **`lodz.gpkg` z `ses_income_lodz/` jest w EPSG:2180**, nie WGS84 — `export_origins.py`
  musi reprojektować centroidy przed zapisem do CSV dla r5r.
- Instalacja R przez `winget` na tym komputerze: R **już był zainstalowany**
  (`winget` zrobił upgrade w miejscu), ale nie było widać `Rscript` w PATH nowej
  sesji bash — użyj pełnej ścieżki `C:\Program Files\R\R-4.6.1\bin\x64\Rscript.exe`.
- `install.packages()` do domyślnej library (`C:\Program Files\R\...\library`) rzuca
  "not writable" bez uprawnień administratora — zawsze przekazuj `R_LIBS_USER`
  (utworzony ręcznie) jako `lib=`.
- `network_data/`, `.Rprofile`, `rjavaenv/`, `*.csv` w tym folderze są w `.gitignore`
  (dane/cache wynikowe, nie kod) — w pełni odtwarzalne z powyższych komend + źródeł
  (OSM pbf w `family_a_reconstruction/`, GTFS z release'u `easy-GTFS-RT`, Overpass).
