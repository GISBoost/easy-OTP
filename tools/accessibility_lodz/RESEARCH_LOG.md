# Dziennik badania: SES i dostępność transportowa Łodzi (2026-08-22)

Chronologiczny zapis całego badania dla Łodzi — od warstwy dochodowej po dostępność
transportową r5r. Cel: żeby ktoś (albo ja za miesiąc) mógł to odtworzyć i zrozumieć bez
czytania całej rozmowy. Szczegóły techniczne (dokładne komendy, gotchas) są w `HANDOFF.md`
obu folderów — ten dokument to narracja + wyniki + decyzje, nie substytut tamtych.

Dwa foldery, dwa etapy tego samego badania:
- `tools/ses_income_lodz/` — warstwa społeczno-ekonomiczna (dochód, demografia, głosowanie)
- `tools/accessibility_lodz/` — dostępność transportowa (r5r), zbudowana NA warstwie z etapu 1

---

## Etap 1 — warstwa SES (dochód, demografia, głosowanie), 6 miast

### 1.1 Dochód (proxy, nie dane bezpośrednie)

GUS nie publikuje dochodu per obwód. Zbudowany **indeks proxy**: rozkład głosów wyborczych
2023 (PKW) per obwód głosowania × wagi dochodowe per partia z sondażu CBOS (`compute_precinct_
income.py`/`_generic.py`/`compute_income_from_tileset.py`). Wynik: `income_index_pln` — **nie
jest to realny dochód w PLN**, tylko indeks pozycjonujący obwody względem siebie. Pełna
metodologia, ograniczenia i alternatywy rozważone: `ses_income_lodz/METHODOLOGY.md` §1-3.

Geometria i populacja: `SU_BREC_2021_OBW.shp` (GUS, obwody spisowe) + arkusz GUS NSP2021
(`extract_population_generic.py`). Dla Łodzi/Krakowa geometria obwodów głosowania z portali
miejskich (ArcGIS); dla Warszawy/Poznania/Gdańska/Szczecina z kafli `wybory.it` (brak własnego
portalu) — dwie różne ścieżki, patrz `ses_income_lodz/HANDOFF.md` §3.1-3.2.

**Wynik weryfikacyjny**: suma `population` w Łodzi 669995 vs oficjalne GUS 670642 (99,9%
zgodności). 3854 obwodów spisowych, 283 obwodów głosowania.

### 1.2 Struktura rodzin/gospodarstw (2026-08-22, rano)

4 pliki GUS NSP2021 (typy rodzin, skład gospodarstw, liczba dzieci, wielkość gospodarstwa) —
`extract_family_household_stats.py` (parsuje xlsx, filtruje po kodach GMINA) +
`join_family_household_stats.py` (liczy pochodne %, dominujący typ, zapisuje do `.gpkg` przez
`sqlite3`+`mod_spatialite` — **gotcha**: GPKG ma triggery walidacji geometrii wołające
`ST_IsEmpty`, goły `sqlite3` bez wczytanego rozszerzenia Spatialite rzuca błąd na każdym
`UPDATE`/`ALTER TABLE`, nawet gdy zmiana dotyczy tylko atrybutów). 18 nowych pól: `fam_*`
(typy rodzin, dzieci), `hh_*` (gospodarstwa domowe).

### 1.3 Backfill `pis_proc` dla Łodzi/Krakowa (2026-08-22, później)

Te dwa miasta straciły surowe pola partyjne we wcześniejszych porządkach (tylko
`income_index_pln`/`valid_votes` zostało). Odtworzone przez `backfill_pis_share.py`: ponowne
pobranie oficjalnego CSV PKW (`danewyborcze.kbw.gov.pl/dane/2023/sejmsenat/wyniki_gl_na_listy_
po_obwodach_sejm_csv.zip`), filtr po TERYT gminy (106101 Łódź, 126101 Kraków — **6-cyfrowy kod
KBW**, inny niż 7-cyfrowy `GMINA` GUS), zapis `pis_proc` do obu warstw. 100% dopasowania.

### 1.4 Pytania badawcze i wyniki — część statystyczna (H1-H5)

5 hipotez, korelacja Pearsona, per miasto (6 miast) + pula (`analyze_correlations.py`):

| # | Hipoteza | Wynik |
|---|---|---|
| H1 | niższy dochód → więcej samotnych matek | **Potwierdzona**, jedyna spójna we wszystkich 6 miastach: Łódź −0,11, Kraków −0,25, Warszawa −0,33, Poznań −0,14, Gdańsk −0,34, Szczecin −0,20 |
| H2 | wyższy dochód → mniejsze gospodarstwa | Nie potwierdzona — znak niespójny między miastami (Warszawa ~0, Kraków −0,32) |
| H3 | wyższy dochód → mniej dzieci | Nie potwierdzona — bliskie zeru, znak zmienny |
| H4 | więcej PiS → więcej dzieci | Nie potwierdzona — w Warszawie (n=8480, najlepsza próba) wręcz odwrotnie (−0,15) |
| H5 | wyższy dochód → więcej gospodarstw 1-osobowych | Częściowo, niespójnie — silna w Krakowie (+0,30), zerowa/odwrotna gdzie indziej |

**Wniosek**: tylko H1 jest solidnym, uogólnialnym wynikiem. Reszta to szum albo efekt
specyficzny dla jednego miasta.

### 1.5 Pytania badawcze i wyniki — część przestrzenna (I Morana)

Globalny wskaźnik I Morana (`spatial_analysis.py`, k-NN k=8, test permutacyjny 299 permutacji,
własna implementacja numpy+sklearn — `esda`/`libpysal` niezainstalowane, zdecydowano nie
dodawać nowej zależności). Wszystkie zmienne istotnie sklastrowane (p<0,01) we wszystkich
miastach, ale z bardzo różną siłą:

| zmienna | I Morana (zakres po 6 miastach) | interpretacja |
|---|---|---|
| `income_index_pln`, `pis_proc` | 0,71–0,84 | bardzo silna — **mechaniczny artefakt**: jednolite w obrębie obwodu głosowania z konstrukcji, i `pis_proc` współtworzy `income_index_pln` |
| `hh_avg_size`, `hh_pct_jednoosobowe` | 0,35–0,52 | umiarkowana-silna, realna |
| `fam_pct_matki_samotne`, `fam_avg_children` | 0,19–0,32 | najsłabsza — struktura rodzinna zmienia się drobniej niż obwód głosowania |

**Wniosek (MAUP)**: `income_index_pln` jest "gładki" (zmienia się tylko na granicach obwodu
głosowania), struktura rodzinna zmienia się drobniej (obwód spisowy). Porównywanie ich
punktowo strukturalnie ogranicza możliwą siłę korelacji — słabe H2-H5 mogą częściowo wynikać
z niedopasowania skali pomiaru (potwierdzone później w Etapie 3 przez porównanie obwody vs
heksagony — sygnał faktycznie się wzmacnia po ujednoliceniu skali jednostki przestrzennej).

---

## Etap 2 — dostępność transportowa r5r, Łódź (pilot)

**Decyzja o zakresie** (zapytane i potwierdzone przez Michała, 2026-08-22): tylko Łódź (jedyne
miasto z kompletem: SES + granularne dane + zrealizowany GTFS Family A). Cele docelowe: usługi
publiczne z OSM, nie miejsca pracy — REGON odrzucony jako główne źródło (brak bulk-downloadu z
adresami bez rejestracji/klucza API, wymagałby geokodowania dziesiątek tysięcy rekordów; możliwy
stretch goal później, patrz `HANDOFF.md` §4).

### 2.1 Instalacja (jednorazowa)

R 4.6.1 (`winget install RProject.R`) + pakiety `r5r`/`sf`/`data.table`/`rJavaEnv` do
`R_LIBS_USER` (domyślna library nie miała uprawnień zapisu). Scoped JDK 21 przez
`rJavaEnv::java_quick_install(version=21)` — symlinkowany tylko do folderu projektu przez
`.Rprofile`, zero zmian systemowego `JAVA_HOME` (Java 25 systemowa i JDK8 od OTP nietknięte).
Pełne komendy: `HANDOFF.md` §3.1.

### 2.2 Dane wejściowe

- **Sieć drogowa**: `lodz.osm.pbf` (reużyty z `tools/family_a_reconstruction/graphs/`, wrzesień
  2025 — wystarczająco świeży dla sieci ulic/tras, które zmieniają się wolno).
- **GTFS**: **zrealizowany** (Family A, nie statyczny rozkład) — release `lodz-realized-2026-
  08-21-phone` z `GISBoost/easy-GTFS-RT`, wariant **p50** (mediana skorygowanego rozkładu na
  podstawie faktycznie obserwowanego dnia; p85 = wariant bardziej pesymistyczny, dostępny,
  nieużyty). To jest kluczowa różnica metodologiczna względem typowej analizy dostępności:
  liczymy dostępność na tym, co **faktycznie jeździło** 2026-08-21, nie na tym, co obiecuje
  rozkład.
- **Cele (opportunities)**: 1328 POI z OSM (Overpass API, `fetch_osm_services.py`) — edukacja
  (szkoły+przedszkola), zdrowie (szpitale+przychodnie+lekarze+apteki), kultura
  (biblioteki+domy kultury), sklepy (supermarkety).
- **Punkty startowe**: pierwsza wersja — centroidy 3854 obwodów spisowych (`export_origins.py`,
  reprojekcja EPSG:2180→4326). Druga wersja (Etap 3) — centroidy 1479 heksagonów 500m.

### 2.3 Silnik: `build_network()` + `accessibility()`

`run_accessibility.R`. **Pełne wytłumaczenie logiki `time_window`/percentyli — patrz sekcja
osobna niżej, to było pytanie Michała i zasługuje na własny rozdział, nie skrót tutaj.**

Parametry finalne (po korekcie): `mode=c("WALK","TRANSIT")`, `departure_datetime="2026-08-21
07:00:00"`, `time_window=120` (okno 07:00–09:00, poranny szczyt — pierwsza wersja miała
błędnie tylko 60 min/08:00-09:00, poprawione na prośbę Michała), `cutoffs=c(15,30,45,60)` min,
`decay_function="step"` (liczy POI osiągalne w progu, nie ważoną funkcję zanikania),
`max_trip_duration=90`.

**Ostrzeżenie r5r** "Less than 20% of the transit services in the GTFS are running on the
selected departure date" — **zweryfikowane, nie błąd**: ten konkretny feed ma 9 `service_id`
(3 warianty rozkładu × 3 okresy ważności skumulowane w jednym pliku obejmującym cały rok do
2026-12-31), ale tylko 1 (~9893 kursów, typowy dzień powszedni) jest aktywny na wybrany dzień.
Ostrzeżenie liczy proporcję względem WSZYSTKICH `service_id` w pliku, nie względem realnego
ruchu tego dnia. Liczba kursów sensowna, zgodna z release notes (283520 obserwacji dopasowanych
tego dnia).

### 2.4 Pytanie badawcze H6 i wynik

**H6: czy niższy dochód koreluje z gorszą dostępnością transportową do usług publicznych?**

`analyze_accessibility.py` — Pearson r, `income_index_pln` × liczba POI osiągalnych, per
kategoria × próg czasowy (poziom obwodów spisowych, n=3854):

| próg | education | health | culture | groceries | total |
|---|---|---|---|---|---|
| 15 min | −0,128 | −0,110 | +0,019 | −0,123 | **−0,117** |
| 30 min | +0,013 | +0,070 | +0,127 | −0,010 | +0,049 |
| 60 min | +0,128 | +0,109 | +0,110 | +0,127 | +0,120 |

**Nie potwierdzona — kierunek przy krótkim progu jest odwrotny do naiwnej hipotezy.** Przy
15 min biedniejsze obwody mają WIĘCEJ usług w zasięgu, nie mniej. Dodatkowo `%matek samotnych`
koreluje **dodatnio** z dostępnością (r=+0,38 przy 30 min, `analyze_accessibility.py`,
"Secondary").

**Dlaczego**: sprawdzone przez porównanie z odległością od centrum miasta
(`plot_correlations.py`, panel C) — odległość centroidu obwodu od centrum miasta koreluje z
dostępnością (total, 30 min) na poziomie **r=−0,71**, wielokrotnie silniej niż dochód
(r=+0,05...+0,12). Mapy (`map_accessibility_total_30min.png` vs `map_income_index.png`)
pokazują to samo wizualnie: dostępność jest niemal idealnie monocentryczna (promieniście
maleje od centrum), dochód nie ma takiego wzorca wcale.

**Wniosek badawczy**: w Łodzi deprywacja transportowa jest przede wszystkim **geograficzna**
(odległość od centrum/gęstość sieci tramwajowej), nie **ekonomiczna** — miasto posocjalistyczne
o zwartym historycznym centrum, gdzie biedniejsze/gęściej zaludnione dzielnice (stara zabudowa,
więcej gospodarstw jednorodzicielskich) leżą centralnie, tam gdzie transport jest najlepszy —
odwrotnie niż w typowym mieście zachodnim z peryferyjną biedą.

---

## Etap 3 — heksagony 500m i populacja z dostępem (2026-08-22, wieczór, na prośbę Michała)

### 3.1 Dlaczego heksagony

Obwody spisowe są mikroskopijne w centrum (gęsta zabudowa) i ogromne na granicy miasta (MAUP —
ten sam problem zdiagnozowany w Etapie 1.5). Jednolita siatka 500m eliminuje ten artefakt.

Metoda ze skilla `qgis-hex-atlas-map` (Kroki 1-2, **nie cała reszta skilla** — ten skill jest
napisany pod atlas wielomiastowy stop-headway, wykorzystano tylko konstrukcję siatki):
`native:creategrid` TYPE=4 (hex), spacing 500m, potem `native:extractbylocation` PREDICATE=
intersects względem dissolve `obwody_spisowe` — **whole-hex, nie clip** (każda komórka ma
dokładnie 6 wierzchołków, żadna nie jest przycięta na granicy). Granica miasta: dissolve
własnych `obwody_spisowe` (dokładniejsza niż OSM/QuickOSM, którego użyłby skill dla nowego
miasta bez własnych danych). Wynik: **1479 heksagonów**.

### 3.2 Ponowne przeliczenie r5r na heksagonach

`export_hex_origins.py` (centroidy heksagonów → origins) + `run_accessibility_hex.R` (identyczne
parametry co §2.3, **sieć reużyta z cache'u**, brak rebuildu) + `join_hex_results.py` (pivot +
join SES + zapis do `lodz_hex500.gpkg`).

**SES per heksagon**: agregacja przez centroid obwodu spisowego wpadający w heksagon
(`gpd.sjoin`, point-in-polygon), populacja-ważona średnia dochodu. **Ograniczenie**: tylko 646
z 1479 heksagonów ma dopasowany SES (peryferyjne heksagony leżą wewnątrz jednego ogromnego
obwodu, którego centroid wpada gdzie indziej) — do poprawy przez area-weighted overlay, jeśli
SES na heksagonach ma być użyty poważniej niż jako sprawdzian.

**Wynik H6 na heksagonach** (n=646, tylko heksagony z dopasowanym SES):

| próg | r (income vs total) |
|---|---|
| 15 min | −0,002 |
| 30 min | +0,147 |
| 45 min | +0,221 |
| 60 min | +0,277 |

Sygnał **wzmocnił się** względem obwodów (0,12→0,28 przy 60 min) — MAUP faktycznie tłumił
korelację, ale nawet po poprawce związek zostaje słaby-umiarkowany, nie silny. **Wniosek z
Etapu 1.5 (MAUP) potwierdzony empirycznie na drugim, niezależnym przykładzie.**

### 3.3 Druga metryka: populacja z dostępem pasywnym

Odróżniona od metryki podstawowej (liczba POI w zasięgu) — patrz `COLUMNS.md` (pełny opis
każdej kolumny, żeby dwóch metryk nie mylić). `compute_population_coverage.py`: obwód "ma
dostęp" (`has_access_{kategoria}_{próg}min = 1`) jeśli ma **choć jedną** placówkę danej
kategorii w zasięgu; populacja z dostępem = suma `population` po obwodach z flagą.

| kategoria | 15 min | 30 min | 45 min | 60 min |
|---|---|---|---|---|
| edukacja | 90,7% | 97,7% | 99,6% | 99,8% |
| zdrowie | 90,2% | 97,8% | 99,5% | 99,8% |
| kultura | 75,0% | 95,7% | 99,0% | 99,8% |
| sklepy | 86,4% | 97,3% | 99,5% | 99,8% |
| **dowolna** | **94,0%** | 98,7% | 99,7% | 99,8% |

Kultura najsłabiej pokryta w 15 min (biblioteki/domy kultury rzadsze niż szkoły/przychodnie).

### 3.4 Wizualizacja

`lodz_dostepnosc.qgz` — 3 warstwy: `dostepnosc_obwody`, `dostepnosc_hex500`, `poi_uslugi`.
Mapy PNG: `map_accessibility_total_30min.png` (obwody), `map_accessibility_hex500_total_
30min.png` (heksagony — wyraźnie czytelniejsza, czysty gradient bez szumu mikro-obwodów),
`map_income_index.png` (porównanie — brak wzorca promienistego).
Wykresy: `out/lodz_H6_correlation_bars.png`, `out/lodz_H6_income_scatter.png`,
`out/lodz_H6_distance_scatter.png` (styl `transit_charts`: samowyjaśniające tytuły, PNG+CSV).

---

## Wszystkie pytania badawcze zadane w tym badaniu — skrót

| # | Pytanie | Odpowiedź (skrót) |
|---|---|---|
| H1 | dochód ↔ % samotnych matek | **Tak**, spójnie we wszystkich 6 miastach |
| H2 | dochód ↔ wielkość gospodarstwa | Nie, niespójne |
| H3 | dochód ↔ liczba dzieci | Nie, bliskie zeru |
| H4 | %PiS ↔ liczba dzieci | Nie, w Warszawie odwrotnie |
| H5 | dochód ↔ % gosp. 1-osobowych | Częściowo, niespójnie |
| — | czy zmienne SES są sklastrowane przestrzennie? | Tak wszystkie, ale dochód/PiS znacznie silniej niż demografia (artefakt konstrukcyjny) |
| H6 | dochód ↔ dostępność transportowa do usług | **Nie** — odległość od centrum (r=−0,71) wyjaśnia dostępność wielokrotnie lepiej niż dochód (r=+0,05...+0,28 zależnie od progu/jednostki) |
| — | ile mieszkańców Łodzi ma dostęp do usług w zadanym czasie? | 94% w 15 min do dowolnej usługi, 98,7% w 30 min |
| — | czy MAUP (rozmiar jednostki przestrzennej) tłumi sygnał? | Tak — korelacja H6 rośnie 0,12→0,28 po przejściu z obwodów na jednolite heksagony 500m |

---

## Etap 4 — dostępność studencka: Metoda A/C, mapa dwuwymiarowa (2026-08-23)

Pełny opis: **`STUDENTS_ANALYSIS.md`** (ten plik ma tylko skrót). Zmiana grupy docelowej:
populacja 20-29 lat (proxy studentów, GUS NSP2021, `extract_age2029_generic.py`) zamiast całej
populacji; cel = 47 budynków akademickich PŁ/UŁ/UM (OSM, `fetch_universities.py`), nie usługi
publiczne. Dochód świadomie pominięty na tym etapie.

**Metoda A** (okno 06:00-22:00, 5 percentyli r5r — max na wywołanie): rozrzut dostępności w
ciągu dnia. Bezwzględny rozrzut (p5-p95, uwaga na znak — p95 to WOLNIEJSZE, nie wyższe wartości)
koreluje ujemnie z odległością od centrum (r=−0,50, bo centrum ma wyższą bazę), względny
(znormalizowany) dodatnio (r=+0,16, peryferie proporcjonalnie bardziej zmienne).

**Metoda C** (P50 vs P85 GTFS, replika Braga et al. 2026 *JTG* dla Fortalezy): zły dzień
(P85) obniża dostępność średnio o −32,7% (mediana −18,8%), silniej na peryferiach (r=−0,28
z odległością) — ten sam kierunek co w Fortalezie, słabszy efekt, spójne z inną strukturą
przestrzenną miasta (Łódź: biedni centralnie, nie na peryferiach — patrz Etap 2).

**Mapa dwuwymiarowa** (inspiracja Joshua Stevens, bivariate choropleth, zaadaptowana do
zmiennej kategorycznej × ilościowej): dominująca uczelnia (hue) × populacja 20-29 (odcień).
**Kluczowy wynik: 61% heksagonów z populacją studencką nie ma dostępu do żadnej z 3 uczelni
w 30 min** — 3 uczelnie mają rozłączne przestrzennie strefy wpływu.

**Wydajność r5r** (benchmark, `STUDENTS_ANALYSIS.md` §5): throughput 6-31 tys. wyszukiwań/s
zależnie od przebiegu; liczba origins × szerokość okna determinuje czas, liczba destinations
prawie nie ma znaczenia (RAPTOR liczy do wszystkich przystanków naraz per przeszukanie). Cały
pipeline (2 sieci, 5 przebiegów r5r) mieści się w kilku minutach na zwykłym laptopie.

## Co dalej / otwarte kwestie

- **REGON (miejsca pracy)** jako stretch goal — nieporuszone od czasu decyzji w Etapie 2 (brak
  bulk-downloadu, wymagałoby geokodowania). Nadal aktualne, jeśli badanie ma iść w stronę
  "job accessibility" a nie tylko usług publicznych.
- **Area-weighted SES na heksagonach** — obecnie tylko 646/1479 heksagonów ma dopasowany
  dochód (point-in-polygon, nie overlay ważony powierzchnią). Do poprawy, jeśli heksagony mają
  zastąpić obwody jako główna jednostka analizy, nie tylko wizualny sprawdzian.
- **Kumulatywna dostępność w oknie całodobowym** (metoda podobna do wcześniejszych analiz
  Michała w OTP: % okna czasowego, w którym trasa A→B mieści się w progu) — **nie jest tym, co
  liczy obecny pipeline r5r** (patrz sekcja "Jak działa `time_window` w r5r" niżej). Możliwe do
  zrobienia, ale wymaga innego podejścia niż `accessibility()` z jednym oknem/medianą — patrz
  wyjaśnienie i rekomendacja niżej.
- Kolejny etap zapowiedziany przez Michała: dalsze aspekty dostępności transportowej (nie
  sprecyzowane jeszcze co dokładnie).

---

## Jak działa `time_window`/percentyle w r5r — i czy da się policzyć jak w OTP

**Odpowiedź na wprost zadane pytanie: nie, "dostępne w 15 min" NIE znaczy "był choć jeden
moment w oknie 07:00-09:00, w którym dojazd zajął ≤15 min".** To by było `MAX`/`best-case` po
oknie — r5r liczy co innego, patrz niżej.

### Mechanika (potwierdzona w dokumentacji r5r/R5, nie zgadywana)

1. `departure_datetime` + `time_window` definiują okno odjazdu (u nas: 07:00 + 120 min =
   07:00–09:00). R5 **oblicza czas przejazdu osobno dla każdej minuty odjazdu w tym oknie** —
   przy `time_window=120` to ~120 osobnych obliczeń tras dla każdej pary origin×destination.
   Dla naszego GTFS (rozkład jawny, `stop_times.txt`, nie rozkład częstotliwościowy
   `frequencies.txt`) te obliczenia są **deterministyczne** — nie ma losowości Monte Carlo,
   każda minuta odjazdu daje konkretny, policzalny czas przejazdu wynikający wprost z rozkładu
   (najbliższy odjazd + czas jazdy + przesiadki).
2. Dla `accessibility()` z `decay_function="step"`: **na każdą z tych ~120 minut odjazdu**
   liczona jest osobna wartość dostępności (ile POI osiągalnych w progie X minut, licząc od
   TEJ KONKRETNEJ minuty odjazdu).
3. r5r **agreguje te ~120 wartości przez percentyl** (parametr `percentiles`, domyślnie **50**,
   czyli mediana — nieustawiony jawnie w naszych skryptach, więc użyty domyślny). Liczba, którą
   dostajemy w `lodz_accessibility.csv` (kolumna `percentile=50`), to **mediana dostępności po
   wszystkich minutach odjazdu w oknie** — czyli "typowa" dostępność dla pasażera wychodzącego
   o losowej minucie w porannym szczycie, nie najlepszy ani najgorszy przypadek.

### Dlaczego to nie jest to samo, co liczyłeś w OTP

Twoja metoda OTP: **cumulative window coverage** — dla ustalonego progu (np. 30 min), jaki
**% całego okna** (np. 12h, 6:00-22:00) daje dojazd A→B w tym progu. To jest w istocie **CDF
czasu przejazdu po oknie**, z której czytasz konkretny punkt (frakcja poniżej progu).

r5r z `accessibility()` daje **jeden punkt** tej krzywej (percentyl 50. domyślnie) dla **jednego,
z góry wybranego** okna (u nas 2h szczytu) — nie całą krzywą, i nie frakcję poniżej progu.
To są **różne wielkości**: Twoja metoda odpowiada na "jaka część dnia jest dla mnie dobra?",
metoda domyślna r5r odpowiada na "jak wygląda typowa podróż w tym oknie?".

### Czy da się zrobić coś podobnego jak w OTP w r5r — tak, dwa sposoby

**Sposób A (przybliżenie, tanie)**: `accessibility()`/`travel_time_matrix()` w r5r przyjmują
parametr `percentiles` — **maksymalnie 5 wartości na wywołanie** (ograniczenie R5, nie r5r).
Wywołanie z `percentiles=c(5,25,50,75,95)` na oknie 6:00-22:00 (960 min) da 5 punktów krzywej
CDF czasu przejazdu — z tego można **przybliżyć** (interpolacją) "jaki % okna daje dojazd
≤30 min", ale nie dokładnie — 5 punktów to gruba siatka.

**Sposób B (dokładna replikacja Twojej metody OTP, droższe obliczeniowo)**: pętla po
minutach/krokach odjazdu w R (np. co 5-10 min od 6:00 do 22:00 = 96-192 osobnych wywołań
`travel_time_matrix()` z `time_window=1` każde), zapisanie czasu przejazdu dla każdego kroku,
a potem w Pythonie/R policzenie **dokładnej frakcji kroków, w których czas ≤ próg** — to jest
dosłownie ta sama metoda co w OTP, tylko realizowana przez wiele małych wywołań zamiast
jednego dużego okna. Koszt: 96-192× więcej wywołań silnika niż obecny pojedynczy przebieg
(który zajął ~15-30s dla 1-2 tys. origins) — realnie kilkadziesiąt minut do godziny dla całego
miasta na heksagonach, ale wykonalne z reużytym cache sieci (`network_data/network.dat`, bez
rebuildu). Nieuruchomione w tej sesji — czeka na decyzję, czy to ma być kolejny krok badania.

**Rekomendacja**: jeśli celem kolejnego etapu jest metryka "niezawodności dostępu w ciągu
całego dnia" (a nie tylko szczytu porannego), Sposób B jest właściwym podejściem — daje
dokładnie tę samą interpretowalność, co Twoje wcześniejsze analizy OTP, kosztem czasu obliczeń.
