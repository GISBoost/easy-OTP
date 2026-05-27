# Milestone 5 — Strefy i klasyfikacja: notatki implementacyjne

## Co zostało zbudowane

### Nowe pliki

**`easy_otp/core/zonal.py`**
Trzy funkcje publiczne:
- `run_zonal_stats(count_raster_path, hex_layer, context, feedback)` — wywołuje
  `native:zonalstatisticsfb` z prefiksem kolumny `otp_`; wynikowe pole to `otp_mean`
  (Float64). CRS mismatch między rastrem a siatką obsługiwany wewnętrznie przez
  algorytm natywny QGIS (nie wykonujemy reprojekcji rastra).
- `classify_service_time(zonal_layer, feedback, mean_field="otp_mean", interval_min=1)` —
  dodaje pole `st_class` (String) z 4 kategoriami. Wartość `otp_mean * interval_min`
  przeliczana na minuty przed porównaniem z progami artykułu (720/360/180). NULL i 0 → NULL.
- `log_summary_stats(layer, feedback)` — loguje tabelę podsumowującą liczby i % komórek
  w każdej kategorii.

**`easy_otp/styles/service_time.qml`**
Kategoryzowany renderer QGIS na polu `st_class`. Paleta:
- constantly accessible (≥12h): `#1a9641` (ciemna zieleń)
- regularly accessible (6–12h): `#a6d96a` (jasna zieleń)
- periodically accessible (3–6h): `#fdae61` (pomarańcz)
- episodically accessible (0–3h): `#d7191c` (czerwień)
- NULL/niedostępne: niewidoczne

### Zmodyfikowane pliki

**`easy_otp/algorithms/run_temporal_accessibility.py`**
- Po `count_below_threshold`: wywołuje `run_zonal_stats` → `classify_service_time`
  (z `interval_min`) → kopiuje featury do `OUTPUT_HEX` sink.
- `postProcessAlgorithm`: aplikuje `service_time.qml` do wyniku.
- Loguje podsumowanie kategorii.

**`easy_otp/algorithms/count_from_surfaces.py`**
- Dodany parametr `INTERVAL` (enum: 1/15/60 min) — musi odpowiadać interwałowi
  użytemu przy generowaniu powierzchni.
- Opcjonalne parametry `HEX_GRID` i `OUTPUT_HEX` — jeśli podane, uruchamia pełny
  pipeline (count → zonal → classify → styl).
- `postProcessAlgorithm`: j.w.

## Naprawione bugi

| Bug | Przyczyna | Fix |
|-----|-----------|-----|
| Błędne wartości na krawędziach hexagonów | `gdal:warpreproject` wprowadzał artefakty pikselowe na krawędziach przed `native:zonalstatisticsfb` | Usunięto krok reprojekcji rastra; CRS obsługiwane wewnętrznie przez zonal stats |
| Kolizja nazwy pola `_mean` | Prefiks `_` kolidował z istniejącymi polami jeśli hex grid był wynikiem poprzedniego runu | Zmieniono prefiks na `otp_` → pole `otp_mean` |
| Wszystkie hexagony czerwone przy interwale 60 min | `otp_mean` liczy timestampy, nie minuty; progi (720/360/180) zakładały 1-min interwał | Mnożnik `interval_min` w `classify_service_time`: `service_min = otp_mean * interval_min` |

## Znane ograniczenia / future work

### Subfolder surfaces per analiza (M7/twardnienie)
Aktualnie: `surfaces_dir = work_dir / "surfaces"` — jeden folder na wszystkie analizy
per `work_dir`. Wielokrotne uruchomienia z różnymi parametrami (data, punkt startowy,
interwał) nadpisują pliki powierzchni. `CountFromExistingSurfaces` jest na to narażony
najbardziej — bierze WSZYSTKIE `surface_*.tiff` z podanego folderu.

**Proponowany fix:** `surfaces_dir = work_dir / "surfaces" / f"{date_s}_{router_id}"`.
Wymaga zmiany w `run_temporal_accessibility.py` i aktualizacji `CountFromExistingSurfaces`
(użytkownik wskazuje konkretny subfolder).

### Rozbieżności CountFromExistingSurfaces vs główny algorytm
Jeśli folder surfaces zawiera pliki z wielu uruchomień (różne interwały, daty),
`CountFromExistingSurfaces` daje inne wyniki niż ostatni run głównego algorytmu.
Tymczasowe obejście: przed uruchomieniem `CountFromExistingSurfaces` ręcznie
wyczyścić folder surfaces lub wskazać folder z poprzednim runem.
(Automatyczne czyszczenie przy starcie generowania jest niemożliwe na Windows
— QGIS trzyma uchwyty do plików TIF z poprzedniego runu.)

**Konkretny scenariusz:** uruchomienie głównego algorytmu z interwałem 1 min
(961 plików), a następnie `CountFromExistingSurfaces` z interwałem 60 min
wskazującym ten sam folder — algorytm znajdzie 961 plików i przemnożnik ×60
spowoduje drastycznie zawyżone wartości `service_min` (np. `otp_mean=900 × 60
= 54 000 min`), co sklasyfikuje prawie wszystko jako "constantly accessible".
Fix: ten sam co subfolder per analiza — po wdrożeniu M7/twardnienia user będzie
wskazywał konkretny subfolder z 17 plikami, nie folder z 961.

## Progi klasyfikacji (z artykułu)

| Kategoria | Zakres | Kolor |
|-----------|--------|-------|
| constantly accessible | ≥ 720 min (12h) | `#1a9641` |
| regularly accessible | 360–719 min (6–12h) | `#a6d96a` |
| periodically accessible | 180–359 min (3–6h) | `#fdae61` |
| episodically accessible | 1–179 min (0–3h) | `#d7191c` |
| inaccessible | 0 lub NULL | niewidoczne |
