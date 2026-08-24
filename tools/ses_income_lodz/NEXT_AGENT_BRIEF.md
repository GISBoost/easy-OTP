# Brief dla kolejnej sesji: analiza SES + dostępność transportowa (6 miast PL)

Czytasz to, bo poprzednia sesja (lub jej kontekst po `/compact`) się skończyła, a Michał chce
kontynuować. Przeczytaj to w całości, potem `METHODOLOGY.md` i `HANDOFF.md` w tym samym folderze
zanim cokolwiek zrobisz.

## Co to za projekt

Repo `easy-OTP` to wtyczka QGIS do pomiaru dostępności czasowej transportu publicznego (przez
OpenTripPlanner) — pełny opis w `CLAUDE.md` w korzeniu repo (**przeczytaj go, obowiązuje jako
twarde instrukcje projektu**, ale ma jedno zastrzeżenie: *to zadanie jest efektywnie "zadaniem
pobocznym"* — analizą w `tools/`, nie rozwojem samej wtyczki, więc rygory wtyczki typu "jeden
kamień milowy na raz" czy `.tr()` na stringach nie mają tu zastosowania).

## Czym jest ten konkretny podprojekt (folder `tools/ses_income_lodz/`)

Michał chce zbadać, czy biedniejsze obszary miast mają gorszy dostęp do transportu publicznego
(inspiracja: Braga, Loureiro & Pereira 2026, *Journal of Transport Geography* — analiza dla
Fortalezy w Brazylii). Problem: polski spis powszechny **nie zbiera danych o dochodzie** na
żadnym poziomie drobniejszym niż gmina. Rozwiązanie: estymacja pośrednia dochodu na poziomie
obwodu spisowego GUS, metodą MRP-lite — wynik wyborczy Sejm 2023 jako predyktor obszarowy,
skalibrowany ankietą CBOS o profilu dochodowym elektoratów partii.

**To NIE jest zmierzony dochód — to szacunek.** Metoda jest naukowo uzasadniona (nazwana w
literaturze, nie improwizacja), ale ma realne ograniczenia — pełna lista w `METHODOLOGY.md` §6.
Jeśli Michał albo ktoś inny kwestionuje rzetelność tej warstwy, **to jest dobre pytanie, nie
przeszkadzanie** — odpowiadaj rzeczowo z METHODOLOGY.md, nie broń się.

## Co jest zrobione (stan na 2026-08-22)

**6 miast, każde jako `tools/ses_income_lodz/{miasto}.gpkg`** (lodz/krakow/warszawa/poznan/
gdansk/szczecin), warstwa `obwody_spisowe` = wynik (populacja + `income_index_pln`), warstwa
`obwody_glosowania` = referencja. Wszystkie zweryfikowane niezależnie (suma populacji =
dokładnie oficjalna liczba GUS dla 5/6 miast, 99.9% dla Łodzi; dla 4 miast też 0 rozbieżności
głosów vs oficjalny PKW). Projekt QGIS ze wszystkim wczytanym i wystylowanym:
`docs/gis/lodz_ses_dochod.qgz`.

Szczegóły metody i pełna lista napotkanych błędów (i jak je wykryto) — `METHODOLOGY.md`.
Dokładne kroki pipeline'u i jak dodać 7. miasto — `HANDOFF.md`.

**Od 2026-08-22** `obwody_spisowe` ma też pola struktury rodzin/gospodarstw domowych z GUS NSP2021
(`fam_*`, `hh_*` — dominujący typ rodziny, % samotnych rodziców, średnia wielkość gospodarstwa,
średnia liczba dzieci itd., wszystkie **zmierzone wprost przez spis**, nie estymowane jak dochód).
Metoda i pełna lista pól: `METHODOLOGY.md` §4a. Warstwy `obwody_spisowe` we wszystkich 6 miast
mają teraz styl kategoryzowany po `fam_dominant_type` w projekcie QGIS.

## Jak "my" pracujemy (żebyś nie musiał się tego uczyć od zera)

- **Rozmowa po polsku, kod/commity po angielsku** (konwencja z całego repo).
- Michał pyta o rzetelność i chce **odtwarzalnych, weryfikowalnych** kroków — nie akceptuj
  własnych obliczeń bez krzyżowej weryfikacji względem niezależnego źródła, jeśli to możliwe.
  Przy tej analizie to się opłaciło: **znaleziono i naprawiono 2 realne błędy merytoryczne**
  (zła gmina przez zgadywanie TERYT; złe pole jako mianownik głosów) właśnie dzięki takiej
  weryfikacji, nie dzięki "wygląda dobrze".
- Kiedy coś jest niejednoznaczne i to prawdziwa decyzja (nie technikalia) — **pytaj**
  (`AskUserQuestion`), nie zgaduj. Przykład z tej sesji: co robić z 4 miastami bez publicznej
  geometrii obwodów — zapytano, Michał wybrał "poczekam, sam dociągnę dane" (i faktycznie
  dociągnął — wskazał `wybory.it`).
- Do pracy w QGIS używamy **`mcp__qgis__*`** (żywa instancja QGIS sterowana przez MCP) —
  nie CLI, nie ręczne skrypty PyQGIS poza `execute_code` gdy MCP-owe narzędzia nie wystarczają.
  **`native:downloadvectortiles` crashuje QGIS — nie używać** (patrz HANDOFF.md §6).
  Po każdym crashu QGIS: `ping` → jeśli martwe, poczekaj/sprawdź → `load_project` **zanim**
  cokolwiek zapiszesz, inaczej nadpiszesz dobry projekt pustym (stało się raz w tej sesji,
  naprawione, ale bądź czujny).
- Ponytail (lazy-coding mode) jest aktywny w tej sesji — najprostsze działające rozwiązanie,
  bez nadmiarowej abstrakcji. Skrypty w tym folderze (`extract_population_generic.py` itd.) są
  celowo proste — trzymaj się tego stylu przy rozszerzeniach.
- Pliki robocze/pośrednie (surowe pobrania, kroki pipeline'u) **czyść po zweryfikowaniu wyniku**
  — folder był kiedyś ~212MB bałaganu z 6 miast, teraz jest ~24MB (6 gpkg + skrypty + docs).

## Co dalej (to, o co Michał prosił na koniec ostatniej sesji)

Dwa kierunki, oba otwarte — **zapytaj Michała o priorytety zanim zaczniesz szeroko**, ale
możesz zacząć od researchu / propozycji:

### A) Więcej warstw socjo-ekonomiczno-demograficznych

**Zrobione 2026-08-22:** struktura rodzin (typ biologiczny, dominujący typ, % samotnych
rodziców), skład gospodarstw domowych (jedno-/wielorodzinne, % singli), wielkość gospodarstwa
(średnia, % 5+ osób), liczba dzieci w rodzinie (średnia, % bezdzietnych, % 3+ dzieci) — patrz
METHODOLOGY.md §4a i HANDOFF.md §3.4. Skrypty: `extract_family_household_stats.py` +
`join_family_household_stats.py`, oba reużywalne do kolejnych plików GUS o tej samej strukturze
(flat, arkusz "Dane - obwody spisowe", kolumna `Numer obwodu spisowego` = gotowy klucz `OBWOD`).

GUS NSP 2021 (`docs/gis/ludnosc_nsp_2021.xlsx`) **nadal zawiera** — niewykorzystane —
strukturę wieku (10-letnie grupy) i grupy ekonomiczne (przedprodukcyjny/produkcyjny mobilny i
niemobilny/poprodukcyjny) na poziomie obwodu spisowego, w innym formacie arkusza (per-województwo,
wymaga logiki `extract_population_generic.py`, nie prostego pivota jak §3.4). To wciąż najtańszy
kolejny krok — nie trzeba nowego źródła danych, tylko rozszerzyć istniejący parser o dodatkowe
kolumny wieku/grup ekonomicznych.

Inne możliwe źródła (niesprawdzone, do researchu):
- Wskaźnik G (dochody podatkowe gmin, Ministerstwo Finansów) — tylko poziom gminy, ale jako
  kotwica/walidacja krzyżowa dla całego miasta.
- Rejestr Cen Nieruchomości (RCN), geoportal-krajowy.pl — ceny transakcyjne nieruchomości,
  punktowo, bezpłatne od lutego 2026 — potencjalna niezależna walidacja `income_index_pln`
  (patrz `phd-research/papers/geodane-ses-wysokiej-granularnosci/reviews/literature-review.md`
  §5 punkt 4 — to była rekomendacja z przeglądu literatury, jeszcze niezrealizowana).
- GUS BDL (Bank Danych Lokalnych) — może mieć więcej wskaźników na poziomie gminy/powiatu.

### B) Dalsze analizy dostępności transportowej

Cel końcowy z `_status.md` w `phd-research/...geodane-ses.../`: połączyć tę warstwę z
pomiarem dostępności czasowej z wtyczki `easy-OTP` (algorytm `RunTemporalAccessibility` —
patrz `docs/prd/PR_easy-OTP.md` w korzeniu repo dla pełnego opisu tego algorytmu). To wymaga
**uruchomienia serwera OTP** — a to jest dokładnie ten rodzaj rzeczy, której agent (Ty) **nie
może zweryfikować sam** (patrz `CLAUDE.md`, sekcja "Czego NIE testuje agent"). Michał musi
albo dostarczyć gotowe powierzchnie travel-time z wcześniejszych przebiegów wtyczki, albo
uruchomić pipeline ręcznie.

## Pierwsza rzecz do zrobienia w nowej sesji

Nie zgaduj priorytetu — zapytaj Michała czy woli (A) rozszerzać dane SES, (B) przechodzić do
analizy dostępności (jeśli ma gotowe dane z OTP), czy coś trzeciego. Jeśli powie "dane o
ludności" (jak zapowiedział na koniec poprzedniej sesji) — to najpewniej kierunek A, konkretnie
struktura wieku/grup ekonomicznych z arkusza GUS, który już masz lokalnie.
