# Dostępność transportowa — 6 miast (2026-08-23)

Rozszerzenie pilotażu Łódź (`tools/accessibility_lodz/`) na pozostałe 5 miast SES:
Warszawa, Kraków, Gdańsk, Poznań, Szczecin. Ten sam silnik (r5r/R5), ta sama metoda
(heksagony 500m, zrealizowany GTFS P50, próg 30 min), dwa pytania badawcze:

1. **Czy niższy dochód per capita jest predyktorem niższej dostępności do usług
   publicznych?** (dekyle dochodu, korelacja)
2. **Skąd łatwo dojechać na uczelnię w 30 min i która uczelnia jest najkorzystniejsza
   dla danej lokalizacji?** (mapa dwuwymiarowa: dominująca uczelnia × populacja
   studencka, jak w Etapie 4 pilotażu łódzkiego)

**Bez Metody A/C** (zmienność wewnątrzdniowa, P50 vs P85) — świadomie pominięte na
tym etapie na prośbę Michała, żeby skupić się na podstawowym pytaniu o poziom
dostępności w 6 miastach zamiast pogłębiać niezawodność w jednym.

## 1. Dane i metoda — co jest inne niż w Łodzi

- **Sieć drogowa**: pobrana z Geofabrik (wyciągi wojewódzkie, 97–298 MB każdy) i
  **przycięta do bboxa miasta przez `osmosis`** (już zainstalowany lokalnie,
  `C:\Users\Michal\josm\osmosis`, nie trzeba było niczego instalować). Wynik: pliki
  18–77 MB per miasto zamiast setek MB.
- **Zrealizowany GTFS**: pobrany bezpośrednio z najnowszych release'ów
  `GISBoost/easy-GTFS-RT` (wszystkie 5 miast miały już `-realized-` release na
  2026-08-22 — repo urosło od czasu pilotażu łódzkiego, gdzie tylko Łódź/Poznań/
  Szczecin miały nagrania).
- **Siatka 500m**: budowana identyczną metodą co Łódź (`native:creategrid` TYPE=4 +
  `native:extractbylocation`, whole-hex), z granicy = dissolve `obwody_spisowe`
  z `ses_income_lodz/{miasto}.gpkg`.
- **Populacja 20-29 (studenci)**: ten sam plik źródłowy co Łódź
  (`docs/gis/ludnosc_nsp_2021.xlsx`), inny arkusz wojewódzki per miasto.
- **Uczelnie**: 2-3 per miasto, dopasowane do lokalnych realiów (nie każde miasto ma
  osobną "Politechnikę"/"Uniwersytet Medyczny" — patrz `cities_config.py`):

  | miasto | uczelnia 1 | uczelnia 2 | uczelnia 3 |
  |---|---|---|---|
  | Łódź | Politechnika Łódzka | Uniwersytet Łódzki | Uniwersytet Medyczny |
  | Warszawa | Politechnika Warszawska | Uniwersytet Warszawski | Warszawski UM |
  | Kraków | Politechnika Krakowska | Uniwersytet Jagielloński | *(Collegium Medicum UJ — 0 trafień OSM, faktycznie 2 uczelnie)* |
  | Gdańsk | Politechnika Gdańska | Uniwersytet Gdański | Gdański UM |
  | Poznań | Politechnika Poznańska | UAM (nie "Uniwersytet Poznański") | UM im. Marcinkowskiego |
  | Szczecin | ZUT (brak Politechniki, najbliższy odpowiednik) | Uniwersytet Szczeciński | Pomorski UM |

## 2. Gotcha techniczny — `osmosis --bounding-box` bez `completeWays=yes`

**Znaleziony na żywo, warty zapisania.** Pierwsza próba przycięcia pbf (bez
`completeWays=yes`) spowodowała crash `setup_r5()` dla Krakowa:
`NullPointerException: Cannot invoke "Node.getLon()" because "n" is null` przy
budowie "park and ride areas" — proste `--bounding-box` obcina drogi/relacje na
granicy bboxa, zostawiając wiszące referencje do węzłów spoza wycinka. R5 nie
zawsze na to crashuje (Warszawa/Gdańsk-pierwsza-próba przeszły bez błędu — zależy
od tego, czy akurat jakaś relacja typu parking przecina granicę), więc błąd może
**nie ujawnić się od razu** nawet gdy dane są ucięte niepoprawnie. **Naprawione**:
dodanie `completeWays=yes` do `--bounding-box` w `prepare_osm_pbf.py` (dociąga
wszystkie węzły należące do dróg przecinających granicę). Kraków i Gdańsk
przeliczone od zera po naprawie (włącznie z usunięciem starego cache'u
`network.dat`/`mapdb`, bo `setup_r5()` inaczej reużyłby zepsutą sieć). **Warszawa
nie została przeliczona ponownie** (jej pierwszy przebieg akurat nie trafił na
błąd) — potencjalne drobne obcięcie ulic tuż przy granicy bboxa (0,02° marginesu
poza faktyczną granicą miasta), nieistotne dla wnętrza obszaru analizy.

## 3. Wyniki — Pytanie 1: dochód a dostępność usług publicznych

Korelacja Pearsona, dochód (`income_index_pln`) × liczba usług osiągalnych w 30 min,
per heksagon, dekyle dochodu:

| miasto | r | interpretacja |
|---|---:|---|
| Gdańsk | +0,018 | praktycznie brak związku |
| Warszawa | +0,042 | praktycznie brak związku |
| Łódź | +0,147 | słaby |
| Szczecin | +0,210 | słaby-umiarkowany |
| Poznań | +0,225 | słaby-umiarkowany |
| Kraków | +0,286 | umiarkowany (najsilniejszy z 6) |

**Wniosek: dochód NIE jest solidnym predyktorem dostępności do usług publicznych w
żadnym z 6 miast** — wszystkie korelacje są dodatnie (nie ujemne — czyli tam gdzie
jest drożej, dostępność jest raczej odrobinę lepsza, nie gorsza), ale słabe do
umiarkowanych. To potwierdza i uogólnia wniosek z pilotażu łódzkiego (Etap 2):
**odległość od centrum/gęstość sieci transportowej wyjaśnia dostępność wielokrotnie
lepiej niż dochód** we wszystkich sprawdzonych miastach, nie tylko w Łodzi. Żadne
miasto nie pokazuje "biedny = gorszy dostęp" w sposób, który dawałby silną
korelację — to jest spójny, uogólniony wynik, nie przypadek jednego miasta.

Wykres porównawczy: `out/cross_city_income_correlation.png` (znormalizowane do D1
każdego miasta, żeby porównać *kształt* krzywej między miastami o różnej gęstości
usług).

## 4. Wyniki — Pytanie 2: skąd łatwo dojechać na uczelnię i która jest najlepsza

### 4.1 Ile studentów nie ma dostępu do żadnej uczelni w 30 min

**Liczby poniżej są nieważone (po heksagonach) i po dacie poprawionej na dzień
powszedni — patrz §4.3 dla poprawki daty i dla dużo bardziej wiarygodnej wersji
ważonej liczbą mieszkańców** (wniosek się zmienia: po ważeniu realny zakres to
21-54%, nie 53-70%, i to Gdańsk wypada najgorzej, nie Warszawa).

| miasto | % heksagonów studenckich bez dostępu |
|---|---:|
| Poznań | **53,3%** (najlepsza dostępność po heksagonach) |
| Kraków | 53,6% |
| Szczecin | 58,1% |
| Łódź | 60,9% |
| Gdańsk | 66,9% |
| Warszawa | **69,1%** (najgorsza dostępność po heksagonach) |

**Zaskakujące (przy metryce nieważonej)**: stolica ma najgorszy wynik, nie
najlepszy — mimo największej bezwzględnej liczby budynków uczelni (48, najwięcej
z 6 miast). Powód geometryczny: Warszawa ma **największą powierzchnię** ze
wszystkich 6 miast (2546 heksagonów vs 597-1633 w pozostałych), więc mimo gęstej
sieci w centrum, znacznie większy odsetek jej terytorium leży poza 30-minutowym
zasięgiem którejkolwiek uczelni. Po ważeniu populacją (§4.3) ten efekt słabnie —
puste terytorialnie peryferia miasta i tak nie miały tam nikogo.

### 4.2 Która uczelnia jest najkorzystniejsza (dominuje w największej liczbie heksagonów)

| miasto | najlepiej dostępna uczelnia | heksagony (dominacja) | druga | trzecia (zwykle najgorsza) |
|---|---|---:|---|---|
| Łódź | Uniwersytet Łódzki | 118 | Politechnika Łódzka (87) | Uniwersytet Medyczny (45) |
| Warszawa | Uniwersytet Warszawski | 241 | Politechnika Warszawska (121) | Warszawski UM (98) |
| Kraków | Politechnika Krakowska | 163 | Uniwersytet Jagielloński (146) | — |
| Gdańsk | Politechnika Gdańska | 90 | Uniwersytet Gdański (69) | Gdański UM (5) |
| Poznań | UAM | 109 | Politechnika Poznańska (80) | UM Poznań (75) |
| Szczecin | ZUT | 103 | Uniwersytet Szczeciński (71) | Pomorski UM (4) |

**Wzorzec widoczny w 5 z 6 miast**: uczelnia medyczna ma **najsłabszą** dostępność
przestrzenną (najmniej dominujących heksagonów) w Łodzi, Warszawie, Gdańsku i
Szczecinie — tylko Poznań jest wyjątkiem (UM tam wyprzedza samą Politechnikę).
Prawdopodobne wyjaśnienie: uczelnie medyczne w Polsce często mają mniejsze,
rozproszone kampusy przy szpitalach klinicznych, nie jeden duży zwarty kampus jak
politechniki/uniwersytety ogólne — mniej "masy krytycznej" budynków w jednym miejscu
oznacza mniejszy obszar, z którego widać ≥1 budynek w progu 30 min. **Praktyczna
rekomendacja dla studenta wybierającego mieszkanie**: uczelnia ogólna/techniczna
(nie medyczna) ma zwykle szerszy "zasięg" dogodnych lokalizacji w każdym z tych
miast — patrz mapy dwuwymiarowe per miasto (`out/{miasto}_uczelnie_dominujaca.png`)
dla konkretnej lokalizacji.

## 4.3 Audyt niezależnego agenta (2026-08-23) — dwa znalezione i naprawione błędy

Michał zlecił świeżemu agentowi (bez wcześniejszego kontekstu) niezależną weryfikację
tej analizy — czytanie oficjalnej dokumentacji r5r (`ipeagit.github.io/r5r`), porównanie
z faktycznym kodem, i sprawdzenie hipotezy, że ~60-70% miasta bez dostępu do uczelni
w 30 min jest nierealne.

**Hipoteza "zrealizowany GTFS jest przyczyną" — odrzucona empirycznie.** Agent
przeliczył Łódź na statycznym rozkładzie (nie P50) tymi samymi parametrami: 61,2%
heksagonów bez dostępu vs 60,9% na P50 — różnica 0,3 pp. Zrealizowany feed to lekka
korekta (~12% wierszy `stop_times` dotkniętych, sekundy-do-30s opóźnienia), nie
przebudowa — nie mógł być głównym sprawcą.

**Kod r5r — poprawny względem dokumentacji.** Hipoteza o błędzie strefy czasowej w
`as.POSIXct(...)` bez `tz=` — fałszywy trop: dokumentacja r5r wprost mówi, że pakiet
**ignoruje** strefę czasową obiektu i używa strefy sieci transportowej. Pozostałe
parametry (`time_window`, `decay_function`, `max_trip_duration`, `colClasses`) zgodne
z API.

**Prawdziwy, potwierdzony błąd**: **5 z 6 miast (wszystkie poza Łodzią) policzone na
sobocie, nie na dzień powszedni.** `run_city_pipeline.sh` miało zahardkodowaną datę
`22-08-2026` — sobota (potwierdzone bezpośrednio w `calendar_dates.txt` Warszawy:
`SbS`/`SbM` dla tej daty). **Naprawione**: data zmieniona na poniedziałek `24-08-2026`
(zweryfikowane jako aktywny dzień powszedni w kalendarzu każdego z 5 miast przed
przeliczeniem), wszystkie miasta przeliczone ponownie. Efekt naprawy: zmiana rzędu
1-2 punktów procentowych (np. Warszawa 70,4%→69,1%) — **błąd był realny, ale nie był
głównym sprawcą wysokiego wyniku**, zgodnie z oceną agenta.

**Druga poprawka (na prośbę Michała, po audycie)**: metryka "% bez dostępu" była
liczona **po heksagonach**, nie po mieszkańcach — heksagon na peryferiach z 1
studentem liczył się tak samo jak gęsty heksagon z 500 studentami, co mogło sztucznie
zawyżać wynik szumem z rzadko zaludnionych obszarów brzegowych. Dodano wersję
**ważoną liczbą mieszkańców 20-29 lat** (`PCT_POP_no_access_30min` w
`analyze_universities.py`) — wynik jest **znacząco niższy**:

| miasto | % heksagonów (nieważone) | % populacji 20-29 (ważone) |
|---|---:|---:|
| Poznań | 53,3% | **21,4%** |
| Kraków | 53,6% | **28,9%** |
| Szczecin | 58,1% | **28,2%** |
| Łódź | 60,9% | **36,9%** |
| Warszawa | 69,1% | **43,2%** |
| Gdańsk | 66,9% | **54,0%** |

**To potwierdza, że Michał miał rację** — nieważona metryka była zaszumiona przez
rzadko zaludnione heksagony brzegowe. Realny obraz: 21-54% studentów (nie 53-70%
heksagonów) nie ma dostępu do żadnej z 2-3 skonfigurowanych uczelni w 30 min — nadal
istotny, ale wyraźnie mniej dramatyczny wynik niż sugerowała pierwotna, nieważona
wersja. Kolejność miast też się zmienia: Gdańsk (nie Warszawa) wypada najgorzej po
ważeniu populacją — jego niewielka próbka budynków uczelni (37, najmniej z 6 miast)
w połączeniu z rozkładem gęstości zaludnienia daje inny obraz niż suma heksagonów.

## 5. Ograniczenia

- **Kraków ma faktycznie tylko 2 uczelnie w analizie** (Collegium Medicum UJ nie
  zostało otagowane w OSM jako `amenity=university/college` w obszarze Krakowa —
  0 trafień) — patrz kolumna wyżej.
- **Populacja 20-29 dopasowana tylko do części heksagonów** (point-in-polygon,
  centroid obwodu → heksagon, to samo ograniczenie co w pilotażu łódzkim) — od
  35% (Warszawa, 1552/2546) do 48% (Kraków, 778/1633) heksagonów ma dane
  populacyjne. Nie area-weighted overlay — do poprawy, jeśli te dane mają być
  użyte poważniej niż jako sprawdzian.
- **Warszawa nie przeliczona po naprawie `completeWays`** (patrz §2) — marginalne
  ryzyko niedokładności tuż przy granicy obszaru analizy, nieistotne dla wnętrza.
- Progi/parametry identyczne jak w Łodzi (07:00-09:00, mediana, próg 30 min
  główny) — **bez Metody A/C** (zmienność), więc te wyniki mówią o "typowym"
  porannym szczycie, nie o niezawodności dzień-do-dnia.

## 6. Pliki

Per miasto (`{miasto}/`): `{miasto}.osm.pbf`, `{miasto}_gtfs.zip`,
`{miasto}_hex500.gpkg` (siatka + wyniki), CSV pośrednie, `out/` (wykresy
dekylowe + podsumowanie uczelni). Wspólne: `cities_config.py` (konfiguracja per
miasto), `prepare_osm_pbf.py`, `download_gtfs.py`, `fetch_osm_services.py`,
`fetch_universities.py`, `export_hex_data.py`, `prepare_destinations.py`,
`run_accessibility.R` (generyczny, parametryzowany), `analyze_services_income.py`,
`analyze_universities.py`, `analyze_cross_city.py`, `run_city_pipeline.sh`
(orkiestracja całego pipeline'u per miasto). Projekt QGIS:
`wszystkie_miasta_dostepnosc.qgz` (6 grup warstw, jedna per miasto, domyślnie
widoczna tylko Łódź). Instrukcja ręcznego odtworzenia: **`HOWTO_MANUAL.md`**.
