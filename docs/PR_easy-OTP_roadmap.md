# PR easy-OTP — Roadmapa (v0.2)

## Cel pliku i polityka wersji

Ten plik rozszerza `PR_easy-OTP.md` (v0.1, frozen) o szczegółowe
specyfikacje elementów rozwojowych planowanych na wersję v0.2. Plik
jest samodzielny — **agent kodujący nie musi czytać
`easy-OTP_ustalenia_etap1-2.md`**. Wymagane wejście do pracy: ten
plik + `PR_easy-OTP.md` (v0.1, frozen) + `CLAUDE.md` (twarde
ograniczenia środowiska).

**Konwencja wersji:**

- **v0.1** — bieżący stan code-complete. Odpowiada `PR_easy-OTP.md`
  (frozen) z §10 odhaczoną. Zawartość: 4 algorytmy Processing
  (`GenerateHexGrid`, `RunTemporalAccessibility`,
  `CountFromExistingSurfaces`, `TestOtpServer`), serwer OTP 1.5.0
  z Javą 8, pipeline spatio-temporal z artykułu Kaczorowski &
  Wróblewski.
- **v0.2** — zakres tej roadmapy. **Realnie implementowane:** R1b,
  R1a, R3, R2. Sekcje R6 i R7 są w pliku jako pełna
  specyfikacja referencyjna, ale **przeniesione do v0.3** (powody
  w blokach „Status" tych sekcji); w v0.2 ich nie tykamy. Silnik
  R5 oraz analizy multi-origin zostały wydzielone do **osobnej
  wtyczki `easy-r5`** — patrz wzmianka na końcu pliku.
- **v0.3** — następna wersja. Synteza zakresu w sekcji „Plany na
  v0.3" na końcu pliku.

## Kolejność wdrożenia w v0.2

Sekcje w pliku są ułożone w kolejności wykonawczej. Implementacja
powinna iść strikte tą kolejnością, ponieważ każdy kolejny krok
albo konsumuje produkt poprzedniego, albo korzysta z infrastruktury,
którą poprzedni krok wprowadza/refaktoruje.

| # | Element | Status w v0.2 | Wymaga | Komentarz |
|---|---|---|---|---|
| 1 | **R1b** — `PopulationOverlay` | done | siatka heksagonów z v0.1 | Port modelu QGIS + fix bugu int→float w kroku 6. |
| 2 | **R1a** — `PrepareStudentLayer` | done | — | From-scratch, 3 fixtures testowe w katalogu projektu. |
| 3 | **R3** — `DownloadJre` | done | — | Auto pobieranie Temurin 8 (x64). Najprostszy element. |
| 4 | **R2** — `DownloadTransitData` | done | — | OSM (Geofabrik) + GTFS (Transitland). Dwa niezależne checkboxy. |
| — | **R6** — `RunCarDependency` | **v0.3** | OSM POI, wydajność | Pełna spec w pliku jako materiał startowy. Wymaga rozwiązania problemu skalowalności — domyślnie przez wtyczkę `easy-r5`. |
| — | **R7** — Siatka H3 | **v0.3** | spike decyzyjny | Wspólnie z R6. |

**Krytyczna ścieżka:** elementy R1b, R1a, R3, R2 są względnie niezależne
między sobą — sensowna kolejność wykonawcza wynika z gęstości
materiału przygotowawczego (R1b/R1a mają najwięcej kontekstu z §5
ustaleń) i z naturalnego przepływu od pre-procesingu danych
(R1a → R1b) przez infrastrukturę (R3, R2). Po zakończeniu v0.2 cała
funkcjonalność wtyczki `easy-OTP` jest zamknięta na bazie silnika
OTP 1.5.0.

## Mapa zależności

```
v0.1 (frozen) ──── GenerateHexGrid ─────────┐
                                            │
              ──── RunTemporalAccessibility ─┼───── konsumuje:
                                            │      • java_path (z R3)
                                            │      • .osm.pbf + GTFS (z R2)
                                            │
              ──── TestOtpServer ───────────┤
                                            │
              ──── CountFromExistingSurfaces ┘


v0.2 (ten plik):

R1b (PopulationOverlay) ──── konsumuje siatkę z v0.1
                       └──── konsumuje warstwę populacji z R1a
                       └──── testowalne ręcznie z fixtures (bez R1a)

R1a (PrepareStudentLayer) ──── samodzielne (fixtures w projekcie)
                          └─── produkuje warstwę dla R1b

R3 (DownloadJre) ──── samodzielne
                 └─── zapisuje QSettings.java_path
                              ↑ używane przez OTP server (v0.1)

R2 (DownloadTransitData) ──── samodzielne
                         └─── produkuje .osm.pbf + folder GTFS
                                       ↑ wklejane w RunTemporalAccessibility
```

Silnik R5 oraz multi-origin (pierwotnie planowane jako R5 i R4)
przeniesione do osobnej wtyczki `easy-r5` — patrz wzmianka na końcu
pliku.

## Szablon sekcji

Każda sekcja R-X ma tę samą strukturę 9 podsekcji (analogicznie do
§9 ustaleń etapu 1–2):

1. **Cel i wartość** — jednoakapitowy opis, co algorytm robi i
   dlaczego jest w roadmapie.
2. **Kontekst i zależności** — co z v0.1 jest reusowane, jakie są
   zależności od innych R-X, jakie są ograniczenia środowiska
   (CLAUDE.md).
3. **Parametry algorytmu QGIS** — tabela z parametrami Processing,
   typami, defaultami.
4. **Algorytm krok po kroku** — numerowane kroki implementacji.
   Cytujemy `processing.run` wywołania, klasy PyQGIS,
   pełne formuły matematyczne.
5. **Pliki referencyjne / wzorzec do portu** — co konkretnie portujemy
   z istniejących skryptów (jeśli dotyczy) lub z jakiej dokumentacji
   bierzemy formuły.
6. **Edge cases i walidacja** — co może pójść nie tak, jak reagujemy.
7. **Tryb błędu i komunikaty** — tabela ze stringami błędów (po
   angielsku, w `self.tr()`).
8. **Kryteria akceptacji** — testowalne warunki sukcesu.
9. **Otwarte pytania / spike'y wymagane przed implementacją** —
   decyzje do podjęcia przed kodowaniem, szacowany koszt spike'ów.

Sekcje R6 i R7 dodatkowo mają na początku blok **Status** —
informujący o przeniesieniu do v0.3.

## Konwencje

- **Język tekstu:** polski. **Język kodu, identyfikatorów, komunikatów
  błędów:** angielski (CLAUDE.md, „Standardy kodu"). Komunikaty błędów
  w `self.tr()`.
- **Cross-referencje do v0.1:** w postaci `§X PR_easy-OTP.md` (np.
  `§6.2 PR_easy-OTP.md` = sekcja 6.2 frozen PR-a v0.1).
- **Cross-referencje wewnątrz tego pliku:** po identyfikatorze R-X
  (np. „konsumuje warstwę z R1a"). Sekcje numerowane są w kolejności
  wdrożenia, nie alfabetycznie.
- **Brak `pip install` z poziomu wtyczki** (CLAUDE.md, twardy zakaz)
  — bez wyjątków w zakresie easy-OTP.
- **Wszystkie warstwy w CRS metrycznym** dla obliczeń areal
  interpolation i polowych. EPSG:2180 (PUWG 1992) jako rekomendowane
  dla Polski.

---

## R1b — `PopulationOverlay` (port modelu populacji z fixem typu)

### Cel i wartość

Algorytm nakłada warstwę demograficzną (poligony z polem populacji) na siatkę
heksagonalną metodą areal interpolation wagowanej polem powierzchni — i zwraca
heksagony z liczbą osób z danej grupy populacyjnej (`num_students`).
Domyka demograficzną połowę metody Kaczorowski & Wróblewski (krok 11 z §3
PR_easy-OTP.md), który dziś jest wykonywany ręcznie przez model QGIS
`ludnosc_studentow.model3`. Bez tego algorytmu wtyczka realizuje wyłącznie
część spatio-temporal i nie produkuje końcowych liczb studentów per heksagon
ani Tabeli 2 z artykułu.

### Kontekst i zależności

- Algorytm jest **konsumentem siatki heksagonów** wygenerowanej przez
  `RunTemporalAccessibility` lub bezpośrednio `GenerateHexGrid` (§4
  PR_easy-OTP.md). Nie wymaga zmian w v0.1.
- Algorytm jest **konsumentem warstwy populacji** w postaci poligonów z
  numerycznym polem populacji (np. `pop20_29`). W v0.2 warstwę tę
  produkuje R1a (`PrepareStudentLayer`); na czas implementacji i testów R1b
  warstwę można przygotować ręcznie (kilka poligonów + ręcznie wpisana
  liczba osób) — nie ma zależności kompilacyjnej R1b od R1a.
- **Implementacja: port** modelu referencyjnego
  `reference/ludnosc_studentow_model_qgis.py`. Sekwencja 7 wywołań
  `processing.run(...)` z `is_child_algorithm=True`, identyczna co do
  algorytmów wywoływanych, z **jedną zmianą** opisaną w „Pliki referencyjne".
- Brak spike'ów. Brak otwartych decyzji.

### Parametry algorytmu QGIS

| Parametr | Typ | Wymagany | Default | Opis |
|---|---|---|---|---|
| `HEX_GRID` | `QgsProcessingParameterVectorLayer` (Polygon) | tak | — | Siatka heksagonalna. Wynik `RunTemporalAccessibility` lub `GenerateHexGrid`. |
| `POPULATION_LAYER` | `QgsProcessingParameterVectorLayer` (Polygon) | tak | — | Warstwa demograficzna z polem populacji. W v0.2 typowo wynik R1a. |
| `POPULATION_FIELD` | `QgsProcessingParameterField` (Numeric, na `POPULATION_LAYER`) | tak | `pop20_29` | Pole zawierające liczbę osób per poligon (Float lub Int). |
| `OUTPUT` | `QgsProcessingParameterFeatureSink` | tak | — | Heksagony z dodanym polem `num_students` (Float). |

`shortHelpString()` opisuje: zastosowanie metody (areal interpolation wagowana
polem powierzchni), wymóg poligonów po obu stronach, oczekiwany kierunek pól
(populacja agregowana po obwodach spisowych → heksagony).

### Algorytm krok po kroku

Port logiki z `ludnosc_studentow_model_qgis.py`. Każdy krok to wywołanie
`processing.run(..., is_child_algorithm=True)` w pętli
`QgsProcessingMultiStepFeedback(7, model_feedback)`. Po każdym kroku check
`feedback.isCanceled()` z czystym wyjściem.

1. **Pole `area` na warstwie populacji** — `native:fieldcalculator` z
   `FORMULA='$area'`, `FIELD_TYPE=0` (Double), `FIELD_NAME='area'`.
2. **Pole `density` (gęstość zaludnienia)** — `native:fieldcalculator` z
   `FORMULA='"<POPULATION_FIELD>"/"area"'`, `FIELD_TYPE=0` (Double),
   `FIELD_NAME='_eo_density'` (prefix `_eo_` chroni przed kolizją z polami
   użytkownika).
3. **Podział poligonów wg linii siatki** — `native:splitwithlines` z
   `INPUT=<density>`, `LINES=HEX_GRID`. Każdy obwód zostaje pocięty
   krawędziami heksagonów, dając zestaw kawałków pokrywających przecięcia
   obwodów z heksagonami.
4. **Pole `_eo_part_area` na kawałkach** — `native:fieldcalculator` z
   `FORMULA='$area'`, `FIELD_TYPE=0` (Double).
5. **Punkt reprezentatywny per kawałek** — `native:pointonsurface` z
   `ALL_PARTS=False`. Wynikiem są punkty wewnątrz kawałków, niosące atrybuty
   kawałków (`_eo_density`, `_eo_part_area`).
6. **Pole `_eo_part_pop` na punktach** — `native:fieldcalculator` z
   `FORMULA='"_eo_part_area"*"_eo_density"'`. **Wymóg: `FIELD_TYPE=0`
   (Double), `FIELD_PRECISION=2`.** To jest punkt różny od modelu
   referencyjnego (patrz „Pliki referencyjne / wzorzec do portu").
7. **Zliczenie punktów w heksagonach z wagowaniem** —
   `native:countpointsinpolygon` z `POINTS=<step6>`,
   `POLYGONS=HEX_GRID`, `WEIGHT='_eo_part_pop'`,
   `FIELD='num_students'`. `OUTPUT=parameters['OUTPUT']` (sink wtyczki).

Atrybuty pomocnicze prefiksowane `_eo_` istnieją wyłącznie w warstwach
pośrednich (TEMPORARY_OUTPUT) i są usuwane przy zamknięciu kontekstu
przetwarzania. W wynikowej warstwie pojawia się wyłącznie pole `num_students`
dodane do oryginalnych atrybutów `HEX_GRID`.

### Pliki referencyjne / wzorzec do portu

`reference/ludnosc_studentow_model_qgis.py` — model QGIS wyeksportowany do
Pythona, 7 kroków `processing.run`. **Wzorzec do portu 1:1** dla sekwencji
wywołań algorytmów `native:*` z parametrami. Jedyna zmiana:

> **Krok 6 (bug fix):** w modelu referencyjnym pole `liczba_studentow` jest
> tworzone z `FIELD_TYPE=1` (Integer) i `FIELD_PRECISION=0`. Skutek:
> liczba studentów per kawałek poligonu jest zaokrąglana do liczby
> całkowitej **na etapie pośrednim**, przed zliczeniem do heksagonów.
> Kawałki o wartości < 0.5 (np. fragment obwodu na granicy heksagonu z
> niewielką powierzchnią) trafiają do 0 i są tracone. Suma `num_students`
> po heksagonach jest systematycznie niższa od sumy `pop20_29` z warstwy
> wejściowej, im drobniejsza siatka — tym większy ubytek.
>
> **Naprawa:** w porcie używaj `FIELD_TYPE=0` (Double) i
> `FIELD_PRECISION=2` w kroku 6. Zaokrąglanie (jeśli w ogóle) wykonuje
> użytkownik dopiero w stylizacji warstwy wynikowej; w atrybutach
> `num_students` pozostaje wartość Float.

### Edge cases i walidacja

- **Niezgodność CRS** `HEX_GRID` vs `POPULATION_LAYER` — algorytm reprojektuje
  warstwę populacji do CRS heksagonów (`native:reprojectlayer`) przed krokiem
  1; w logu zapisuje obie wartości CRS i kierunek reprojekcji. Powodem jest
  to, że pole `$area` wymaga układu metrycznego — algorytm wymaga, aby CRS
  heksagonów był metryczny (np. EPSG:2180, EPSG:3857), inaczej `area` da
  wartości w jednostkach kątowych i wszystkie liczby będą bezsensowne.
- **Heksagony niepokryte warstwą populacji** — finalny `countpointsinpolygon`
  ustawi `num_students=0` (nie NULL); to jest poprawne zachowanie.
- **Warstwa populacji nie pokrywa pełnego obszaru `HEX_GRID`** — log z
  ostrzeżeniem: liczba heksagonów z `num_students == 0` jako wartość
  informacyjna. Nie jest to błąd.
- **Pole `POPULATION_FIELD` ma wartości NULL w niektórych poligonach** —
  `native:fieldcalculator` w kroku 2 zwróci NULL dla `_eo_density`, kolejne
  kroki propagują NULL i finalnie ten poligon nie kontrybuuje. To zachowanie
  zgodne z modelem referencyjnym; algorytm nie zamienia NULL na 0
  automatycznie (decyzja: brak danych ≠ zero osób). Konwersja `-` → 0 leży
  po stronie R1a.
- **`HEX_GRID` ma już pole `num_students`** — algorytm zamiast nadpisywać
  podnosi błąd: „Output field `num_students` already exists in `HEX_GRID`.
  Remove it or rename it before running PopulationOverlay." (uniknięcie cichej
  utraty danych z poprzedniego runu).
- **CRS heksagonów geograficzny (np. EPSG:4326)** — algorytm odrzuca z
  komunikatem: „Hex grid must be in a projected CRS with metric units
  (e.g. EPSG:2180, EPSG:3857). Got: EPSG:4326."

### Tryb błędu i komunikaty

| Sytuacja | Komunikat |
|---|---|
| Brak pola `POPULATION_FIELD` w warstwie | `"Population layer has no field '{field}'."` |
| Pole `POPULATION_FIELD` nie jest numeryczne | `"Field '{field}' must be numeric (Int or Float), got '{type}'."` |
| Warstwa populacji bez geometrii poligonowej | `"Population layer must be polygonal, got '{geom_type}'."` |
| CRS heksagonów geograficzny | jak wyżej w „Edge cases" |
| Pole `num_students` już istnieje w `HEX_GRID` | jak wyżej w „Edge cases" |

Wszystkie stringi w `self.tr()`, po angielsku (CLAUDE.md, „Standardy kodu").

### Kryteria akceptacji

- Algorytm zarejestrowany w `easy_otp/provider.py` oraz w
  `easy_otp/algorithms/__init__.py`.
- Wywołanie z ręcznie przygotowaną warstwą populacji (3–5 poligonów z
  `pop20_29`) i siatką heksagonów (np. 500 m, wynik `GenerateHexGrid`) daje
  warstwę heksagonów z polem `num_students` (Float).
- **Test poprawności areal interpolation:** suma `num_students` po wszystkich
  heksagonach pokrywających pełny obszar warstwy populacji ≈ suma
  `pop20_29` w warstwie populacji, z dokładnością ±0.1% (mała dyskrepancja
  od krawędziowych przybliżeń `pointonsurface`).
- **Test braku ubytku zaokrąglenia:** dla siatki heksagonów dużo drobniejszej
  od obwodów (np. heksagony 100 m, obwody NSP ~kilkaset m do km),
  `num_students` na większości heksagonów jest wartością ułamkową (np.
  `3.47`), nie zaokrągloną do całkowitej. Dla siatki referencyjnej z modelu
  `ludnosc_studentow.model3` suma `num_students` z R1b jest **wyższa lub
  równa** sumie z modelu referencyjnego (różnica = odzyskane fragmenty
  < 0.5 osoby).
- Pasek postępu działa, anulowanie czyste, brak osieroconych warstw
  pośrednich po anulowaniu.
- README.md zaktualizowany: nowa sekcja „Population overlay (R1b)" z
  przykładowym uruchomieniem.

### Otwarte pytania / spike'y wymagane przed implementacją

Brak. Algorytm jest portem skończonej referencji z dobrze zdefiniowaną
korektą. Implementacja może wystartować natychmiast.

---

## R1a — `PrepareStudentLayer` (Excel GUS → warstwa poligonów)

### Cel i wartość

Algorytm produkuje warstwę poligonów z numerycznym polem populacji
(`pop20_29`) z dwóch wejść: pliku Excel GUS (NSP 2021, warstwa
demograficzna) i warstwy geometrii obwodów spisowych. Wyjście jest
bezpośrednim wejściem dla R1b. Bez tego algorytmu użytkownik musi
ręcznie przepiąć Excel na warstwę za każdym razem, gdy chce policzyć
populację studentów — co dla każdego polskiego miasta oznacza pracę
ręczną: scalanie nagłówków, zbudowanie klucza złączenia z dwóch kolumn,
poprawa typów (znak `-` w GUS oznaczający stłumione zera degraduje całą
kolumnę do tekstu). Algorytm tę pracę automatyzuje, akceptując plik w
dowolnym z trzech zaobserwowanych stanów (`raw` / `wrong` / `done`).

### Kontekst i zależności

- Algorytm **specyficzny dla GUS NSP 2021** — działa na danych
  publikowanych przez Główny Urząd Statystyczny dla obwodów spisowych.
  Działanie poza Polską / poza NSP 2021 nie jest celem v0.2.
- **Implementacja from-scratch** — nie istnieje skrypt referencyjny do
  portu. Specyfikacja w tej sekcji jest pełną definicją zachowania.
  Pliki `referencensp2021raw.xlsx`, `reference_ludnosc_nsp_2021_wrong.xlsx`,
  `reference_dolnoslaskie_ludnosc_nsp_2021done.xlsx` w katalogu projektu
  służą jako **fixtures testowe** reprezentujące trzy stany wejścia
  (najtrudniejszy, częściowo przetworzony, idealny).
- Wyjście R1a jest kompatybilne z parametrem `POPULATION_LAYER` w R1b.
- Brak zależności od v0.1 PR.

### Parametry algorytmu QGIS

| Parametr | Typ | Wymagany | Default | Opis |
|---|---|---|---|---|
| `EXCEL_FILE` | `QgsProcessingParameterFile` (Filter: `*.xlsx`) | tak | — | Plik xlsx GUS NSP 2021. |
| `EXCEL_SHEET` | `QgsProcessingParameterString` | nie | `""` (= pierwszy arkusz) | Nazwa arkusza w pliku wieloarkuszowym (np. „dolnośląskie"). Pusty = pierwszy arkusz. |
| `POPULATION_COLUMN` | `QgsProcessingParameterString` | nie | `pop20-29` | Nazwa kolumny w nagłówku Excela do wyciągnięcia. |
| `GEOMETRY_LAYER` | `QgsProcessingParameterVectorLayer` (Polygon) | tak | — | Warstwa obwodów spisowych NSP 2021. Musi mieć pole zdefiniowane w `KEY_FIELD`. |
| `KEY_FIELD` | `QgsProcessingParameterField` (na `GEOMETRY_LAYER`) | tak | `OBWOD` | Pole geometrii do złączenia (string, 7-znakowe). |
| `OUTPUT_FIELD_NAME` | `QgsProcessingParameterString` | nie | `pop20_29` | Nazwa pola w warstwie wyjściowej (sanityzowana — bez myślnika, kompatybilna z R1b). |
| `OUTPUT` | `QgsProcessingParameterFeatureSink` | tak | — | Warstwa poligonów geometrii z dodanym polem populacji (Float). |

`shortHelpString()` zawiera link do strony GUS z eksportem danych NSP 2021
na poziomie obwodu spisowego oraz przykład formatu pliku (link do
przykładowego `done` fixtures z testów, jeśli akceptowalne licencyjnie).

### Algorytm krok po kroku

Implementacja czysto pythonowa (bez `processing.run`). Wszystkie operacje
na danych Excela w stdlib + `openpyxl` (dostępne w QGIS 3.40 — patrz
„Otwarte pytania"). Wszystkie operacje na warstwie geometrii przez
PyQGIS API (`QgsVectorLayer`, `QgsFeature`, `QgsFields`, `QgsFeatureSink`).

1. **Wczytanie arkusza Excela.** Otwórz `EXCEL_FILE` przez `openpyxl`,
   wybierz arkusz: `EXCEL_SHEET` jeśli niepusty, inaczej pierwszy.
   Przeczytaj wszystkie wiersze do listy list (typy: int/float/str/None
   z openpyxl, bez wymuszania).

2. **Normalizacja nagłówka.** Wyszukaj wiersz nagłówkowy — pierwszy wiersz
   w pierwszych 10 wierszach, który zawiera **wszystkie** z: `Symbol`,
   `Struktura`, `POPULATION_COLUMN`. Jeśli nie znaleziono — błąd. Indeks
   tego wiersza = `header_row`. Wszystkie wiersze < `header_row`
   odrzucone. Wiersze >= `header_row + 1` to dane. Mapowanie nazw kolumn
   do indeksów: `col_symbol`, `col_struktura`, `col_population`.

3. **Single-pass przez wiersze danych** (od `header_row + 1`) ze stanowym
   śledzeniem `current_rejon`. Dla każdego wiersza:

   - Jeśli `Struktura == 'rejon statystyczny'`: zapisz wartość
     `Symbol` jako `current_rejon = str(row[col_symbol])`. Wiersz
     odrzucony z dalszego przetwarzania (to suma na poziomie rejonu,
     nie obwód).
   - Jeśli `Struktura == 'obwód spisowy'`:
     - **Budowa klucza.** Wartość `row[col_symbol]` zrzutuj na string
       (`sym = str(row[col_symbol])`). Jeśli `len(sym) >= 7` (case
       `done`/`wrong` — klucz jest już zbudowany), `klucz = sym`.
       Jeśli `len(sym) < 7` (case `raw` — `Symbol` to tylko numer
       obwodu), `klucz = current_rejon + sym` (plain concat, bez
       zero-paddingu — geometria stosuje tę samą regułę).
     - Jeśli `current_rejon is None` w momencie napotkania obwodu
       (case `raw` uszkodzony) — błąd z numerem wiersza.
     - **Koercja wartości populacji.** Wartość `row[col_population]`:
       - `'-'` (string „minus") → `0`
       - `None` lub `''` → `0`
       - string numeryczny (np. `'12'`) → `int` lub `float` przez
         `float()`
       - liczba (już int/float) → bez zmiany
       Inne wartości (np. tekst niebędący liczbą ani `-`) → błąd z
       numerem wiersza, opisem oczekiwanego formatu.
       Licznik konwersji `-` → 0 zachowaj do logu.
     - Dodaj `(klucz, pop)` do dykcjonarza `excel_data: dict[str, float]`.
       Jeśli klucz już istnieje — przerwa, dopisz do listy duplikatów.
   - Inne wartości `Struktura` (`'województwo'`, `'powiat'`, `'gmina'`)
     — odrzuć, bez wpływu na `current_rejon`.

4. **Walidacja duplikatów Excela.** Jeśli lista duplikatów niepusta —
   błąd z pierwszymi 10 kluczami i komunikatem o możliwym fallbacku
   na klucz złożony (patrz „Otwarte pytania").

5. **Iteracja po featuresach geometrii.** Dla każdego feature
   `GEOMETRY_LAYER`:
   - Pobierz wartość `KEY_FIELD` jako string (forsuj cast — jeśli
     atrybut numeryczny, użyj `str(int(value))`, **uwaga na zera
     wiodące**: jeśli wartość zapisana jako int gubi zera wiodące, log
     warning; rekomendacja w komunikacie: w QGIS przechowywać `OBWOD`
     jako string).
   - Lookup w `excel_data`. Jeśli znaleziono — przepisz feature do
     `OUTPUT` z dodanym polem `OUTPUT_FIELD_NAME` (Float) = wartość.
     Jeśli nie znaleziono — przepisz z `OUTPUT_FIELD_NAME = NULL`,
     dolicz do licznika niedopasowanych.
   - Schema `OUTPUT` = atrybuty `GEOMETRY_LAYER` + 1 pole
     `OUTPUT_FIELD_NAME` (`QVariant.Double`).

6. **Raport końcowy** (do logu Processing, jako informacja, nie błąd):
   - Liczba wierszy obwodu w Excelu.
   - Liczba featuresów geometrii.
   - Liczba dopasowanych (oba zbiory).
   - Liczba kluczy Excela niewystępujących w geometrii.
   - Liczba featuresów geometrii bez dopasowania (mają `pop20_29 = NULL`).
   - Liczba wartości `-` zamienionych na 0.
   - Statystyki: min/max/sum `pop20_29` w wyniku.

### Pliki referencyjne / wzorzec do portu

Wszystkie pliki w katalogu projektu, **read-only** (CLAUDE.md):

- **`wroclaw-su-brec-nsp-2021-obw.geojson`** — warstwa geometrii. Pola
  istotne: `WW` (2-znak.), `PP` (2-znak.), `GG` (2-znak.), `R` (1-znak.),
  `OBWOD` (7-znak. = `REJ`+`OBW`), `REJ` (6-znak.), `OBW` (1-znak.).
  Wszystkie jako string. `OBWOD` to gotowy klucz złączenia. Fallback
  złożony: `WW`+`PP`+`GG`+`OBWOD` — patrz „Otwarte pytania".
- **`referencensp2021raw.xlsx`** — case `raw`. Test najtrudniejszej
  ścieżki kodowej: wielowierszowy nagłówek (wiersze 0–5), `Symbol` na
  poziomie obwodu zawiera tylko numer obwodu (1, 2, 3…), rejon w
  poprzedzającym wierszu `Struktura == 'rejon statystyczny'`. Wymaga
  pełnego forward-fill z kroku 3.
- **`reference_ludnosc_nsp_2021_wrong.xlsx`** — case `wrong`. Klucz
  zbudowany (`Symbol == '2308501'`), nagłówek czysty, ale `pop20-29` to
  stringi (z `-` jako wartością stłumioną). Test koercji typów.
  Plik wieloarkuszowy (arkusz per województwo).
- **`reference_dolnoslaskie_ludnosc_nsp_2021done.xlsx`** — case `done`.
  Idealny wzorzec docelowy. Test ścieżki minimum: bez forward-fill,
  bez koercji `-` → 0. Jedyny rzeczywisty zgrzyt: `Symbol` zapisany
  jako liczba całkowita w xlsx, geometria trzyma `OBWOD` jako string —
  rzutowanie klucza na tekst z kroku 5.

**Spodziewane zachowanie testów:**
- Wszystkie trzy pliki Excel z `wroclaw-su-brec-nsp-2021-obw.geojson`
  → output funkcjonalnie identyczny w polu `pop20_29` (tolerancja:
  identyczne wartości dla wszystkich obwodów z Wrocławia obecnych
  w danym pliku).
- `raw` i `wrong` mają zasięg ogólnopolski (lub wojewódzki); R1a
  pomija wiersze niedopasowane do geometrii Wrocławia, więc joint
  produkuje tylko obwody Wrocławia z populacją.
- `done` ma zasięg Wrocławia bezpośrednio.

### Edge cases i walidacja

- **Excel wieloarkuszowy bez wskazania `EXCEL_SHEET`** — domyślnie
  pierwszy arkusz, ale log z listą wszystkich arkuszy w pliku
  (informacja, że można wybrać inny). Realne: `wrong` fixture ma arkusze
  per województwo; wymaga `EXCEL_SHEET = 'dolnośląskie'` (lub
  odpowiednika dla innego miasta).
- **`OBWOD` w geometrii numeryczny (Int)** — możliwe po reimporcie
  geojson do shp z domyślną interpretacją. Algorytm forsuje cast
  `str(int(value))`, ale traci zera wiodące. Log warning z liczbą
  potencjalnie poszkodowanych obwodów (wszystkie zaczynające się na 0).
  Rekomendacja w komunikacie: przekonwertować pole klucza na string
  przed użyciem.
- **Wartość `-` w GUS** w innych kolumnach niż `POPULATION_COLUMN` —
  bez znaczenia, R1a czyta wyłącznie wskazaną kolumnę.
- **Brak `Struktura == 'rejon statystyczny'` w pliku** (case `done`,
  gdzie hierarchia wyższych poziomów może być wycięta) — `current_rejon`
  pozostaje None, ale wszystkie obwody mają `Symbol >= 7` znaków (klucz
  zbudowany), więc forward-fill nie jest potrzebny. Brak błędu.
- **Klucz nieunikalny po stronie Excela** — błąd, lista duplikatów,
  rekomendacja fallbacku na klucz złożony (patrz niżej).
- **Niedopasowanie pełne** (`matched_count == 0`) — błąd, nie tylko
  ostrzeżenie. Sugeruje błędne dane wejściowe (zły plik Excela dla
  danej geometrii, np. inne województwo).
- **`POPULATION_COLUMN` nie istnieje w nagłówku** — błąd z listą
  wszystkich znalezionych kolumn w nagłówku.

### Tryb błędu i komunikaty

| Sytuacja | Komunikat |
|---|---|
| Nie wykryto nagłówka w pierwszych 10 wierszach | `"Could not detect header row. Searched rows 0–9 for columns 'Symbol', 'Struktura' and '{POPULATION_COLUMN}'. Check that the sheet '{sheet}' is the correct one."` |
| `POPULATION_COLUMN` nie znaleziona | `"Column '{POPULATION_COLUMN}' not found in header. Available columns: {list}."` |
| Pierwszy obwód napotkany bez poprzedzającego rejonu (case `raw` uszkodzony) | `"Row {N}: census tract '{symbol}' encountered without a preceding 'rejon statystyczny' row. Cannot build join key."` |
| Wartość populacji w nieoczekiwanym formacie | `"Row {N}: cannot interpret '{value}' as a number in column '{POPULATION_COLUMN}'. Expected a number, an empty cell, or '-'."` |
| Duplikaty klucza w Excelu | `"Duplicate keys in Excel: {first_10}. Cannot join uniquely. Consider using a composite key (WW+PP+GG+OBWOD) — open ticket if needed."` |
| `KEY_FIELD` nie istnieje w warstwie geometrii | `"Geometry layer has no field '{KEY_FIELD}'. Available fields: {list}."` |
| Zero dopasowań | `"None of the {N} Excel rows match the geometry layer. Check that you provided the correct file for this region."` |
| Brak arkusza `EXCEL_SHEET` w pliku | `"Sheet '{EXCEL_SHEET}' not found in '{file}'. Available sheets: {list}."` |

### Kryteria akceptacji

- Algorytm zarejestrowany w `easy_otp/provider.py` i
  `easy_otp/algorithms/__init__.py`.
- **Test fixtures (test integracyjny ręczny, instrukcja w PR Claude'a do
  użytkownika):**
  - `referencensp2021raw.xlsx` + `wroclaw-su-brec-nsp-2021-obw.geojson`
    → warstwa wynikowa ma `pop20_29` (Float) wypełnione dla obwodów
    Wrocławia, pasują wartości oczekiwane z `done`.
  - `reference_ludnosc_nsp_2021_wrong.xlsx` (arkusz `dolnośląskie`) +
    geojson → identyczny output, w logu wzmianka o N wartościach `-`
    zamienionych na 0.
  - `reference_dolnoslaskie_ludnosc_nsp_2021done.xlsx` + geojson →
    identyczny output, ścieżka „minimum" (bez forward-fill).
- Wszystkie trzy outputy mają taką samą liczbę featuresów = liczba
  featuresów w geojson.
- `pop20_29` jest typu Double w schema warstwy wyjściowej (kompatybilne
  z `POPULATION_FIELD` w R1b).
- Output R1a podany jako `POPULATION_LAYER` do R1b produkuje
  spodziewane liczby studentów w heksagonach (test end-to-end).
- README.md zaktualizowany: sekcja „Preparing the population layer (R1a)"
  z pełnym przykładem od pliku GUS do uruchomienia R1b.

### Otwarte pytania / spike'y wymagane przed implementacją

1. **Dostępność `openpyxl` w QGIS 3.40 LTR out-of-box.** Domyślne
   założenie: dostępne na wszystkich platformach (Windows / Linux / macOS)
   bez `pip install` — `openpyxl` jest standardową zależnością w
   dystrybucji QGIS LTR. **Spike (15 min):** uruchomić w konsoli QGIS
   `from openpyxl import load_workbook` na czystej instalacji QGIS 3.40
   na Windows. Jeśli niedostępne — fallback na czysty stdlib
   (`zipfile` + `xml.etree.ElementTree` — xlsx to spakowany XML), lecz
   to znacząco rozdmuchuje implementację. Decyzja przed pisaniem kroku 1.
2. **Unikalność `OBWOD` w obrębie województwa.** Metoda Kaczorowski &
   Wróblewski jest geograficznie uniwersalna (działa wszędzie, gdzie da
   się dostarczyć OSM + GTFS), ale **R1a jest specyficzne dla GUS NSP
   2021** — preprocessing tego konkretnego polskiego formatu danych.
   Dla v0.2 maksymalny praktyczny zakres pojedynczej analizy =
   obszar **województwa** (powyżej tego rozmiaru OTP 1.5.0 nie wykonuje
   pipeline'u w sensownym czasie i RAMie). Założenie operacyjne:
   7-znakowy `OBWOD` jest unikalny w obrębie województwa. Duplikaty
   w obrębie pojedynczego pliku Excela GUS (gdzie plik zwykle = jedno
   województwo lub jego fragment) są bardzo mało prawdopodobne. Jeśli
   wystąpią — błąd z kroku 4 zatrzymuje algorytm z pełną listą duplikatów
   i rekomendacją zweryfikowania pliku wejściowego. Klucz złożony
   `WW`+`PP`+`GG`+`OBWOD` jako fallback **nie jest implementowany w
   v0.2**; rozważyć dopiero przy pierwszym realnym wystąpieniu duplikatu.

---

## R3 — `DownloadJre` (automatyczne pobieranie przenośnego JRE 8)

### Cel i wartość

Algorytm pomocniczy pobiera, weryfikuje i rozpakowuje przenośny build
Eclipse Temurin 8 (JRE) z publicznego Adoptium API dla aktualnej platformy,
po czym zapisuje ścieżkę binarki `java` w QSettings wtyczki — eliminując
najtrudniejszy ręczny krok z `README.md` (sekcja „Getting Java 8").
W v0.1 użytkownik musi sam znaleźć stronę Adoptium, wybrać odpowiednią
wersję / architekturę / format pakietu, pobrać, rozpakować i wskazać
binarkę w parametrze algorytmu — to największa pojedyncza bariera wejścia.
R3 redukuje to do jednego uruchomienia algorytmu.

### Kontekst i zależności

- Zadanie **izolowane** — zero zmian w pipeline, zero zależności od
  innych elementów roadmapy.
- **Reuse z v0.1:** funkcja `check_java_version()` z `core/otp_server.py`
  (§6.1 PR_easy-OTP.md) — używana do walidacji wersji rozpakowanej Javy.
- **Reuse z v0.1:** odczyt/zapis `QSettings` z `core/settings.py`
  (§4 PR_easy-OTP.md) — klucz `easy_otp/java_path` istnieje już w v0.1
  jako miejsce trzymania ścieżki do binarki.
- **Brak zależności od `pip install`** — wszystko stdlib: `urllib`,
  `hashlib`, `zipfile`, `tarfile`, `json`.

### Parametry algorytmu QGIS

| Parametr | Typ | Wymagany | Default | Opis |
|---|---|---|---|---|
| `JRE_DEST_DIR` | `QgsProcessingParameterFile` (Folder) | tak | — | Folder docelowy, w którym zostanie rozpakowane JRE. Musi istnieć i mieć uprawnienia do zapisu. |
| `PLATFORM` | `QgsProcessingParameterEnum` (Windows / Linux / macOS) | nie | wykryta automatycznie z `sys.platform` | Pozwala wymusić platformę (rzadkie — np. setup na maszynie wirtualnej). |
| `SET_AS_DEFAULT` | `QgsProcessingParameterBoolean` | nie | `True` | Czy po sukcesie zapisać ścieżkę w QSettings (`easy_otp/java_path`). |

W v0.2 algorytm obsługuje wyłącznie architekturę **x64** (Windows/Linux/macOS-Intel).
Wsparcie `aarch64` (Apple Silicon, ARM Linux) przewidziane na v0.3 — patrz
„Plany na v0.3" na końcu pliku.

`shortHelpString()` zawiera link do Adoptium (`https://adoptium.net`) jako
źródła i informację, że pobranie zajmuje 40–80 MB.

### Algorytm krok po kroku

Implementacja w `easy_otp/algorithms/download_jre.py`. Praca w tle przez
mechanikę `QgsProcessingAlgorithm.processAlgorithm()` z paskiem postępu
sterowanym ręcznie (chunk-based progress przy pobieraniu).

1. **Wykrycie platformy.** `sys.platform` →
   `windows`/`linux`/`mac` (mapowanie tabelaryczne). Jeśli `PLATFORM`
   podane ręcznie — nadpisz wykrycie. Architektura zawsze `x64` (zob.
   uwaga pod tabelą parametrów).

2. **Query Adoptium API v3.** URL:
   `https://api.adoptium.net/v3/assets/latest/8/hotspot?architecture=x64&image_type=jre&os={os}&vendor=eclipse`
   gdzie `{os}` ∈ {`windows`, `linux`, `mac`}.
   Wywołanie przez `urllib.request.urlopen` z `User-Agent: easy-OTP/0.2`
   (Adoptium API zwraca 403 na puste UA). Odpowiedź = JSON, lista z 1 assetem
   (najnowszy minor release). Z pierwszego elementu wyciągnij:
   - `binary.package.link` — URL archiwum
   - `binary.package.checksum` — SHA256 (hex)
   - `binary.package.name` — nazwa pliku (do zapisu lokalnie)
   - `release_name` — nazwa wersji (np. `jdk8u422-b05`) do logu

3. **Pobranie archiwum.** Zapisz pod `JRE_DEST_DIR/{package.name}.tmp`
   (sufix `.tmp` żeby przy anulowaniu zostawić ślad i posprzątać).
   `urllib.request.urlopen` z odczytem w blokach po 64 KB; po każdym
   bloku:
   - `feedback.setProgress(percent)` — `percent = downloaded/total * 100`
   - `feedback.isCanceled()` → jeśli True: zamknij stream, usuń plik
     `.tmp`, zwróć `{}`.
   Po sukcesie zmień nazwę z `.tmp` na finalną.

4. **Weryfikacja SHA256.** `hashlib.sha256()` na pobranym pliku w
   blokach po 1 MB. Porównaj z `checksum` z kroku 2. Mismatch → usuń
   plik, błąd.

5. **Rozpakowanie.**
   - Windows (`.zip`): `zipfile.ZipFile(...).extractall(JRE_DEST_DIR)`.
   - Linux / macOS (`.tar.gz`): `tarfile.open(...).extractall(JRE_DEST_DIR)`.
   Po rozpakowaniu archiwum może zostawić folder `jdk8u422-b05-jre/` —
   przeszukaj `JRE_DEST_DIR` rekurencyjnie do głębokości 3 w poszukiwaniu
   `bin/java.exe` (Windows) lub `bin/java` (Linux/macOS). Skoryguj uprawnienia
   na binarce (Linux/macOS: `os.chmod(path, 0o755)` — tar potrafi gubić
   bity wykonywalności w zależności od `umask`).

6. **Walidacja wersji.** Uruchom `check_java_version(path)` z
   `core/otp_server.py`. Wynik musi być `8.x` (major `1.8` w
   tradycyjnym formacie `1.8.0_422`). Inna wersja → błąd, nie zapisuj
   ścieżki w QSettings.

7. **Zapis w QSettings.** Jeśli `SET_AS_DEFAULT == True`:
   `QSettings().setValue('easy_otp/java_path', path)`. Log z potwierdzeniem.

8. **Sprzątanie.** Usuń pobrane archiwum (`.zip`/`.tar.gz`) po
   poprawnym rozpakowaniu, żeby nie zaśmiecać `JRE_DEST_DIR`. Pozostaw
   tylko strukturę JRE.

9. **Output.** Algorytm zwraca w wyjściach (`results`):
   - `JAVA_PATH` (String) — pełna ścieżka do binarki
   - `JAVA_VERSION` (String) — pełna wersja z `java -version`

### Pliki referencyjne / wzorzec do portu

Brak. Algorytm from-scratch. Wzorzec architektoniczny: każdy inny
algorytm w `easy_otp/algorithms/` (z v0.1) — struktura klasy, mechanika
postępu, integracja z providerem.

Adoptium API jest dobrze udokumentowane:
`https://api.adoptium.net/q/swagger-ui/#/Assets`. Endpoint
`/v3/assets/latest/{feature_version}/{jvm_impl}` zwraca w stabilnym
formacie — Eclipse Adoptium gwarantuje wsteczną kompatybilność API.

### Edge cases i walidacja

- **Brak internetu / DNS nie działa** — `urllib.error.URLError` z
  kroku 2 lub 3. Komunikat: „Cannot reach Adoptium API. Check your
  network connection."
- **`JRE_DEST_DIR` nie istnieje** — Processing wymusza istnienie
  folderu (typ `QgsProcessingParameterFile.Behavior.Folder`), ale
  walidacja na początku algorytmu: czy zapisywalny.
- **`JRE_DEST_DIR` zawiera już rozpakowane JRE** (cache hit) —
  przeszukaj folder na początku; jeśli `bin/java[.exe]` znaleziony i
  `check_java_version` zwraca Java 8 → pomiń pobieranie, zapisz ścieżkę
  w QSettings (jeśli `SET_AS_DEFAULT`), zwróć. Log: „Existing Java 8
  found in {path}, skipping download." To pozwala bezstresowo
  uruchomić R3 dwa razy.
- **Apple Silicon / ARM Linux (`platform.machine()` zwraca `arm64`/`aarch64`)**
  — algorytm w v0.2 odrzuca uruchomienie z komunikatem o niewspieranej
  architekturze i sugestią ręcznego pobrania z Adoptium (z fallbackiem
  na Rosetta 2 na macOS jako tymczasowe obejście). Pełne wsparcie
  natywne — v0.3.
- **Niedostateczna przestrzeń na dysku** — `shutil.disk_usage(JRE_DEST_DIR)`
  przed pobraniem; potrzeba ~250 MB (archiwum ~80 MB + rozpakowane
  ~150 MB). Jeśli mniej → błąd przed pobraniem.
- **Pełny rate limit Adoptium** — API w praktyce nie ma rate limitów dla
  pojedynczych użytkowników, ale w razie 429 → komunikat „Too many
  requests, please try again in a few minutes."
- **Mismatch SHA256** — usuń plik, błąd. Nie próbuj ponownie automatycznie.
- **Anulowanie w trakcie rozpakowania** — `zipfile` / `tarfile` nie
  obsługują anulowania natywnie. Akceptowalne ograniczenie: anulowanie
  działa tylko podczas pobierania (najdłuższy etap). W trakcie
  rozpakowania (kilka sekund) anulowanie jest ignorowane.

### Tryb błędu i komunikaty

| Sytuacja | Komunikat |
|---|---|
| Adoptium API niedostępny | `"Cannot reach Adoptium API at https://api.adoptium.net. Check your network connection."` |
| Brak assetu dla platformy x64 | `"No JRE 8 x64 build available for {os} on Adoptium. Supported combinations: see https://adoptium.net/temurin/releases/?version=8"` |
| Uruchomienie na ARM (arm64/aarch64) | `"Automatic JRE download in v0.2 supports x64 only. Detected architecture: {arch}. Please download Temurin 8 manually from https://adoptium.net/temurin/releases/?version=8 (native build for your architecture, or x64 build for use under Rosetta 2 on macOS)."` |
| `JRE_DEST_DIR` nie zapisywalny | `"Destination folder '{path}' is not writable. Check permissions or choose another folder."` |
| Mało miejsca na dysku | `"Not enough disk space in '{path}'. Need ~250 MB, have {available} MB."` |
| SHA256 mismatch | `"Downloaded archive checksum does not match Adoptium API. Likely network corruption — please retry. Expected {expected}, got {got}."` |
| Rozpakowane JRE nie zwraca Java 8 | `"Unpacked JRE reports version '{version}', expected '1.8.x'. Adoptium API may have returned the wrong asset — please open an issue."` |
| `bin/java[.exe]` nie znaleziony po rozpakowaniu | `"Cannot find 'bin/java[.exe]' inside the unpacked archive at '{path}'. Archive structure may have changed."` |

### Kryteria akceptacji

- Algorytm zarejestrowany w `easy_otp/provider.py` i widoczny w
  Processing Toolbox.
- W pustym `JRE_DEST_DIR`: pobranie + weryfikacja + rozpakowanie + zapis
  ścieżki kończy się sukcesem w < 2 min na łączu domowym.
- Po sukcesie `TestOtpServer` (z v0.1) uruchomiony bez parametru
  `JAVA_PATH` (lub z domyślnym pustym) automatycznie odczytuje ścieżkę
  z QSettings i raportuje „Java 8 OK".
- **Test cache:** drugi run R3 w tym samym folderze kończy się w
  < 5 sek bez pobierania (cache hit).
- **Test SHA256:** mock URL ze złym hashem → spodziewany błąd
  z komunikatem o mismatch.
- **Test anulowania:** Cancel w trakcie pobierania → archiwum `.tmp`
  usunięte, `JRE_DEST_DIR` w stanie sprzed uruchomienia.
- README.md zaktualizowany: nowa sekcja „Getting Java 8 (automated)"
  zastępuje (lub uzupełnia jako preferowaną ścieżkę) obecną
  instrukcję ręcznego pobrania.

### Otwarte pytania / spike'y wymagane przed implementacją

1. **Decyzja: Temurin czy Zulu.** v0.2 — **Temurin** (Adoptium API
   publiczne, brak rejestracji, najszerzej testowane buildy). Zulu
   wymaga rejestracji konta na azul.com dla pobrań, co jest
   bezsensowne dla automatyzacji. Decyzja podjęta.

---

## R2 — `DownloadTransitData` (auto pozyskiwanie OSM + GTFS)

### Cel i wartość

Algorytm pomocniczy pobiera dwa zewnętrzne zestawy danych wymagane przez
`RunTemporalAccessibility`: ekstrakt OpenStreetMap (`.osm.pbf`) z Geofabrik
oraz rozkłady jazdy (GTFS `.zip`) z Transitland — dla wskazanego obszaru
geograficznego. Zwraca w logu Processing ścieżki gotowe do skopiowania w
parametry algorytmu głównego. Eliminuje to dwa ręczne kroki (znalezienie
właściwego ekstraktu OSM dla regionu, znalezienie i pobranie GTFS dla
operatorów transportu publicznego obsługujących obszar) — które dziś są
opisane w `README.md` jako prerequisite, ale wymagają od użytkownika
samodzielnego nawigowania na Geofabriku i w bazach GTFS.

### Kontekst i zależności

- Zadanie **izolowane** — zero zmian w pipeline, zero zależności od
  innych elementów roadmapy. Output to dwie ścieżki na dysku, które
  użytkownik wkleja w `RunTemporalAccessibility`.
- **Brak zależności od `pip install`** — wszystko stdlib: `urllib`,
  `json`. Geofabrik index w formacie GeoJSON / JSON, Transitland API
  zwraca JSON.
- Maksymalny zakres analizy (zgodny z R1a): **województwo** dla Polski,
  odpowiednik (region administracyjny średniej wielkości) dla innych
  krajów. R2 wybiera ekstrakt OSM z poziomu, który najbliżej pasuje
  rozmiarem (np. dla Polski: `poland/dolnoslaskie` z Geofabrik, nie
  `poland` jako całość ani `poland/dolnoslaskie/wroclaw` — to ostatnie
  nie istnieje na Geofabriku jako oddzielny ekstrakt).

### Parametry algorytmu QGIS

| Parametr | Typ | Wymagany | Default | Opis |
|---|---|---|---|---|
| `AREA_NAME` | `QgsProcessingParameterString` | tak | — | Nazwa obszaru z indeksu Geofabrik (np. `dolnoslaskie`, `mazowieckie`, `berlin`). Dopasowanie case-insensitive po polu `id` lub `name`. |
| `DEST_DIR` | `QgsProcessingParameterFile` (Folder) | tak | — | Folder docelowy. Algorytm tworzy w nim podfoldery `osm/` i `gtfs/`. |
| `DOWNLOAD_OSM` | `QgsProcessingParameterBoolean` | nie | `True` | Czy pobrać ekstrakt OSM. Odznacz, jeśli używasz już lokalnego `.osm.pbf` i potrzebujesz tylko świeżych GTFS. |
| `DOWNLOAD_GTFS` | `QgsProcessingParameterBoolean` | nie | `True` | Czy pobrać feedy GTFS. Odznacz, jeśli używasz już lokalnego folderu GTFS i potrzebujesz tylko świeżego OSM. |
| `GTFS_API_KEY` | `QgsProcessingParameterString` | nie | `""` | Klucz Transitland (https://www.transit.land/documentation). Bez klucza działa z rate limitem 100 req/h — dla R2 wystarczające. Aktywny tylko gdy `DOWNLOAD_GTFS == True`. |
| `OUTPUT_OSM` | `QgsProcessingParameterFile` (Output, File) | tak | — | Ścieżka do pobranego `.osm.pbf`. Wartość zwracana w `results` — pusta gdy `DOWNLOAD_OSM == False`. |
| `OUTPUT_GTFS_DIR` | `QgsProcessingParameterFile` (Output, Folder) | tak | — | Ścieżka do folderu z pobranymi feedami GTFS. Pusta gdy `DOWNLOAD_GTFS == False`. |

Co najmniej jeden z `DOWNLOAD_OSM` / `DOWNLOAD_GTFS` musi być `True` —
inaczej algorytm zatrzymuje się na walidacji z komunikatem „Nothing to
download". Backend GTFS w v0.2 = wyłącznie Transitland v2; własna lista
URL / własny serwer GTFS planowane w v0.3 (patrz „Plany na v0.3" na
końcu pliku).

`shortHelpString()` zawiera linki do Geofabrik (`https://download.geofabrik.de`)
oraz Transitland (`https://www.transit.land`) jako źródeł, informację o
licencjach (OSM ODbL, GTFS różne — każdy operator), i przewidywany rozmiar
pobrania (~50–500 MB dla OSM województwa, ~5–20 MB sumarycznie dla GTFS).
Wzmianka, że oba pobrania są niezależne — można odznaczyć któreś z nich,
jeśli dany zestaw danych użytkownik już posiada lokalnie.

### Algorytm krok po kroku

Implementacja w `easy_otp/algorithms/download_transit_data.py`.

#### Krok 0 — walidacja konfiguracji

Jeśli `DOWNLOAD_OSM == False` i `DOWNLOAD_GTFS == False` → błąd
„Nothing to download. Enable at least one of DOWNLOAD_OSM /
DOWNLOAD_GTFS." Algorytm kończy się przed jakimkolwiek zapytaniem
sieciowym.

#### Część A — OSM (Geofabrik)

Krok A1 wykonywany **zawsze** (potrzebny dla bbox w części B, nawet
gdy `DOWNLOAD_OSM == False`). Kroki A2–A4 zależne od `DOWNLOAD_OSM`.

A1. **Pobierz indeks Geofabrik.** URL:
`https://download.geofabrik.de/index-v1.json` — pełna struktura
kontynentów → krajów → regionów z metadata i URL-ami do `.osm.pbf`.
Cache lokalnie w `DEST_DIR/.geofabrik-index.json` z TTL 7 dni (indeks
zmienia się rzadko, codzienne snapshoty są pod stabilnymi URL).

A2. **Znajdź obszar po `AREA_NAME`.** Przeszukaj `features[*].properties.id`
i `features[*].properties.name` (case-insensitive `contains` lub dokładne
dopasowanie). Jeśli wiele dopasowań — błąd z listą znalezionych
(np. `AREA_NAME='gdansk'` może trafić w gdańsk PL i Gdansk DE). Jeśli
0 dopasowań — błąd z sugestiami (top 5 najbliższych przez
`difflib.get_close_matches`). Z dopasowanego rekordu zachowaj bbox
(potrzebny w B1) i URL do `.osm.pbf` (potrzebny w A3, jeśli będzie wykonane).

A3. **Pobranie `.osm.pbf`.** Wykonywane tylko gdy `DOWNLOAD_OSM == True`.
URL z `features[i].properties.urls.pbf`. Chunk-based pobieranie do
`DEST_DIR/osm/{area_id}.osm.pbf` (analogiczne do R3 kroku 3).
Anulowanie + cleanup `.tmp`. Pasek postępu częściowy (40% wagi w
sumarycznym postępie algorytmu, gdy oba pobrania aktywne; 90% gdy
tylko OSM).

A4. **Weryfikacja MD5.** Wykonywane tylko gdy `DOWNLOAD_OSM == True`.
Geofabrik publikuje plik `*.osm.pbf.md5` obok każdego ekstraktu.
Pobierz, porównaj, niezgodność → błąd.

#### Część B — GTFS (Transitland)

Cała wykonywana tylko gdy `DOWNLOAD_GTFS == True`.

B1. **Bbox obszaru.** Z bbox zapisanego w A2 wyciągnij prostokąt
obejmujący (lon_min, lat_min, lon_max, lat_max).

B2. **Query Transitland API v2.** URL:
`https://transit.land/api/v2/rest/feeds?bbox={lon_min},{lat_min},{lon_max},{lat_max}&spec=gtfs`
+ nagłówek `apikey: {GTFS_API_KEY}` jeśli klucz podany. Response: JSON
z listą feedów, każdy z `urls.static_current` (URL do najnowszego
statycznego `.zip`).

B3. **Pobranie każdego feedu.** Dla każdego feedu w odpowiedzi:
`DEST_DIR/gtfs/{onestop_id}.zip` (gdzie `onestop_id` to unikalny
identyfikator Transitland, np. `f-u3-ztm~warszawa`). Chunk-based,
anulowanie z cleanup. Pasek postępu (proporcjonalna pozostała waga
postępu — 60% wagi gdy oba pobrania aktywne, 90% gdy tylko GTFS).

B4. **Walidacja struktury GTFS.** Dla każdego pobranego `.zip`:
otwórz przez `zipfile`, sprawdź obecność `agency.txt`, `stops.txt`,
`routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt` lub
`calendar_dates.txt` (minimum wymagane przez GTFS). Braki → ostrzeżenie
(nie błąd; OTP poradzi sobie z większością niespełnionych
specyfikacji), log nazw brakujących plików.

#### Wyjście

Zwróć w `results`:
- `OUTPUT_OSM` = pełna ścieżka do `.osm.pbf` (gdy pobrane) lub pusty
  string (gdy `DOWNLOAD_OSM == False`).
- `OUTPUT_GTFS_DIR` = pełna ścieżka do `DEST_DIR/gtfs/` (gdy pobrane)
  lub pusty string (gdy `DOWNLOAD_GTFS == False`).
- Log z listą wszystkich pobranych plików, ich rozmiarów, źródeł
  (Transitland onestop_id) — gotowy do wklejenia w parametry
  `RunTemporalAccessibility`.

### Pliki referencyjne / wzorzec do portu

Brak. Algorytm from-scratch.

Wzorzec API:
- **Geofabrik index format**:
  `https://download.geofabrik.de/technical.html` — dokumentacja struktury
  GeoJSON-podobnej, z URL-ami do `.osm.pbf` / `.shp.zip` / `.osm.bz2`
  per ekstrakt.
- **Transitland v2 REST API**:
  `https://www.transit.land/documentation/rest-api/feeds.html` —
  endpoint `/api/v2/rest/feeds` z filtrowaniem po `bbox`, response
  schema, mechanika klucza API.

### Edge cases i walidacja

- **Wieloznaczność nazwy obszaru** (np. „Gdańsk" jako miasto, jako
  powiat, jako województwo) — błąd z pełną listą dopasowań i sugestią
  użycia bardziej precyzyjnego `id` (np. `pomorskie` zamiast `gdansk`).
- **Obszar zbyt drobny do dostępnego w Geofabrik** (np. konkretne
  miasto — Geofabrik nie ma miejskich ekstraktów dla większości miast
  poniżej Berlina/Hamburga). Reakcja: błąd z sugestią użycia
  nadrzędnego ekstraktu i opcjonalnego clippingu w v0.3.
- **Transitland zwraca 0 feedów dla bbox** (np. region wiejski bez
  zdigitalizowanych rozkładów) — ostrzeżenie, nie błąd; algorytm
  kontynuuje z pustym `gtfs/`, użytkownik dostaje informację w
  logu o braku.
- **Feed pobrany przez Transitland zwraca 404** (link `static_current`
  wskazuje na operatora, który aktualnie nie publikuje) — pomiń,
  log z numerem feedu i URL-em. Nie przerywaj całości.
- **GTFS feed nie ma poprawnej struktury** — ostrzeżenie, jak wyżej.
- **Cache hit dla `.osm.pbf`** — jeśli `{area_id}.osm.pbf` istnieje
  w `DEST_DIR/osm/` i ma poprawny MD5 (lub nawet bez sprawdzania, jeśli
  `mtime` < 7 dni) → pomiń pobranie, log „Using cached OSM extract".
  Cache dla GTFS — bez TTL, nadpisuj zawsze (rozkłady się zmieniają
  bez przewidywalnego cyklu).
- **Klucz Transitland nieważny** — Transitland zwraca 401. Komunikat:
  „Transitland API key is invalid. Get a free key at
  https://www.transit.land or leave the field empty for unauthenticated
  access (lower rate limit)."

### Tryb błędu i komunikaty

| Sytuacja | Komunikat |
|---|---|
| Geofabrik index niedostępny | `"Cannot reach Geofabrik index at https://download.geofabrik.de. Check your network connection."` |
| `AREA_NAME` ambiguous | `"Area '{name}' matches multiple regions: {list}. Please use a more specific id."` |
| `AREA_NAME` nie znaleziony | `"Area '{name}' not found in Geofabrik index. Closest matches: {top5}."` |
| `.osm.pbf` MD5 mismatch | `"OSM extract checksum does not match Geofabrik manifest. Likely network corruption — please retry."` |
| Transitland 401 | jak wyżej w „Edge cases" |
| Transitland 0 feedów | ostrzeżenie (nie błąd): `"No GTFS feeds found in Transitland for the bounding box of '{area}'. The GTFS folder will be empty — you can add feeds manually by copying their .zip files into '{path}' after the algorithm finishes."` |
| Mało miejsca na dysku | `"Not enough disk space in '{path}'. Need ~{est} MB, have {available} MB."` |

### Kryteria akceptacji

- Algorytm zarejestrowany i widoczny w toolboxie.
- **Test podstawowy** — `AREA_NAME='dolnoslaskie'`, `DEST_DIR` pusty,
  bez klucza Transitland:
  - `OUTPUT_OSM` istnieje, otwiera się jako prawidłowy ekstrakt
    OSM (test: rozmiar > 50 MB, nie zerowy).
  - `OUTPUT_GTFS_DIR` zawiera ≥ 1 `.zip` z feedem (np. ZDiTM Wrocław).
  - Po wklejeniu obu ścieżek w `RunTemporalAccessibility` (60-min
    interwał, jeden punkt docelowy) algorytm buduje graf OTP bez błędu.
- **Test cache** — drugi run z tym samym `AREA_NAME` pomija pobieranie
  OSM (log „Using cached…").
- **Test obszaru wieloznacznego** — `AREA_NAME='berlin'` → błąd z
  listą dopasowań (Berlin DE i Berlin land DE, ewentualnie miasta w
  innych krajach jeśli są w Geofabrik).
- **Test trybu „tylko GTFS"** — `AREA_NAME='dolnoslaskie'`,
  `DOWNLOAD_OSM=False`, `DOWNLOAD_GTFS=True`:
  - `OUTPUT_OSM` pusty, `OUTPUT_GTFS_DIR` zawiera feedy.
  - Indeks Geofabrik pobrany (potrzebny dla bbox), ale `.osm.pbf` nie.
- **Test trybu „tylko OSM"** — `DOWNLOAD_OSM=True`, `DOWNLOAD_GTFS=False`:
  - `OUTPUT_OSM` istnieje, `OUTPUT_GTFS_DIR` pusty.
  - Brak zapytań do Transitland API w logu.
- **Test walidacji** — `DOWNLOAD_OSM=False`, `DOWNLOAD_GTFS=False` →
  błąd „Nothing to download" przed jakimkolwiek zapytaniem sieciowym.
- README.md zaktualizowany: nowa sekcja „Getting OSM and GTFS data
  (automated)" przed dotychczasową sekcją z ręcznym wskazaniem ścieżek.

### Otwarte pytania / spike'y wymagane przed implementacją

1. **Spike Geofabrik index (15 min).** Pobierz
   `https://download.geofabrik.de/index-v1.json` ręcznie, zweryfikuj
   strukturę pól (`features[*].properties.id`, `urls.pbf`, `geometry.bbox`).
   Potwierdź obecność wszystkich 16 województw polskich jako oddzielnych
   ekstraktów. Jeśli któreś brakuje (np. tylko `poland` jako całość) —
   skoryguj domyślne założenia, że jednostka pobierania = województwo.
2. **Spike Transitland API v2 (30 min).** Wykonaj query
   `GET /api/v2/rest/feeds?bbox=...` bez klucza i z kluczem darmowym.
   Potwierdź pole `urls.static_current` jako stabilny link do `.zip`,
   nie URL formularza pobrania. Zweryfikuj rate limit bez klucza (100/h
   wystarcza dla pojedynczego użytkownika; jeśli mniej — wymuszać klucz).
3. **Decyzja: zachowanie przy braku Transitland API key.** v0.2:
   próba bez klucza, jeśli rate limit przekroczony → komunikat z
   sugestią założenia darmowego konta. Klucz jako opcjonalny parametr,
   nie wymagany. Alternatywa odrzucona: wymuszanie klucza — to
   blokowałoby pierwsze uruchomienie i wymagało rejestracji konta od
   użytkownika, co kłóci się z duchem automatyzacji R2.
4. **Funkcjonalności odłożone do v0.3:**
   - **Clipping `.osm.pbf` do mniejszego bbox** — wymaga `osmium` lub
     `osmconvert` (zewnętrzne narzędzia poza QGIS, złamałoby zasadę
     „zero pip install / zero zewnętrznych binarek" z CLAUDE.md).
     v0.2 pobiera pełny ekstrakt regionu.
   - **Manualna lista GTFS / własny URL feedu / własny serwer GTFS** —
     na wypadek operatorów nieobecnych w Transitland (problem dotyka
     części polskich miast — np. lokalni przewoźnicy bez agregacji).
     v0.2 ogranicza się do Transitland; obejście dla użytkownika to
     ręczne wgranie `.zip` do `DEST_DIR/gtfs/` po uruchomieniu R2.

---

## R6 — `RunCarDependency` (Car Dependency Index)

### Status

> **Sekcja przeniesiona do v0.3.** **W v0.2 nie
> implementujemy.** Reszta informacji w R6-R7-easy-OTP_PR.md

---

## R7 — Siatka H3 (rozszerzenie `GenerateHexGrid`)

### Status

> **Sekcja przeniesiona do v0.3**. **W v0.2 nie implementujemy.** 
> Reszta informacji w R6-R7-easy-OTP_PR.md

---

## Plany na v0.3

Sekcja katalogowa — co odkładamy do następnej wersji wtyczki.
Lista nie jest specyfikacją do natychmiastowej implementacji; każda
pozycja w v0.3 dostanie własną sekcję w roadmapie v0.3 (analogicznej
do tego pliku).

### Rozszerzenia algorytmów v0.2

Pozycje wynikłe z otwartych pytań w sekcjach R-X:

- **R3: wsparcie Apple Silicon / ARM Linux** (architektura `aarch64`).
  Adoptium publikuje natywne buildy. W v0.2 algorytm odrzuca
  uruchomienie na ARM z komunikatem instruktażowym; v0.3 dorzuca
  natywne wykrywanie i pobieranie. Patrz „Otwarte pytania" w R3.
- **R2: alternatywne źródła GTFS** — własna lista URL-i podawana
  ręcznie, własny serwer GTFS (mirror/cache instytucjonalny),
  Mobility Database jako drugi backend obok Transitland. Dotyka
  problemu polskich operatorów nieobecnych w Transitland. Patrz
  „Otwarte pytania" w R2 punkt 4.
- **R2: clipping `.osm.pbf` do mniejszego bbox.** Wymaga `osmium`
  lub `osmconvert` (zewnętrzne narzędzia poza QGIS); rozważyć
  bundlowanie z wtyczką lub instrukcję instalacji w README.
  Patrz „Otwarte pytania" w R2 punkt 4.

### Nowe funkcjonalności (poza zakresem v0.2)

- **Wybór trybu transportu w `RunTemporalAccessibility`** poza
  domyślnym `mode=TRANSIT` (i `mode=CAR` używanym w R6). Możliwe
  tryby OTP 1.5.0: `WALK`, `BICYCLE`, `TRANSIT,WALK`,
  `TRANSIT,BICYCLE` (z zastrzeżeniem: **`TRANSIT,BICYCLE` nie jest
  obsługiwany przez OTP** — ustalenie potwierdzone przez właściciela
  projektu), `CAR`, `CAR_PARK`. Parametr `MODES` w UI z enumeracją.
- **Median CDI / city-wide aggregaty** (analogicznie do Tabeli 1
  w artykule CDI — porównanie 18 miast). Algorytm pomocniczy
  agregujący wynik R6 do pojedynczego wiersza statystyk miasta.
- **Eksport wyników do formatów publikacyjnych** — automatyczna
  generacja map w stylu Figure 3 (CDI) i Figure 7 (Tabela 2 z
  artykułu spatio-temporal) bezpośrednio z wtyczki.
- **Integracja z platformą Project Chronos** — jeśli i kiedy
  Project Chronos dostarczy publiczne API, wtyczka mogłaby
  pushować wyniki do platformy. Spike rekonesansowy.

### Polityka aktualizacji tego pliku

Po wdrożeniu każdego elementu R-X w v0.2 — sekcja pozostaje
w pliku bez zmian (frozen historia). Jednocześnie wszystkie testy
akceptacji z sekcji „Kryteria akceptacji" muszą być spełnione, a
zmiany odzwierciedlone w `README.md`

Po wdrożeniu całego scope'u v0.2 — utworzyć nowy plik
`PR_easy-OTP_roadmap_v0.3.md` z sekcjami przeniesionymi tutaj
(R6, R7) plus rozwinięciem listy z „Plany na v0.3". Ten plik
zostaje wówczas zamrożony jako referencja historyczna.

---

## Powiązana wtyczka: `easy-r5`

Pierwotnie planowane elementy roadmapy `easy-OTP` v0.2 — silnik R5
przez `r5py` (oznaczony jako R5) oraz wielo-origin analizy (R4) —
zostały **wydzielone do osobnej wtyczki `easy-r5`**. Powód
architektoniczny: wtyczka `easy-OTP` obsługuje wyłącznie silnik OTP
1.5.0 (zgodnie z nazwą); silnik R5 z odmiennym backendem
(wymagającym `r5py` i Javy 21+) zasługuje na własną wtyczkę.

Pełna specyfikacja (draft) znajduje się w pliku
`PR_easy-r5.md` — zawiera przeniesione sekcje R5 i R4 oraz wstęp
opisujący relację między wtyczkami. Plik jest punktem startowym do
napisania pełnego PR wtyczki `easy-r5` w osobnym chacie / etapie.

**Konsekwencja dla v0.2 wtyczki easy-OTP:** zakres został zredukowany
do 4 implementowanych elementów (R1b, R1a, R3, R2). Wtyczka pozostaje
w pełni funkcjonalna na silniku OTP, ze wszystkimi jego znanymi
ograniczeniami wydajnościowymi (długie runy dla małych kroków
czasowych, brak multi-origin). Użytkownicy potrzebujący skalowalności
lub analiz wielu origins mogą sięgnąć po `easy-r5` jako uzupełnienie.