# tools/chart_lab — interactive GUI for transit_charts

> **Standalone tool.** Not part of the QGIS plugin easy-OTP and never imported by
> `easy_otp/`. Imports [`tools/transit_charts`](../transit_charts/README.md) and, through it,
> [`tools/family_a_reconstruction`](../family_a_reconstruction/README.md) by path.

A local, browser-based GUI for `transit_charts chart`, for people who don't want a
terminal or to remember CLI flags: pick a chart, adjust its parameters, see the result. All
16 `transit_charts` charts are available, with the exact parameter set each one needs shown
automatically (driven by `transit_charts/registry.py` — a new chart added there needs no
change here to appear).

**What this is not:** it does not run `extract`/`match`/`build`/`record`, and it does not
touch the cloud pipeline (the Termux phone, `easy-GTFS-RT` Actions, `gtfs-dashboard`) in any
way — it is a pure consumer of already-published tidy tables.

**Data sources**, any combination active at once:
- The bundled example (Łódź, 2026-07-23, 7 routes) — active by default, zero setup.
- Your own tidy table file, produced by `transit_charts extract` (upload button).
- `gtfs-dashboard`'s published catalogue of already-recorded city-days (fetched from its
  `manifest.json` on GitHub Pages — never the GitHub REST API — then downloaded and cached
  locally on first use).

## Uruchomienie

**Gotowy plik Windows** (najprostsza opcja, bez instalowania Pythona): pobierz z zakładki
Releases tego repo (tag `chart_lab-v*`), rozpakuj, uruchom `chart_lab.exe`. W przeglądarce
otworzy się strona aplikacji.

**Ze źródeł** (do developmentu):
```bat
cd tools\chart_lab
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m chart_lab.app
```

## Instrukcja obsługi (krok po kroku)

Po uruchomieniu w przeglądarce otwiera się jedna strona, od góry w dół:

1. **Sekcja "Data"** — skąd biorą się dane do wykresów:
   - Po lewej: pole do wgrania własnej tabeli tidy (plik `.csv`/`.csv.gz`/`.parquet`
     wygenerowany przez `transit_charts extract`) — przeciągnij plik albo kliknij, żeby
     wybrać z dysku.
   - Po prawej: **"Active tables"** — checkboxy wszystkich wczytanych tabel. Domyślnie
     zaznaczona jest dołączona przykładowa tabela (Łódź, 2026-07-23). Możesz mieć
     zaznaczonych kilka naraz — wykresy liczą się wtedy ze wszystkich razem (to wymagane
     dla niektórych wykresów, patrz punkt 5).
2. **"Online catalogue"** (zwinięty panel, kliknij żeby rozwinąć) — dane już nagrane dla
   innych miast, publikowane przez `gtfs-dashboard`:
   - Kliknij **"1. Fetch available cities"**, żeby pobrać listę dostępnych miast.
   - Zawęź wybór trzema rozwijanymi listami z rzędu: **City** → **Month** → **Day** (każda
     kolejna pokazuje tylko to, co pasuje do wcześniejszego wyboru — żadna z nich nie jest
     jedną wielką listą wszystkich miast/dni naraz).
   - Kliknij **"2. Download and add to active tables"** — plik się pobierze (i zapisze w
     lokalnym cache, więc drugi raz nie czeka), wejdzie do listy "Active tables" i od razu
     zostanie zaznaczony.
3. **"Chart"** — rozwijana lista wszystkich 16 wykresów `transit_charts`, opisana kodem i
   nazwą (np. "C9 — dot-and-whisker delay per stop"). Pod listą pojawia się jedno zdanie
   wyjaśniające, co dany wykres właściwie pokazuje. Wybór wykresu automatycznie pokazuje
   tylko te parametry, których ten konkretny wykres używa — reszta jest ukryta.
4. **Parametry** (widoczne zależnie od wykresu) — trasa i wykluczone trasy (**Route(s)** /
   **Exclude route(s)**) wybiera się klikając w siatkę przycisków, nie z rozwijanej listy;
   dla większości wykresów trzeba wybrać dokładnie jedną trasę. Kierunek to również klikane
   przyciski. Reszta (szerokość kubełka czasowego, próg `min n`, próg pokrycia kursu, próg
   bunching itd.) to suwaki. Każda zmiana od razu przerysowuje wykres — nie ma osobnego
   przycisku "generuj". Najedź na etykietę dowolnego pola, żeby zobaczyć krótkie
   wyjaśnienie, co ono robi.
5. Niektóre wykresy (D15, E20, J39) wymagają kilku tabel naraz (D15 — co najmniej 3 dni;
   E20/J39 — co najmniej 2 różne miasta). Jeśli aktywnych tabel jest za mało, zamiast
   wykresu pojawia się czytelny komunikat (⚠️) mówiący, czego brakuje — dodaj kolejne
   tabele przez upload albo katalog online i wybór zniknie sam.
6. Wygenerowany wykres pojawia się poniżej, a pod nim — **"Downloads"**: PNG wykresu, CSV
   z policzonymi liczbami i JSON z parametrami/źródłem (plus HTML, jeśli zaznaczono "Also
   write interactive HTML" dla wykresów, które to obsługują — C9/C10/B6).

## Building the Windows executable yourself

```bat
cd tools\chart_lab
build_installer.bat
```

Produces `dist\chart_lab\chart_lab.exe` (a `--onedir` PyInstaller build — a folder, not a
single file, for faster startup and easier debugging of a first build). The same script runs
in CI (`.github/workflows/chart_lab_release.yml`) on every `chart_lab-v*` tag push.

`chart_lab.spec` bundles the CL-2 example data and the repo's `LICENSE` alongside the code.
It also has to work around one real PyInstaller/Gradio interaction: Gradio reads its own
`.py` source files back off disk at import time (for `.pyi` stub generation), which a normal
PyInstaller build strips down to compiled bytecode — the spec bundles gradio's raw source
via `collect_data_files("gradio", include_py_files=True)` to keep that working frozen.

## License

GPL-3.0-or-later, same as `transit_charts`/`family_a` — this package imports their code
directly (not a subprocess wrapper). Source: https://github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab
