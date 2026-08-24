# Znaczenie kolumn — dostępność transportowa Łodzi

Dwie **różne** metryki dostępności w tym samym pliku — nie mylić ich ze sobą.

## 1. `lodz_accessibility.csv` / `lodz_accessibility_wide.csv` — surowy wynik r5r

Format długi (`lodz_accessibility.csv`, wyjście `accessibility()`) i szeroki
(`lodz_accessibility_wide.csv`, spivotowany + zjoinowany z SES) — te same liczby.

| kolumna | znaczenie |
|---|---|
| `id` | identyfikator obwodu spisowego (`OBWOD` z `ses_income_lodz/lodz.gpkg`) |
| `income_index_pln` | szacowany dochód obwodu głosowania (patrz `ses_income_lodz/METHODOLOGY.md`) |
| `population` | liczba mieszkańców obwodu spisowego (GUS NSP2021) |
| `fam_pct_matki_samotne` | % rodzin z samotną matką w obwodzie |
| `{kategoria}_{próg}min` | **dostępność kumulatywna typu "opportunity count"**: liczba punktów usługowych (POI z OSM) danej kategorii osiągalnych z centroidu tego obwodu w ciągu `{próg}` minut (walk+transit, odjazd 07:00–09:00). **To NIE jest populacja** — to surowa liczba placówek. `total_60min=1034` znaczy "z tego miejsca da się dojechać do 1034 pojedynczych POI (wszystkich kategorii razem) w ≤60 min", nie "1034 osoby mają dostęp" |
| `has_access_{kategoria}_{próg}min` | **binarna flaga dostępności pasywnej**: `1` jeśli w tym obwodzie `{kategoria}_{próg}min >= 1` (czyli istnieje **choć jedna** placówka tej kategorii w zasięgu), inaczej `0`. To jest budulec metryki populacyjnej niżej — sam w sobie mówi tylko "tak/nie", nie "ile" |

**Kategorie**: `education` (szkoły+przedszkola), `health` (szpitale+przychodnie+lekarze+apteki),
`culture` (biblioteki+domy kultury), `groceries` (supermarkety), `total` (suma wszystkich 4,
każdy POI liczony raz niezależnie od kategorii).

**Progi**: 15/30/45/60 minut, `decay_function="step"` (twardy próg, nie ważona funkcja
zanikania) — patrz `run_accessibility.R`.

## 2. `out/lodz_population_coverage_summary.csv` — dostępność pasywna, poziom miasta

To jest **agregat z `has_access_*`**, odpowiadający na pytanie "ile mieszkańców Łodzi ma
dostęp do ≥1 placówki X w ≤Y minut" — nie "ile placówek widać z jednego miejsca".

| kolumna | znaczenie |
|---|---|
| `category` | jak wyżej |
| `cutoff_min` | próg czasowy |
| `population_covered` | suma `population` po wszystkich obwodach z `has_access_{category}_{cutoff_min}min == 1` |
| `population_total` | suma `population` po wszystkich obwodach (mianownik, stały w obrębie miasta) |
| `pct_covered` | `population_covered / population_total * 100` — **to jest ta liczba, o którą chodziło**: "94.0% ludności Łodzi ma dostęp do ≥1 dowolnej usługi w 15 min" |

**Ograniczenie metodologiczne**: `population` jest przypisana obwodowi spisowemu jako całości
(nie rozłożona wewnątrz obwodu), a dostępność liczona jest z **centroidu** obwodu — dla dużych
peryferyjnych obwodów (patrz niżej, sekcja o heksagonach) to przybliżenie jest gorsze niż dla
małych, gęstych obwodów centrum. Dlatego równolegle budowana jest wersja heksagonalna (500 m) —
mniejsza, bardziej jednorodna jednostka przestrzenna zmniejsza ten błąd.

## 3. `lodz_services.csv` / warstwa `poi_services`

| kolumna | znaczenie |
|---|---|
| `category` | jedna z 4 kategorii wyżej |
| `osm_type` / `osm_id` | identyfikator OSM (node/way) |
| `name` | nazwa placówki z OSM (może być pusta) |
| `lon`, `lat` | współrzędne WGS84 (dla way: centroid) |

## 4. Warstwa `obwody_spisowe` w `lodz_accessibility.gpkg`

Geometria + pola SES z `ses_income_lodz/lodz.gpkg` (patrz tamtejszy `HANDOFF.md`) + wszystkie
20 kolumn `{kategoria}_{próg}min` z sekcji 1 (dopisane przez `join_accessibility.py`). Kolumny
`has_access_*`/populacyjne **nie są jeszcze zapisane do gpkg** (na razie tylko w CSV) — do
zrobienia, jeśli mapa binarnej dostępności okaże się potrzebna.
