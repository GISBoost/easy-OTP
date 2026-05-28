# easy-OTP — Roadmapa: Prompty i status

## 1. Kolejność priorytetów

Elementy roadmapy (sekcja 14 PR) uszeregowane według wartości dla użytkownika
i nakładu pracy. R8 pominięty — zaimplementowany w M5.

| Priorytet | Milestone | Uzasadnienie |
|-----------|-----------|--------------|
| 1 | **R3** Auto pobieranie JRE 8 | Usuwa największą barierę wejścia (ręczna instalacja Javy); zadanie izolowane, `urllib` + rozpakowanie, zero zmian w pipeline |
| 2 | **R7** Siatka H3 | Rozszerzenie istniejącego `GenerateHexGrid`; izolowana zmiana, brak wpływu na pipeline |
| 3 | **R2** Auto pozyskiwanie OSM + GTFS | Wysoka wartość UX; umiarkowane ryzyko (badanie API Geofabrik + Transitland) |
| 4 | **R1** Moduł populacji | Port gotowego modelu referencyjnego; wymaga decyzji projektowej dot. formatu danych wejściowych |
| 5 | **R6** Moduł Car Dependency CDI | Nowy moduł sub-platformy; wymaga zbadania routingu samochodowego w OTP 1.5.0 |
| 6 | **R5** Silnik R5 przez r5py | Duża zmiana architektoniczna (warstwa abstrakcji silnika); konflikt Java 8 vs Java 21 |
| 7 | **R4** Wiele punktów docelowych | Zablokowane przez R5 (potwierdzone problemy RAM w OTP 1.5.0) |

---

## 2. Status kryteriów akceptacji v1 (sekcja 10 PR)

| # | Kryterium | Status |
|---|-----------|--------|
| 1 | Wtyczka instaluje się w QGIS 3.40 LTR i rejestruje provider `easy-OTP` | ✅ Zaimplementowane (M1) |
| 2 | `TestOtpServer` poprawnie wykrywa wersję Javy i diagnozuje konfigurację | ✅ Zaimplementowane (M2, M7) |
| 3 | `GenerateHexGrid` tworzy poprawną siatkę o zadanym rozmiarze komórki | ✅ Zaimplementowane (M6) |
| 4 | `RunTemporalAccessibility` przechodzi pełny pipeline na danych testowych | ⚠️ Zaimplementowane (M6) — wymaga ręcznego testu z OTP |
| 5 | Działa dla interwałów 1-min i 15-min | ⚠️ Zaimplementowane (M3+M6) — wymaga ręcznego testu |
| 6 | Warstwa heksagonów ma poprawne wartości service-time i klasyfikację 4-kategorialną | ⚠️ Zaimplementowane (M5) — wymaga ręcznego testu |
| 7 | Raster zliczeń zapisany i poprawny (względem logiki `skrypt_wro.py`) | ✅ Zweryfikowane manualnie (M4) |
| 8 | Brak zależności od R i od providera GRASS | ✅ By design (przestrzegane przez cały projekt) |
| 9 | Brak zależności wymagających `pip install` poza tym co dostarcza QGIS | ✅ By design (przestrzegane przez cały projekt) |
| 10 | Algorytm działa w tle, ma działający pasek postępu i anulowanie | ⚠️ Zaimplementowane (M3) — wymaga ręcznego testu |
| 11 | Komunikaty błędów czytelne dla nietechnicznego użytkownika | ✅ Zaimplementowane (M7) |
| 12 | Brak osieroconych procesów Javy po zakończeniu / anulowaniu | ⚠️ Zaimplementowane (M3+) — wymaga ręcznego testu anulowania |
| 13 | `README.md` z instrukcją konfiguracji i przykładowym uruchomieniem | ✅ Zaimplementowane (M7) |

**Kryteria wymagające ręcznego testu w QGIS (#4, 5, 6, 10, 12):**

- **#4, 5**: Uruchom `RunTemporalAccessibility` z interwałem 60 min, następnie 15 min
  i 1 min. Sprawdź logi (oczekiwana liczba surfaces: 17 / 65 / 961).
- **#6**: Po zakończeniu runu sprawdź warstwę hexagonów — czy ma pole `st_class`
  z wartościami `constantly_accessible` / `regularly_accessible` /
  `periodically_accessible` / `episodically_accessible` i styl QML.
- **#10**: Uruchom długi run (interwał 1 min) i kliknij Cancel w połowie —
  sprawdź, czy pasek postępu się aktualizuje i czy po anulowaniu nie zostaje
  osierocony proces Java (`tasklist | findstr java` na Windows).
- **#12**: Jw. — sprawdź brak osieroconych procesów po cancel i po normalnym
  zakończeniu z `KEEP_SERVER_ALIVE=False`.

---

## 3. Status elementów roadmapy (sekcja 14 PR)

| # | Element | Status |
|---|---------|--------|
| R1 | Moduł populacji studentów (`ludnosc_studentow`) | Nie rozpoczęte |
| R2 | Automatyczne pozyskiwanie danych (OSM + GTFS) | Nie rozpoczęte |
| R3 | Automatyczne pobieranie przenośnego JRE 8 | Nie rozpoczęte |
| R4 | Wiele punktów docelowych | Nie rozpoczęte (zablokowane przez R5) |
| R5 | Silnik R5 przez r5py | Nie rozpoczęte |
| R6 | Moduł Car Dependency Index (CDI) | Nie rozpoczęte |
| R7 | Generowanie siatki H3 | Nie rozpoczęte |
| R8 | CountFromExistingSurfaces — pełny pipeline | ✅ Zaimplementowane (M5) |

---

## 4. Prompty dla kamieni roadmapy

Kolejność prezentacji = kolejność priorytetów.

---

## Prompt R3 — Kamień R3: Automatyczne pobieranie przenośnego JRE 8

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 5, 6.1, 14.3).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

Zakres: Nowy algorytm pomocniczy `DownloadJre` w `easy_otp/algorithms/download_jre.py`.
Pobiera, weryfikuje i rozpakowuje przenośny build Eclipse Temurin 8 (lub Azul Zulu 8)
dla bieżącej platformy (Windows/Linux/macOS), a następnie zapisuje ścieżkę do binarki
`java` w QSettings wtyczki — eliminując konieczność ręcznego pobrania i konfiguracji
Javy przez użytkownika.

Do zrobienia:
- Zbadaj Adoptium API v3:
  `https://api.adoptium.net/v3/assets/latest/8/hotspot`
  (parametry: `os=windows|linux|mac`, `arch=x64`, `image_type=jre`).
  Potwierdź, że URL zwraca metadane z linkiem do pobrania i sumą SHA256.
  Udokumentuj format odpowiedzi w komentarzu kodu.
- Zaimplementuj `easy_otp/algorithms/download_jre.py` jako `QgsProcessingAlgorithm`
  z parametrami:
  `JRE_DEST_DIR` (QgsProcessingParameterFile, Folder — katalog docelowy),
  `PLATFORM` (QgsProcessingParameterEnum: Windows/Linux/macOS,
               domyślnie wykrywany automatycznie z `sys.platform`).
- Pobieranie wyłącznie przez `urllib` (bez `requests` — sekcja 5 PR).
- Weryfikacja SHA256 pobranego archiwum względem sumy z Adoptium API.
- Rozpakowanie: `.zip` na Windows (stdlib `zipfile`), `.tar.gz` na Linux/macOS
  (stdlib `tarfile`).
- Po rozpakowaniu: zlokalizuj `bin/java.exe` (Windows) / `bin/java` (Linux/macOS),
  zweryfikuj wersję wywołując `check_java_version()` z `easy_otp/core/otp_server.py`.
- Zapisz ścieżkę binarki w QSettings klucz `easy_otp/java_path`.
- Pasek postępu: chunk-based podczas pobierania + info podczas rozpakowywania.
- Anulowanie na każdym etapie; po anulowaniu usuń częściowo pobrany plik.
- Zarejestruj algorytm w `easy_otp/provider.py` i `easy_otp/algorithms/__init__.py`.
- Zaktualizuj `README.md`: dodaj sekcję „Automatyczne pobieranie Javy 8".
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** W QGIS uruchom `DownloadJre` wskazując pusty folder docelowy.
Sprawdź: (1) czy archiwum jest pobierane i rozpakowywane do wskazanego folderu,
(2) czy weryfikacja SHA256 działa — podaj celowo inne archiwum i oczekuj błędu,
(3) czy po zakończeniu `TestOtpServer` automatycznie widzi `JAVA_PATH` z QSettings
i zgłasza „Java 8 OK", (4) czy anulowanie w trakcie pobierania usuwa plik tymczasowy.

---

## Prompt R7 — Kamień R7: Generowanie siatki H3

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 5, 14.7).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

Zakres: Rozszerzenie algorytmu `GenerateHexGrid` w
`easy_otp/algorithms/generate_hex_grid.py` o tryb H3 —
generowanie siatki heksagonalnej indeksowanej H3 zamiast
(lub obok) siatki natywnej QGIS `native:creategrid`.
Gdy `H3_MODE=False` (domyślnie): zachowanie bez zmian.

Do zrobienia:
- Zaimplementuj podejście bez biblioteki `h3` — pakiet `h3` nie wchodzi
  w skład standardowej dystrybucji QGIS i wymaga `pip install`, co jest
  twardym zakazem (sekcja 5 PR). Obliczaj wierzchołki heksagonów H3 z centrum
  komórki metodą matematyki sferycznej — promień komórki H3 dla każdej
  rozdzielczości (0–15) jest znany ze specyfikacji H3
  (`https://h3geo.org/docs/core-library/restable`). Dodaj pole `h3index`
  (String) obliczane przez prostą funkcję indeksowania lat/lon → H3 cell ID
  (algorytm enkodowania H3 jest publiczny).
- Dodaj parametry do `GenerateHexGrid`:
  `H3_MODE` (QgsProcessingParameterBoolean, domyślnie False),
  `H3_RESOLUTION` (QgsProcessingParameterNumber, Integer, domyślnie 8,
                   zakres 0–15; aktywny tylko gdy `H3_MODE=True`).
- Gdy `H3_MODE=True`:
  wygeneruj siatkę pokrywającą `EXTENT` w wybranej rozdzielczości H3,
  każdy hexagon ma pole `h3index` (String) z identyfikatorem H3.
  Geometrie w CRS wyjściowym (domyślnie EPSG:3857, jak obecne wyjście).
- Zaktualizuj `shortHelpString()` algorytmu.
- Dodaj test jednostkowy (w `easy_otp/test/`) weryfikujący, że dla małego
  zasięgu i `H3_RESOLUTION=8` generowane są heksagony z niepustym polem `h3index`.
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** W QGIS uruchom `GenerateHexGrid` z `H3_MODE=True`,
`H3_RESOLUTION=8` dla zasięgu centrum Wrocławia.
Sprawdź: (1) czy heksagony mają poprawne geometrie (sześciokąty),
(2) czy każdy heksagon ma pole `h3index` z poprawnym stringiem H3,
(3) czy zasięg siatki pokrywa wskazany extent.
Uruchom też z `H3_MODE=False` — upewnij się, że istniejące zachowanie
nie zmieniło się.

---

## Prompt R2 — Kamień R2: Automatyczne pozyskiwanie danych OSM i GTFS

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 2, 3 (kroki 2–3), 8.1, 14.2).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

Zakres: Nowy algorytm pomocniczy `DownloadTransitData` w
`easy_otp/algorithms/download_transit_data.py`. Pobiera plik OSM (`.osm.pbf`)
i pliki GTFS (`.zip`) na podstawie wskazania nazwy / obszaru miasta,
bez konieczności ręcznego pobierania plików.

Do zrobienia:
- Zbadaj i udokumentuj dostępne API (wyniki badania umieść w planie):
  * Geofabrik: `https://download.geofabrik.de/` — sprawdź czy istnieje
    plik `index-v1.json` z listą obszarów i deterministycznymi URLami
    (np. `europe/poland-latest.osm.pbf`).
  * Transitland API v2: `https://transit.land/api/v2/` — katalog feedów GTFS,
    filtrowanie po bbox lub nazwie; sprawdź czy wymaga klucza API.
  * Mobility Database: `https://mobilitydatabase.org/` — REST API, sprawdź
    publiczny dostęp bez klucza.
  * OSM Slice API: potwierdź status (z PR sekcja 14.2: udokumentowanego API
    nie potwierdzono) — jeśli nieaktywne, pomiń.
- Zaimplementuj `easy_otp/algorithms/download_transit_data.py`:
  parametry: `AREA_NAME` (String — np. „Wrocław"),
  `DEST_DIR` (QgsProcessingParameterFile, Folder),
  `OSM_SOURCE` (QgsProcessingParameterEnum, domyślnie Geofabrik),
  `GTFS_SOURCE` (QgsProcessingParameterEnum, np. Transitland / Mobility Database),
  `GTFS_API_KEY` (String, opcjonalny — dla źródeł wymagających klucza).
- Pobieranie wyłącznie przez `urllib` (bez `requests` — sekcja 5 PR).
- Pasek postępu chunk-based; anulowanie z usunięciem częściowych plików.
- Po zakończeniu wypisz w logu Processing ścieżki pobranych plików
  gotowe do skopiowania w parametry `RunTemporalAccessibility`.
- Zarejestruj algorytm w `easy_otp/provider.py` i `easy_otp/algorithms/__init__.py`.
- Zaktualizuj `README.md`.
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** W QGIS uruchom `DownloadTransitData` dla Wrocławia.
Sprawdź: (1) czy pobierany jest `.osm.pbf` właściwego obszaru,
(2) czy pobierany jest przynajmniej jeden plik GTFS `.zip`,
(3) czy pliki są prawidłowymi archiwami (otwarcie bez błędów).
Następnie uruchom `RunTemporalAccessibility` z pobranymi plikami —
sprawdź, czy OTP poprawnie buduje graf.

---

## Prompt R1 — Kamień R1: Moduł populacji studentów

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 3 (krok 11), 8.2, 14.1).
Przeczytaj też `reference/ludnosc_studentow_model_qgis.py`
(plik TYLKO do odczytu — nie modyfikuj).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

Zakres: Port modelu `ludnosc_studentow` z pliku referencyjnego jako nowy
algorytm `PopulationOverlay` w `easy_otp/algorithms/population_overlay.py`.
Algorytm nakłada warstwę demograficzną na siatkę heksagonalną i oblicza liczbę
studentów w każdym heksagonie metodą areal interpolation (interpolacja wagowana
polem).

Do zrobienia:
- Przeanalizuj logikę modelu referencyjnego (7 kroków: pole area, gęstość
  `pop20_29/area`, splitwithlines, przeliczenie area, pointonsurface,
  szacunek liczby studentów jako `pow_podzielone * gestosc`,
  countpointsinpolygon z wagowaniem).
- Zaprojektuj wymagania dla warstwy wejściowej `POPULATION_LAYER`:
  wymagane pole: `pop20_29` (populacja w wieku 20–29 lat, Float lub Int).
  W `shortHelpString()` opisz skąd pobrać warstwę
  (GUS / NSP 2021 — dane na poziomie jednostek przestrzennych).
- Zaimplementuj `easy_otp/algorithms/population_overlay.py`:
  parametry: `HEX_GRID` (QgsProcessingParameterVectorLayer — warstwa hexagonów,
             wynik `RunTemporalAccessibility` lub dowolna siatka),
  `POPULATION_LAYER` (QgsProcessingParameterVectorLayer — polygony z `pop20_29`),
  `POPULATION_FIELD` (QgsProcessingParameterField, domyślnie `pop20_29`),
  `OUTPUT` (QgsProcessingParameterFeatureSink — hexagony z dodanym polem
            `num_students` Float).
- Port logiki: wszystkie 7 kroków jako wywołania `processing.run(...)`
  z `is_child_algorithm=True`.
- Obsługa błędów: brak pola `pop20_29` → czytelny komunikat; niezgodność CRS →
  automatyczna reprojekcja + info w logu; pusta warstwa populacji → ostrzeżenie.
- Zarejestruj algorytm w `easy_otp/provider.py` i `easy_otp/algorithms/__init__.py`.
- Zaktualizuj `README.md`.
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** Przygotuj testową warstwę populacji (kilka poligonów z polem
`pop20_29`) i siatkę hexagonów z poprzedniego runu `RunTemporalAccessibility`.
W QGIS uruchom `PopulationOverlay`.
Sprawdź: (1) czy wynikowa warstwa hexagonów ma pole `num_students`,
(2) czy suma `num_students` po wszystkich hexagonach jest zbliżona do
sumy `pop20_29` w warstwie populacji nakrywającej obszar siatki
(weryfikacja poprawności areal interpolation),
(3) czy hexagony poza obszarem populacji mają wartość 0.

---

## Prompt R6 — Kamień R6: Moduł Car Dependency Index (CDI)

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 1, 14.6).
Przeczytaj też streszczenie artykułu `papers/car dependency in urban accessibility.pdf`
(sekcja metod i definicja CDI).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

Zakres: Nowy moduł obliczający Car Dependency Index (CDI) — stosunek
dostępności samochodem do dostępności komunikacją publiczną dla tych samych
heksagonów. Moduł wchodzi w zakres platformy Project Chronos, ale jest
zaimplementowany jako algorytm Processing wtyczki `easy-OTP`.

Do zrobienia:
- Przeanalizuj definicję CDI z artykułu: jak mierzona jest dostępność
  samochodem (jaki wskaźnik, jakie parametry OTP), jak obliczany jest CDI
  (wzór, normalizacja).
- Zbadaj: czy OTP 1.5.0 obsługuje `mode=CAR` w trybie analyst surface
  (endpoint `/otp/surfaces`). Sprawdź dokumentację OTP 1.5.0 lub source.
  Jeśli tak — możliwy reuse `OtpClient`, `OtpServer`, `surface_runner.py`.
  Jeśli nie — zatrzymaj się i zapytaj użytkownika o decyzję architektoniczną
  przed pisaniem kodu. Nie projektuj alternatywy samodzielnie — to decyzja
  wymagająca zgody właściciela projektu.
- Zaimplementuj `easy_otp/algorithms/run_car_dependency.py`:
  parametry: `PT_HEX_GRID` (QgsProcessingParameterVectorLayer —
             warstwa hexagonów z wynikiem `RunTemporalAccessibility`,
             musi mieć pole `otp_mean`),
  `OSM_PBF`, `ORIGIN_POINT`, `JAVA_PATH`, `OTP_JAR_PATH`, `WORK_DIR`,
  parametry serwera (zaawansowane — jak w `RunTemporalAccessibility`),
  `OUTPUT_CDI` (QgsProcessingParameterFeatureSink — hexagony z polem `cdi`).
- Definicja `cdi` zgodna z artykułem. Dodaj pole `cdi` (Float) do OUTPUT_CDI.
- Reuse 100% istniejącego kodu zarządzania serwerem OTP z `core/otp_server.py`,
  `core/otp_client.py`, `core/surface_runner.py` tam, gdzie to możliwe.
- Zarejestruj algorytm w `easy_otp/provider.py` i `easy_otp/algorithms/__init__.py`.
- Zaktualizuj `README.md`.
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** Uruchom `RunCarDependency` z tymi samymi danymi wejściowymi
co poprzedni run PT. Sprawdź: (1) czy hexagony mają pole `cdi` z wartościami
numerycznymi, (2) czy wartości CDI są sensowne dla znanych obszarów
(centrum miasta z dobrym PT: CDI < 1; obrzeża bez PT: CDI > 1),
(3) czy mapa CDI wizualnie odpowiada opisowi z artykułu.

---

## Prompt R5 — Kamień R5: Silnik R5 przez r5py

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 1, 4, 8.2, 14.4, 14.5).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

Zakres: Dodanie warstwy abstrakcji silnika do architektury wtyczki i
implementacja backendu R5 przez `r5py` jako alternatywy dla OTP 1.5.0.
R5 natywnie oblicza macierze travel-time z oknem czasowym odjazdów — jest
to znacznie wydajniejsze niż sekwencyjne generowanie 961 surface'ów OTP.
Ta funkcjonalność jest też wymagana przez Kamień R4 (wiele punktów docelowych).

Do zrobienia:
- Zaprojektuj interfejs `EngineBackend` w `easy_otp/core/engine.py`:
  klasa abstrakcyjna z metodami:
  `build(osm_pbf, gtfs_files, work_dir, feedback) -> None`,
  `compute_travel_times(origin, time_list, params, work_dir, feedback) -> np.ndarray`,
  `stop(feedback) -> None`.
- Zaimplementuj `easy_otp/core/engine_otp.py` — wrapper istniejącego pipeline OTP
  (`otp_server` + `otp_client` + `surface_runner`) za interfejsem `EngineBackend`.
  Zero zmiany w logice, tylko opakowanie. Istniejące pliki pozostają bez zmian.
- Zbadaj r5py:
  * Wymagania JVM: r5py wymaga Java 21+ — KONFLIKT z Java 8 dla OTP 1.5.0.
    Zaprojektuj rozwiązanie: osobne ścieżki `JAVA_PATH_OTP` (Java 8) i
    `JAVA_PATH_R5` (Java 21+) w QSettings i w parametrach algorytmu.
  * API r5py: `r5py.TravelTimeMatrixComputer` — jakie parametry, jaki format
    wyjścia (DataFrame). Sprawdź, czy `r5py` wchodzi w skład standardowej
    dystrybucji QGIS 3.40. Jeśli wymaga `pip install` (co jest twardym zakazem —
    sekcja 5 PR), zaimplementuj `engine_r5.py` jako **stub**: wszystkie metody
    rzucają `NotImplementedError` z komunikatem wyjaśniającym, że backend R5
    wymaga manualnej instalacji `r5py` poza standardowym QGIS. Nie próbuj obejść
    ograniczenia `pip install`.
  * Udokumentuj wnioski w planie.
- Zaimplementuj `easy_otp/core/engine_r5.py` — backend r5py za interfejsem
  `EngineBackend`. Jeśli r5py dostępne: pełna implementacja z
  `r5py.TravelTimeMatrixComputer`, wynik `np.ndarray` identyczny kształtem
  jak z pętli OTP. Jeśli r5py niedostępne bez pip: stub z `NotImplementedError`.
- Zaktualizuj `RunTemporalAccessibility`: dodaj parametr `ENGINE`
  (QgsProcessingParameterEnum: `OTP 1.5.0` (domyślny) / `R5`).
  Dispatcher wybiera backend na podstawie wartości tego parametru.
- Zaktualizuj `README.md`: opisz wymagania Java 21 dla backendu R5. Jeśli r5py
  wymaga `pip install`, zaznacz to wyłącznie jako informację dla zaawansowanych
  użytkowników uruchamiających wtyczkę poza standardową dystrybucją QGIS —
  nie jako domyślną ścieżkę instalacji.
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** Uruchom `RunTemporalAccessibility` z `ENGINE=R5`
dla małego zestawu danych (interwał 60 min, małe miasto).
Sprawdź: (1) czy pipeline kończy się bez błędów i bez OOM,
(2) czy wynikowa warstwa hexagonów ma pole `st_class`,
(3) porównaj czas wykonania i rozkład kategorii z backendem OTP
dla tych samych parametrów — wartości nie muszą być identyczne,
ale rozkład powinien być zbliżony.

---

## Prompt R4 — Kamień R4: Wiele punktów docelowych

```
Przeczytaj CLAUDE.md i docs/PR_easy-OTP.md (sekcje 8.1, 8.2, 14.4, 14.5).
Jeszcze nie pisz kodu — najpierw przedstaw plan.

WAŻNE: Ta funkcjonalność wymaga ukończenia Kamienia R5 (silnik R5).
OTP 1.5.0 ma potwierdzone problemy z RAM przy wielu źródłach SPT
(patrz sekcja 14.4 PR). Implementuj TYLKO po ukończeniu i przetestowaniu R5.

Zakres: Rozszerzenie `RunTemporalAccessibility` o obsługę wielu punktów
docelowych (origins) jednocześnie, wyłącznie przez backend R5.

Do zrobienia:
- Zastąp (lub uzupełnij) parametr `ORIGIN_POINT` o `ORIGIN_LAYER`
  (QgsProcessingParameterVectorLayer — warstwa punktowa z wieloma origins).
  Zachowaj `ORIGIN_POINT` jako alternatywę dla wstecznej kompatybilności.
- Zaprojektuj strategię wyjściową — do uzgodnienia w planie:
  A) Osobne pliki `OUTPUT_HEX` per origin (lista / folder z numeracją),
  B) Jedna scalona warstwa z dodatkowym polem `origin_id` (String),
  C) Agregacja (maksimum / suma service-time per hexagon).
  Uzasadnij wybór jednej strategii.
- Backend OTP: przy `|origins| > 1` — zablokuj uruchomienie z czytelnym
  komunikatem: „OTP 1.5.0 supports only a single origin point.
  Use ENGINE=R5 for multiple origins."
- Backend R5: obsługuje wiele origins natywnie przez
  `r5py.TravelTimeMatrixComputer` — przekaż wszystkie punkty jako batch.
- Zaktualizuj pasek postępu: pokazuj postęp per origin lub per timestamp
  w zależności od strategii.
- Zaktualizuj `README.md`.
- Na końcu invoke milestone-reviewer agent w głównej sesji (PRZED /clear) → napraw blokery → commit.

Po zakończeniu wypisz, które kryteria akceptacji wymagają ręcznej weryfikacji
z mojej strony i jak je sprawdzić.
```

**Weryfikacja:** Przygotuj warstwę punktową z 3–5 punktami docelowymi
(np. kampusy uczelni). Uruchom `RunTemporalAccessibility`
z `ENGINE=R5` i `ORIGIN_LAYER`.
Sprawdź: (1) czy algorytm kończy się bez OOM,
(2) czy wynikowe warstwy / pola mają poprawne `st_class` per origin,
(3) czy przy `ENGINE=OTP` z warstwą wielopunktową pojawia się
czytelny komunikat blokujący uruchomienie.
