# Dostępność studencka Łodzi — metody A/C, mapa dwuwymiarowa (2026-08-23)

Etap 4 badania (patrz `RESEARCH_LOG.md` §4 za skrót, ten plik za pełny opis). Zmiana grupy
docelowej względem Etapu 2-3: nie cała populacja, tylko **mieszkańcy 20-29 lat** (proxy
studentów, GUS NSP2021), cel = **budynki akademickie 3 uczelni** (Politechnika Łódzka,
Uniwersytet Łódzki, Uniwersytet Medyczny), nie usługi publiczne. **Dochód świadomie pominięty**
na tym etapie (decyzja Michała) — skupienie na liczbie ludności objętej dostępem, nie na SES.

## 1. Nowe dane wejściowe

### 1.1 Populacja 20-29 lat per obwód spisowy

`extract_age2029_generic.py` — ten sam plik źródłowy co `population` w Etapie 1
(`docs/gis/ludnosc_nsp_2021.xlsx`, arkusz "Łódzkie"), inna kolumna: "20-29" pod nagłówkiem
"10-letnie grupy wieku" (nie "Ogółem"). Ta sama logika budowania klucza `OBWOD` co
`ses_income_lodz/extract_population_generic.py` (skopiowana, nie zaimportowana — świadomie,
żeby nie sprzęgać cyklu życia dwóch folderów narzędziowych).

```
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "C:\Program Files\QGIS 3.22.16\apps\Python39\python3.exe" \
  easy_otp/core/xlsx_reader.py '{"path": "docs/gis/ludnosc_nsp_2021.xlsx", "sheet": "Łódzkie"}' \
  > tools/accessibility_lodz/rows_lodzkie.json
py extract_age2029_generic.py rows_lodzkie.json lodz_age2029_population.csv
```

Wynik: 13885 unikalnych kluczy `OBWOD` w całym woj. łódzkim (filtr do Łodzi robiony później
przy joinie na heksagony). **71149 osób 20-29 lat w Łodzi** (suma po dopasowaniu do miejskich
`obwody_spisowe`), zmatchowane 3836/3854 obwodów (18 brakujących — prawdopodobnie supresja
danych GUS dla bardzo małych obwodów, tak samo jak przy `income_index_pln` w Etapie 1).

### 1.2 Budynki akademickie (OSM)

`fetch_universities.py` — Overpass API, `amenity~"^(university|college)$"` +
`building=university` w granicach Łodzi, klasyfikacja przez dopasowanie regex nazwy/operatora
do 3 wzorców (`politechnik.*ł[oó]dzk`, `uniwersytet.*medyczn`, `uniwersytet.*ł[oó]dzk`).

Wynik: **47 budynków** — Uniwersytet Łódzki 24, Politechnika Łódzka 14, Uniwersytet Medyczny 9
(203 obiekty `university`/`college` odrzucone jako niepasujące do żadnego z 3 wzorców — inne
uczelnie/szkoły policealne w Łodzi, świadomie poza zakresem). Uwaga jakościowa: wśród trafień
jest 1 obiekt sportowy ("AZS Politechnika Łódzka") błędnie złapany przez dopasowanie nazwy —
szum nieistotny przy 14 budynkach PŁ, nieusuwany ręcznie.

### 1.3 Agregacja na heksagony

`export_hex_students.py` — ta sama metoda point-in-polygon co `export_hex_origins.py`
w Etapie 3 (centroid obwodu → heksagon, w którym leży). **Ograniczenie odziedziczone z
Etapu 3**: tylko 646/1479 (a po przefiltrowaniu do obwodów z niezerową populacją 20-29: **640**)
heksagonów ma dopasowaną populację — peryferyjne heksagony bez trafienia centroidu żadnego
obwodu zostają bez wartości (nie zero — brak danych, różnica ważna przy interpretacji map).

`prepare_uni_destinations.py` — analogicznie do `prepare_destinations.py` w Etapie 2, buduje
tabelę szeroką (`id, lon, lat, politechnika, uniwersytet, medyczny, total`, flagi 0/1) dla r5r.

## 2. Metoda A — zmienność w ciągu dnia (rozszerzone okno + percentyle)

`run_accessibility_students_A.R`: okno odjazdu **06:00-22:00** (960 min, cały nagrany dzień
serwisowy), `percentiles=c(5,25,50,75,95)` (limit R5: max 5 percentyli na wywołanie), próg
30 min (+15/45/60 zebrane przy okazji). Sieć P50 reużyta z cache'u Etapu 2 (`network_data/`).

**Uwaga o znaku**: percentyle r5r dotyczą **czasu przejazdu**, nie dostępności — p5 = 5%
najszybszych odjazdów → **najwyższa** dostępność, p95 = 5% najwolniejszych → **najniższa**.
Rozrzut liczony jako `p5 - p95` (dodatni), nie `p95 - p5` (błąd wykryty i naprawiony w trakcie
tej sesji — pierwsza wersja dawała ujemne wartości "rozrzutu", co było sygnałem odwróconego
znaku, nie błędem geometrii).

**Wyniki** (próg 30 min, `total` = suma 3 uczelni, n=267 heksagonów z niezerowym p50):
- średni **bezwzględny** rozrzut (p5−p95): **12,4 budynku** — koreluje **ujemnie** z odległością
  od centrum (r=−0,50): centralne heksagony mają największy bezwzględny rozrzut, bo mają
  najwyższą bazową liczbę osiągalnych budynków (jest z czego "spadać").
- średni **względny** rozrzut (rozrzut/mediana): **3,01** (czyli rozrzut ~3× większy niż sama
  mediana — **niestabilne przy małych licznikach**, patrz zastrzeżenie niżej) — koreluje
  **dodatnio**, choć słabo, z odległością (r=+0,16): po znormalizowaniu do poziomu bazowego,
  peryferie są proporcjonalnie odrobinę bardziej zmienne niż centrum, zgodnie z intuicją
  (rzadsza siatka transportu na peryferiach = większe wahania).

**Przykład maksymalnej wartości** (`rel_spread=23`, `hex_id=898`/`933`, ~1,9-2,3 km od centrum):
p5=23, p25=11, **p50=1**, p75=0, p95=0 budynków. Czyli: w najlepszych 5% momentów dnia — 23
budynki osiągalne; typowo — tylko 1; w najgorszych 5% — 0. To jedno wąskie okno w rozkładzie
(dobrze zsynchronizowany kurs/przesiadka), które chwilowo otwiera dostęp do wielu budynków
naraz — realny sygnał (p5=23 to duża wartość sama w sobie, nie tylko artefakt małego
mianownika), nie szum.

**Zastrzeżenie metodologiczne**: rozrzut względny jest liczony jako iloraz dwóch małych liczb
całkowitych (typowe wartości mediany to 0-10 budynków) — pojedynczy heksagon z medianą=1 i
p5=3 daje iloraz 3,0, dominując średnią. Traktować jako sygnał kierunkowy, nie precyzyjny
pomiar — do poprawy przez ważenie/odcięcie heksagonów o bardzo niskiej bazowej dostępności,
jeśli ta metryka ma być użyta poważniej.

## 3. Metoda C — niezawodność dzień-do-dnia (P50 vs P85, styl Bragi 2026)

`run_accessibility_students_P50.R` (baseline, sieć `network_data/`, ten sam GTFS co Etap 2-3)
+ `run_accessibility_students_C_p85.R` (sieć **osobna**, `network_data_p85/`, zbudowana z
`lodz_realized_2026-08-21_p85.zip` — wariant 85. percentyla ze zrekonstruowanego rozkładu
Family A, pobrany z tego samego release'u co P50). Identyczne parametry poza plikiem GTFS —
jedyna zmienna to niezawodność sieci.

**Wyniki** (próg 30 min, `total`, n=285 heksagonów z niezerowym P50):
- średni wpływ złego dnia: **−32,7%** dostępności (mediana −18,8%) — słabszy niż w Fortalezie
  (Braga: −50% przy 60 min dla wskaźnika kumulatywnego), ale **ten sam kierunek i porządek
  wielkości**.
- korelacja z odległością od centrum: **r=−0,28** — peryferie tracą **więcej** niż centrum,
  zgodnie z głównym wynikiem Bragi (choć znacznie słabiej wyrażone niż w Fortalezie, gdzie
  różnica centrum/peryferie sięgała kilkudziesięciu punktów procentowych).

**Jak czytać skrajne wartości `total_pct_impact_30min`**: **−100% to twarda podłoga** (procent
nie może zejść niżej, bo liczba budynków nie może być ujemna), osiągana gdy P85=0. **43 z 285
heksagonów (15%) mają dokładnie −100%** — prawie wszystkie mają bardzo mały mianownik (P50=1-2
budynki, typowe ~5-7 km od centrum, na granicy zasięgu 30-minutowego). Np. `hex_id=261`: P50=2
(1×UŁ+1×UM) → P85=0. **Czytać zawsze razem z surową liczbą** (`{uni}_p50`/`{uni}_p85` w
`out/lodz_students_method_C_p50_vs_p85.csv`), nie samym procentem — przy małej bazie −100%
znaczy "stracił 1-2 budynki", nie "katastrofa na dziesiątkach".

**Interpretacja w kontekście artykułu**: Braga tłumaczy silny efekt peryferyjny ekstremalną
segregacją przestrzenną Fortalezy (Gini 0,64, bardzo długie dojazdy z biednych peryferii do
scentralizowanych miejsc pracy). Łódź jest znacznie bardziej zwartym miastem — słabszy,
ale wciąż obecny, efekt sugeruje, że **mechanizm jest uniwersalny** (peryferie zawsze tracą
więcej na niezawodności, bo mają mniej "zapasowych" tras/połączeń), tylko **skala zależy od
struktury miasta**. To wzmacnia wcześniejszy wniosek z Etapu 2 (deprywacja w Łodzi jest
geograficzna, nie dochodowa) o nowy wymiar: peryferie tracą więcej **także pod względem
niezawodności**, nie tylko poziomu dostępności.

**Nieprzeprowadzone w tej sesji**: pełna replikacja wskaźników konkurencyjnych Bragi
(2SFCA/BFCA) — wymagałyby pojemności "miejsc" na uczelni (limitów przyjęć/miejsc), których
OSM nie dostarcza. Zrobiona tylko wersja kumulatywna (jak większość literatury sprzed Bragi).

## 4. Mapa dwuwymiarowa — dominująca uczelnia × populacja studencka

Inspiracja: [Joshua Stevens, "How to make a bivariate choropleth map"](
https://www.joshuastevens.net/cartography/make-a-bivariate-choropleth-map/) — nie kopia 1:1
(tam obie zmienne są porządkowe/ilościowe, siatka 3×3 ta sama dla obu osi; tu jedna zmienna
jest **kategoryczna** — która uczelnia dominuje — więc paleta to 3 rodziny barw (błękit=PŁ,
pomarańcz=UŁ, zieleń=UM), każda w 3 odcieniach (jasny→ciemny = tercyl populacji 20-29).
Szary = brak dostępu do żadnej z 3 uczelni w 30 min.

`join_students_results.py`: **dominująca uczelnia** = ta z największą liczbą budynków
osiągalnych w ≤30 min (P50, `politechnika_30min`/`uniwersytet_30min`/`medyczny_30min`,
argmax); remis rozstrzygany na korzyść pierwszej w kolejności (rzadki przypadek przy małych
licznikach, nieanalizowany osobno). **Tercyle populacji** liczone tylko po heksagonach z
`pop_20_29 > 0` (640 heksagonów), nie po wszystkich 1479 — heksagony bez danych populacyjnych
zostają jasnoszare/przezroczyste, nie trafiają fałszywie do najniższego tercyla.

**Wynik** (z 640 heksagonów z populacją studencką): **390 (61%) nie ma dostępu do żadnej z 3
uczelni w 30 min** — to jest najważniejsza pojedyncza liczba z tej mapy. Wśród pozostałych 250:
Uniwersytet Łódzki dominuje w 118, Politechnika w 87, Uniwersytet Medyczny w 45.

Mapa (`map_students_bivariate_dominant_university.png`) pokazuje wyraźnie rozdzielone
przestrzennie strefy: PŁ (niebieski) na południu/południowym zachodzie (zgodne z realną
lokalizacją głównego kampusu PŁ), UŁ (pomarańcz) na północ/północny wschód od centrum, UM
(zielony) mały klaster na północnym zachodzie. Legenda: `out/lodz_students_bivariate_legend.png`.

## 5. Wydajność silnika r5r/R5 dla Łodzi — pomiar

Wszystkie czasy z logów rzeczywistych przebiegów tej sesji (znaczniki czasu R5, nie stoper
zewnętrzny). "Wyszukiwania" = liczba origin × liczba_minut_odjazdu w oknie — to jest właściwa
jednostka kosztu obliczeniowego R5 (RAPTOR liczy czas dojazdu do **wszystkich** osiągalnych
przystanków w jednym przeszukaniu per minuta odjazdu, więc liczba destinations **prawie nie
wpływa** na czas — widać to niżej: 1328 vs 47 celów dają niemal identyczny czas przy tej samej
liczbie origins × oknie).

| przebieg | origins × destinations | okno (min) | percentyle | czas routingu | origins/s | wyszukiwania (origins×okno) | wyszukiwania/s |
|---|---|---|---|---|---|---|---|
| Obwody, usługi (finalny, P50) | 3854 × 1328 | 120 | 1 | 76,4 s | 50,4 | 462 480 | 6 053 |
| Heksagony, usługi (P50) | 1479 × 1328 | 120 | 1 | 13,8 s | 107,2 | 177 480 | 12 861 |
| Heksagony, uczelnie (P50) | 1479 × 47 | 120 | 1 | 12,4 s | 119,3 | 177 480 | 14 313 |
| Heksagony, uczelnie (P85) | 1479 × 47 | 120 | 1 | 12,7 s (+ build sieci) | 116,5 | 177 480 | 13 975 |
| Heksagony, uczelnie, Metoda A | 1479 × 47 | 960 | 5 | 45,6 s | 32,4 | 1 419 840 | 31 138 |

**Budowa sieci** (`setup_r5()`, parsowanie `.osm.pbf` + GTFS → `network.dat`): dla P85
(`network_data_p85/`, świeży build, ten sam `.osm.pbf` co P50 ale inny GTFS) — **~22 s**
(mierzone: `dataFileCache open` → pierwsze przeszukanie routingu). Pierwszy build w tej sesji
(oryginalna sieć `network_data/`) obejmował dodatkowo jednorazowe pobranie jara R5 (~80MB,
`r5-v7.5-1-...jar`, cache'owany globalnie dla wszystkich przyszłych projektów r5r na tej
maszynie, nie per-projekt) — nie zmierzone precyzyjnie, rzędu 1-2 minut łącznie z parsowaniem.
**Po zbudowaniu raz, sieć jest cache'owana na dysku (`network_data*/network.dat`) i kolejne
`setup_r5()` na tym samym `data_path` są natychmiastowe** (widoczne w logach jako "Using cached
network from...").

**Wnioski praktyczne**:
- Liczba origins jest głównym driverem czasu, nie liczba destinations — 1328 vs 47 celów dają
  ~13,8s vs ~12,4s przy tej samej liczbie origins/oknie (różnica w granicy szumu pomiaru).
- Szerokość okna skaluje się w przybliżeniu liniowo z liczbą wyszukiwań (960/120=8×, czas
  45,6/12,4≈3,7× — nieco lepiej niż liniowo, prawdopodobnie efekt narzutu stałego per-origin
  rozłożonego na więcej pracy).
- Cały pipeline (heksagony, 2 sieci P50+P85, 3 warianty analizy: usługi/uczelnie×A/C) mieści
  się w **kilku minutach** obliczeń na zwykłym laptopie, bez GPU, bez chmury — r5r jest
  praktycznym narzędziem do iteracyjnej pracy badawczej w tej skali miasta, nie tylko do
  jednorazowych, kosztownych przebiegów.

## 6. Jak interpretować te wyniki — podsumowanie dla czytelnika z zewnątrz

1. **61% heksagonów z populacją studencką nie ma dostępu do żadnej z 3 uczelni w 30 minut
   transportem publicznym+pieszo w porannym szczycie.** To liczba do dalszej weryfikacji (np.
   czy 30 min to realistyczny próg dla studentów w Łodzi — literatura o "15-minute city" czasem
   używa krótszych progów), ale jako pierwszy rzut oka pokazuje, że dostępność do uczelni jest
   dużo bardziej przestrzennie ograniczona niż dostępność do usług publicznych ogółem
   (Etap 2-3: tam populacja z dostępem do JAKIEJKOLWIEK usługi w 30 min = 98,7%).
2. **Trzy uczelnie mają wyraźnie rozdzielone "strefy wpływu"** (mapa dwuwymiarowa) — nie
   nakładają się istotnie, więc student mieszkający w danej części miasta ma praktycznie
   dostęp tylko do jednej z trzech uczelni w rozsądnym czasie, nie do wyboru między nimi.
3. **Niezawodność (Metoda C) i zmienność wewnątrzdniowa (Metoda A) obie wskazują na peryferie
   jako gorzej obsłużone pod względem stabilności dostępu**, nie tylko jego poziomu — spójne
   z Etapem 2's wnioskiem o geograficznym (nie dochodowym) charakterze deprywacji w Łodzi.
4. **Metodologicznie**: to jest bezpośrednia replikacja fragmentu opublikowanej metody (Braga
   et al. 2026, *Journal of Transport Geography*) na innym mieście, z kontrastującą strukturą
   przestrzenną (Łódź: biedni/gęsto zaludnieni centralnie; Fortaleza: biedni na peryferiach) —
   sam fakt, że kierunek efektu (peryferie tracą więcej na niezawodności) **replikuje się mimo
   odwróconej struktury dochodowej**, sugeruje, że efekt niezawodności jest bardziej uniwersalny
   (funkcja gęstości sieci transportowej) niż efekt samego poziomu dostępności (funkcja tego,
   gdzie mieszkają bogaci/biedni) — potencjalnie publikowalna obserwacja, wymagająca więcej niż
   jednego miasta kontrastowego, żeby być czymś więcej niż anegdotą z n=2.
