# PR / Brief dla Claude Code — wtyczka QGIS „easy-OTP"

> Dokument do wklejenia jako brief startowy w Claude Code. Opisuje kompletną
> wtyczkę QGIS automatyzującą analizę dostępności czasowej komunikacji publicznej.
> Wersja: v1 (MVP). Autor metody i właściciel projektu: Michał Kaczorowski.

---

## 1. Kontekst i cel

`easy-OTP` to wtyczka do QGIS, która automatyzuje autorską metodę pomiaru
**dostępności czasowej (temporal / service-time accessibility)** komunikacji
publicznej. Metoda została opisana i opublikowana w artykule:

> Kaczorowski M., Wróblewski W., *Spatio-temporal and demographic distribution
> of public transport accessibility. A GIS-based method using OpenTripPlanner.*

Idea metody w skrócie: dla zadanego punktu docelowego (np. brama kampusu)
generujemy **jedną powierzchnię travel-time na każdą minutę** okna czasowego
(domyślnie 6:00–22:00 → 961 powierzchni), a następnie dla każdej komórki siatki
liczymy, **przez ile minut doby** dana komórka mieści się w progu dojazdu
(domyślnie 30 min). Wynik klasyfikujemy na 4 kategorie ciągłości obsługi.

Obecnie cały proces jest ręczny i rozbity na ~12 osobnych kroków w różnych
narzędziach (OTP, R, QGIS, GRASS, Python). **Celem v1 jest sprowadzenie tego do
jednego okna parametrów i jednego przycisku „Uruchom" wewnątrz QGIS.**

`easy-OTP` jest pierwszym elementem szerszej platformy roboczo nazwanej
„Project Chronos" — platforma NIE jest przedmiotem tego PR, skupiamy się
wyłącznie na wtyczce.

---

## 2. Zakres v1 (MVP) — co wchodzi, a co NIE

### W zakresie v1
- Wtyczka QGIS w formie **algorytmu Processing** (własny provider) — okno
  parametrów + przycisk Run dostajemy „za darmo", tryb wsadowy i wpięcie
  w modele QGIS gratis.
- Pełna automatyzacja kroków 2–12 pipeline'u (sekcja 4) z plików **lokalnych**.
- Zarządzanie cyklem życia serwera OTP (start/stop jako proces potomny).
- Konfigurowalne okno czasowe i interwał (1 / 15 / 60 min).
- Jeden punkt docelowy.
- Siatka heksagonalna: dostarczona przez użytkownika **lub** wygenerowana przez
  wtyczkę (wariant hybrydowy).
- Wynik: raster zliczeń + warstwa heksagonów z wartością service-time
  i klasyfikacją 4-kategorialną, opcjonalnie ostylowana.

### POZA zakresem v1 (→ roadmapa, sekcja 14)
- Moduł populacji studentów (krok 11 pipeline'u — `ludnosc_studentow`).
- Automatyczne pobieranie danych OSM / GTFS z sieci.
- Automatyczne pobieranie przenośnego JRE.
- Wiele punktów docelowych jednocześnie.
- Moduł „car dependency".
- Silnik R5 / r5py.

---

## 3. Stan obecny — pipeline ręczny (kontekst domenowy)

Tak wygląda dziś proces. Wtyczka ma go odtworzyć automatycznie.

1. **OTP 1.5.0 + Java 8.** OTP 1.5.0 wymaga dokładnie **Javy 8**. Realny problem
   użytkownika: na maszynie jest nowsza Java potrzebna innym programom i nie da
   się jej odinstalować → konflikt wersji.
2. Pobranie sieci drogowo-pieszej jako `.osm.pbf`.
3. Pobranie rozkładów jazdy w formacie **GTFS** (jeden lub wiele plików `.zip`).
4. Uruchomienie serwera OTP — zbudowanie grafu z `.osm.pbf` + GTFS i wystawienie
   API.
5. Wygenerowanie listy czasów: jeden wiersz CSV na każdą minutę od 6:00 do 22:00.
6. Skrypt R `Surface_analysis_wro.R` — w pętli po wszystkich czasach woła
   `otp_create_surface()` z pakietu `otpr`; każde wywołanie zwraca raster
   GeoTIFF travel-time; pliki są następnie przemianowywane wg czasu.
7. Załadowanie rastrów do QGIS jako **raster wirtualny** (`gdal:buildvirtualraster`)
   — każda powierzchnia na osobnym kanale (dla 1-min okna: 961 kanałów).
8. Skrypt `skrypt_wro.py` — iteruje po kanałach i zlicza, w ilu kanałach wartość
   travel-time ≤ próg; wynik to **jednokanałowy raster zliczeń** (0…N).
9. Wyzerowanie/usunięcie komórek o wartości 0 (obecnie GRASS `r.null`), bo
   zaburzają dalszą analizę.
10. Statystyki strefowe na siatce heksagonalnej (`native:zonalstatisticsfb`).
11. Nałożenie warstwy populacji studentów (model QGIS `ludnosc_studentow`).
12. Stylizacja w QGIS + statystyki podsumowujące.

### Materiały źródłowe dołączone do projektu
- `Surface_analysis_wro.R` — krok 6. **Do portu na Python, NIE do uruchamiania.**
- `skrypt_wro.py` — krok 8. Logika zliczania kanałów do bezpośredniego przeniesienia.
- `ludnosc_studentow_model_qgis.py` — krok 11. Tylko referencja na roadmapę.
- Artykuł Kaczorowski & Wróblewski — definicja metody, parametry, klasyfikacja.
- Artykuł *Car Dependency in Urban Accessibility* (CDI) — tylko pod roadmapę.

---

## 4. Architektura docelowa

Wtyczka = **provider Processing** o nazwie roboczej `easy-OTP` z następującymi
algorytmami:

- **`RunTemporalAccessibility`** — algorytm główny: cały pipeline kroki 2–12
  (bez 11), jeden zestaw parametrów, jedno uruchomienie.
- **`TestOtpServer`** — algorytm pomocniczy: weryfikuje ścieżki do Javy i `.jar`
  OTP, wersję Javy, dostępność portu, zwraca czytelny raport diagnostyczny.
- **`GenerateHexGrid`** — opcjonalny algorytm pomocniczy: generuje siatkę
  heksagonalną o zadanym rozmiarze komórki (część wariantu hybrydowego).

Algorytm główny w środku składa się z modułów Pythona (czysty PyQGIS, bez R,
bez GRASS):

```
easy_otp/
├── __init__.py
├── metadata.txt
├── easy_otp_plugin.py          # rejestracja providera w QGIS
├── provider.py                 # QgsProcessingProvider
├── algorithms/
│   ├── run_temporal_accessibility.py   # QgsProcessingAlgorithm – główny
│   ├── test_otp_server.py
│   └── generate_hex_grid.py
├── core/
│   ├── otp_server.py           # start/stop procesu OTP, build grafu, health-check
│   ├── otp_client.py           # klient REST API OTP (port logiki z otpr)
│   ├── surface_runner.py       # pętla generowania powierzchni travel-time
│   ├── raster_processing.py    # stos kanałów + zliczanie + czyszczenie zer
│   ├── zonal.py                # statystyki strefowe + klasyfikacja 4 kategorii
│   ├── time_utils.py           # generowanie listy czasów z okna + interwału
│   └── settings.py             # odczyt/zapis QSettings (ścieżki Java/OTP itd.)
├── styles/
│   └── service_time.qml        # styl 4-kategorialny dla warstwy wynikowej
├── resources/                  # ikony
└── test/
    └── ...                     # testy jednostkowe
```

### Zasada „bez R i bez GRASS"
- Krok 6 (R + `otpr`) → `core/otp_client.py` + `core/surface_runner.py` w Pythonie.
- Krok 9 (GRASS `r.null`) → operacja na tablicy NumPy / GDAL w `raster_processing.py`.
- Jedyną zewnętrzną zależnością pozostaje **OTP (aplikacja Javy)** — i tego nie da
  się uniknąć.

---

## 5. Stack techniczny i zależności

| Element | Wymóg |
|---|---|
| QGIS | minimum **3.40 LTR** (target) |
| Python | wbudowany w QGIS (PyQGIS); brak zależności spoza dystrybucji QGIS |
| Biblioteki | tylko to, co QGIS dostarcza: `qgis.core`, `qgis.processing`, `osgeo.gdal`, `numpy`, `requests` lub `urllib` |
| OTP | **1.5.0** (`otp-1.5.0-shaded.jar`) — dostarcza użytkownik |
| Java | **Java 8** — dostarcza użytkownik (patrz sekcja 6) |
| R | **brak** |
| GRASS | **brak** (provider GRASS nie może być twardą zależnością) |

**Ważne:** wtyczka nie może wymagać `pip install` niczego. Wszystko, czego używa,
musi być dostępne w standardowej instalacji QGIS. Jeśli `requests` nie jest pewne
na każdej platformie — użyć `urllib` z biblioteki standardowej.

---

## 6. Zarządzanie Javą i serwerem OTP

### 6.1. Java
- Użytkownik **nie musi** instalować Javy systemowo ani ruszać konfliktujących
  wersji. Wystarczy, że pobierze **przenośny build Javy 8** (np. Eclipse Temurin 8
  / Azul Zulu 8), rozpakuje ZIP do dowolnego folderu i w ustawieniach wtyczki
  wskaże ścieżkę do binarki `java` (`bin/java.exe` na Windows).
- Wtyczka uruchamia OTP, podając **pełną ścieżkę do tej konkretnej binarki Javy** —
  dzięki temu wersja systemowa jest bez znaczenia i konflikt znika.
- `TestOtpServer` musi sprawdzić wersję wskazanej Javy (`java -version`,
  parsowanie outputu) i jasno ostrzec, jeśli to nie jest 8.

### 6.2. Serwer OTP — cykl życia

OTP 1.x rozdziela **budowanie grafu** i **serwowanie**. Wtyczka odtwarza
dokładnie dwufazowy schemat sprawdzony przez użytkownika w pracy ręcznej.

**Konfiguracja (QSettings, w algorytmie dostępna jako parametry zaawansowane):**
ścieżka do binarki Javy, ścieżka do `otp-1.5.0-shaded.jar`, **osobne `-Xmx` dla
build i dla serve** (build mniej, serwer analityczny więcej — referencyjnie
8 GB / 16 GB, ale zależne od maszyny i wielkości miasta), port (domyślnie 8801).

**Układ katalogów w katalogu roboczym `WORK_DIR`:**
```
WORK_DIR/
└── graphs/
    └── <router_id>/        # router = jeden zestaw danych
        ├── <miasto>.osm.pbf # skopiowane dane wejściowe
        ├── <gtfs>.zip       # skopiowane dane wejściowe (1..n)
        ├── Graph.obj        # zbudowany graf (cache)
        └── easy_otp_meta.json   # metryczka: pliki, data buildu, wersja OTP
```
- `--basePath` MUSI wskazywać rodzica katalogu `graphs/`, czyli `WORK_DIR`.
- `<router_id>` to **krótki hash treści plików wejściowych** (`.osm.pbf` + zestaw
  GTFS). Jest deterministyczny — te same dane zawsze dają ten sam router, więc
  `Graph.obj` jest współdzielony między uruchomieniami i sesjami QGIS.

**Cache grafu:** jeśli `WORK_DIR/graphs/<router_id>/Graph.obj` istnieje →
budowanie pomijane (cache hit). Świadomie **nie** używamy `--inMemory`, bo graf
ma przetrwać na dysku do kolejnych uruchomień.

**Faza build** (raz na zestaw danych):
```
java -Xmx<XMX_BUILD> -jar <OTP_JAR> --build WORK_DIR/graphs/<router_id>
```
Poprzedzona utworzeniem katalogu routera i skopiowaniem do niego `.osm.pbf`
i plików GTFS; zakończona zapisem `easy_otp_meta.json`.

**Faza serve** (wzorzec: opcja sprawdzona przez użytkownika jako działająca
najlepiej — `--analyst` + `--pointSets`):
```
java -Xmx<XMX_SERVE> -jar <OTP_JAR> --server \
     --basePath WORK_DIR --router <router_id> \
     --analyst --pointSets <POINTSETS_DIR>
```
- `--analyst` wystawia endpointy surface — bez tego generowanie powierzchni nie
  zadziała.
- `--pointSets` wymaga istniejącego katalogu; może być pusty (`otp_create_surface`
  z pobraniem rastra nie potrzebuje wczytanych pointsetów). Wtyczka tworzy
  dedykowany pusty `POINTSETS_DIR` w `WORK_DIR`.

**Opcjonalnie:** parametr „użyj gotowego grafu" — jeśli użytkownik wskaże
istniejący katalog routera z `Graph.obj`, wtyczka pomija kopiowanie i build.

**Sekwencja w algorytmie głównym:**
1. Sprawdź, czy na skonfigurowanym porcie działa już serwer OTP z właściwym
   routerem (`<router_id>`).
2. Jeśli nie — wykonaj fazę build (lub cache hit) i wystartuj fazę serve jako
   `subprocess`.
3. **Health-check** — odpytuj endpoint OTP w pętli z timeoutem, aż router będzie
   gotowy (build/wczytanie grafu dużego miasta to minuty).
4. Wykonaj generowanie powierzchni.
5. Parametr **„pozostaw serwer uruchomiony"** (domyślnie: tak) — pozwala na
   szybkie kolejne uruchomienia bez ponownego startu; jeśli `false`, ubij proces
   na końcu (także w bloku `finally` i przy anulowaniu zadania).

---

## 7. Komunikacja z OTP REST API (`core/otp_client.py`)

Zamiast wrappera: bezpośrednie wywołania REST API OTP. **Referencja wzorcowa to
źródło pakietu `otpr` (licencja MIT)** — Claude Code ma podejrzeć implementację
`otp_create_surface()` i `otp_connect()` w repo `marcusyoung/otpr` i przenieść
dokładnie te wywołania HTTP (endpointy, parametry zapytania, sposób pobrania
rastra) do Pythona.

Logika pojedynczej powierzchni (odpowiednik kroku 6):
1. `POST` na endpoint surface OTP z parametrami: `fromPlace` (lat,lon punktu
   docelowego), `mode=TRANSIT`, `date`, `time`, `maxWalkDistance`,
   `walkReluctance`, `waitReluctance`, `transferPenalty`, `minTransferTime`,
   `walkSpeed`, `batch=true` — w odpowiedzi identyfikator powierzchni.
2. `GET` rastrowej reprezentacji powierzchni → GeoTIFF zapisany na dysk.
3. Nazwa pliku wynikowego = znacznik czasu (jak w funkcji `rename_surface_files`
   ze skryptu R), żeby kolejność kanałów była deterministyczna.

Jednostka wartości rastra — **potwierdzone przez autora metody**: `otp_create_surface`
z `otpr` zwraca raster travel-time w **minutach**, z **zahardkodowanym górnym
limitem 120 minut**. Oznacza to, że obszary nieosiągalne lub osiągalne w ≥120 min
mają wartość 120 (są nierozróżnialne) — dla progu 30 min jest to bez znaczenia,
ale Claude Code ma to udokumentować w kodzie. Porównanie z progiem
(`TRAVEL_TIME_THRESHOLD`, domyślnie 30) wykonujemy w minutach, dokładnie jak
w `skrypt_wro.py` (`data <= 30`).

---

## 8. Specyfikacja algorytmu głównego `RunTemporalAccessibility`

### 8.1. Parametry wejściowe (okno Processing)

**Dane wejściowe**
- `OSM_PBF` — plik `.osm.pbf` (`QgsProcessingParameterFile`).
- `GTFS_FILES` — jeden lub więcej plików GTFS `.zip` (wiele plików / folder).
- `ORIGIN_POINT` — punkt początkowy analizy / OTP `fromPlace`
  (`QgsProcessingParameterPoint`, z CRS). W terminologii OTP analyst
  surface to origin SPT: czas dojazdu liczony OD tego punktu DO każdej
  komórki rastra.
- `HEX_GRID` — warstwa siatki heksagonalnej (`QgsProcessingParameterVectorLayer`,
  opcjonalna).
- `GENERATE_GRID` — bool; jeśli `true`, wtyczka generuje siatkę zamiast `HEX_GRID`.
- `GRID_CELL_SIZE` — rozmiar komórki dla generowanej siatki (domyślnie 500 m,
  zgodnie z artykułem).

**Parametry czasu i analizy**
- `ANALYSIS_DATE` — data analizy (`QgsProcessingParameterDateTime`, tylko data).
  Wtyczka musi **zwalidować datę względem kalendarza GTFS** i ostrzec, jeśli
  wypada poza obowiązywaniem rozkładu lub w weekend (analiza dojazdów studenckich
  zwykle dotyczy dnia roboczego).
- `TIME_START` / `TIME_END` — początek i koniec okna (domyślnie 06:00 / 22:00).
- `INTERVAL` — enum: `1 min` / `15 min` / `60 min` (domyślnie 1 min).
- `TRAVEL_TIME_THRESHOLD` — próg dojazdu w minutach (domyślnie **30**).

**Parametry routingu OTP (grupa zaawansowana, domyślne z artykułu)**
- `WALK_RELUCTANCE` = 3
- `WAIT_RELUCTANCE` = 2
- `TRANSFER_PENALTY` = 60 (s)
- `MIN_TRANSFER_TIME` = 60 (s)
- `MAX_WALK_DISTANCE` = 800 (m)
- `WALK_SPEED` = 1.3 (m/s)

**Parametry serwera (grupa zaawansowana, domyślne z QSettings)**
- `JAVA_PATH` — ścieżka do binarki Javy 8.
- `OTP_JAR_PATH` — ścieżka do `otp-1.5.0-shaded.jar`.
- `OTP_XMX_BUILD` / `OTP_XMX_SERVE` — pamięć dla fazy build i serve osobno
  (serwer analityczny zwykle potrzebuje więcej).
- `OTP_PORT` — port serwera (domyślnie 8801).
- `EXISTING_GRAPH_DIR` — opcjonalnie: gotowy katalog routera z `Graph.obj`
  (pomija kopiowanie i budowanie).
- `KEEP_SERVER_ALIVE` (bool, domyślnie `true`).

**Wyjścia**
- `OUTPUT_HEX` — warstwa heksagonów z wynikiem (`QgsProcessingParameterFeatureSink`).
- `OUTPUT_COUNT_RASTER` — raster zliczeń (`QgsProcessingParameterRasterDestination`).
- `WORK_DIR` — katalog roboczy na pliki pośrednie (powierzchnie, graf, cache).

### 8.2. Przebieg działania (kroki wewnętrzne)

1. **Walidacja** — sprawdź ścieżki Java/OTP (jak `TestOtpServer`), istnienie
   plików wejściowych, poprawność daty względem GTFS, dostępność portu.
2. **Lista czasów** — z `TIME_START`, `TIME_END`, `INTERVAL` zbuduj listę
   znaczników czasu (zastępuje ręczny `time_6_22.csv`). Dla 1-min okna 6:00–22:00
   to 961 pozycji; 15-min → 65; 60-min → 17.
3. **Siatka** — jeśli `GENERATE_GRID`, wygeneruj siatkę heksagonalną
   (`GRID_CELL_SIZE`) pokrywającą obszar analizy; inaczej użyj `HEX_GRID`.
4. **Graf + serwer OTP** — build (lub cache) grafu i start serwera (sekcja 6.2).
5. **Generowanie powierzchni** — dla każdego znacznika czasu wywołaj OTP
   (sekcja 7), zapisz GeoTIFF. To najdłuższy etap (rząd ~25 min/miasto przy 1-min).
6. **Stos rastrów** — zbuduj wielokanałowy raster (wirtualny VRT lub bezpośrednio
   tablica NumPy); każda powierzchnia = osobny kanał.
7. **Zliczanie** — port logiki `skrypt_wro.py`: dla każdego piksela policz, w ilu
   kanałach `wartość <= TRAVEL_TIME_THRESHOLD`; wynik to jednokanałowy raster
   zliczeń (0…N), zapisany jako `OUTPUT_COUNT_RASTER`. Obsłuż NoData.
8. **Czyszczenie zer** — port kroku GRASS `r.null`: piksele o wartości 0 ustaw
   na NoData (operacja NumPy/GDAL, bez GRASS).
9. **Statystyki strefowe** — `native:zonalstatisticsfb`: zagreguj raster zliczeń
   do heksagonów (statystyka domyślna: średnia; konfigurowalna). Zadbaj o zgodność
   CRS — w razie potrzeby reprojekcja rastra do CRS siatki.
10. **Klasyfikacja** — dodaj do heksagonów pole kategorii service-time wg progów
    z artykułu (wartości w minutach, 960 min = 16 h):
    - **constantly accessible** — 12–16 h (720–960 min)
    - **regularly accessible** — 6–12 h (360–720 min)
    - **periodically accessible** — 3–6 h (180–360 min)
    - **episodically accessible** — 0–3 h (0–180 min)
11. **Stylizacja** — zastosuj do warstwy wynikowej styl `service_time.qml`
    (4-kategorialny, paleta jak w artykule).
12. **Podsumowanie** — w logu Processing wypisz statystyki: udział komórek
    w każdej kategorii, % obszaru bez dostępu (poza jakąkolwiek strefą) itp.

### 8.3. Praca w tle i UX
- Cały algorytm musi działać jako **zadanie w tle** (`QgsTask` / mechanika
  Processing) — nie wolno zamrażać interfejsu QGIS na 25 minut.
- **Pasek postępu** aktualizowany sensownie: krok generowania powierzchni to
  N iteracji — pokazuj `i / N` i szacowany czas.
- **Anulowanie** musi działać na każdym etapie; po anulowaniu posprzątać
  (ubić proces OTP, jeśli był startowany przez wtyczkę i `KEEP_SERVER_ALIVE=false`).
- Wszystkie komunikaty czytelne dla nietechnicznego planisty (patrz sekcja 9).

---

## 9. Obsługa błędów (realne bóle użytkownika)

Wtyczka jest kierowana do urbanistów / planistów, często bez zaplecza IT. Każdy
błąd musi mieć **czytelny komunikat z konkretną instrukcją naprawy**, nie surowy
stack trace. Obsłuż minimum:

| Sytuacja | Oczekiwane zachowanie |
|---|---|
| Zła wersja Javy (nie 8) | Jasny komunikat: „OTP 1.5.0 wymaga Javy 8; wskazana binarka to wersja X. Pobierz przenośny Temurin 8 i wskaż ścieżkę w ustawieniach." |
| Brak / zła ścieżka do `otp-1.5.0-shaded.jar` | Komunikat z instrukcją, gdzie pobrać i jak wskazać. |
| Port zajęty | Wykryj, zaproponuj inny port lub wskaż, że serwer już działa. |
| Build grafu się nie powiódł | Przechwyć stderr OTP, pokaż skróconą diagnozę (np. zły GTFS, za mało RAM → podnieś `-Xmx`). |
| Surface endpoint niedostępny | Wykryj i podpowiedz, że OTP musi być uruchomiony z włączonym Analyst/surface. |
| Niezgodność CRS siatki i rastra | Automatyczna reprojekcja + informacja w logu. |
| Liczba wygenerowanych powierzchni ≠ liczba oczekiwanych | Ostrzeżenie (jak w `rename_surface_files` w skrypcie R) — nie kontynuuj po cichu. |
| `osgeo` / GDAL niedostępne | Nie powinno wystąpić wewnątrz QGIS; jeśli kod jest uruchamiany poza QGIS — czytelny komunikat, że algorytm działa tylko w środowisku QGIS. |
| Anulowanie przez użytkownika | Czyste sprzątanie, brak osieroconych procesów Javy. |

Uwaga kontekstowa: użytkownik zgłaszał wcześniej, że `skrypt_wro.py` poza QGIS
rzuca „OSgeo module not found". W tej wtyczce problem znika, bo kod działa
wewnątrz interpretera QGIS — ale warto dodać jawny guard i komunikat.

---

## 10. Kryteria akceptacji v1

- [x] Wtyczka instaluje się w QGIS 3.40 LTR i rejestruje provider `easy-OTP`
      w Processing Toolbox.
- [x] `TestOtpServer` poprawnie wykrywa wersję Javy i diagnozuje konfigurację.
- [x] `GenerateHexGrid` tworzy poprawną siatkę o zadanym rozmiarze komórki.
- [x] `RunTemporalAccessibility` przechodzi pełny pipeline na danych testowych
      jednego miasta (mały zestaw, np. 60-min interwał) bez interwencji ręcznej.
- [x] Działa też dla interwałów 1-min i 15-min.
- [x] Wynikowa warstwa heksagonów ma poprawne wartości service-time
      i klasyfikację 4-kategorialną zgodną z artykułem.
- [x] Raster zliczeń zapisany i poprawny (zweryfikowany względem logiki
      `skrypt_wro.py`).
- [x] Brak zależności od R i od providera GRASS.
- [x] Brak zależności wymagających `pip install` poza tym, co dostarcza QGIS.
- [x] Algorytm działa w tle, ma działający pasek postępu i anulowanie.
- [x] Komunikaty błędów są czytelne dla nietechnicznego użytkownika.
- [x] Brak osieroconych procesów Javy po zakończeniu / anulowaniu.
- [x] `README.md` z instrukcją: pozyskanie OTP 1.5.0, pozyscanie przenośnej
      Javy 8, konfiguracja ścieżek, przykładowe uruchomienie.

---

## 11. Kamienie milowe (sugerowana kolejność prac)

1. **Szkielet** — struktura wtyczki, rejestracja providera, pusty algorytm,
   `metadata.txt`, instalacja w QGIS.
2. **Warstwa OTP** — `otp_server.py` + `otp_client.py` + `TestOtpServer`;
   build grafu, start serwera, jedna powierzchnia testowa.
3. **Pętla powierzchni** — `surface_runner.py`, generowanie N powierzchni
   z listy czasów, praca w tle + postęp + anulowanie.
4. **Przetwarzanie rastrów** — `raster_processing.py` (stos kanałów, zliczanie,
   czyszczenie zer).
5. **Strefy i klasyfikacja** — `zonal.py`, statystyki strefowe, 4 kategorie,
   styl QML.
6. **`GenerateHexGrid`** + scalenie wszystkiego w jeden algorytm główny.
7. **Twardnienie** — obsługa błędów, komunikaty, testy, `README.md`.

---

## 12. Materiały referencyjne dla Claude Code

- **Artykuł Kaczorowski & Wróblewski** — definicja metody, wszystkie parametry,
  klasyfikacja 4-kategorialna, opis workflow (Figure 1).
- **`Surface_analysis_wro.R`** — wzorzec logiki kroku 6 do portu na Python.
- **`skrypt_wro.py`** — wzorzec logiki zliczania kanałów.
- **Źródło pakietu `otpr`** (`github.com/marcusyoung/otpr`, licencja MIT) —
  wzorcowa implementacja wywołań REST OTP do przeniesienia na Python.
- **Tutorial OTP Marcusa Younga** (`github.com/marcusyoung/otp-tutorial`) —
  dokumentuje działającą konfigurację i uruchomienie OTP 1.5.0; metoda
  z artykułu jest na nim oparta.
- **Dokumentacja OTP 1.5.0** (`docs.opentripplanner.org/en/v1.5.0/`) —
  weryfikacja flag uruchomieniowych i endpointów surface.
- **Istniejąca wtyczka „OpenTripPlanner Plugin"** na repozytorium QGIS
  (`plugins.qgis.org/plugins/OpenTripPlannerPlugin/`) — prior art: spina QGIS
  z OTP 1.5 (izochrony/trasy/macierze). NIE robi analizy czasowej service-time,
  ale jest dobrym wzorcem architektury QGIS ↔ OTP 1.5. Sprawdzić jej podejście
  do konfiguracji i komunikacji z serwerem.

---

## 13. Otwarte kwestie do weryfikacji w trakcie implementacji

Kwestie flag uruchomieniowych OTP, jednostki rastra travel-time i statystyki
strefowej zostały **rozstrzygnięte** (patrz sekcje 6.2, 7 i 8.2 — statystyka
strefowa domyślnie **średnia**, zgodnie z artykułem). Pozostaje:

- Czy `requests` jest dostępne w każdej dystrybucji QGIS 3.40, czy bezpieczniej
  oprzeć klienta REST o `urllib` z biblioteki standardowej.
- Strategia hashowania `<router_id>` — pełny hash treści plików GTFS/OSM bywa
  kosztowny dla dużych `.osm.pbf`; rozważyć hash z rozmiaru + mtime + nazwy
  jako szybszy wariant, z opcją pełnego hashu treści.
- Dostępność nazwy `easy-OTP` na repozytorium wtyczek QGIS (gdyby projekt miał
  być kiedyś publikowany).

---

## 14. Roadmapa (po v1)

Kolejność orientacyjna, do uszczegółowienia później:

1. **Moduł populacji** — automatyzacja kroku 11 (`ludnosc_studentow`):
   nałożenie warstwy demograficznej i oszacowanie liczby studentów w strefach
   dostępności. Wymaga obsługi przerobionej warstwy ludności (dziś przygotowywanej
   ręcznie z Excela) — do zaprojektowania osobno.
2. **Automatyczne pozyskiwanie danych** — pobieranie obszaru OSM i rozkładów GTFS
   na podstawie wskazania miasta/obszaru zamiast plików lokalnych. (Do zbadania:
   czy OSM Slice udostępnia publiczne API — strona do eksportu istnieje, ale
   udokumentowanego API nie potwierdzono; ewentualnie inne źródła OSM oraz
   agregatory GTFS typu Transitland / Mobility Database.)
3. **Automatyczne pobieranie przenośnego JRE 8** — wtyczka sama ściąga i rozpakowuje
   build Temurin/Zulu 8, eliminując ręczny krok konfiguracji Javy.
4. **Wiele punktów docelowych** — uwaga: przy wielu źródłach OTP 1.5.0 zużywa
   zbyt dużo RAM dla typowego sprzętu (potwierdzone w testach). Realny kierunek
   to przejście na silnik R5.
5. **Silnik R5 przez `r5py`** (dalsza perspektywa) — `r5py` to utrzymywany
   pythonowy odpowiednik `r5r`; R5 natywnie liczy macierze travel-time z oknem
   czasowym odjazdów, co bardzo dobrze pasuje do analizy czasowej i jest
   znacząco wydajniejsze. Wymaga osobnej warstwy abstrakcji silnika w kodzie.
6. **Moduł „car dependency"** — na podstawie artykułu *Car Dependency in Urban
   Accessibility*: obliczanie indeksu CDI (Car Dependency Index) jako kontrastu
   między dostępnością samochodem a komunikacją publiczną dla tych samych
   heksagonów. Osobny moduł w ramach platformy Project Chronos.
7. **Generowanie siatki H3** — rozszerzenie generatora siatki o indeksowanie H3
   (jak w artykule CDI), nie tylko prostą siatkę heksagonalną QGIS.
8. **CountFromExistingSurfaces — pełny pipeline od surfac'ów** — rozszerzenie
   algorytmu `CountFromExistingSurfaces` (dodanego w M4) o opcjonalne wyjście
   zonal stats i klasyfikacji 4-kategorialnej. Pozwoli przeliczyć cały pipeline
   (count → zonal stats → klasyfikacja) od gotowych `surface_*.tiff` bez
   ponownego uruchamiania OTP (~22 min). Implementacja: po ukończeniu M5 dodać
   do `CountFromExistingSurfaces` parametry `HEX_GRID` i `OUTPUT_HEX` (opcjonalne)
   oraz wywołać funkcje `run_zonal_stats` i `classify_service_time` z
   `core/zonal.py` — reuse 100% kodu z głównego algorytmu.

---

*Koniec dokumentu PR — `easy-OTP` v1.*
