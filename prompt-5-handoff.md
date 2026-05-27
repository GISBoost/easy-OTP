# Kamień 4 — handoff do następnej konwersacji

## 1. Co zostało zrobione (sesja M4 naprawa P0)

### P0 — inflacja zliczeń — NAPRAWIONY

**Root cause:** OTP surfaces mają `NoData = 0.0` (Byte dtype). Stary kod:
```python
data[data == nodata] = 0   # no-op (0 → 0)
accumulator += (data <= threshold)  # 0 <= 30 = True → NoData liczone jako dostępne!
```
Każdy piksel NoData dostawał zliczenie w każdym z 961 kanałów → ~100% valid w
outpucie zamiast ~3%.

**Fix w `easy_otp/core/raster_processing.py`:**
- `count_below_threshold` zmieniona na `list[Path]` zamiast VRT
- Maska: `(data != nodata_val) & (data <= threshold_min)` — NoData wykluczone
- Diagnostyczny `pushInfo` po otwarciu pierwszej powierzchni
- Każdy dataset domykany w pętli (nie 961 otwartych uchwytów)

**Zmiany towarzyszące:**
- `build_surface_vrt` — oznaczona jako DEBUG ARTIFACT ONLY w docstringu
- `run_temporal_accessibility.py` — VRT build owinięty w try/except (warning,
  nie crash); `count_below_threshold` wywołana z `surfaces` (nie `vrt_path`)

### Testy jednostkowe

**`easy_otp/test/test_raster_processing.py`** — 7 testów, wszystkie zielone:
```
test_basic_count                PASSED
test_nodata_zero_excluded       PASSED
test_nodata_negative_excluded   PASSED
test_threshold_boundary         PASSED
test_output_zero_is_nodata      PASSED
test_cancellation               PASSED
test_empty_surfaces_raises      PASSED
```

Uruchomienie w QGIS Python console:
```python
import sys; sys.path.insert(0, r"C:\Users\Michal\Desktop\easy-OTP")
import pytest
pytest.main([
    r"C:\Users\Michal\Desktop\easy-OTP\easy_otp\test\test_raster_processing.py",
    "-v", "--tb=short", "-p", "no:faulthandler"
])
```

## 2. Diagnostyka porównania source surfaces

Porównano `surface_06-00-00.tiff` ze starego i nowego setu:
```
Old: NoData=0.0, dtype=Byte, min/max=1–128, pixel(384,512) = 29
New: NoData=0.0, dtype=Byte, min/max=1–128, pixel(384,512) = 30
Arrays equal: False
```
**Wniosek:** Różnica leży w source surfaces, nie w algorytmie zliczania. Ten sam
algorytm (`CountFromExistingSurfaces`) daje różne wyniki dla różnych surfac'ów.
Stary set daje niższe wartości (~2.84% valid), nowy daje wyższe (~2.96% valid).
Prawdopodobna przyczyna: minimalnie różny origin (snapping do innego węzła grafu OTP)
lub inne parametry OTP. **Algorytm jest poprawny.**

## 4. Wyniki T3 (porównanie z wro_under_30.tif)

```
Shapes: (768, 1024) (768, 1024)
Arrays equal: False
Max abs diff: 961
New valid%: 2.96
Ref valid%: 2.84
```

Diff raster (new − old):
```
STATISTICS_MINIMUM=-45
STATISTICS_MAXIMUM=660
STATISTICS_MEAN=21.875
STATISTICS_STDDEV=68.28
STATISTICS_VALID_PERCENT=2.842
```

### Interpretacja różnic

Dobra wiadomość: P0 jest naprawiony — valid% obu rastrów jest zbliżone (~3%),
wcześniej new był ~100%. Pozostałe różnice:

- `Max abs diff = 961` = dokładnie liczba surface'ów → to piksele graniczne,
  które są valid w nowym a NoData w referencji (lub odwrotnie)
- `mean = 21.875` (nowy ma systematycznie trochę więcej) → może być efekt
  nieznacznie innych współrzędnych origin lub różnic w danych GTFS
- `valid% diff = 2.842` → tylko ~2.8% pikseli ma w ogóle jakąś różnicę

### Hipotezy co do przyczyn

1. **Różne origin coordinates** — nawet ułamek stopnia powoduje snap do innego
   węzła grafu OTP → inne trasy, inne zliczenia
2. **Różne dane GTFS/PBF** użyte do budowania grafu w reference vs nowy run
3. **`wro_under_30.tif` generowany STARYM logiką** (bez fix NoData) — jeśli
   referencyjny `tiffs_wro.tif` miał NoData ≠ 0 (np. -9999), wówczas stary
   skrypt zamieniał -9999 → 0, a 0 ≤ 30 = True, więc reference TEŻ mógł
   być zawyżony dla pikseli NoData. Porównanie z "zawyżoną referencją" będzie
   dawać diff.

### Rekomendacja przed eskalacją

Zamiast kolejnego 22-minutowego runu OTP — użyj nowego algorytmu
`CountFromExistingSurfaces` (patrz sekcja 3) żeby przeliczyć istniejące
surfaces bez OTP i wyeliminować zmienność z różnych runów serwera.

## 5. Nowy algorytm — CountFromExistingSurfaces (commit `a2638d5`+)

**Plik:** `easy_otp/algorithms/count_from_surfaces.py`

Algorytm Processing w tej samej grupie "Analysis" co `RunTemporalAccessibility`.
Parametry:
- `SURFACES_FOLDER` — folder z `surface_*.tiff` (QgsProcessingParameterFile, Folder)
- `TRAVEL_TIME_THRESHOLD` — integer 1–120, default 30
- `OUTPUT_COUNT_RASTER` — raster output

Deleguje całą logikę do `count_below_threshold` z `core/raster_processing.py`.

Zarejestrowany w `provider.py` między `RunTemporalAccessibility` a `TestOtpServer`.

## 6. Co dalej (dla następnej konwersacji)

### Priorytet 1: Weryfikacja T3 przez CountFromExistingSurfaces

1. W QGIS: przeładuj wtyczkę (lub restart), otwórz Processing Toolbox
2. easy-OTP → Analysis → "Count reachable minutes from existing surfaces"
3. Wskaż folder z wcześniej wygenerowanymi surface_*.tiff (np.
   `C:\Users\Michal\otp_data\surfaces\`)
4. Threshold = 30, uruchom
5. Porównaj wynik z `wro_under_30.tif` skryptem T3:
```python
from osgeo import gdal; import numpy as np
ds_new = gdal.Open(r"<twoja ścieżka count rastra>")
ds_ref = gdal.Open(r"C:/Users/Michal/otp_data/scripts/wro_under_30.tif")
a = ds_new.GetRasterBand(1).ReadAsArray()
b = ds_ref.GetRasterBand(1).ReadAsArray()
print("Equal:", np.array_equal(a, b))
print("Max diff:", int(np.abs(a.astype(int) - b.astype(int)).max()))
print("New valid%:", round(np.count_nonzero(a)/a.size*100, 2))
print("Ref valid%:", round(np.count_nonzero(b)/b.size*100, 2))
```

### Priorytet 2: LP-1 — TIME_START / TIME_END ignorowane

Z `prompt-4-issue.md` sekcja 4, LP-1. Przed fixem dodać diagnostic pushInfo:
```python
feedback.pushInfo(f"DEBUG TIME_START raw={parameters.get(self.TIME_START)!r}")
feedback.pushInfo(f"DEBUG TIME_START qdt={qdt_start.toString('hh:mm:ss')!r}")
```
Uruchomić z TIME_END=10:00, 60-min interval → powinno być 5 surfaces, nie 17.
Fixować dopiero po potwierdzeniu co konkretnie nie wchodzi.

### Priorytet 3: Kamień 5

Po zamknięciu T3 → Kamień 5: zonal stats na siatce hex + klasyfikacja
4-kategorialna + styl QML (patrz PR sekcja 11, punkt 5).

## 7. Pliki kluczowe

| Plik | Rola |
|------|------|
| `easy_otp/core/raster_processing.py` | count_below_threshold (naprawiony) |
| `easy_otp/algorithms/count_from_surfaces.py` | nowy algorytm |
| `easy_otp/algorithms/run_temporal_accessibility.py` | główny pipeline |
| `easy_otp/provider.py` | rejestracja algorytmów |
| `easy_otp/test/test_raster_processing.py` | testy jednostkowe |
| `docs/PR_easy-OTP.md` | specyfikacja — źródło prawdy |
| `reference/skrypt_wro.py` | wzorzec logiki zliczania (read-only) |
