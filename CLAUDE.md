# CLAUDE.md — easy-OTP (wtyczka QGIS)

## Czym jest projekt
`easy-OTP` to wtyczka QGIS automatyzująca pomiar dostępności czasowej
(service-time) komunikacji publicznej. Generuje powierzchnie travel-time przez
OpenTripPlanner dla każdej minuty okna doby, zlicza ciągłość obsługi i klasyfikuje
ją na siatce heksagonalnej. Docelowo trafia do oficjalnego repozytorium wtyczek QGIS.

## Źródło prawdy
- **Pełna specyfikacja: `docs/PR_easy-OTP.md`** — kompletny opis funkcjonalny,
  architektura, kroki pipeline'u, kryteria akceptacji. CLAUDE.md tego NIE powiela.
- **`reference/`** — skrypty referencyjne i dane testowe. **TYLKO do czytania,
  NIGDY do uruchamiania.** To wzorce logiki do portu, nie kod produkcyjny.

## Twarde ograniczenia (nie wolno złamać)
- QGIS minimum **3.40 LTR**. Tylko **PyQGIS** + biblioteki z dystrybucji QGIS.
- **ZERO `pip install`.** Nic spoza tego, co dostarcza standardowa instalacja QGIS.
- **ZERO R, ZERO GRASS.** Całość w czystym Pythonie.
- OTP dokładnie **1.5.0**. Java dokładnie **8** (uruchamiana przez pełną ścieżkę
  do binarki, nie przez wersję systemową).
- Licencja: **GPLv2 lub nowsza** (wymóg oficjalnego repozytorium wtyczek QGIS).
- Kod, komentarze, docstringi, stringi UI i komunikaty commitów: **po angielsku**.
  Stringi widoczne dla użytkownika owijać w `self.tr()` (gotowość do tłumaczeń).

## Architektura (skrót — szczegóły w PR, sekcja 4)
Wtyczka = provider Processing z trzema algorytmami: `RunTemporalAccessibility`
(główny), `TestOtpServer`, `GenerateHexGrid`. Logika w modułach `core/`
(`otp_server`, `otp_client`, `surface_runner`, `raster_processing`, `zonal`,
`time_utils`, `settings`).

## Workflow pracy (przestrzegaj zawsze)
- **Jeden kamień milowy na raz** (PR, sekcja 11). Nie wybiegaj naprzód.
- Przy zmianie dotykającej 3+ plików: **najpierw plan, potem kod.**
- **Nie zgaduj** — gdy coś jest niejasne lub niedoprecyzowane w PR, zapytaj.
  (Conventional Commits, np. `feat:`, `fix:`, `chore:`).
- Nie dodawaj frameworków, zależności ani „ulepszeń" spoza PR.
- Po każdym kamieniu milowym: test w QGIS → invoke milestone-reviewer agent
  w głównej sesji (PRZED /clear) → napraw blokery/uwagi → **commit** z opisowym komunikatem → /clear.

## Czego NIE testuje agent (testuje człowiek)
Claude Code nie ma dostępu do serwera OTP, Javy 8, danych GTFS ani uruchomionego
QGIS-a. **Nie zakładaj, że kod „działa".** Po każdym kamieniu jasno wypisz, co
użytkownik musi ręcznie zweryfikować w QGIS i jak. Kryteria akceptacji z sekcji 10
PR to lista kontrolna człowieka.

## Standardy kodu
- Python 3 (wersja z QGIS), PEP 8, type hints, zwięzłe docstringi.
- Algorytmy: klasy `QgsProcessingAlgorithm`; provider: `QgsProcessingProvider`.
- Operacje długie (build grafu, pętla powierzchni) działają **w tle**
  (mechanika Processing / `QgsTask`) z paskiem postępu i obsługą anulowania.
- Zasoby sprzątane w `finally` — proces OTP NIE może zostać osierocony,
  także przy anulowaniu i przy wyjątku.
- `metadata.txt` kompletny i zgodny z oficjalną specyfikacją wtyczek QGIS;
  na czas developmentu `experimental=True`.

## Gotchas (realne pułapki — pamiętaj)
- Raster surface z OTP: wartości w **minutach**, zahardkodowany limit **120 min**.
- `osgeo` / GDAL działa **tylko wewnątrz interpretera QGIS** — dodać guard.
- Graf OTP: **build (`--build`) i serwowanie (`--server`) to OSOBNE kroki.**
  Graf zapisywany na dysk, cache po hashu danych wejściowych. **Nie `--inMemory`.**
- Serwer dla powierzchni musi wystartować z flagami **`--analyst --pointSets`**.
- Okno 6:00–22:00 z interwałem 1 min = **961 powierzchni = 961 kanałów**.
- Statystyka strefowa: **średnia** (zgodnie z artykułem).
- Klient REST: rozważyć `urllib` zamiast `requests` (pewność dostępności w QGIS).

## .gitignore — nie wersjonować
Dane wejściowe i wyjściowe, `*.osm.pbf`, archiwa GTFS `*.zip`, katalogi grafów
i `Graph.obj`, rastry pośrednie/wynikowe, katalog roboczy, `*.jar`,
`__pycache__/`, `*.pyc`, artefakty QGIS i IDE. Wersjonujemy kod, `docs/`,
`styles/`, `metadata.txt` — nie dane.
