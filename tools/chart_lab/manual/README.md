# chart_lab — instrukcja użytkownika

`chart_lab` to lokalna aplikacja z interfejsem w przeglądarce, która zastępuje terminal przy
pracy z narzędziami z `tools/`: rysuje wykresy punktualności i regularności, a od wersji 0.2
potrafi też zbudować dane od zera z nagrania GTFS-RT. Działa na Twoim komputerze — nic nie
jest wysyłane w sieć poza pobieraniem danych z katalogu online (patrz [§ 8](#8-skąd-wziąć-dane-gtfs-dashboard)).

> English version: [README.en.md](README.en.md).
> Przykłady analiz z gotowymi wykresami: [EXAMPLES.md](EXAMPLES.md).
> Opis techniczny i decyzje projektowe: `docs/prd/PR_easy-OTP_chart_lab_v02.md` (lokalnie).

## 1. Uruchomienie

**Gotowy plik Windows** (bez Pythona): pobierz z zakładki *Releases* repozytorium plik
`chart_lab-windows.zip` (tag `chart_lab-v*`), rozpakuj, uruchom `chart_lab.exe`. Otworzy się okno
konsoli (zostaw je — to ono jest „serwerem" aplikacji) i karta przeglądarki.

**Ze źródeł:**

```bat
cd tools\chart_lab
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m chart_lab.app
```

Adres aplikacji to `http://127.0.0.1:7860`. Zamknięcie okna konsoli kończy aplikację.

## 2. Co jest w aplikacji

| Zakładka | Do czego | Odpowiednik w CLI |
|---|---|---|
| **Charts** | 16 wykresów punktualności, regularności, bunchingu, prędkości | `transit_charts chart` |
| **Pipeline** | z nagrania i statycznego GTFS do gotowej tabeli tidy | `family_a match` → `family_a build` → `transit_charts extract` |
| **Stop headway** | odstępy na przystankach do mapy heksagonalnej + wykres H31 | `transit_charts stop-headway` |
| **Realized (TripUpdates)** | zrealizowany GTFS z archiwum TripUpdates (np. ŁKA) | `family_b_realized/build_realized.py` |
| **Diagnose RT** | czy statyczny GTFS pasuje do żywego feedu RT | `rt_diagnose/compare_rt_vs_static.py` |

Czego aplikacja **nie robi**: nie nagrywa feedów (`record` — robi to telefon z Termuxem), nie
uruchamia skryptów z `tools/analysis/` i nie dotyka potoku w chmurze (`easy-GTFS-RT`).

Formularze zakładek danych są generowane z parserów CLI, więc mają dokładnie te same opcje i te
same opisy co komendy terminalowe. Pole zaznaczone `*` jest wymagane; reszta opcji jest w
zwiniętym panelu **Advanced**. Pole puste albo równe wartości domyślnej CLI nie jest w ogóle
przekazywane do komendy — zachowanie jest takie samo jak w terminalu bez tej flagi.

## 3. Zakładka Charts

1. **Data.** Domyślnie aktywna jest dołączona tabela przykładowa (Łódź, 2026-07-23). Możesz
   dodać własną tabelę tidy (przeciągnij `.csv`, `.csv.gz` lub `.parquet`) albo pobrać gotową z
   panelu **Online catalogue**. Zaznaczone tabele są używane wspólnie przez każdy wykres.
2. **Chart.** Wybierz wykres; pod listą pojawia się jedno zdanie, co on pokazuje. Widać tylko te
   parametry, których dany wykres używa.
3. **Parametry.** Zmiana dowolnego od razu przerysowuje wykres (nie ma przycisku „generuj").
   Trasy i kierunek wybiera się klikając przyciski. Najedź na etykietę pola, żeby zobaczyć opis.
4. **Downloads** pod wykresem: PNG, CSV z liczbami, JSON z parametrami (i HTML dla C9/C10/B6,
   jeśli zaznaczysz „Also write interactive HTML"). Pliki lądują w `%TEMP%\chart_lab_output`
   (przycisk obok otwiera ten folder).
   Wykres zajmuje na stronie najwyżej 80% wysokości okna: szerokie mieści się w całości, a wysokie
   (np. H29/H30 przy wielu liniach) przewija się wewnątrz własnego panelu, bez wydłużania strony.

Wymagania danych:

| Wykres | Potrzebuje |
|---|---|
| większość (C9, C10, C11, A2, B5–B8, D14, D17, H28–H30) | 1 tabela (jeden dzień jednego miasta) |
| **D15** (systematyczne vs losowe opóźnienie) | ≥ 3 tabele z **różnych dni** |
| **E20, J39** (porównania między miastami) | ≥ 2 tabele z **różnych miast** |

Gdy tabel jest za mało, zamiast wykresu widać komunikat ⚠️ z informacją, czego brakuje.

**Bunching (zbijanie się pojazdów):** B8 (przystanek × godzina, jedna linia) i H30 (linia ×
godzina, całe miasto) liczą udział odstępów krótszych niż `Bunching threshold` (domyślnie 0,25)
**własnego rozkładowego odstępu** tej pary przystanków — ułamek, nie minuty, więc linie co 5 i
co 20 minut są porównywalne. Komórki z mniej niż 3 odstępami są kreskowane (brak danych).

## 4. Zakładka Pipeline — od nagrania do tabeli tidy

Używasz jej, gdy masz **własne nagranie** VehiclePositions (katalog z plikami
`snapshot_YYYYmmdd-HHMMSS.pb`) i statyczny GTFS. Jeśli wystarczy Ci gotowy dzień z katalogu
online — nie potrzebujesz tej zakładki.

1. Wpisz **City** (etykieta w tabeli, np. `lodz`).
2. Wskaż **Recording folder(s)**: jeden folder na linię. Kilka folderów = scalenie kilku dni
   (`--positions-dir` z wieloma wartościami). Przycisk *Add recording folder…* otwiera okno
   systemowe.
3. Wskaż **Static GTFS .zip** (*Browse…*). **To musi być ta sama publikacja, która obowiązywała w
   dniu nagrania** — `trip_id` w innym wydaniu się nie zgadzają i wynik jest bezwartościowy
   (patrz ostrzeżenia FA-15/FA-16 niżej).
4. **Work folder** (opcjonalnie): gdzie zapisać wyniki. Puste = `~/Documents/chart_lab/<city>/`.
5. Kliknij **Run all** albo uruchamiaj kroki osobno: **Run match → Run build → Run extract**.
   Wyjście każdego kroku jest wejściem następnego, nic nie przepisujesz.

Pliki w folderze roboczym:

| Plik | Krok | Zawartość |
|---|---|---|
| `matched.csv` | match | pozycje pojazdów dopasowane do kształtów tras |
| `realized_p50.zip`, `realized_p85.zip` | build | zrealizowany GTFS (mediana i P85 czasów przejazdu) |
| `<city>_tidy.csv.gz` | extract | tabela tidy dla wykresów; po zakończeniu trafia do aktywnych tabel w **Charts** |

Panele **options** każdego kroku to flagi CLI; w `extract` pole `--route` pozwala ograniczyć
tabelę do wybranych linii (`55*` = wszystko od `55`), co znacznie przyspiesza krok.

**Ostrzeżenia jakości.** Nad logiem pojawia się lista „Quality warnings — read before trusting
the output", a status zmienia ikonę z ✅ na ⚠️. Nie ignoruj ich:

- `WARNING (FA-15)` — duży udział odrzuconych obserwacji albo linie, które miały obserwacje, ale
  żadna nie została przyjęta (takie linie wyglądają później jak idealnie punktualne, bo zachowują
  rozkład); albo zbyt mało linii dostało jakąkolwiek korektę.
- `WARNING (FA-16)` — statyczny GTFS nie rozpoznaje większości `trip_id` z tabeli: prawie na pewno
  to inne wydanie GTFS niż to, na którym zrobiono `match`. Policz od nowa z właściwym plikiem.
- „N library warning(s) in the log" — setki powtarzalnych ostrzeżeń z modułu dopasowania
  (np. `FA-12 windowed search found nothing`); są zwinięte do jednej linii, pełna lista jest w logu.

Czas i pamięć (jeden dzień Łodzi, ~960 snapshotów co 30 s): `match` ok. 1,5 min i do ~1 GB RAM,
`build` ok. 2 min i ~0,9 GB. Pamięć wraca do systemu po każdym zadaniu.

## 5. Zakładka Stop headway

Wskaż `matched.csv`, statyczny GTFS, miasto i prefiks wyjścia. Powstają: `<prefix>_stops.csv`
(przystanek, współrzędne, `n`, mediana odstępu — wejście do mapy heksagonalnej) oraz wykres
`<prefix>_H31.png/.csv/.json` (odstępy w ciągu dnia dla całego miasta). Zawsze używa całego
feedu — filtr linii zaniżałby częstotliwość przystanków obsługiwanych przez kilka linii.

## 6. Zakładka Realized (TripUpdates)

Dla operatorów bez VehiclePositions (np. ŁKA): z archiwum migawek TripUpdates
(`polish_trains_updates_<dzień>.pb[.gz]`) buduje zrealizowany GTFS `<prefix>_p50.zip` i
`<prefix>_p85.zip`. Pola: folder z migawkami, statyczny GTFS z dnia nagrania, prefiks wyjścia;
opcjonalnie `--service-days` (RRRR-MM-DD po przecinku) i `--agency-id` (domyślnie `LKA`; puste
pole = bez filtra, cała krajowa sieć). Narzędzie kończy się kodem 0 także wtedy, gdy własne
kontrole wypiszą `PROBLEM` — zakładka wtedy dopisuje uwagę, żeby sprawdzić linie `[p50]`/`[p85]`
w logu.

## 7. Zakładka Diagnose RT

Dwa pola: plik `.pb` z TripUpdates i statyczny GTFS `.zip`. Wynik to werdykt:
**EXACT-MATCH POSSIBLE** (statyczny GTFS pasuje do feedu), **FUZZY POSSIBLE** (`trip_id` różne,
ale można dopasować po `route_id` i godzinie startu) albo **NEITHER** (para nie nadaje się do
RT w OTP; użyj nagrywania i rekonstrukcji).

## 8. Skąd wziąć dane (gtfs-dashboard)

Katalog nagrań to <https://gisboost.github.io/gtfs-dashboard/>. Dane publikuje `easy-GTFS-RT`
jako dzienne wydania; strona czyta je z pliku
[`manifest.json`](https://gisboost.github.io/gtfs-dashboard/manifest.json) (stan z 2026-10-03:
28 miast, 1799 dni, 1578 tabel tidy; odświeżany codziennie). `chart_lab` korzysta wyłącznie z tego
manifestu i z adresów plików w nim (nigdy z API GitHuba, które ma limit 60 zapytań/h).

Dla każdego miasta i dnia w katalogu są następujące pliki:

| Plik (pole w manifeście) | Rozmiar | Do czego |
|---|---|---|
| **tabela tidy** `<miasto>_tidy_<data>.csv.gz` (`tidy_table`) | 9–20 MB | **wszystkie wykresy w chart_lab**; własne analizy w pandas |
| zrealizowany GTFS `…_p50.zip`, `…_p85.zip` | rzędu 10 MB (zależy od miasta) | routing (OTP/R5), wtyczka easy-OTP — analizy dostępności „jak jeździ naprawdę" |
| statyczny GTFS `…_static_gtfs_<data>.zip` | zależy od miasta | para dla realized; potrzebny do własnego `extract`/`stop-headway` |
| `…_diff_<data>_p50_summary.csv`, `…_chart.png` | małe | gotowe zestawienie opóźnień dnia |
| surowe migawki `<miasto>_snapshots_<RRRR-MM>.tar.xz` | 10–390 MB | tylko gdy chcesz własnego `match` z innymi parametrami |

Uwagi o dostępności:

- **Tabela tidy** istnieje od 2026-08-03; wcześniejsze dni jej nie mają (są tylko realized i
  statyczny GTFS). ŁKA (`lka`) nie ma tabel tidy w ogóle — to źródło TripUpdates (zakładka Realized).
- Pole `status` dnia: `ok` — pełne okno nagrania; `partial` — luki w nagraniu (`coverage_ranges`
  mówi, które godziny są pokryte, np. `08:52-22:00`). Do porównań wybieraj dni `ok`.

**Jak pobrać w aplikacji:** zakładka **Charts → Online catalogue** → *1. Fetch available
cities* → wybierz City → Month → Day → *2. Download and add to active tables*. Plik trafia do
cache (`%TEMP%\chart_lab_cache`), więc drugie użycie jest natychmiastowe. Ręcznie: na stronie
katalogu wybierz miasto i dzień i pobierz pliki z listy.

**Co pobrać do jakiej analizy:**

| Chcesz zobaczyć… | Pobierz |
|---|---|
| punktualność, regularność, bunching jednego miasta w jednym dniu | 1 tabela tidy |
| czy opóźnienie jest systematyczne czy losowe (D15) | tabele tidy jednej linii z ≥ 3 dni (najlepiej `ok`) |
| porównanie miast (E20, J39) | po 1 tabeli tidy z każdego miasta, najlepiej z **tej samej daty** |
| tydzień roboczy vs weekend | po kilka dni obu typów, ten sam kalendarz |
| wpływ opóźnień na dostępność (OTP/R5) | realized p50/p85 + statyczny GTFS z tego dnia |

## 9. Rozwiązywanie problemów

| Objaw | Co robić |
|---|---|
| Karta przeglądarki się nie otwiera | wejdź ręcznie na `http://127.0.0.1:7860` (jeśli port jest zajęty, Gradio wybierze kolejny — adres jest w konsoli) |
| Karta „zamarza" (np. po bardzo długim logu) | odśwież stronę; wyniki zadania są i tak w folderze roboczym |
| „Another job is already running" | naraz działa jedno zadanie — poczekaj albo kliknij **Cancel** |
| Cancel nic nie robi między krokami „Run all" | kliknięcie jest zapamiętane i zatrzyma kolejny krok przed startem |
| `matched.csv not found - run match first` | uruchom najpierw **match** albo wskaż ten sam folder roboczy co poprzednio |
| `FA-16` / prawie wszystko odrzucone | zły statyczny GTFS dla tego nagrania — pobierz wydanie z dnia nagrania |
| Zakładka *Realized* pokazuje „unavailable" | brakuje `gtfs-realtime-bindings` lub modułu `easy_otp` — `pip install -r requirements.txt` |
| Okno *Browse…* nie widać | bywa pod innymi oknami; wpisz ścieżkę ręcznie (cudzysłowy i spacje na końcu są usuwane) |
| Wykres D15/E20/J39: „needs at least…" | dodaj kolejne tabele (dni lub miasta), zob. § 3 |

Ścieżki: pobrane tabele — `%TEMP%\chart_lab_cache`; wyniki wykresów — `%TEMP%\chart_lab_output`;
wyniki Pipeline — folder roboczy. Żadne z nich nie leży w repozytorium.

## 10. Licencja i źródła

GPL-3.0-or-later (aplikacja importuje kod `transit_charts` i `family_a` bezpośrednio). Kod:
<https://github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab>. Metodologia rekonstrukcji:
[`tools/family_a_reconstruction/README.md`](../../family_a_reconstruction/README.md), opis wykresów:
[`tools/transit_charts/README.md`](../../transit_charts/README.md).
