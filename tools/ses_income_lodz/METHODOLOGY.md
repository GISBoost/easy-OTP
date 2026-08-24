# Metodologia: szacowanie dochodu per capita na poziomie obwodu spisowego

**Status:** zakończone dla 6 miast (Łódź, Kraków, Warszawa, Poznań, Gdańsk, Szczecin), zweryfikowane.
**Data:** 2026-08-22. **Autor syntezy:** Claude (Anthropic) na zlecenie Michała Kaczorowskiego.

## 1. Cel i geneza

Inspiracja: Braga, Loureiro & Pereira (2026), *Journal of Transport Geography*, 131, 104526 —
analiza nierówności dostępności transportu publicznego wg grup dochodowych (Fortaleza, Brazylia),
możliwa dzięki brazylijskiemu spisowi, który zbiera dochód na poziomie *setor censitário*.
Polski spis (NSP 2021) **nie zbiera dochodu w ogóle** na żadnym poziomie granularności drobniejszym
niż gmina (wskaźnik G Ministerstwa Finansów) — stąd potrzeba estymacji pośredniej.

Pełny przegląd literatury i ocena wykonalności: `phd-research/papers/geodane-ses-wysokiej-granularnosci/`
(`_status.md` + `reviews/literature-review.md`) — tam uzasadnienie metody (MRP z predyktorem
wyborczym, nie naiwne "ecological inference") i pełna bibliografia.

**Cel końcowy** (jeszcze nieosiągnięty w tym repo): połączyć tę warstwę SES z pomiarem dostępności
czasowej transportu publicznego z wtyczki `easy-OTP` (`RunTemporalAccessibility`), żeby zbadać
czy biedniejsze obwody mają gorszy dostęp do transportu — analogicznie do Bragi i in. (2026).

## 2. Metoda w skrócie (MRP-lite / area-level)

To **nie jest** zmierzony dochód mieszkańców. To estymacja obszarowa: indeks dochodowy obwodu
głosowania = ważona średnia (wg udziałów głosów partii) średnich dochodów deklarowanych przez
elektoraty tych partii w ogólnopolskiej ankiecie CBOS. Metoda jest nazwana w literaturze jako
MRP (Multilevel Regression + Poststratification) z predyktorem obszarowym = wynik wyborczy
(Hanretty, Lauderdale & Vivyan 2016, *Political Science Research and Methods*, 6(3), 571–591,
https://doi.org/10.1017/psrm.2015.79).

## 3. Krok po kroku

### Krok 1 — dochód per partia (z CBOS)

Źródło: CBOS, komunikat nr 98/2023 **"Kim są wyborcy partii politycznych w Polsce?"**
https://www.cbos.pl/SPISKOM.POL/2023/K_098_23.PDF — Aneks, Tabela 1, wiersz
**"Dochody na jedną osobę"** (pytanie: miesięczny dochód per capita w gospodarstwie domowym,
deklarowany, w złotych). Badanie: zagregowane dane z dwóch pomiarów CBOS, czerwiec+lipiec 2023,
N=1604 (metoda mixed-mode: CAPI/CATI/CAWI na próbie z rejestru PESEL).

Przedziały CBOS i przyjęte środki przedziałów (zł/miesiąc):

| Przedział CBOS | Środek przedziału |
|---|---|
| do 1499 zł | 1250 |
| 1500–1999 zł | 1750 |
| 2000–2999 zł | 2500 |
| 3000–3999 zł | 3500 |
| 4000 zł i więcej | **4500** ← jedyne założenie arbitralne w całej metodzie (przedział otwarty w CBOS) |

Wiersze "trudno powiedzieć" / "odmowa odpowiedzi" odrzucone, rozkład przeliczony na 100% pozostałych.

Średnia ważona środkami przedziałów, per partia (dokładne liczby użyte w kodzie —
`compute_precinct_income.py` / `compute_precinct_income_generic.py` / `compute_income_from_tileset.py`,
funkcja `mean_income()`):

| Partia | % CBOS (do1499/1500-1999/2000-2999/3000-3999/4000+) | Dochód (zł) |
|---|---|---|
| PiS | 12/18/26/12/12 | **2593.8** |
| KO | 4/9/26/16/25 | **3178.1** |
| Trzecia Droga | 6/5/20/7/25 | **3226.2** |
| Nowa Lewica | 3/5/27/15/23 | **3232.9** |
| Konfederacja | 4/5/18/17/25 | **3344.2** |
| Ogółem (fallback dla partii marginalnych) | 7/13/23/14/17 | **2898.6** |

Fallback "Ogółem" (średnia dla całej próby, nie jednej partii) stosowany dla komitetów spoza 5
głównych: Bezpartyjni Samorządowcy, Polska Jest Jedna, Ruch Dobrobytu i Pokoju, Normalny Kraj,
Antypartia, Ruch Naprawy Polski, Mniejszość Niemiecka — w praktyce <2% głosów w dużych miastach.

### Krok 2 — udział % głosów per obwód głosowania (Sejm 2023)

Dla każdego obwodu głosowania: `udział_partii = głosy_partii / total_valid_votes`, gdzie
`total_valid_votes` = suma głosów na wszystkie listy (musi być liczona jako **suma pól
partyjnych**, nie brana z gotowego pola — patrz Sekcja 5, błąd #1).

`income_index(obwód) = Σ_partia( udział_partii × dochód_partii )`

Przykład (obwód nr 1, Łódź, 1124 głosów ważnych): KO 602 (53.6%×3178.1) + Trzecia Droga 170
(15.1%×3226.2) + PiS 132 (11.7%×2593.8) + Lewica 122 (10.9%×3232.9) + Konfederacja 67
(6.0%×3344.2) + drobne (31 głosów × 2898.6 fallback) → **3124.9 zł**. Ręcznie przeliczone i
zgodne z wynikiem skryptu co do dziesiątej części złotówki.

### Krok 3 — dopasowanie przestrzenne obwód głosowania → obwód spisowy

Obwody spisowe GUS (jednostka ~100–500 mieszkańców) są dużo drobniejsze niż obwody głosowania
(~kilkanaście obwodów spisowych na jeden obwód głosowania). Metoda: policz centroid każdego
obwodu spisowego → sprawdź, w którym poligonie obwodu głosowania leży → przypisz **tę samą**
wartość `income_index_pln` wszystkim obwodom spisowym w danym obwodzie głosowania.

**Konsekwencja:** brak zróżnicowania wewnątrz jednego obwodu głosowania — to nie jest błąd
implementacji, to twardy limit granularności źródła (głosuje się per obwód głosowania, nie
per budynek).

### Krok 4 — ludność (mianownik dla przyszłych analiz per capita)

GUS NSP 2021, plik `docs/gis/ludnosc_nsp_2021.xlsx` (arkusz per województwo), kolumna
**"Ogółem"** (całkowita ludność obwodu spisowego — NIE domyślna "pop20-29" z wtyczki
`PrepareStudentLayer`, która liczy tylko grupę wiekową 20-29 lat). Klucz łączenia: `rejon
statystyczny` (6 cyfr) + `numer obwodu spisowego`, dokładnie ta sama logika co
`easy_otp/algorithms/prepare_student_layer.py`, odtworzona w `extract_population_generic.py`
(patrz Sekcja 4 HANDOFF.md — wtyczka nie była załadowana w sesji QGIS).

## 4. Znaczenie pól w warstwie wynikowej (`obwody_spisowe` w każdym `{miasto}.gpkg`)

| Pole | Znaczenie |
|---|---|
| `OBWOD`, `REJ`, `OBW` | identyfikator obwodu spisowego GUS (rejon+numer) |
| `GMINA`, `WW`/`PP`/`GG`/`R` | wewnętrzne kody TERYT/GUS (gmina, ew. delegatura/dzielnica) |
| `Shape_Area` | powierzchnia poligonu (m²) |
| `population` | ludność obwodu spisowego, NSP 2021 (kolumna "Ogółem") |
| `precinct_nr` | numer obwodu głosowania, z którego pochodzi `income_index_pln` |
| `precinct_valid_votes` | głosy ważne w tym obwodzie głosowania (proxy "pewności" udziałów %) |
| `income_index_pln` | szacowany dochód per capita w gospodarstwie domowym (zł/miesiąc), area-level |

Warstwa `obwody_glosowania` w tym samym pliku = geometria + dane źródłowe na poziomie obwodu
głosowania (referencja/do wglądu, domyślnie ukryta w projekcie QGIS).

Od 2026-08-22 warstwa `obwody_spisowe` zawiera też pola struktury rodzin i gospodarstw domowych —
patrz Sekcja 4a.

## 4a. Rozszerzenie: struktura rodzin i gospodarstw domowych (2026-08-22)

**Dlaczego:** `income_index_pln` to jedyny wymiar SES w warstwie. Michał poprosił o dodanie
demografii rodzinnej — w szczególności udziału samotnych rodziców, rodzin z dziećmi i wielkości
gospodarstw domowych — jako **bezpośrednio zmierzonych** (spis powszechny, nie estymacja)
wymiarów uzupełniających dochód szacowany.

**Źródło:** GUS NSP 2021, 4 pliki nationwide (flat, sheet "Dane - obwody spisowe", kolumny
`Kod TERYT gminy / Numer obwodu spisowego / <kategoria> / <liczba>`) — dużo prostsza struktura
niż `ludnosc_nsp_2021.xlsx` (nie trzeba parsować hierarchii "rejon statystyczny"/"obwód spisowy"
z `xlsx_reader.py` — `Numer obwodu spisowego` to już gotowy klucz `OBWOD`, bez dodatkowego
key-buildingu). Lokalne kopie w `docs/gis/`:

- `rodziny_w_rejonach_i_obwodach_wg_typow_nsp2021.xlsx` — typ rodziny biologicznej (małżeństwa
  z/bez dzieci, związki nieformalne z/bez dzieci, samotne matki/ojcowie z dziećmi)
- `gospodarstwa_w_rejonach_i_obwodach_wg_skladu_rodzinnego_nsp2021_2.xlsx` — skład rodzinny
  gospodarstwa domowego (jedno-/dwu-/trzy i więcej rodzinne, nierodzinne jedno-/wieloosobowe)
- `rodziny_w_rejonach_i_obwodach_wg_liczby_dzieci_nsp2021.xlsx` — liczba dzieci w rodzinie (0–4+)
- `gospodarstwa_w_rejonach_i_obwodach_wg_liczby_osob_nsp2021.xlsx` — wielkość gospodarstwa (1–5+ osób)

**Ekstrakcja:** `extract_family_household_stats.py <plik.xlsx> <out_dir> <prefix>` — jeden
przebieg całego pliku (ogólnopolskiego, ~30MB/~0.8–1M wierszy), filtr po znanych kodach `GMINA`
(z `cities_teryt.md`) dla wszystkich 6 miast naraz, pivot kategorii do formatu szerokiego per
`OBWOD` (liczby + % + kategoria dominująca).

**Agregacja i join:** `join_family_household_stats.py <miasto>` liczy pola pochodne z 4 surowych
CSV (patrz niżej) i zapisuje je bezpośrednio do `{miasto}.gpkg` przez `sqlite3` (GPKG to zwykły
SQLite plik) — **z ominięciem `native:joinattributestable`**, żeby uniknąć błędu zamiany pól
(Sekcja 5, błąd #2). Wymaga wczytania rozszerzenia `mod_spatialite.dll` (z instalacji QGIS) do
połączenia `sqlite3.connect()`, bo triggery GPKG na `UPDATE` wołają funkcje przestrzenne
(`ST_IsEmpty` itd.) niedostępne w gołym module `sqlite3` — patrz Sekcja 5, błąd #7.

**Pola dodane do `obwody_spisowe`:**

| Pole | Znaczenie | Źródło (plik) |
|---|---|---|
| `fam_total` | liczba rodzin w obwodzie spisowym (mianownik dla `fam_pct_*`) | typy rodzin |
| `fam_pct_malzenstwa_bez_dzieci` / `_z_dziecmi` | % rodzin: małżeństwo bez/z dziećmi | typy rodzin |
| `fam_pct_matki_samotne` / `fam_pct_ojcowie_samotni` | % rodzin: samotny rodzic z dziećmi | typy rodzin |
| `fam_pct_kohabitacja_bez_dzieci` / `_z_dziecmi` | % rodzin: związek nieformalny bez/z dziećmi | typy rodzin |
| `fam_dominant_type` | najliczniejszy typ rodziny w obwodzie (etykieta GUS, PL) | typy rodzin |
| `hh_total` | liczba gospodarstw domowych (mianownik dla `hh_pct_*`) | skład gosp. |
| `hh_pct_jednoosobowe` | % gospodarstw nierodzinnych jednoosobowych (osoby samotne) | skład gosp. |
| `hh_pct_jednorodzinne` | % gospodarstw z dokładnie jedną rodziną | skład gosp. |
| `hh_pct_dwurodzinne_plus` | % gospodarstw wielorodzinnych (2+ rodziny, np. wielopokoleniowe) | skład gosp. |
| `hh_dominant_type` | najliczniejszy typ składu gospodarstwa w obwodzie | skład gosp. |
| `hh_avg_size` | średnia liczba osób w gospodarstwie (środek przedziału 6 dla "5 i więcej") | wielkość gosp. |
| `hh_pct_5plus_osob` | % gospodarstw 5-osobowych i większych (przeludnienie) | wielkość gosp. |
| `fam_avg_children` | średnia liczba dzieci w rodzinie (środek przedziału 5 dla "4 i więcej") | liczba dzieci |
| `fam_pct_bez_dzieci` | % rodzin bez dzieci na utrzymaniu | liczba dzieci |
| `fam_pct_3plus_dzieci` | % rodzin z 3+ dziećmi | liczba dzieci |

Wszystkie `NULL` tam, gdzie GUS nie publikuje rozkładu dla obwodu (**anonimizacja małych prób**,
nie błąd — potwierdzone: obwody z `fam_total IS NULL` mają średnio ~28 mieszkańców, maks. 133,
czyli to realne mikro-obwody z za małą liczbą rodzin do publikacji bez ryzyka identyfikacji;
1–6% obwodów zależnie od miasta).

**Weryfikacja:** (a) `pct_*` sumują się do ~100% w każdym obwodzie (sprawdzone SQL-em dla
wszystkich 6 miast, odchylenie ≤0.01 — zaokrąglenie); (b) kierunek korelacji zgodny z
oczekiwaniem socjoekonomicznym: w każdym z 6 miast dolny tercyl `income_index_pln` ma wyższy
`fam_pct_matki_samotne` niż górny tercyl (różnica 2.5–8.5 p.p.) — spójne z literaturą
(niższy dochód obszarowy koreluje z wyższym udziałem samotnego rodzicielstwa), nie jest to
tautologia bo źródła (wyniki wyborcze vs. struktura rodzin) są całkowicie niezależne.

## 5. Napotkane błędy i jak je wykryto (ważne dla rzetelności)

1. **Błędna identyfikacja gminy przez zgadywanie kodu TERYT.** Pierwsza próba dla Łodzi użyła
   `GMINA='1062011'` w geometrii GUS — to był **Piotrków Trybunalski** (populacja 68 978), nie
   Łódź. Wykryte przez sumę ludności (68 978 ≠ ~670 tys.). **Zasada od tej pory: zawsze
   weryfikować przez sumę populacji z Excela GUS, nigdy nie zgadywać kodu TERYT.** Duże miasta
   (Łódź, Warszawa, Kraków, Poznań) są w Excelu GUS dzielone na delegatury/dzielnice —
   **wiele kodów GMINA na miasto**, nie jeden.
2. **Zamiana wartości pól przy `native:joinattributestable` z `FIELDS_TO_COPY`.** Gdy lista
   `FIELDS_TO_COPY` nie odpowiadała dokładnie kolejności pól w warstwie źródłowej, QGIS czasem
   podstawiał wartości pod złe nazwy pól (np. `income_index_pln` i `valid_votes` zamienione
   miejscami). Wykryte przez krzyżową weryfikację wybranego rekordu z CSV źródłowym. **Zasada:
   po każdym takim joinie sprawdzić 1 rekord ręcznie względem źródła.**
3. **`native:downloadvectortiles` w QGIS crashuje aplikację** (access violation, potwierdzone
   raportem crashu użytkownika). Zastąpione własnym skryptem `fetch_tiles_mbtiles.py`
   (czysty HTTP + zapis do poprawnego MBTiles, bez udziału silnika przetwarzania QGIS).
4. **Pole `all_votes` w danych `wybory.it` ≠ suma głosów na listy partyjne** (różnica
   kilka–kilkanaście głosów na obwód, prawdopodobnie inna definicja PKW, np. "ważne karty" vs
   "ważne głosy"). Właściwy mianownik to pole `total` (= suma pól partyjnych, zweryfikowane
   ręcznie). Po poprawce: **0 rozbieżności na wszystkich 4 miastach** względem oficjalnego CSV
   PKW (805/805 Warszawa, 202/202 Gdańsk, 258/207 Poznań/Szczecin — różnice tylko w liczbie
   obwodów, nie w wartościach głosów).
5. **Kafle wektorowe (MVT) dają geometrię pofragmentowaną per-tile** — trzeba `dissolve` po
   parze (teryt, numer_obwodu), żeby odtworzyć cały poligon obwodu.
6. **Domyślny User-Agent Pythona (`urllib`) bywa blokowany (HTTP 403) przez WAF/Cloudflare** —
   `fetch_tiles_mbtiles.py` wysyła nagłówek `User-Agent` udający przeglądarkę.
7. **`UPDATE` na tabeli GPKG przez goły `sqlite3` rzuca `no such function: ST_IsEmpty`.** GPKG ma
   triggery na `INSERT`/`UPDATE` wołające funkcje Spatialite (walidacja geometrii), których moduł
   `sqlite3` z Pythona nie zna. Naprawione: `conn.enable_load_extension(True)` +
   `conn.load_extension(".../mod_spatialite.dll")` (z instalacji QGIS) przed jakimkolwiek
   `UPDATE`/`ALTER TABLE` na warstwie GPKG przez surowy SQL.

## 6. Ograniczenia metody (pełna lista)

1. To **nie jest zmierzony dochód** — to estymacja pośrednia (area-level), zależna od założenia,
   że skład polityczny obwodu koreluje z jego profilem dochodowym tak jak wynika z ogólnopolskiej
   ankiety CBOS.
2. CBOS jest **ogólnopolski**, nie lokalny dla żadnego z 6 miast — zakłada się, że zależność
   partia↔dochód w danym mieście jest zbliżona do średniej krajowej.
3. Górny przedział dochodowy (4000+ zł) domknięty **arbitralnie na 4500 zł**.
4. **Brak zróżnicowania wewnątrz jednego obwodu głosowania** (patrz Krok 3) — realna granulacja
   efektywna to obwód głosowania (283–8849 zależnie od miasta), nie obwód spisowy.
5. Małe podpróby CBOS dla Lewicy/Trzeciej Drogi (~80 osób każda, orientacyjnie) — mniej pewne
   statystycznie niż PiS/KO (~450–500 osób).
6. Ryzyko **błędu ekologicznego** (ecological fallacy) — wniosek "w tym obwodzie żyją ludzie o
   dochodzie X" to średnia obszarowa, nie cecha każdego mieszkańca z osobna.
7. Dla Warszawy/Poznania/Gdańska/Szczecina geometria obwodów głosowania pochodzi z serwisu
   `wybory.it` (rekonstrukcja adresowa + Woronoj, nie oficjalna geometria administracyjna) —
   dokładność granic może być niższa niż dla Łodzi/Krakowa (oficjalne geoportale miejskie),
   choć same **wyniki głosowania per obwód są identyczne z oficjalnym PKW** (zweryfikowane).
8. Część obwodów spisowych (0.05–5% zależnie od miasta, patrz `HANDOFF.md` §6) nie dopasowała
   populacji (brak klucza w Excelu GUS) lub indeksu dochodowego (centroid poza wszystkimi
   poligonami obwodów głosowania) — pominięte jako NULL, wpływ na sumę ludności miasta <0.1%.

## 7. Źródła — pełna lista z linkami

- CBOS, "Kim są wyborcy partii politycznych w Polsce?" (nr 98/2023):
  https://www.cbos.pl/SPISKOM.POL/2023/K_098_23.PDF
- Wyniki głosowania Sejm 2023 per obwód (KBW/PKW), plik
  `wyniki_gl_na_listy_po_obwodach_sejm_csv.zip` + rejestr `obwody_glosowania_csv.zip`:
  https://danewyborcze.kbw.gov.pl/indexc6e4.html?title=Parlament_2023
- GUS NSP 2021, ludność obwodów spisowych: https://stat.gov.pl/spisy-powszechne/nsp-2021/
  (lokalna kopia: `docs/gis/ludnosc_nsp_2021.xlsx`)
- GUS NSP 2021, struktura rodzin i gospodarstw domowych (portal Bank Danych Lokalnych /
  udostępnianie NSP2021 na żądanie, ten sam serwis co ludność): 4 pliki, lokalne kopie w
  `docs/gis/{rodziny_w_rejonach_i_obwodach_wg_typow,gospodarstwa_w_rejonach_i_obwodach_wg_skladu_rodzinnego_2,rodziny_w_rejonach_i_obwodach_wg_liczby_dzieci,gospodarstwa_w_rejonach_i_obwodach_wg_liczby_osob}_nsp2021.xlsx`
- GUS, geometria obwodów spisowych (`SU_BREC_2021_OBW`): https://geo.stat.gov.pl/
  (lokalna kopia: `docs/gis/SU_BREC_2021_OBW/`)
- Geometria obwodów głosowania:
  - Łódź: ArcGIS REST, `https://mapa.lodz.pl/3/rest/services/ObwodyWyborcze/MapServer/8`
    (publiczna mapa: https://nowa.mapa.lodz.pl/obwody-wyborcze-2024/)
  - Kraków: ArcGIS REST, `https://msip3.um.krakow.pl/server/rest/services/Wybory/Wybory_Aktualne/MapServer/27`
  - Warszawa/Poznań/Gdańsk/Szczecin: kafle wektorowe (Martin/MVT) z
    https://wybory.it (repo: https://github.com/michalpazur/obwody-wyborcze),
    endpoint `https://wybory.it/api/martin/parl_2023`
- Literatura metodologiczna: `phd-research/papers/geodane-ses-wysokiej-granularnosci/reviews/literature-review.md`
