<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="pl">
    <context>
        <name>BuildRealizedGtfs</name>
        <message>
            <source>Build realized GTFS from RT snapshots</source>
            <translation>Zbuduj zrealizowany feed GTFS z migawek RT</translation>
        </message>
        <message>
            <source>4 · Realtime</source>
            <translation>4 · Czas rzeczywisty</translation>
        </message>
        <message>
            <source>Reconstructs a 'realized timetable' from an RT-2 snapshot archive and a matching static GTFS feed.

Outputs two modified GTFS .zip files:
  &lt;prefix&gt;_p50.zip — P50 (median) segment travel times (typical conditions)
  &lt;prefix&gt;_p85.zip — P85 (85th percentile) travel times (reliability / TTV)

Run the standard RunTemporalAccessibility algorithm on each output to compare schedule-based, median-realized, and reliability-adjusted accessibility.

Method: Braga et al. (2023) stop-pair segment aggregation across all trips and days. Segments with no observations keep their scheduled duration (gap). CANCELED trips are dropped from the output.

IMPORTANT — same-day static required:
If the archive's trip_ids embed the service date (e.g. Gdańsk), the static GTFS must be downloaded the same day as the archive. A wrong-day static will yield near-zero overlap and the output will be uncorrected.

Dependency:
This algorithm requires google.protobuf and gtfs-realtime-bindings. If missing, the error message includes install instructions. This is the only easy-OTP feature that needs this dependency.

Methodological limitations:
- TripUpdates carry predicted times / delay offsets, not empirically recorded stop events (Wessel 2017, rt2gtfs 2026). The realized feed reflects predicted operations.
- P50 ≈ typical conditions; P85 ≈ reliability bound (travel-time variability).
- Aggregation is by stop-pair segment across all trips/days, not per trip_id.
- Gaps (unobserved segments) fall back to scheduled travel time.
- Output is a reproducible static GTFS feed; it is not a record of any single actual day.

See docs/RT-3_realized-gtfs-notes.md for full methodology and references.</source>
            <translation>Rekonstruuje „zrealizowany rozkład jazdy” na podstawie archiwum migawek RT-2 i pasującego statycznego feedu GTFS.

Wyprowadza dwa zmodyfikowane pliki GTFS .zip:
  &lt;prefix&gt;_p50.zip — Czasy przejazdu segmentów P50 (warunki typowe)
  &lt;prefix&gt;_p85.zip — Czasy przejazdu segmentów P85 (wiarygodność / TTV)

Uruchom standardowy algorytm RunTemporalAccessibility na każdym z wyników, aby porównać dostępność opartą na rozkładzie, medianową zrealizowaną i skorygowaną pod kątem wiarygodności.

Metoda: Braga et al. (2023) agregacja segmentów par przystanków dla wszystkich przejazdów i dni. Segmenty bez obserwacji zachowują swój zaplanowany czas trwania (luka). PRZEJAZDY ANULOWANE są usuwane z wyniku.

WAŻNE — wymagany statyczny na dany dzień:
Jeśli identyfikatory przejazdów w archiwum zawierają datę usługi (np. Gdańsk), statyczny GTFS musi zostać pobrany tego samego dnia co archiwum. Statyka z innego dnia spowoduje bliskie zeru nakładanie się i wynik nie zostanie skorygowany.

Zależność:
Ten algorytm wymaga google.protobuf oraz gtfs-realtime-bindings. Jeśli są brakujące, komunikat o błędzie zawiera instrukcje instalacji. Jest to jedyna funkcja easy-OTP, która wymaga tej zależności.

Ograniczenia metodologiczne:
- TripUpdates zawierają przewidywane czasy / przesunięcia opóźnień, a nie empirycznie zarejestrowane zdarzenia przystankowe (Wessel 2017, rt2gtfs 2026). Zrealizowany feed odzwierciedla operacje przewidywane.
- P50 ≈ warunki typowe; P85 ≈ granica wiarygodności (zmienność czasu przejazdu).
- Agregacja odbywa się według segmentu pary przystanków dla wszystkich przejazdów/dni, a nie na podstawie pojedynczego trip_id.
- Luki (nieobserwowane segmenty) przyjmują zaplanowany czas trwania.
- Wynik jest powtarzalnym statycznym feedem GTFS; nie jest to zapis jakiegokolwiek konkretnego dnia rzeczywistego.

Zobacz docs/RT-3_realized-gtfs-notes.md po pełną metodologię i odniesienia.</translation>
        </message>
        <message>
            <source>RT-2 snapshot archive directory</source>
            <translation>Katalog archiwum migawek RT-2</translation>
        </message>
        <message>
            <source>Static GTFS feed (.zip, must match archive service date)</source>
            <translation>Statyczny feed GTFS (.zip, musi odpowiadać dacie usługi w archiwum)</translation>
        </message>
        <message>
            <source>Output base name  (saved next to the static GTFS as &lt;name&gt;_p50.zip / &lt;name&gt;_p85.zip)</source>
            <translation>Nazwa bazowa wyjścia (zapisana obok statycznego GTFS jako &lt;name&gt;_p50.zip / &lt;name&gt;_p85.zip)</translation>
        </message>
        <message>
            <source>Also write P85 (85th-percentile) realized feed</source>
            <translation>Zapisz również zrealizowany feed P85 (85. percentyl)</translation>
        </message>
        <message>
            <source>P50 realized GTFS path</source>
            <translation>Ścieżka do zrealizowanego GTFS P50</translation>
        </message>
        <message>
            <source>P85 realized GTFS path (empty if WRITE_P85 is False)</source>
            <translation>Ścieżka do zrealizowanego GTFS P85 (pusta, jeśli WRITE_P85 jest False)</translation>
        </message>
        <message>
            <source>easy-OTP: missing dependency</source>
            <translation>easy-OTP: brak zależności</translation>
        </message>
        <message>
            <source>google.protobuf / gtfs-realtime-bindings is not installed.

It is required by Build Realized GTFS (RT-3) only.

Install it now? (downloads wheels via urllib; requires internet access)

Choosing 'No' will stop the algorithm.</source>
            <translation>Nie zainstalowano google.protobuf / gtfs-realtime-bindings.

Wymagane tylko przez Build Realized GTFS (RT-3).

Zainstalować teraz? (pobiera koła za pomocą urllib; wymaga dostępu do Internetu)

Wybór 'Nie' zatrzyma algorytm.</translation>
        </message>
        <message>
            <source>Dependency not installed — algorithm cancelled.</source>
            <translation>Brak zainstalowanej zależności — algorytm anulowany.</translation>
        </message>
        <message>
            <source>Installing google.protobuf + gtfs-realtime-bindings…</source>
            <translation>Instalowanie google.protobuf + gtfs-realtime-bindings…</translation>
        </message>
        <message>
            <source>Auto-install failed:

%1

Install manually from the OSGeo4W Shell:

    python -m pip install protobuf==3.20.3 gtfs-realtime-bindings==1.0.0

Then restart QGIS.</source>
            <translation>Automatyczna instalacja nie powiodła się:

%1

Zainstaluj ręcznie z Shella OSGeo4W:

    python -m pip install protobuf==3.20.3 gtfs-realtime-bindings==1.0.0

Następnie uruchom QGIS.</translation>
        </message>
        <message>
            <source>Installed successfully.</source>
            <translation>Zainstalowano pomyślnie.</translation>
        </message>
        <message>
            <source>Output base name is required.</source>
            <translation>Wymagana jest nazwa bazowa wyjścia.</translation>
        </message>
        <message>
            <source>No snapshot_*.pb files found in: {snapshot_dir}
Make sure this is an RT-2 archive directory produced by RecordGtfsRt.</source>
            <translation>Nie znaleziono plików snapshot_*.pb w: {snapshot_dir}
Upewnij się, że jest to katalog archiwum RT-2 wygenerowany przez RecordGtfsRt.</translation>
        </message>
        <message>
            <source>Found {len(snapshot_paths)} snapshot(s) in {snapshot_dir.name}</source>
            <translation>Znaleziono {len(snapshot_paths)} migawkę(i) w {snapshot_dir.name}</translation>
        </message>
        <message>
            <source>Loading static GTFS: {static_gtfs}</source>
            <translation>Ładowanie statycznego GTFS: {static_gtfs}</translation>
        </message>
        <message>
            <source>Failed to read static GTFS: {exc}</source>
            <translation>Nie udało się odczytać statycznego GTFS: {exc}</translation>
        </message>
        <message>
            <source>Static index loaded: {len(static_index.all_trip_ids):,} trips, {len(static_index.stop_map):,} stop-time entries</source>
            <translation>Załadowano indeks statyczny: {len(static_index.all_trip_ids):,} przejazdów, {len(static_index.stop_map):,} wpisów czasowych przystanków</translation>
        </message>
        <message>
            <source>Checking trip_id overlap (archive vs static)…</source>
            <translation>Sprawdzanie nakładania się ID przejazdu (archiwum vs statyczny)…</translation>
        </message>
        <message>
            <source>Overlap check failed: {exc}</source>
            <translation>Weryfikacja nakładania się nie powiodła: {exc}</translation>
        </message>
        <message>
            <source>Trip-id overlap: {overlap:.0%} ({'OK' if overlap &gt;= 0.05 else 'LOW — see warning below'})</source>
            <translation>Pokrycie trip-id: {overlap:.0%} ({'OK' if overlap &gt;= 0.05 else 'LOW — see warning below'})</translation>
        </message>
        <message>
            <source>Only {overlap:.0%} of TripUpdate trip_ids are present in the static feed. Likely causes:
  • The static GTFS is from a different service date than the archive (feeds whose trip_ids embed the date, e.g. Gdańsk).
  • The static GTFS is from a different city or agency.
The output will be produced but most segments will be uncorrected (gaps falling back to scheduled times).</source>
            <translation>Tylko {overlap:.0%} ID przejazdów z TripUpdate obecne jest w feedzie statycznym. Prawdopodobne przyczyny:
  • Statyczny GTFS pochodzi z innego dnia usługi niż archiwum (feedy, których ID przejazdu zawierają datę, np. Gdańsk).
  • Statyczny GTFS pochodzi z innego miasta lub agencji.
Wynik zostanie wygenerowany, ale większość segmentów będzie niepoprawna (luki będą bazować na czasach rozkładu).</translation>
        </message>
        <message>
            <source>Parsing {len(snapshot_paths)} snapshot(s)…</source>
            <translation>Parsowanie {len(snapshot_paths)} zrzutu/zrzutów…</translation>
        </message>
        <message>
            <source>Segments observed: {len(segment_times):,}  |  CANCELED trips: {len(canceled_trip_ids)}</source>
            <translation>Zaobserwowano segmenty: {len(segment_times):,}  |  ANULOWANE przejazdy: {len(canceled_trip_ids)}</translation>
        </message>
        <message>
            <source>Aggregating segment statistics (P50, P85)…</source>
            <translation>Agregowanie statystyk segmentów (P50, P85)…</translation>
        </message>
        <message>
            <source>Rebuilding stop_times for P50 feed…</source>
            <translation>Przebudowywanie czasów przystanków dla feedu P50…</translation>
        </message>
        <message>
            <source>Rebuilding stop_times for P85 feed…</source>
            <translation>Przebudowywanie czasów przystanków dla feedu P85…</translation>
        </message>
        <message>
            <source>Writing P50 feed → </source>
            <translation>Zapisywanie feedu P50 → </translation>
        </message>
        <message>
            <source>Writing P85 feed → </source>
            <translation>Zapisywanie feedu P85 → </translation>
        </message>
        <message>
            <source>  P85 corrected    : {0}  |  gaps: {1}
</source>
            <translation>  P85 skorygowany    : {0}  |  luki: {1}
</translation>
        </message>
        <message>
            <source>
Done.
  Snapshots parsed : {0}
  Segments observed: {1}
  P50 corrected    : {2}  |  gaps: {3}
</source>
            <translation>
Gotowe.
  Parsowane zrzuty : {0}
  Zaobserwowano segmenty: {1}
  P50 skorygowany    : {2}  |  luki: {3}
</translation>
        </message>
        <message>
            <source>  Trips dropped    : {0} (CANCELED, policy=skip)
  Trips in output  : {1}
  P50 feed         : {2}
</source>
            <translation>  Przejazdy odrzucone    : {0} (ANULOWANE, polityka=pomijanie)
  Przejazdy w wyjściu  : {1}
  Feed P50         : {2}
</translation>
        </message>
        <message>
            <source>  P85 feed         : {0}
</source>
            <translation>  Feed P85         : {0}
</translation>
        </message>
        <message>
            <source>BuildRealizedGtfs failed: {exc}</source>
            <translation>BuildRealizedGtfs nie powiódł się: {exc}</translation>
        </message>
    </context>
    <context>
        <name>CompareTemporalAccessibility</name>
        <message>
            <source>Compare temporal accessibility</source>
            <translation>Porównanie dostępności czasowej</translation>
        </message>
        <message>
            <source>3 · Analysis</source>
            <translation>3 · Analiza</translation>
        </message>
        <message>
            <source>Runs the full temporal-accessibility pipeline twice — once for GTFS scenario A and once for scenario B — using a shared OSM extract, origin point, and time window.  Each scenario has its own analysis date, enabling date-vs-date comparisons (e.g. summer vs. winter timetable) in addition to feed-vs-feed comparisons.

After both pipelines complete, subtracts the two count rasters (delta = count_B − count_A) and aggregates the result onto a shared hex grid.  Outputs three hex layers:
  • OUTPUT_HEX_A — service-time classification for scenario A
  • OUTPUT_HEX_B — service-time classification for scenario B
  • OUTPUT_HEX_DELTA — delta_mean (minutes) and delta_class
    (improved / unchanged / degraded)

Intermediate rasters (count_A.tif, count_B.tif, delta.tif) are saved to the working directory for inspection.

Requires Java 8 and otp-1.5.0-shaded.jar.  Runs two OTP server instances sequentially on the same port.</source>
            <translation>Uruchamia pełny potok analizy dostępności czasowej dwukrotnie — raz dla scenariusza GTFS A, a raz dla scenariusza B — korzystając z wspólnego ekstrakta OSM, punktu początkowego i okna czasowego. Każdy scenariusz ma własną datę analizy, co umożliwia porównania typu data-vs-data (np. rozkład jazdy letni vs. zimowy) oprócz porównań feed-vs-feed.

Pojedynczo po zakończeniu obu potoków odejmuje dwa rastry liczników (delta = count_B − count_A) i agreguje wynik na wspólną siatkę heksagonalną. Generuje trzy warstwy heksagonalne:
  • OUTPUT_HEX_A — klasyfikacja czasu usługi dla scenariusza A
  • OUTPUT_HEX_B — klasyfikacja czasu usługi dla scenariusza B
  • OUTPUT_HEX_DELTA — delta_mean (minuty) i delta_class
    (poprawiony / niezmieniony / pogorszony)

Współrzędne pośrednie (count_A.tif, count_B.tif, delta.tif) są zapisywane w katalogu roboczym do inspekcji.

Wymaga Java 8 i otp-1.5.0-shaded.jar. Uruchamia dwie instancje serwera OTP sekwencyjnie na tym samym porcie.</translation>
        </message>
        <message>
            <source>OSM extract (.osm.pbf) — shared by both scenarios</source>
            <translation>Ekstrakt OSM (.osm.pbf) — wspólny dla obu scenariuszy</translation>
        </message>
        <message>
            <source>GTFS feed A (.zip) — scenario A (baseline)</source>
            <translation>Feed GTFS A (.zip) — scenariusz A (bazowy)</translation>
        </message>
        <message>
            <source>GTFS feed B (.zip) — scenario B (comparison)</source>
            <translation>Feed GTFS B (.zip) — scenariusz B (porównawczy)</translation>
        </message>
        <message>
            <source>Origin point — shared by both scenarios (OTP fromPlace)</source>
            <translation>Punkt początkowy — wspólny dla obu scenariuszy (OTP fromPlace)</translation>
        </message>
        <message>
            <source>Hexagonal grid (polygon layer; leave blank when 'Generate hex grid' is checked)</source>
            <translation>Siatka heksagonalna (warstwa wielokątów; zostaw puste, gdy zaznaczono 'Generuj siatkę heksagonalną')</translation>
        </message>
        <message>
            <source>Generate hex grid from scenario A extent</source>
            <translation>Generuj siatkę heksagonalną z obszaru scenariusza A</translation>
        </message>
        <message>
            <source>Hex grid cell size (m)</source>
            <translation>Rozmiar komórki siatki heksagonalnej (m)</translation>
        </message>
        <message>
            <source>Analysis date — scenario A</source>
            <translation>Data analizy — scenariusz A</translation>
        </message>
        <message>
            <source>Analysis date — scenario B (leave same as A for GTFS comparison)</source>
            <translation>Data analizy — scenariusz B (zostaw taką samą jak dla A w celu porównania GTFS)</translation>
        </message>
        <message>
            <source>Window start time</source>
            <translation>Czas początkowy okna</translation>
        </message>
        <message>
            <source>Window end time</source>
            <translation>Czas końcowy okna</translation>
        </message>
        <message>
            <source>Sampling interval (minutes)</source>
            <translation>Interwał próbkowania (minuty)</translation>
        </message>
        <message>
            <source>Travel-time threshold (min) — shared by both scenarios</source>
            <translation>Próg czasu podróży (min) — wspólny dla obu scenariuszy</translation>
        </message>
        <message>
            <source>Arrive by (reverse routing — measure latest departure to arrive at destination by T)</source>
            <translation>Przybycie do (odwrotne routowanie — mierzenie najpóźniejszego odjazdu, aby dotrzeć na cel do T)</translation>
        </message>
        <message>
            <source>Minimum delta for 'improved' class (min) — delta_mean ≥ this value → improved</source>
            <translation>Minimalna delta dla klasy 'poprawiony' (min) — delta_mean ≥ ta wartość → poprawiony</translation>
        </message>
        <message>
            <source>Maximum delta for 'degraded' class (min) — delta_mean ≤ this value → degraded</source>
            <translation>Maksymalna delta dla klasy 'pogorszony' (min) — delta_mean ≤ ta wartość → pogorszony</translation>
        </message>
        <message>
            <source>Walk reluctance</source>
            <translation>Niechęć do chodzenia</translation>
        </message>
        <message>
            <source>Wait reluctance</source>
            <translation>Niechęć do oczekiwania</translation>
        </message>
        <message>
            <source>Transfer penalty (s)</source>
            <translation>Kara za przesiadkę (s)</translation>
        </message>
        <message>
            <source>Minimum transfer time (s)</source>
            <translation>Minimalny czas przesiadki (s)</translation>
        </message>
        <message>
            <source>Maximum walk distance (m) — limited effect in OTP analyst mode</source>
            <translation>Maksymalna odległość piesza (m) — ograniczony efekt w trybie analityka OTP</translation>
        </message>
        <message>
            <source>Walk speed (m/s)</source>
            <translation>Prędkość chodu (m/s)</translation>
        </message>
        <message>
            <source>Use Java path saved by 'Download Java Runtime Environment' (QSettings)</source>
            <translation>Użyj ścieżki Java zapisanej przez 'Pobierz środowisko uruchomieniowe Java' (QSettings)</translation>
        </message>
        <message>
            <source>Java 8 binary</source>
            <translation>Binarka Java 8</translation>
        </message>
        <message>
            <source>OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)</source>
            <translation>Plik jar OpenTripPlanner 1.5.0 (otp-1.5.0-shaded.jar)</translation>
        </message>
        <message>
            <source>OTP heap for graph build (e.g. 2G)</source>
            <translation>Pamięć heap OTP do budowania grafu (np. 2G)</translation>
        </message>
        <message>
            <source>OTP heap for analyst server (e.g. 4G)</source>
            <translation>Pamięć heap OTP dla serwera analityka (np. 4G)</translation>
        </message>
        <message>
            <source>OTP server port — reused sequentially for A then B</source>
            <translation>Port serwera OTP — używany sekwencyjnie dla A, a następnie B</translation>
        </message>
        <message>
            <source>Existing graph router directory for scenario A (skip build)</source>
            <translation>Istniejący katalog routera grafu dla scenariusza A (pomijanie budowania)</translation>
        </message>
        <message>
            <source>Existing graph router directory for scenario B (skip build)</source>
            <translation>Istniejący katalog routera grafu dla scenariusza B (pomijanie budowania)</translation>
        </message>
        <message>
            <source>Keep OTP server alive after run (applies to scenario B's server; scenario A's server is always stopped to free the port)</source>
            <translation>Utrzymuj serwer OTP aktywny po uruchomieniu (dotyczy serwera scenariusza B; serwer scenariusza A jest zawsze zatrzymywany, aby zwolnić port)</translation>
        </message>
        <message>
            <source>Working directory (intermediate surfaces, graphs, count rasters)</source>
            <translation>Katalog roboczy (powierzchnie pośrednie, grafy, rastery liczbowe)</translation>
        </message>
        <message>
            <source>Output hex grid — scenario A (service-time classification)</source>
            <translation>Wyjściowy hex grid — scenariusz A (klasyfikacja czasu usługi)</translation>
        </message>
        <message>
            <source>Output hex grid — scenario B (service-time classification)</source>
            <translation>Wyjściowy hex grid — scenariusz B (klasyfikacja czasu usługi)</translation>
        </message>
        <message>
            <source>Output hex grid — delta (delta_mean in minutes, delta_class)</source>
            <translation>Wyjściowy hex grid — delta (delta_mean w minutach, delta_class)</translation>
        </message>
        <message>
            <source>No Java path saved in QSettings. Run 'Download Java Runtime Environment' first, or uncheck 'Use saved Java path (QSettings)' and supply the path manually.</source>
            <translation>Brak ścieżki Java zapisanej w QSettings. Najpierw uruchom 'Pobierz środowisko uruchomieniowe Java', lub odznacz 'Użyj zapisanej ścieżki Java (QSettings)' i podaj ścieżkę ręcznie.</translation>
        </message>
        <message>
            <source>Using Java path from QSettings: {java}</source>
            <translation>Używana ścieżka Java z QSettings: {java}</translation>
        </message>
        <message>
            <source>Java OK: version {java_ver}</source>
            <translation>Java OK: wersja {java_ver}</translation>
        </message>
        <message>
            <source>Download otp-1.5.0-shaded.jar from Maven Central (groupId=org.opentripplanner, artifactId=otp, version=1.5.0, classifier=shaded) and set the 'OpenTripPlanner 1.5.0 jar' parameter.</source>
            <translation>Pobierz otp-1.5.0-shaded.jar z Maven Central (groupId=org.opentripplanner, artifactId=otp, version=1.5.0, classifier=shaded) i ustaw parametr 'OpenTripPlanner 1.5.0 jar'.</translation>
        </message>
        <message>
            <source>Working directory is required.</source>
            <translation>Wymagane jest katalog roboczy.</translation>
        </message>
        <message>
            <source>HEX_GRID is required when 'Generate hex grid' is unchecked. Supply a polygon layer or enable the 'Generate hex grid' option.</source>
            <translation>HEX_GRID jest wymagany, gdy opcja „Generuj siatkę hex” nie jest zaznaczona. Podaj warstwę wielokątów lub włącz opcję „Generuj siatkę hex”.</translation>
        </message>
        <message>
            <source>Origin (lat, lon) sent to OTP: ({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})</source>
            <translation>Początek (lat, lon) wysłany do OTP: ({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})</translation>
        </message>
        <message>
            <source>Analysis dates: A = {date_s_a}, B = {date_s_b}</source>
            <translation>Daty analizy: A = {date_s_a}, B = {date_s_b}</translation>
        </message>
        <message>
            <source>Sampling interval ({interval_min} min) is longer than the analysis window.</source>
            <translation>Próbkowanie ({interval_min} min) jest dłuższe niż okno analizy.</translation>
        </message>
        <message>
            <source>Invalid time window: {e}</source>
            <translation>Nieprawidłowe okno czasowe: {e}</translation>
        </message>
        <message>
            <source>Sampling {len(time_list)} surfaces at {interval_min}-min interval ({time_list[0]}–{time_list[-1]}).</source>
            <translation>Próbkowanie {len(time_list)} powierzchni z interwałem co {interval_min}-min ({time_list[0]}–{time_list[-1]}).</translation>
        </message>
        <message>
            <source>=== Pipeline A: building graph and generating surfaces ===</source>
            <translation>=== Potok A: budowanie grafu i generowanie powierzchni ===</translation>
        </message>
        <message>
            <source>Run cancelled by user.</source>
            <translation>Przerwano przez użytkownika.</translation>
        </message>
        <message>
            <source>=== Pipeline B: building graph and generating surfaces ===</source>
            <translation>=== Potok B: budowanie grafu i generowanie powierzchni ===</translation>
        </message>
        <message>
            <source>Computing delta raster (count_B − count_A)…</source>
            <translation>Obliczanie rastra różnicowego (count_B − count_A)…</translation>
        </message>
        <message>
            <source>Generating hex grid from scenario A count raster extent (cell size {cell_size} m)…</source>
            <translation>Generowanie siatki hex z zasięgu rastra liczników scenariusza A (rozmiar komórki {cell_size} m)…</translation>
        </message>
        <message>
            <source>No pixels were accessible in scenario A within the travel-time threshold. Check ORIGIN_POINT and TRAVEL_TIME_THRESHOLD, or supply a HEX_GRID layer manually.</source>
            <translation>W scenariuszu A nie znaleziono dostępnych pikseli w ramach progu czasu podróży. Sprawdź ORIGIN_POINT i TRAVEL_TIME_THRESHOLD lub podaj warstwę HEX_GRID ręcznie.</translation>
        </message>
        <message>
            <source>Running zonal statistics for scenario A…</source>
            <translation>Uruchamianie statystyk strefowych dla scenariusza A…</translation>
        </message>
        <message>
            <source>Running zonal statistics for scenario B…</source>
            <translation>Uruchamianie statystyk strefowych dla scenariusza B…</translation>
        </message>
        <message>
            <source>Running zonal statistics for delta raster…</source>
            <translation>Uruchamianie statystyk strefowych dla rastra różnicowego…</translation>
        </message>
        <message>
            <source>Comparison pipeline complete. Three hex layers written.</source>
            <translation>Potok porównawczy zakończony. Zapisano trzy warstwy hex.</translation>
        </message>
        <message>
            <source>EXISTING_GRAPH_DIR_{label} does not contain Graph.obj: {existing_dir}. Point to the router directory (e.g. …/graphs/abc123/).</source>
            <translation>EXISTING_GRAPH_DIR_{label} nie zawiera Graph.obj: {existing_dir}. Wskaż katalog routera (np. …/graphs/abc123/).</translation>
        </message>
        <message>
            <source>[{label}] Using existing graph: {router_dir} (router_id={router_id}); skipping build.</source>
            <translation>[{label}] Użycie istniejącego grafu: {router_dir} (id routera={router_id}); pomijanie budowania.</translation>
        </message>
        <message>
            <source>[{label}] Router ID: {router_id}</source>
            <translation>[{label}] ID routera: {router_id}</translation>
        </message>
        <message>
            <source>[{label}] Graph cache hit — skipping build.</source>
            <translation>[{label}] Trafienie w pamięci podręcznej grafu — pomijanie budowania.</translation>
        </message>
        <message>
            <source>[{label}] Building OTP graph (this can take minutes)…</source>
            <translation>[{label}] Budowanie grafu OTP (może to trwać kilka minut)…</translation>
        </message>
        <message>
            <source>[{label}] Reusing OTP already running on port {port} (version {ver_str}). Ensure its loaded router matches router_id={router_id}; mismatch will cause surface errors.</source>
            <translation>[{label}] Ponowne użycie już działającego OTP na porcie {port} (wersja {ver_str}). Upewnij się, że załadowany router pasuje do router_id={router_id}; niezgodność spowoduje błędy powierzchniowe.</translation>
        </message>
        <message>
            <source>Port {port} is held by a non-OTP process. Pick a different OTP_PORT or stop the conflicting service.</source>
            <translation>Port {port} jest zajęty przez proces niebędący OTP. Wybierz inny PORT_OTP lub zatrzymaj konfliktujący serwis.</translation>
        </message>
        <message>
            <source>[{label}] Starting OTP server on port {port}…</source>
            <translation>[{label}] Uruchamianie serwera OTP na porcie {port}…</translation>
        </message>
        <message>
            <source>[{label}] Generating {len(time_list)} surface(s)…</source>
            <translation>[{label}] Generowanie {len(time_list)} powierzchni(i)…</translation>
        </message>
        <message>
            <source>[{label}] OTP could not snap the origin point to any vertex in the graph.
Common causes: ORIGIN_POINT is outside the OSM coverage area, or coordinates are swapped (lat/lon).
Original error: {err_text}</source>
            <translation>[{label}] OTP nie mogło dopasować punktu początkowego do żadnego wierzchołka w grafie.
Najczęstsze przyczyny: ORIGIN_POINT znajduje się poza obszarem pokrycia OSM lub współrzędne są zamienione (lat/lon).
Początkowy błąd: {err_text}</translation>
        </message>
        <message>
            <source>[{label}] Surface count mismatch: expected {len(time_list)}, got {len(surfaces)}.</source>
            <translation>[{label}] Niezgodność liczby powierzchni: oczekiwano {len(time_list)}, otrzymano {len(surfaces)}.</translation>
        </message>
        <message>
            <source>[{label}] Generated {len(surfaces)} surface(s) in {surfaces_dir}.</source>
            <translation>[{label}] Wygenerowano {len(surfaces)} powierzchnię(i) w {surfaces_dir}.</translation>
        </message>
        <message>
            <source>[{label}] Debug VRT written: {vrt_path} (visual inspection only).</source>
            <translation>[{label}] Debug VRT zapisany: {vrt_path} (tylko do wizualnej inspekcji).</translation>
        </message>
        <message>
            <source>[{label}] VRT build failed (debug artifact only, pipeline continues): {e}</source>
            <translation>[{label}] Budowa VRT nie powiodła się (tylko artefakt debugowania, potok kontynuuje): {e}</translation>
        </message>
        <message>
            <source>[{label}] Counting pixels ≤ {threshold_min} min across {len(surfaces)} surface(s) → {count_path}</source>
            <translation>[{label}] Liczenie pikseli ≤ {threshold_min} min na {len(surfaces)} powierzchni(ach) → {count_path}</translation>
        </message>
        <message>
            <source>[{label}] Count raster written: {count_path}</source>
            <translation>[{label}] Zapisano raster licznika: {count_path}</translation>
        </message>
        <message>
            <source>Could not fetch router diagnostic: {e}</source>
            <translation>Nie można pobrać diagnostyki routera: {e}</translation>
        </message>
        <message>
            <source>--- OTP router diagnostic ---
hasTransit = {has_transit}; transitServiceStarts = {_epoch_to_iso(transit_starts)}; transitServiceEnds = {_epoch_to_iso(transit_ends)}</source>
            <translation>--- Diagnostyka routera OTP ---
hasTransit = {has_transit}; transitServiceStarts = {_epoch_to_iso(transit_starts)}; transitServiceEnds = {_epoch_to_iso(transit_ends)}</translation>
        </message>
        <message>
            <source>Router polygon bbox (lat, lon): ({min(lats):.4f}, {min(lons):.4f}) .. ({max(lats):.4f}, {max(lons):.4f})</source>
            <translation>Obramowanie (bbox) wielokąta routera (lat, lon): ({min(lats):.4f}, {min(lons):.4f}) .. ({max(lats):.4f}, {max(lons):.4f})</translation>
        </message>
        <message>
            <source>-----------------------------</source>
            <translation>-----------------------------</translation>
        </message>
        <message>
            <source>=== Delta classification summary ===</source>
            <translation>=== Podsumowanie klasyfikacji Delta ===</translation>
        </message>
        <message>
            <source>  {label}: {count} cells ({pct:.1f}%)</source>
            <translation>  {label}: {count} komórek ({pct:.1f}%)</translation>
        </message>
        <message>
            <source>  Total: {total} cells</source>
            <translation>  Łącznie: {total} komórek</translation>
        </message>
        <message>
            <source>ANALYSIS_DATE is a {day_name} ({date_str}). Weekend transit schedules may differ significantly from weekday analyses.</source>
            <translation>DATA_ANALIZY to {day_name} ({date_str}). Rozkłady jazdy weekendowe mogą znacznie różnić się od analiz dni powszednich.</translation>
        </message>
        <message>
            <source>No calendar.txt in {gtfs_path.name} — cannot validate analysis date against GTFS service range.</source>
            <translation>Brak pliku calendar.txt w {gtfs_path.name} — nie można zweryfikować daty analizy względem zakresu usługi GTFS.</translation>
        </message>
        <message>
            <source>{gtfs_path.name}: no services active on {date_str}. OTP may return all-unreachable surfaces for this date.</source>
            <translation>{gtfs_path.name}: żadne usługi aktywne na {date_str}. OTP może zwrócić wszystkie niedostępne powierzchnie dla tej daty.</translation>
        </message>
        <message>
            <source>{gtfs_path.name}: {active} service(s) active on {date_str}.</source>
            <translation>{gtfs_path.name}: {active} usługa(i) aktywna(e) na {date_str}.</translation>
        </message>
        <message>
            <source>Could not read {gtfs_path.name} for date validation: {exc}</source>
            <translation>Nie można odczytać {gtfs_path.name} w celu walidacji daty: {exc}</translation>
        </message>
        <message>
            <source>{label} is required (parameter {key}).</source>
            <translation>{label} jest wymagany (parametr {key}).</translation>
        </message>
        <message>
            <source>{label} not found at: {path} (parameter {key}).</source>
            <translation>{label} nie znaleziono pod adresem: {path} (parametr {key}).</translation>
        </message>
    </context>
    <context>
        <name>CountFromExistingSurfaces</name>
        <message>
            <source>Count reachable minutes from existing surfaces</source>
            <translation>Liczba minut dostępnych z istniejących powierzchni</translation>
        </message>
        <message>
            <source>3 · Analysis</source>
            <translation>3 · Analiza</translation>
        </message>
        <message>
            <source>Counts, for each pixel, how many surface_*.tiff files in the given folder have a travel-time value ≤ TRAVEL_TIME_THRESHOLD. Writes a single-band Int32 GeoTIFF where 0 means NoData (pixel never within threshold).

Set INTERVAL to match the sampling interval used when generating the surfaces — it is used to convert surface counts to minutes for the 4-category classification.

Optionally, if a hexagonal grid is supplied, also runs zonal statistics and 4-category service-time classification, producing an OUTPUT_HEX layer styled with service_time.qml.

Use this to re-run the full pipeline without re-generating surfaces via OTP (~22 min saved).</source>
            <translation>Liczy dla każdego piksela, ile plików surface_*.tiff w podanym folderze ma wartość czasu podróży ≤ TRAVEL_TIME_THRESHOLD. Zapisuje jednobandowy GeoTIFF typu Int32, gdzie 0 oznacza NoData (piksel nigdy nie był w ramach progu).

Ustaw INTERVAL tak, aby odpowiadał interwałowi próbkowania używanemu podczas generowania powierzchni — jest on wykorzystywany do konwersji liczby powierzchni na minuty dla klasyfikacji 4-kategorowej.

Opcjonalnie, jeśli podana jest siatka sześcienna, uruchamia również statystyki strefowe i klasyfikację czasu obsługi w 4 kategoriach, generując warstwę OUTPUT_HEX stylizowaną za pomocą service_time.qml.

Użyj tego do ponownego uruchomienia pełnego potoku bez ponownego generowania powierzchni za pomocą OTP (~22 min oszczędności).</translation>
        </message>
        <message>
            <source>Folder with surface_*.tiff files</source>
            <translation>Folder z plikami surface_*.tiff</translation>
        </message>
        <message>
            <source>Travel-time threshold (minutes)</source>
            <translation>Próg czasu podróży (minuty)</translation>
        </message>
        <message>
            <source>Sampling interval of the surfaces (minutes)</source>
            <translation>Interwał próbkowania powierzchni (minuty)</translation>
        </message>
        <message>
            <source>Output count raster</source>
            <translation>Raster licznika wyjściowego</translation>
        </message>
        <message>
            <source>Hexagonal grid (optional; leave blank when 'Generate hex grid' is checked)</source>
            <translation>Siatka sześcienna (opcjonalnie; pozostaw puste, gdy zaznaczono 'Generuj siatkę sześcienną')</translation>
        </message>
        <message>
            <source>Generate hex grid instead of using supplied layer</source>
            <translation>Generuj siatkę sześcienną zamiast używania podanej warstwy</translation>
        </message>
        <message>
            <source>Hex grid cell size (m)</source>
            <translation>Rozmiar komórki siatki sześciennej (m)</translation>
        </message>
        <message>
            <source>Output hex grid (service-time + classification)</source>
            <translation>Siatka sześcienna wyjściowa (czas obsługi + klasyfikacja)</translation>
        </message>
        <message>
            <source>Export statistics report</source>
            <translation>Eksportuj raport statystyczny</translation>
        </message>
        <message>
            <source>Report file (.xlsx or .csv)</source>
            <translation>Plik raportu (.xlsx lub .csv)</translation>
        </message>
        <message>
            <source>Excel files (*.xlsx);;CSV files (*.csv)</source>
            <translation>Pliki Excel (*.xlsx);;Pliki CSV (*.csv)</translation>
        </message>
        <message>
            <source>No surface_*.tiff files found in: {folder}</source>
            <translation>Nie znaleziono plików surface_*.tiff w: {folder}</translation>
        </message>
        <message>
            <source>Found {len(surfaces)} surface(s) in {folder}. Threshold: {threshold_min} min, interval: {interval_min} min.</source>
            <translation>Znaleziono {len(surfaces)} powierzchnię(y) w {folder}. Próg: {threshold_min} min, interwał: {interval_min} min.</translation>
        </message>
        <message>
            <source>Count raster written: {out_count_path}</source>
            <translation>Raster licznika zapisany: {out_count_path}</translation>
        </message>
        <message>
            <source>Generating hex grid from count raster extent (cell size {cell_size} m)…</source>
            <translation>Generowanie siatki sześciennej z rozpiętości rastera licznika (rozmiar komórki {cell_size} m)…</translation>
        </message>
        <message>
            <source>No pixels were accessible within the travel-time threshold. Check TRAVEL_TIME_THRESHOLD or supply a HEX_GRID layer manually.</source>
            <translation>Nie znaleziono pikseli dostępnych w ramach progu czasu podróży. Sprawdź TRAVEL_TIME_THRESHOLD lub podaj warstwę HEX_GRID ręcznie.</translation>
        </message>
        <message>
            <source>Running zonal statistics on count raster…</source>
            <translation>Uruchamianie statystyk strefowych na rasterze licznika…</translation>
        </message>
        <message>
            <source>Classifying service-time categories…</source>
            <translation>Klasyfikowanie kategorii czasu obsługi...</translation>
        </message>
        <message>
            <source>Statistics report saved to: {actual_path}</source>
            <translation>Raport statystyczny zapisany do: {actual_path}</translation>
        </message>
    </context>
    <context>
        <name>DownloadJre</name>
        <message>
            <source>Download Java 8 JRE and OpenTripPlanner Jar</source>
            <translation>Pobierz Java 8 JRE i plik Jar OpenTripPlanner</translation>
        </message>
        <message>
            <source>1 · Setup</source>
            <translation>1 · Konfiguracja</translation>
        </message>
        <message>
            <source>Downloads a portable Eclipse Temurin 8 JRE (x64) from the public Adoptium API AND otp-1.5.0-shaded.jar from the GitHub Releases page, verifies both files, and saves their paths to QSettings so other easy-OTP algorithms pick them up automatically.

Supported platforms: Windows x64, Linux x64, macOS x64 (Intel). Apple Silicon / ARM Linux are not supported in v0.2 — download a native build manually from https://adoptium.net/temurin/releases/?version=8

Running the algorithm a second time on the same folder detects existing files and exits in seconds (cache hit for each independently).</source>
            <translation>Pobiera przenośne Eclipse Temurin 8 JRE (x64) z publicznego API Adoptium ORAZ otp-1.5.0-shaded.jar ze strony GitHub Releases, weryfikuje oba pliki i zapisuje ich ścieżki w QSettings, aby inne algorytmy easy-OTP mogły je automatycznie wykorzystać.

Obsługiwane platformy: Windows x64, Linux x64, macOS x64 (Intel). Apple Silicon / ARM Linux nie są obsługiwane w wersji v0.2 — pobierz natywną wersję ręcznie z https://adoptium.net/temurin/releases/?version=8

Uruchomienie algorytmu ponownie na tym samym folderze wykrywa istniejące pliki i kończy działanie w ciągu sekund (cache hit dla każdego niezależnie).</translation>
        </message>
        <message>
            <source>Destination folder for JRE and OTP jar</source>
            <translation>Folder docelowy dla JRE i jar OTP</translation>
        </message>
        <message>
            <source>Platform override</source>
            <translation>Nadpisanie platformy</translation>
        </message>
        <message>
            <source>Save paths to QSettings (easy_otp/java_path and easy_otp/otp_jar_path)</source>
            <translation>Zapisz ścieżki do QSettings (easy_otp/java_path i easy_otp/otp_jar_path)</translation>
        </message>
        <message>
            <source>Java binary path</source>
            <translation>Ścieżka do binarki Java</translation>
        </message>
        <message>
            <source>Java version</source>
            <translation>Wersja Java</translation>
        </message>
        <message>
            <source>OTP jar path</source>
            <translation>Ścieżka do jara OTP</translation>
        </message>
        <message>
            <source>Target platform: {os_name} x64</source>
            <translation>Docelowa platforma: {os_name} x64</translation>
        </message>
        <message>
            <source>Existing Java 8 found at {cached}, skipping download.</source>
            <translation>Znaleziono istniejące Java 8 w {cached}, pomijanie pobierania.</translation>
        </message>
        <message>
            <source>Querying Adoptium API for latest Temurin 8 JRE …</source>
            <translation>Zapytanie API Adoptium o najnowsze Temurin 8 JRE …</translation>
        </message>
        <message>
            <source>Found release: {release_name}  ({pkg_name})</source>
            <translation>Znaleziono wydanie: {release_name} ({pkg_name})</translation>
        </message>
        <message>
            <source>Downloading {pkg_link} …</source>
            <translation>Pobieranie {pkg_link} …</translation>
        </message>
        <message>
            <source>Verifying SHA-256 …</source>
            <translation>Weryfikacja SHA-256 …</translation>
        </message>
        <message>
            <source>Extracting archive …</source>
            <translation>Ekstrakcja archiwum …</translation>
        </message>
        <message>
            <source>Could not delete downloaded archive '{archive}': {exc}. You may remove it manually.</source>
            <translation>Nie można usunąć pobranego archiwum '{archive}': {exc}. Możesz je usunąć ręcznie.</translation>
        </message>
        <message>
            <source>Cannot find 'bin/java[.exe]' inside the unpacked archive at '{dest}'. Archive structure may have changed — please report this at https://github.com/GISBoost/easy-OTP/issues</source>
            <translation>Nie można znaleźć 'bin/java[.exe]' wewnątrz rozpakowanego archiwum pod adresem '{dest}'. Struktura archiwum mogła ulec zmianie — proszę zgłosić to na https://github.com/GISBoost/easy-OTP/issues</translation>
        </message>
        <message>
            <source>Unpacked JRE reports version '{version}', expected '1.8.x'. Adoptium API may have returned the wrong asset — please open an issue.</source>
            <translation>Rozpakowane JRE raportuje wersję '{version}', oczekiwano '1.8.x'. API Adoptium mogło zwrócić nieprawidłowy zasób — proszę zgłosić problem.</translation>
        </message>
        <message>
            <source>Java 8 OK: version {version}  ({binary})</source>
            <translation>Java 8 OK: wersja {version} ({binary})</translation>
        </message>
        <message>
            <source>Cancelled before OTP jar download. Java path was already saved to QSettings — run the algorithm again to download the OTP jar.</source>
            <translation>Anulowano przed pobraniem jara OTP. Ścieżka Java została już zapisana w QSettings — uruchom algorytm ponownie, aby pobrać jar OTP.</translation>
        </message>
        <message>
            <source>OTP jar path saved to QSettings (easy_otp/otp_jar_path): {jar_path}</source>
            <translation>Ścieżka do jara OTP zapisana w QSettings (easy_otp/otp_jar_path): {jar_path}</translation>
        </message>
        <message>
            <source>Automatic JRE download in v0.2 supports x64 only. Detected architecture: {machine}. Please download Temurin 8 manually from https://adoptium.net/temurin/releases/?version=8 (native build for your architecture, or x64 build for use under Rosetta 2 on macOS).</source>
            <translation>Automatyczne pobieranie JRE w wersji v0.2 obsługuje tylko x64. Wykryta architektura: {machine}. Proszę ręcznie pobrać Temurin 8 z https://adoptium.net/temurin/releases/?version=8 (wersja natywna dla Twojej architektury lub wersja x64 do użycia pod Rosetta 2 na macOS).</translation>
        </message>
        <message>
            <source>Destination folder '{dest}' does not exist. Create it first or choose an existing folder.</source>
            <translation>Folder docelowy '{dest}' nie istnieje. Proszę go najpierw utworzyć lub wybrać istniejący folder.</translation>
        </message>
        <message>
            <source>Cannot write to '{folder}': administrator rights required.
Choose a folder in your user profile instead, for example:
  C:\Users\{user}\Desktop
  C:\Users\{user}\Documents</source>
            <translation>Nie można zapisać w '{folder}': wymagane uprawnienia administratora.
Proszę wybrać inny folder z profilu użytkownika, na przykład:
  C:\Users\{user}\Desktop
  C:\Users\{user}\Documents</translation>
        </message>
        <message>
            <source>Cannot write to '{folder}': {err}</source>
            <translation>Nie można zapisać w '{folder}': {err}</translation>
        </message>
        <message>
            <source>Not enough disk space in '{dest}'. Need ~{_MIN_FREE_MB} MB, have {free_mb:.0f} MB.</source>
            <translation>Za mało miejsca na dysku w '{dest}'. Potrzebne ~{_MIN_FREE_MB} MB, dostępne {free_mb:.0f} MB.</translation>
        </message>
        <message>
            <source>Unsupported platform '{sys.platform}'. Use the 'Platform override' parameter to select manually.</source>
            <translation>Niesportowana platforma '{sys.platform}'. Użyj parametru 'Platform override', aby wybrać ręcznie.</translation>
        </message>
        <message>
            <source>Cannot reach Adoptium API at https://api.adoptium.net. Check your network connection. ({exc})</source>
            <translation>Nie można dotrzeć do API Adoptium pod adresem https://api.adoptium.net. Sprawdź połączenie sieciowe. ({exc})</translation>
        </message>
        <message>
            <source>No JRE 8 x64 build available for '{os_name}' on Adoptium. Supported combinations: see https://adoptium.net/temurin/releases/?version=8</source>
            <translation>Brak dostępnej wersji JRE 8 x64 dla '{os_name}' na Adoptium. Obsługiwane kombinacje: zobacz https://adoptium.net/temurin/releases/?version=8</translation>
        </message>
        <message>
            <source>Download failed ({url}): {exc}</source>
            <translation>Pobieranie nie powiodło się ({url}): {exc}</translation>
        </message>
        <message>
            <source>Downloaded archive checksum does not match Adoptium API. Likely network corruption — please retry. Expected {expected}, got {got}.</source>
            <translation>Sumowanie kontrolne pobranego archiwum nie zgadza się z API Adoptium. Prawdopodobnie uszkodzenie sieciowe — proszę spróbować ponownie. Oczekiwano {expected}, otrzymano {got}.</translation>
        </message>
        <message>
            <source>Found non-Java-8 JRE directly inside '{dest}' (no top-level subfolder). Please manually remove the existing JRE contents and retry.</source>
            <translation>Znaleziono JRE 8 spoza Java-8 bezpośrednio w '{dest}' (bez głównego podfolderu). Proszę ręcznie usunąć istniejące zawartości JRE i spróbować ponownie.</translation>
        </message>
        <message>
            <source>Found existing JRE at '{old_root}' but it is not Java 8. Removing it before downloading a replacement.</source>
            <translation>Znaleziono istniejące JRE w '{old_root}', ale nie jest to Java 8. Usuwam je przed pobraniem zamiennika.</translation>
        </message>
        <message>
            <source>Could not remove old JRE at '{old_root}': {exc}. Please delete the folder manually and retry.</source>
            <translation>Nie można usunąć starego JRE w '{old_root}': {exc}. Proszę usunąć folder ręcznie i spróbować ponownie.</translation>
        </message>
        <message>
            <source>Java path saved to QSettings (easy_otp/java_path): {binary}</source>
            <translation>Ścieżka Java zapisana w QSettings (easy_otp/java_path): {binary}</translation>
        </message>
        <message>
            <source>Existing OTP jar found at {jar_path}, skipping download.</source>
            <translation>Znaleziono istniejący plik jar OTP pod adresem {jar_path}, pomijam pobieranie.</translation>
        </message>
        <message>
            <source>Not enough disk space for OTP jar in '{dest}'. Need ~{_OTP_JAR_MIN_FREE_MB} MB, have {free_mb:.0f} MB.</source>
            <translation>Za mało miejsca na dysku dla pliku jar OTP w '{dest}'. Potrzebne ~{_OTP_JAR_MIN_FREE_MB} MB, dostępne {free_mb:.0f} MB.</translation>
        </message>
        <message>
            <source>Downloading OTP jar from {_OTP_JAR_URL} …</source>
            <translation>Pobieranie pliku jar OTP z {_OTP_JAR_URL} …</translation>
        </message>
        <message>
            <source>Downloaded OTP jar failed sanity check: must be a valid ZIP file between {_OTP_JAR_MIN_BYTES // (1024 * 1024)} MB and {_OTP_JAR_MAX_BYTES // (1024 * 1024)} MB. The file may be corrupted — please retry.</source>
            <translation>Weryfikacja poprawności pobranego pliku jar OTP nie powiodła się: musi być to ważny plik ZIP o wielkości między {_OTP_JAR_MIN_BYTES // (1024 * 1024)} MB a {_OTP_JAR_MAX_BYTES // (1024 * 1024)} MB. Plik może być uszkodzony — proszę spróbować ponownie.</translation>
        </message>
        <message>
            <source>OTP jar OK: {jar_path}</source>
            <translation>Plik jar OTP OK: {jar_path}</translation>
        </message>
    </context>
    <context>
        <name>DownloadTransitData</name>
        <message>
            <source>Download transit data (OSM + GTFS)</source>
            <translation>Pobierz dane transportowe (OSM + GTFS)</translation>
        </message>
        <message>
            <source>1 · Setup</source>
            <translation>1 · Konfiguracja</translation>
        </message>
        <message>
            <source>Downloads the two data inputs required by Run temporal accessibility:

• An OSM extract (.osm.pbf) from Geofabrik for the named area
• GTFS feed(s) from Transitland v2 for the same area

Use the DOWNLOAD_OSM / DOWNLOAD_GTFS checkboxes to download only what you need — skip OSM if you already have a local .osm.pbf, or skip GTFS if you already have a local feed folder.

OSM data (Geofabrik): https://download.geofabrik.de — licence ODbL
GTFS data (Transitland): https://www.transit.land — licences vary by operator

Expected download sizes: OSM 50–500 MB per voivodeship, GTFS 5–20 MB total for a metropolitan area.

GTFS_API_KEY: a free Transitland API key is required to download GTFS. Sign up at https://www.transit.land — no credit card needed.

OSM extract is cached for 7 days: running the algorithm a second time on the same DEST_DIR skips the OSM download. GTFS feeds are always refreshed (schedules change without a fixed cycle).</source>
            <translation>Pobiera dwa wymagane wejścia danych dla Uruchomienia analizy dostępności czasowej:

• Ekstrakt OSM (.osm.pbf) z Geofabrik dla podanego obszaru
• feed(y) GTFS z Transitland v2 dla tego samego obszaru

Użyj pól wyboru DOWNLOAD_OSM / DOWNLOAD_GTFS, aby pobrać tylko to, czego potrzebujesz — pomiń OSM, jeśli masz już lokalny .osm.pbf, lub pomiń GTFS, jeśli masz już lokalny folder z feedami.

Dane OSM (Geofabrik): https://download.geofabrik.de — licencja ODbL
Dane GTFS (Transitland): https://www.transit.land — licencje różnią się w zależności od operatora

Oczekiwane rozmiary pobierania: OSM 50–500 MB na województwo, GTFS 5–20 MB łącznie dla obszaru metropolitalnego.

GTFS_API_KEY: wymagany jest bezpłatny klucz API Transitland do pobrania GTFS. Zarejestruj się pod adresem https://www.transit.land — nie wymagana karta kredytowa.

Ekstrakt OSM jest buforowany przez 7 dni: ponowne uruchomienie algorytmu dla tego samego DEST_DIR pomija pobieranie OSM. Feedy GTFS są zawsze odświeżane (rozsądki zmieniają się bez stałego cyklu).</translation>
        </message>
        <message>
            <source>Area name (Geofabrik id or name)</source>
            <translation>Nazwa obszaru (ID lub nazwa Geofabrik)</translation>
        </message>
        <message>
            <source>Destination folder</source>
            <translation>Folder docelowy</translation>
        </message>
        <message>
            <source>Download OSM extract (.osm.pbf) from Geofabrik</source>
            <translation>Pobierz ekstrakt OSM (.osm.pbf) z Geofabrik</translation>
        </message>
        <message>
            <source>Download GTFS feed(s) from Transitland</source>
            <translation>Pobierz feed(y) GTFS z Transitland</translation>
        </message>
        <message>
            <source>Transitland API key (required for GTFS download)</source>
            <translation>Klucz API Transitland (wymagany do pobrania GTFS)</translation>
        </message>
        <message>
            <source>OSM extract path</source>
            <translation>Ścieżka ekstraktu OSM</translation>
        </message>
        <message>
            <source>GTFS folder path</source>
            <translation>Ścieżka folderu GTFS</translation>
        </message>
        <message>
            <source>Nothing to download. Enable at least one of DOWNLOAD_OSM / DOWNLOAD_GTFS.</source>
            <translation>Nic do pobrania. Włącz co najmniej jeden z DOWNLOAD_OSM / DOWNLOAD_GTFS.</translation>
        </message>
        <message>
            <source>Loading Geofabrik index …</source>
            <translation>Ładowanie indeksu Geofabrik …</translation>
        </message>
        <message>
            <source>Area '{area_id}' has no .osm.pbf download link in Geofabrik. Try a more specific region (e.g. a voivodeship instead of the whole country).</source>
            <translation>Obszar '{area_id}' nie ma linku do pobrania .osm.pbf w Geofabrik. Spróbuj bardziej szczegółowego regionu (np. województwa zamiast całego kraju).</translation>
        </message>
        <message>
            <source>Found area: '{area_id}'  bbox: [{lon_min:.3f}, {lat_min:.3f}, {lon_max:.3f}, {lat_max:.3f}]</source>
            <translation>Znaleziono obszar: '{area_id}' bbox: [{lon_min:.3f}, {lat_min:.3f}, {lon_max:.3f}, {lat_max:.3f}]</translation>
        </message>
        <message>
            <source>Area '{area_id}' covers {bbox_area:.1f} deg² and is too large for practical routing (limit: {_MAX_BBOX_DEG2} deg²). Use a sub-regional area such as a voivodeship — e.g. 'dolnoslaskie' instead of 'poland'.</source>
            <translation>Obszar '{area_id}' obejmuje {bbox_area:.1f} deg² i jest zbyt duży do praktycznego routingu (limit: {_MAX_BBOX_DEG2} deg²). Użyj obszaru podregionalnego, takiego jak województwo — np. 'dolnoslaskie' zamiast 'poland'.</translation>
        </message>
        <message>
            <source>Using cached OSM extract for '{area_id}'.</source>
            <translation>Używany buforowany ekstrakt OSM dla '{area_id}'.</translation>
        </message>
        <message>
            <source>Downloading OSM extract: {pbf_url} …</source>
            <translation>Pobieranie ekstraktu OSM: {pbf_url} …</translation>
        </message>
        <message>
            <source>Verifying OSM MD5 …</source>
            <translation>Weryfikacja MD5 OSM …</translation>
        </message>
        <message>
            <source>Transitland API requires a free API key. Sign up at https://www.transit.land/documentation/api-key and provide the key in the GTFS_API_KEY parameter.</source>
            <translation>API Transitland wymaga bezpłatnego klucza API. Zarejestruj się pod adresem https://www.transit.land/documentation/api-key i podaj klucz w parametrze GTFS_API_KEY.</translation>
        </message>
        <message>
            <source>Querying Transitland API …</source>
            <translation>Zapytanie do API Transitland …</translation>
        </message>
        <message>
            <source>Skipped {skipped} feed(s) larger than 5× query bbox (continental/national aggregates).</source>
            <translation>Pominięto {skipped} feed(y) większe niż 5× bbox zapytania (agregaty kontynentalne/krajowe).</translation>
        </message>
        <message>
            <source>Found {len(local_feeds)} local feeds — limiting to first {_MAX_GTFS_FEEDS}. Use a more specific region name (e.g. a voivodeship) for a smaller feed set.</source>
            <translation>Znaleziono {len(local_feeds)} lokalnych feedów — ograniczam do pierwszych {_MAX_GTFS_FEEDS}. Użyj bardziej specyficznej nazwy regionu (np. województwa) dla mniejszej liczby feedów.</translation>
        </message>
        <message>
            <source>Feeds to download: {len(local_feeds)}</source>
            <translation>Feedów do pobrania: {len(local_feeds)}</translation>
        </message>
        <message>
            <source>Destination folder '{dest}' does not exist. Create it first or choose an existing folder.</source>
            <translation>Folder docelowy '{dest}' nie istnieje. Utwórz go najpierw lub wybierz istniejący folder.</translation>
        </message>
        <message>
            <source>Destination folder '{dest}' is not writable. Check permissions or choose another folder.</source>
            <translation>Folder docelowy '{dest}' nie jest zapisywalny. Sprawdź uprawnienia lub wybierz inny folder.</translation>
        </message>
        <message>
            <source>Not enough disk space in '{dest}'. Need ~{min_mb} MB, have {free_mb:.0f} MB.</source>
            <translation>Za mało miejsca na dysku w '{dest}'. Potrzebne ~{min_mb} MB, dostępne {free_mb:.0f} MB.</translation>
        </message>
        <message>
            <source>Using cached Geofabrik index.</source>
            <translation>Używany indeks Geofabrik z pamięci podręcznej.</translation>
        </message>
        <message>
            <source>Fetching Geofabrik index from {_GEOFABRIK_INDEX_URL} …</source>
            <translation>Pobieranie indeksu Geofabrik z {_GEOFABRIK_INDEX_URL} …</translation>
        </message>
        <message>
            <source>Cannot reach Geofabrik index at https://download.geofabrik.de. Check your network connection. ({exc})</source>
            <translation>Nie można dotrzeć do indeksu Geofabrik pod adresem https://download.geofabrik.de. Sprawdź połączenie sieciowe. ({exc})</translation>
        </message>
        <message>
            <source>Area '{area_name}' not found in Geofabrik index. Closest matches: {suggestions}.</source>
            <translation>Obszar '{area_name}' nie został znaleziony w indeksie Geofabrik. Najbliższe dopasowania: {suggestions}.</translation>
        </message>
        <message>
            <source>Area '{area_name}' matches multiple regions: {found_ids}. Please use a more specific id.</source>
            <translation>Obszar '{area_name}' pasuje do wielu regionów: {found_ids}. Proszę użyć bardziej specyficznego ID.</translation>
        </message>
        <message>
            <source>Download failed ({url}): {exc}</source>
            <translation>Pobieranie nie powiodło się ({url}): {exc}</translation>
        </message>
        <message>
            <source>Could not fetch MD5 manifest ({exc}). Skipping checksum verification.</source>
            <translation>Nie udało się pobrać manifestu MD5 ({exc}). Pomijam weryfikację sumy kontrolnej.</translation>
        </message>
        <message>
            <source>OSM extract checksum does not match Geofabrik manifest. Likely network corruption — please retry.</source>
            <translation>Suma kontrolna ekstrakcji OSM nie zgadza się z manifestem Geofabrik. Prawdopodobnie uszkodzenie sieciowe — proszę spróbować ponownie.</translation>
        </message>
        <message>
            <source>OSM MD5 OK.</source>
            <translation>MD5 OSM OK.</translation>
        </message>
        <message>
            <source>Transitland API key is invalid or expired. Get a free key at https://www.transit.land — no credit card required.</source>
            <translation>Klucz API Transitland jest nieprawidłowy lub wygasł. Pobierz darmowy klucz pod adresem https://www.transit.land — bez wymagania karty kredytowej.</translation>
        </message>
        <message>
            <source>Transitland API returned HTTP {exc.code}: {exc.reason}</source>
            <translation>API Transitland zwróciło HTTP {exc.code}: {exc.reason}</translation>
        </message>
        <message>
            <source>Cannot reach Transitland API. Check your network connection. ({exc})</source>
            <translation>Nie można dotrzeć do API Transitland. Sprawdź połączenie sieciowe. ({exc})</translation>
        </message>
        <message>
            <source>Transitland: {len(all_feeds)} feed(s) fetched across {page} pages.</source>
            <translation>Transitland: pobrano {len(all_feeds)} feedów na {page} stronach.</translation>
        </message>
        <message>
            <source>No GTFS feeds found in Transitland for the bounding box of '{area_id}'. The GTFS folder will be empty — you can add feeds manually by copying their .zip files into '{gtfs_dir}' after the algorithm finishes.</source>
            <translation>W Transitland nie znaleziono feedów GTFS dla ramki ograniczającej '{area_id}'. Folder GTFS będzie pusty — możesz dodać feedy ręcznie, kopiując ich pliki .zip do '{gtfs_dir}' po zakończeniu algorytmu.</translation>
        </message>
        <message>
            <source>Feed '{onestop_id}' has no static_current URL — skipping.</source>
            <translation>Feed '{onestop_id}' nie ma URL statycznego_current — pomijam.</translation>
        </message>
        <message>
            <source>Downloading GTFS feed '{onestop_id}' …</source>
            <translation>Pobieranie feedu GTFS '{onestop_id}' …</translation>
        </message>
        <message>
            <source>Feed '{onestop_id}': HTTP {exc.code} ({exc.reason}) — skipping.</source>
            <translation>Feed '{onestop_id}': HTTP {exc.code} ({exc.reason}) — pomijanie.</translation>
        </message>
        <message>
            <source>Feed '{onestop_id}': download failed — {exc}</source>
            <translation>Feed '{onestop_id}': nieudane pobranie — {exc}</translation>
        </message>
        <message>
            <source>  Saved: {zip_path.name}  ({size_kb} KB)</source>
            <translation>  Zapisano: {zip_path.name}  ({size_kb} KB)</translation>
        </message>
        <message>
            <source>Feed '{feed_id}': downloaded file is not a valid ZIP archive. The URL may have returned an HTML page instead of a GTFS feed. Add the correct .zip manually.</source>
            <translation>Feed '{feed_id}': pobrany plik nie jest poprawnym archiwum ZIP. URL mógł zwrócić stronę HTML zamiast feedu GTFS. Dodaj ręcznie prawidłowy .zip.</translation>
        </message>
        <message>
            <source>Feed '{feed_id}': missing GTFS files — {issues}. OTP may still load the feed if the missing files are optional.</source>
            <translation>Feed '{feed_id}': brakuje plików GTFS — {issues}. OTP może nadal załadować feed, jeśli brakujące pliki są opcjonalne.</translation>
        </message>
        <message>
            <source>--- Download summary ---</source>
            <translation>--- Podsumowanie pobierania ---</translation>
        </message>
        <message>
            <source>OSM extract : {osm_path}  ({size_mb:.1f} MB)</source>
            <translation>Ekstrakcja OSM : {osm_path}  ({size_mb:.1f} MB)</translation>
        </message>
        <message>
            <source>OSM extract : not downloaded</source>
            <translation>Ekstrakcja OSM : nie pobrano</translation>
        </message>
        <message>
            <source>OSM extract : skipped (DOWNLOAD_OSM=False)</source>
            <translation>Ekstrakcja OSM : pominięto (DOWNLOAD_OSM=False)</translation>
        </message>
        <message>
            <source>GTFS feeds  : {len(zips)} file(s) in {gtfs_dir}</source>
            <translation>Feedy GTFS  : {len(zips)} plik(i) w {gtfs_dir}</translation>
        </message>
        <message>
            <source>  {z.name}  ({size_kb} KB)</source>
            <translation>  {z.name}  ({size_kb} KB)</translation>
        </message>
        <message>
            <source>GTFS feeds  : none (folder: {gtfs_dir})</source>
            <translation>Feedy GTFS  : brak (folder: {gtfs_dir})</translation>
        </message>
        <message>
            <source>GTFS feeds  : skipped (DOWNLOAD_GTFS=False)</source>
            <translation>Feedy GTFS  : pominięto (DOWNLOAD_GTFS=False)</translation>
        </message>
        <message>
            <source>
Ready for RunTemporalAccessibility:
  OSM extract  →  {osm_path}
  GTFS folder  →  {gtfs_dir if download_gtfs else '&lt;your local gtfs folder&gt;'}</source>
            <translation>
Gotowe do RunTemporalAccessibility:
  OSM extract  →  {osm_path}
  GTFS folder  →  {gtfs_dir if download_gtfs else '&lt;your local gtfs folder&gt;'}</translation>
        </message>
    </context>
    <context>
        <name>EasyOtpPlugin</name>
        <message>
            <source>easy-OTP: missing dependency</source>
            <translation>easy-OTP: brak zależności</translation>
        </message>
        <message>
            <source>The openpyxl library is not installed in your QGIS Python environment. It is required by the Prepare Student Layer (R1a) algorithm to read GUS NSP 2021 Excel files.

Install it now? (downloads wheel via urllib, falls back to pip; requires internet access)

Choosing 'No' is safe — all other algorithms work without openpyxl, but R1a will raise an error when run.</source>
            <translation>Biblioteka openpyxl nie jest zainstalowana w środowisku Python QGIS. Jest wymagana przez algorytm Przygotuj warstwę studenta (R1a) do odczytu plików Excel GUS NSP 2021.

Zainstalować teraz? (pobiera koło za pomocą urllib, przechodzi na pip; wymaga dostępu do internetu)

Wybór 'Nie' jest bezpieczny — wszystkie inne algorytmy działają bez openpyxl, ale R1a zgłosi błąd podczas uruchamiania.</translation>
        </message>
        <message>
            <source>easy-OTP</source>
            <translation>easy-OTP</translation>
        </message>
        <message>
            <source>openpyxl installed successfully.</source>
            <translation>openpyxl zainstalowano pomyślnie.</translation>
        </message>
        <message>
            <source>easy-OTP: installation failed</source>
            <translation>easy-OTP: instalacja nieudana</translation>
        </message>
        <message>
            <source>Could not install openpyxl automatically:

%1

You can install it manually by running the following command in the OSGeo4W Shell (Windows) or a terminal with QGIS's Python active:

    python -m pip install openpyxl

Then restart QGIS.</source>
            <translation>Nie udało się automatycznie zainstalować openpyxl:

%1

Mogą Państwo zainstalować to ręcznie, uruchamiając następujące polecenie w Shellu OSGeo4W (Windows) lub w terminalu z aktywnym Pythonem QGIS:

    python -m pip install openpyxl

Następnie uruchom ponownie QGIS.</translation>
        </message>
    </context>
    <context>
        <name>EasyOtpProvider</name>
        <message>
            <source>Easy-OTP</source>
            <translation>Easy-OTP</translation>
        </message>
        <message>
            <source>Easy-OTP — temporal accessibility via OpenTripPlanner</source>
            <translation>Easy-OTP — dostępność czasowa za pomocą OpenTripPlanner</translation>
        </message>
    </context>
    <context>
        <name>GenerateHexGrid</name>
        <message>
            <source>Generate hexagonal grid</source>
            <translation>Generuj siatkę heksagonalną</translation>
        </message>
        <message>
            <source>3 · Analysis</source>
            <translation>3 · Analiza</translation>
        </message>
        <message>
            <source>Generates a hexagonal polygon grid covering the given extent.

Output CRS is EPSG:3857 (Web Mercator). Cell size is the flat-to-flat hexagon width in metres. Default 500 m matches the spatial resolution used in the accessibility article.

Use this to pre-generate the grid for Run temporal accessibility when you need a custom extent or want to inspect the grid first.</source>
            <translation>Generuje siatkę wielokątów heksagonalnych pokrywającą podany obszar.

CRS wyjściowy to EPSG:3857 (Web Mercator). Rozmiar komórki to szerokość heksagonu od płaskiej do płaskiej w metrach. Domyślne 500 m odpowiada rozdzielczości przestrzennej używanej w artykule o dostępności.

Użyj tego do wstępnego wygenerowania siatki dla Uruchomienia temporalnej dostępności, gdy potrzebujesz niestandardowego obszaru lub chcesz najpierw sprawdzić siatkę.</translation>
        </message>
        <message>
            <source>Grid extent</source>
            <translation>Zasięg siatki</translation>
        </message>
        <message>
            <source>Cell size (m)</source>
            <translation>Rozmiar komórki (m)</translation>
        </message>
        <message>
            <source>Hexagonal grid</source>
            <translation>Siatka heksagonalna</translation>
        </message>
    </context>
    <context>
        <name>PopulationOverlay</name>
        <message>
            <source>Population overlay</source>
            <translation>Nakładka demograficzna</translation>
        </message>
        <message>
            <source>3 · Analysis</source>
            <translation>3 · Analiza</translation>
        </message>
        <message>
            <source>Overlays a demographic polygon layer on a hexagonal grid using areal interpolation weighted by surface area.

Each hexagon receives a 'num_students' field (Float) with the estimated number of persons from the chosen population field. The algorithm splits census polygons by hex edges, computes the area-weighted population of each piece, then sums those pieces per hexagon.

The hex grid must be in a projected CRS with metric units (e.g. EPSG:2180, EPSG:3857). If the population layer has a different CRS it is reprojected automatically before processing.</source>
            <translation>Nakłada warstwę poligonów demograficznych na siatkę heksagonalną przy użyciu interpolacji powierzchniowej ważonej powierzchnią.

Każdy heksagon otrzymuje pole 'num_students' (Float) z szacowaną liczbą osób z wybranego pola populacji. Algorytm dzieli poligony spisowe po krawędziach heksagonów, oblicza populację ważoną powierzchnią dla każdego fragmentu, a następnie sumuje te fragmenty w każdym heksagonie.

Siatka heksagonalna musi znajdować się w projekcyjnym CRS z jednostkami metrycznymi (np. EPSG:2180, EPSG:3857). Jeśli warstwa populacji ma inny CRS, zostanie ona automatycznie przekształcona przed przetwarzaniem.</translation>
        </message>
        <message>
            <source>Hex grid</source>
            <translation>Siatka heksagonalna</translation>
        </message>
        <message>
            <source>Population layer</source>
            <translation>Warstwa demograficzna</translation>
        </message>
        <message>
            <source>Population field</source>
            <translation>Pole demograficzne</translation>
        </message>
        <message>
            <source>Output (hex grid with num_students)</source>
            <translation>Wyjście (siatka heksagonalna z num_students)</translation>
        </message>
        <message>
            <source>Hex grid must be in a projected CRS with metric units (e.g. EPSG:2180, EPSG:3857). Got: {}.</source>
            <translation>Siatka heksagonalna musi znajdować się w projekcyjnym CRS z jednostkami metrycznymi (np. EPSG:2180, EPSG:3857). Otrzymano: {}.</translation>
        </message>
        <message>
            <source>Population layer must be polygonal, got '{}'.</source>
            <translation>Warstwa populacji musi być poligonalna, otrzymano '{}'.</translation>
        </message>
        <message>
            <source>Population layer has no field '{}'.</source>
            <translation>Warstwa populacji nie posiada pola '{}'.</translation>
        </message>
        <message>
            <source>Field '{}' must be numeric (Int or Float), got '{}'.</source>
            <translation>Pole '{}' musi być numeryczne (Int lub Float), otrzymano '{}'.</translation>
        </message>
        <message>
            <source>Output field 'num_students' already exists in HEX_GRID. Remove it or rename it before running PopulationOverlay.</source>
            <translation>Pole wyjściowe 'num_students' już istnieje w HEX_GRID. Usuń je lub zmień nazwę przed uruchomieniem PopulationOverlay.</translation>
        </message>
        <message>
            <source>Reprojecting population layer from {} to {}.</source>
            <translation>Przekształcanie warstwy populacji z {} do {}.</translation>
        </message>
        <message>
            <source>{} hexagon(s) have num_students = 0 (not covered by the population layer).</source>
            <translation>{} heksagon(y) mają num_students = 0 (nie są pokryte przez warstwę populacji).</translation>
        </message>
    </context>
    <context>
        <name>PrepareStudentLayer</name>
        <message>
            <source>Prepare student layer</source>
            <translation>Przygotuj warstwę studencką</translation>
        </message>
        <message>
            <source>3 · Analysis</source>
            <translation>3 · Analiza</translation>
        </message>
        <message>
            <source>Reads a GUS NSP 2021 Excel file and joins census-tract population data to a polygon geometry layer.

Handles three observed states of GUS Excel files:
  'raw'   — multi-row header, short symbols; region code forward-filled from preceding 'rejon statystyczny' rows.
  'wrong' — full 7-char keys, but population values are strings with '-' as suppression markers.
  'done'  — clean, numeric values, minimum processing.

Output: a polygon layer with the original geometry attributes plus one added Double field (default 'pop20_29') — ready for use as POPULATION_LAYER in the Population overlay (R1b) algorithm.

Census tract geometry layer: must be the GUS polygon layer of statistical census tracts (obwody spisowe NSP 2021) for your study area. The layer must contain a string field with the census-tract identifier (default 'OBWOD') matching the keys in the Excel file. Download the geometry from the GUS geoportal (https://geo.stat.gov.pl/) or use the GeoJSON published alongside the NSP 2021 results. A shapefile that imported OBWOD as an integer field will lose leading zeros — convert it to text in the Field Calculator before running this algorithm.

Requires openpyxl. If the automatic install at QGIS startup failed (e.g. SSL unavailable in QGIS 3.22), install manually from the OSGeo4W Shell: python -m pip install openpyxl — then restart QGIS.

Input file: download the 'Ludnosc w rejonach statystycznych i obwodach spisowych' table from the GUS NSP 2021 results page (stat.gov.pl/spisy-powszechne/nsp-2021/).</source>
            <translation>Odczytuje plik Excel GUS NSP 2021 i łączy dane populacyjne z obwodów spisowych do warstwy geometrycznej wielokąta.

Obsługuje trzy zaobserwowane stany plików Excel GUS:
  'raw'   — nagłówek w wielu wierszach, krótkie symbole; kod regionu wypełniany do przodu z poprzedzających wierszy 'rejon statystyczny'.
  'wrong' — pełne klucze 7-znakowe, ale wartości populacji są ciągami znaków z '-' jako znacznikami zastąpienia.
  'done'  — czyste, numeryczne wartości, minimalna obróbka.

Wyjście: warstwa wielokąta z oryginalnymi atrybutami geometrycznymi plus jednym dodanym polem typu Double (domyślnie 'pop20_29') — gotowa do użycia jako POPULATION_LAYER w algorytmie nakładki Populacja (R1b).

Warstwa geometrii obwodów spisowych: musi być warstwą wielokąta GUS z obwodów spisowych NSP 2021 dla Twojego obszaru badawczego. Warstwa musi zawierać pole tekstowe z identyfikatorem obwodu spisowego (domyślnie 'OBWOD') pasującym do kluczy w pliku Excel. Pobierz geometrię z geoportalu GUS (https://geo.stat.gov.pl/) lub użyj GeoJSON opublikowanego wraz z wynikami NSP 2021. Plik shapefile, który zaimportował OBWOD jako pole całkowite, straci wiodące zera — przekonwertuj go na tekst w Kalkulatorze pól przed uruchomieniem tego algorytmu.

Wymaga openpyxl. Jeśli automatyczna instalacja przy starcie QGIS się nie powiodła (np. brak SSL w QGIS 3.22), zainstaluj ręcznie z Konsoli OSGeo4W: python -m pip install openpyxl — a następnie uruchom ponownie QGIS.

Plik wejściowy: pobierz tabelę 'Ludność w rejonach statystycznych i obwodach spisowych' ze strony wyników GUS NSP 2021 (stat.gov.pl/spisy-powszechne/nsp-2021/).</translation>
        </message>
        <message>
            <source>GUS NSP 2021 Excel file</source>
            <translation>Plik Excel GUS NSP 2021</translation>
        </message>
        <message>
            <source>Excel files (*.xlsx)</source>
            <translation>Pliki Excel (*.xlsx)</translation>
        </message>
        <message>
            <source>Sheet name (empty = first sheet)</source>
            <translation>Nazwa arkusza (puste = pierwszy arkusz)</translation>
        </message>
        <message>
            <source>Population column name in Excel header</source>
            <translation>Nazwa kolumny populacji w nagłówku Excela</translation>
        </message>
        <message>
            <source>Census tract geometry layer</source>
            <translation>Warstwa geometrii obwodów spisowych</translation>
        </message>
        <message>
            <source>Join key field in geometry layer</source>
            <translation>Pole klucza łączenia w warstwie geometrycznej</translation>
        </message>
        <message>
            <source>Output field name</source>
            <translation>Nazwa pola wyjściowego</translation>
        </message>
        <message>
            <source>Output layer</source>
            <translation>Warstwa wyjściowa</translation>
        </message>
        <message>
            <source>openpyxl is not available. If the automatic install at QGIS startup failed, install manually from the OSGeo4W Shell: python -m pip install openpyxl — then restart QGIS.</source>
            <translation>openpyxl nie jest dostępne. Jeśli automatyczna instalacja przy starcie QGIS się nie powiodła, zainstaluj ręcznie z Konsoli OSGeo4W: python -m pip install openpyxl — a następnie uruchom ponownie QGIS.</translation>
        </message>
        <message>
            <source>Loading Excel file: {}</source>
            <translation>Ładowanie pliku Excel: {}</translation>
        </message>
        <message>
            <source>Excel reader subprocess failed (exit {}):
{}</source>
            <translation>Podprocesor odczytu Excela zakończył się niepowodzeniem (wyjście {}):
{}</translation>
        </message>
        <message>
            <source>Multi-sheet workbook; using first sheet '{}'. All sheets: {}.</source>
            <translation>Książka z wieloma arkuszami; używany pierwszy arkusz '{}'. Wszystkie arkusze: {}.</translation>
        </message>
        <message>
            <source>Could not detect header row. Searched rows 0-29 for columns 'Symbol' and 'Struktura'. Check that the sheet '{}' is correct.</source>
            <translation>Nie udało się wykryć wiersza nagłówka. Sprawdzono wiersze 0-29 pod kątem kolumn 'Symbol' i 'Struktura'. Upewnij się, że arkusz '{}' jest poprawny.</translation>
        </message>
        <message>
            <source>Column '{}' not found in header. Available columns near row {}: {}.</source>
            <translation>Kolumna '{}' nie została znaleziona w nagłówku. Dostępne kolumny w pobliżu wiersza {}: {}.</translation>
        </message>
        <message>
            <source>Header: Symbol/Struktura at row {} (0-based), '{}' at row {}. Columns: Symbol={}, Struktura={}, {}={}.</source>
            <translation>Nagłówek: Symbol/Struktura w wierszu {} (0-bazowy), '{}' w wierszu {}. Kolumny: Symbol={}, Struktura={}, {}={}.</translation>
        </message>
        <message>
            <source>Row {}: census tract '{}' encountered without a preceding 'rejon statystyczny' row. Cannot build join key.</source>
            <translation>Wiersz {}: napotkano obwód spisowy '{}' bez poprzedzającego wiersza 'rejon statystyczny'. Nie można zbudować klucza łączenia.</translation>
        </message>
        <message>
            <source>Row {}: cannot interpret '{}' as a number in column '{}'. Expected a number, an empty cell, or '-'.</source>
            <translation>Wiersz {}: nie można zinterpretować '{}' jako liczby w kolumnie '{}'. Oczekiwano liczby, pustej komórki lub '-'.</translation>
        </message>
        <message>
            <source>{} OBWOD symbol(s) appeared more than once; population values summed (GUS records split census tracts under the same symbol at administrative boundaries): {}{}.</source>
            <translation>{} symbol(e) OBWOD wystąpiły więcej niż raz; wartości demograficzne zsumowano (GUS rejestruje podzielone obwody spisowe pod tym samym symbolem na granicach administracyjnych): {}{}.</translation>
        </message>
        <message>
            <source>Excel extraction: {} tract rows, {} unique keys, {} '-' values converted to 0.</source>
            <translation>Ekstrakcja z Excela: {} wierszy obwodów, {} unikalnych kluczy, {} wartości '-' zamienionych na 0.</translation>
        </message>
        <message>
            <source>Geometry layer has no field '{}'. Available fields: {}.</source>
            <translation>Warstwa geometryczna nie posiada pola '{}'. Dostępne pola: {}.</translation>
        </message>
        <message>
            <source>Key field '{}' is numeric; leading zeros may be lost when converting to string. Consider storing '{}' as a text field to preserve keys like '0123456'.</source>
            <translation>Pole kluczowe '{}' jest numeryczne; wiodące zera mogą zostać utracone podczas konwersji na ciąg znaków. Rozważ zapisanie '{}' jako pola tekstowego, aby zachować klucze takie jak '0123456'.</translation>
        </message>
        <message>
            <source>None of the {} Excel rows match the geometry layer. Check that you provided the correct file for this region.</source>
            <translation>Żaden ze {} wierszy Excela nie pasuje do warstwy geometrycznej. Sprawdź, czy podałeś poprawny plik dla tego regionu.</translation>
        </message>
        <message>
            <source>--- PrepareStudentLayer complete ---
Excel tract rows:              {}
Geometry features:             {}
Matched (both sets):           {}
Excel keys not in geometry:    {}
Geometry features unmatched:   {} ({} = NULL)
'-' values converted to 0:     {}
{} stats:  min={:.1f}  max={:.1f}  sum={:.1f}</source>
            <translation>--- PrepareStudentLayer zakończone ---
Wiersze traktu Excel:              {}
Obiekty geometryczne:             {}
Dopasowane (oba zbiory):           {}
Klucze Excel nieobecne w geometrii:    {}
Obiekty geometryczne niezgodne:   {} ({} = NULL)
Wartości '-' zamienione na 0:     {}
Statystyki {}: min={:.1f}  max={:.1f}  sum={:.1f}</translation>
        </message>
    </context>
    <context>
        <name>RecordGtfsRt</name>
        <message>
            <source>Record GTFS-RT snapshots</source>
            <translation>Rejestruj zrzuty GTFS-RT</translation>
        </message>
        <message>
            <source>4 · Realtime</source>
            <translation>4 · Czas rzeczywisty</translation>
        </message>
        <message>
            <source>Polls a GTFS-RT TripUpdates feed at regular intervals and saves each raw response as a .pb snapshot file.

The output directory will contain one snapshot_YYYYmmdd-HHMMSS.pb file per successful poll plus a recording.json manifest.  Use the BuildRealizedGtfs (RT-3) algorithm to turn the archive into a modified static GTFS.

A full service day (06:00–22:00) at 60 s interval yields ~960 snapshots (~28 MB for a typical TripUpdates feed).

Only TripUpdates feeds are supported.  Cities with VehiclePositions-only feeds (e.g. Warsaw, Wrocław) cannot use this tool.</source>
            <translation>Pobiera feed TripUpdates GTFS-RT w regularnych odstępach czasu i zapisuje każdą surową odpowiedź jako plik zrzutu .pb.

Katalog wyjściowy będzie zawierał jeden plik snapshot_YYYYmmdd-HHMMSS.pb za każdy udany pobór wraz z manifestem recording.json. Użyj algorytmu BuildRealizedGtfs (RT-3), aby przekształcić archiwum w zmodyfikowany statyczny GTFS.

Pełny dzień usługowy (06:00–22:00) przy interwale 60 s daje około 960 zrzutów (~28 MB dla typowego feedu TripUpdates).

Wspierane są tylko feedy TripUpdates. Miasta z feedami zawierającymi tylko VehiclePositions (np. Warszawa, Wrocław) nie mogą używać tego narzędzia.</translation>
        </message>
        <message>
            <source>GTFS-RT TripUpdates URL</source>
            <translation>URL GTFS-RT</translation>
        </message>
        <message>
            <source>Feed ID (recorded in manifest only, not used to fetch)</source>
            <translation>ID feedu (zapisywane tylko w liście manifestu, nie używane do pobierania)</translation>
        </message>
        <message>
            <source>Output directory for snapshots</source>
            <translation>Katalog wyjściowy dla zrzutów</translation>
        </message>
        <message>
            <source>Recording duration (minutes)</source>
            <translation>Czas nagrywania (minuty)</translation>
        </message>
        <message>
            <source>Sampling interval (seconds, 15–600)</source>
            <translation>Interwał próbkowania (sekundy, 15–600)</translation>
        </message>
        <message>
            <source>Output directory</source>
            <translation>Katalog wyjściowy</translation>
        </message>
        <message>
            <source>GTFS-RT URL is required.</source>
            <translation>Wymagany jest URL GTFS-RT.</translation>
        </message>
        <message>
            <source>Validating feed URL: {url}</source>
            <translation>Walidacja URL feedu: {url}</translation>
        </message>
        <message>
            <source>RT feed unreachable: {msg}</source>
            <translation>Feed RT niedostępny: {msg}</translation>
        </message>
        <message>
            <source>Archive folder: {output_dir.name}</source>
            <translation>Folder archiwum: {output_dir.name}</translation>
        </message>
        <message>
            <source>Recording started. Duration: {duration_min} min, interval: {interval_sec} s. Output: {output_dir}</source>
            <translation>Nagrywanie rozpoczęte. Czas trwania: {duration_min} min, interwał: {interval_sec} s. Wyjście: {output_dir}</translation>
        </message>
        <message>
            <source>Recording cancelled by user.</source>
            <translation>Nagrywanie anulowane przez użytkownika.</translation>
        </message>
        <message>
            <source>[{ok_count}] {snapshot_filename(now)} ({len(data):,} B)</source>
            <translation>[{ok_count}] {snapshot_filename(now)} ({len(data):,} B)</translation>
        </message>
        <message>
            <source>Poll {ok_count + failed_count} failed: {exc}</source>
            <translation>Pobór {ok_count + failed_count} nieudany: {exc}</translation>
        </message>
        <message>
            <source>Partial archive: {ok_count} snapshots, {failed_count} failed, {size_kb:.1f} KB. Manifest written to {output_dir / 'recording.json'}</source>
            <translation>Częściowe archiwum: {ok_count} zrzuty, {failed_count} nieudane, {size_kb:.1f} KB. Manifest zapisany do {output_dir / 'recording.json'}</translation>
        </message>
        <message>
            <source>Recording finished: {ok_count} snapshots, {failed_count} failed, {size_kb:.1f} KB total. Manifest: {output_dir / 'recording.json'}</source>
            <translation>Nagrywanie zakończone: {ok_count} zrzutów, {failed_count} nieudane, łącznie {size_kb:.1f} KB. Manifest: {output_dir / 'recording.json'}</translation>
        </message>
    </context>
    <context>
        <name>RunRealtimeAccessibility</name>
        <message>
            <source>Run realtime accessibility</source>
            <translation>Uruchom analizę dostępności w czasie rzeczywistym</translation>
        </message>
        <message>
            <source>4 · Realtime</source>
            <translation>4 · Czas rzeczywisty</translation>
        </message>
        <message>
            <source>Runs the temporal-accessibility pipeline against an OpenTripPlanner 1.5.0 instance fed with live GTFS-RT TripUpdates: before the server starts, a stop-time-updater is written into router-config.json so OTP polls the real-time feed and each per-minute surface reflects the actual delays in the network at that moment.

The analysis window is anchored to the system clock: it starts at the current time and runs forward over the chosen horizon (live GTFS-RT only carries predictions near the present — there is no date/start picker). For whole-day, reproducible realtime analysis use RecordGtfsRt + BuildRealizedGtfs (v0.5) instead.

Requires user-provided Java 8 and otp-1.5.0-shaded.jar.

Important limitations:
- Must be run live, today, on a day the GTFS actually covers (the run fails fast otherwise).
- Results are NOT reproducible: they depend on the live RT state at the moment of the run. The output layer is tagged analysis_type = "realtime".
- The feedId must match the feed_id OTP assigns to the static GTFS (from feed_info.txt, or an OTP-generated numeric id such as '1' when that column is absent). A numeric feedId is correct — it is not an error. Check the OTP log line 'Feed IDs loaded' or use /otp/routers/&lt;id&gt;/index/feeds to confirm.
- The static GTFS MUST be the official agency edition covering today, from the SAME source as the live feed, downloaded close in time to it. For ZTM Poznań that is getGTFSFile (https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile) paired with getGtfsRtFile. Third-party mirrors (transitfeeds, mobilitydatabase, mkuran.pl) regenerate trip_ids and will NEVER match the live feed, so OTP applies 0 updates and the result is silently static. Use tools/rt_diagnose/compare_rt_vs_static.py to confirm a pairing.
- For Gdańsk ZTM (Open Data CKAN feed): trip_ids embed the service date, so you must re-download the static GTFS the same day as the .pb. feedId=1 is correct (feed_info.txt has no feed_id column).
- Cities without a TripUpdates feed (e.g. Wrocław, Warszawa) will produce a warning and fall back to static-like results.

Fuzzy trip matching: matches RT updates by route/direction/start-time when trip_ids differ. A last resort — with an official static GTFS matched to the live feed, exact trip_id matching should work. Requires the live .pb to carry route_id + start_time.
Auto-detect feedId: reads feed_id from feed_info.txt. Many agencies omit that column, so OTP generates its own id — enter it manually then.</source>
            <translation>Uruchamia potok temporalnej dostępności względem instancji OpenTripPlanner 1.5.0 zasilanej żywymi TripUpdates GTFS-RT: przed uruchomieniem serwera, do router-config.json zapisywany jest stop-time-updater, dzięki czemu OTP okresowo odpytuje strumień w czasie rzeczywistym, a każdy minutowy powierzchniowy wynik odzwierciedla faktyczne opóźnienia w sieci w danym momencie.

Okno analizy jest zakotwiczone do zegara systemowego: rozpoczyna się w bieżącym czasie i rozciąga na wybrany horyzont (żywy GTFS-RT przenosi tylko prognozy bliskie teraźniejszości — nie ma wyboru daty/początku). Dla pełnego dnia, powtarzalnej analizy w czasie rzeczywistym użyj zamiast tego RecordGtfsRt + BuildRealizedGtfs (v0.5).

Wymaga podania przez użytkownika Java 8 oraz otp-1.5.0-shaded.jar.

Ważne ograniczenia:
- Musi być uruchomione na żywo, dzisiaj, w dniu, który jest faktycznie pokrywany przez GTFS (w przeciwnym razie analiza kończy się błyskawicznie).
- Wyniki NIE są powtarzalne: zależą od stanu RT w momencie uruchomienia. Warstwa wyjściowa jest oznaczona jako analysis_type = "realtime".
- feedId musi odpowiadać feed_id przypisanemu przez OTP do statycznego GTFS (z feed_info.txt lub numeryczny ID generowany przez OTP, np. '1', gdy ta kolumna jest nieobecna). Numeryczny feedId jest poprawny — to nie jest błąd. Sprawdź w linii loga OTP 'Feed IDs loaded' lub użyj /otp/routers/&lt;id&gt;/index/feeds, aby potwierdzić.
- Statyczny GTFS MUSI być oficjalną edycją agencji pokrywającą dzisiejszy dzień, pobraną z TEGO SAMEGO źródła co strumień na żywo, w czasie bliskim temu. Dla ZTM Poznań jest to getGTFSFile (https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile) połączony z getGtfsRtFile. Lustra stron trzecich (transitfeeds, mobilitydatabase, mkuran.pl) regenerują trip_id i NIGDY nie będą pasować do strumienia na żywo, więc OTP stosuje 0 aktualizacji, a wynik jest cicho statyczny. Użyj narzędzi/rt_diagnose/compare_rt_vs_static.py, aby potwierdzić parowanie.
- Dla Gdańsk ZTM (strumień Open Data CKAN): trip_id zawierają datę usługi, więc musisz ponownie pobrać statyczny GTFS na ten sam dzień co .pb. feedId=1 jest poprawny (feed_info.txt nie ma kolumny feed_id).
- Miasta bez strumienia TripUpdates (np. Wrocław, Warszawa) wygenerują ostrzeżenie i przejdą na wyniki zbliżone do statycznych.

Fuzzy trip matching: dopasowuje aktualizacje RT po trasie/kierunku/czasie rozpoczęcia, gdy trip_id się różnią. Ostateczność — przy oficjalnym statycznym GTFS pasującym do strumienia na żywo, dokładne dopasowanie trip_id powinno działać. Wymaga, aby żywy .pb zawierał route_id + start_time.
Auto-detect feedId: odczytuje feed_id z feed_info.txt. Wiele agencji pomija tę kolumnę, więc OTP generuje własny ID — wprowadź go ręcznie.</translation>
        </message>
        <message>
            <source>OSM extract (.osm.pbf)</source>
            <translation>Ekstrakcja OSM (.osm.pbf)</translation>
        </message>
        <message>
            <source>GTFS folder (containing one or more .zip feeds)</source>
            <translation>Folder GTFS (zawierający jeden lub więcej strumieni .zip)</translation>
        </message>
        <message>
            <source>Origin point (where travel-time analysis starts; OTP fromPlace)</source>
            <translation>Punkt początkowy (gdzie rozpoczyna się analiza czasu podróży; OTP fromPlace)</translation>
        </message>
        <message>
            <source>Hexagonal grid (polygon layer; leave blank when 'Generate hex grid' is checked)</source>
            <translation>Siatka sześcienna (warstwa wielokątna; zostaw puste, gdy zaznaczono 'Generuj siatkę sześcienną')</translation>
        </message>
        <message>
            <source>Generate hex grid instead of using supplied layer</source>
            <translation>Generuj siatkę sześcienną zamiast używać dostarczonej warstwy</translation>
        </message>
        <message>
            <source>Hex grid cell size (m)</source>
            <translation>Rozmiar komórki siatki (m)</translation>
        </message>
        <message>
            <source>GTFS-RT TripUpdates URL (.pb feed)</source>
            <translation>URL strumienia GTFS-RT TripUpdates (.pb feed)</translation>
        </message>
        <message>
            <source>GTFS-RT feedId (must match the feed_id OTP assigns to the static GTFS; leave blank to try Auto-detect). When feed_info.txt has no feed_id column, OTP assigns a numeric id such as '1' — that is correct, not an error. Confirmed working: Gdańsk ZTM with feedId=1.</source>
            <translation>feedId strumienia GTFS-RT (musi odpowiadać feed_id przypisanemu przez OTP do statycznego GTFS; zostaw puste, aby spróbować automatycznego wykrycia). Gdy feed_info.txt nie ma kolumny feed_id, OTP przypisuje numeryczny ID np. '1' — to jest poprawne, a nie błąd. Potwierdzono działanie: Gdańsk ZTM z feedId=1.</translation>
        </message>
        <message>
            <source>Auto-detect feedId from feed_info.txt (best-effort)</source>
            <translation>Automatyczne wykrycie feedId z feed_info.txt (najlepszy wysiłek)</translation>
        </message>
        <message>
            <source>RT polling interval (s)</source>
            <translation>Interwał odpytywania RT (s)</translation>
        </message>
        <message>
            <source>Fuzzy trip matching (fallback: match by route/direction/start-time)</source>
            <translation>Fuzzy trip matching (fallback: dopasowanie po trasie/kierunku/czasie rozpoczęcia)</translation>
        </message>
        <message>
            <source>Measurement horizon ahead of now (min)</source>
            <translation>Horyzont pomiarowy przed teraz (min)</translation>
        </message>
        <message>
            <source>Sampling interval (minutes)</source>
            <translation>Interwał próbkowania (minuty)</translation>
        </message>
        <message>
            <source>Travel-time threshold (min)</source>
            <translation>Próg czasu podróży (min)</translation>
        </message>
        <message>
            <source>Arrive by (reverse routing — measure latest departure to arrive at destination by T)</source>
            <translation>Przybycie do (odwrotne routowanie — mierzenie najpóźniejszego odjazdu, aby dotrzeć na cel do T)</translation>
        </message>
        <message>
            <source>Walk reluctance</source>
            <translation>Niechęć do chodzenia</translation>
        </message>
        <message>
            <source>Wait reluctance</source>
            <translation>Niechęć do oczekiwania</translation>
        </message>
        <message>
            <source>Transfer penalty (s)</source>
            <translation>Kara za przesiadkę (s)</translation>
        </message>
        <message>
            <source>Minimum transfer time (s)</source>
            <translation>Minimalny czas przesiadki (s)</translation>
        </message>
        <message>
            <source>Maximum walk distance (m) — limited effect in OTP analyst mode</source>
            <translation>Maksymalna odległość piesza (m) — ograniczony efekt w trybie analityka OTP</translation>
        </message>
        <message>
            <source>Walk speed (m/s)</source>
            <translation>Prędkość chodu (m/s)</translation>
        </message>
        <message>
            <source>Use Java path saved by 'Download Java Runtime Environment' (QSettings)</source>
            <translation>Użyj ścieżki Java zapisanej przez 'Pobierz środowisko uruchomieniowe Java' (QSettings)</translation>
        </message>
        <message>
            <source>Java 8 binary</source>
            <translation>Binarka Java 8</translation>
        </message>
        <message>
            <source>OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)</source>
            <translation>Plik jar OpenTripPlanner 1.5.0 (otp-1.5.0-shaded.jar)</translation>
        </message>
        <message>
            <source>OTP heap for graph build (e.g. 2G)</source>
            <translation>Pamięć heap OTP do budowy grafu (np. 2G)</translation>
        </message>
        <message>
            <source>OTP heap for analyst server (e.g. 4G)</source>
            <translation>Pamięć heap OTP dla serwera analityka (np. 4G)</translation>
        </message>
        <message>
            <source>OTP server port</source>
            <translation>Port serwera OTP</translation>
        </message>
        <message>
            <source>Existing graph router directory (skip build)</source>
            <translation>Istniejący katalog routera grafu (pomijanie budowy)</translation>
        </message>
        <message>
            <source>Custom router-config.json (optional; overrides the auto-generated default routingDefaults — note the RT updater is always (re)written on top before the server starts)</source>
            <translation>Własny plik router-config.json (opcjonalnie; nadpisuje domyślne ustawienia routingu defaults — pamiętaj, że aktualizator RT jest zawsze (ponownie) zapisywany przed uruchomieniem serwera)</translation>
        </message>
        <message>
            <source>Keep OTP server alive after run</source>
            <translation>Utrzymaj serwer OTP aktywny po uruchomieniu</translation>
        </message>
        <message>
            <source>Export statistics report</source>
            <translation>Eksportuj raport statystyczny</translation>
        </message>
        <message>
            <source>Report file (.xlsx or .csv)</source>
            <translation>Plik raportu (.xlsx lub .csv)</translation>
        </message>
        <message>
            <source>Excel files (*.xlsx);;CSV files (*.csv)</source>
            <translation>Pliki Excel (*.xlsx);;Pliki CSV (*.csv)</translation>
        </message>
        <message>
            <source>Working directory (intermediate surfaces, graph, cache)</source>
            <translation>Katalog roboczy (powierzchnie pośrednie, graf, pamięć podręczna)</translation>
        </message>
        <message>
            <source>Output hex grid (service-time + classification)</source>
            <translation>Wyjście siatki heksagonalnej (czas usługi + klasyfikacja)</translation>
        </message>
        <message>
            <source>Output count raster</source>
            <translation>Raster licznika wyjściowego</translation>
        </message>
        <message>
            <source>No Java path saved in QSettings. Run 'Download Java Runtime Environment' first, or uncheck 'Use saved Java path (QSettings)' and supply the path manually.</source>
            <translation>Nie zapisano ścieżki Java w QSettings. Uruchom najpierw 'Pobierz środowisko uruchomieniowe Java', lub odznacz 'Użyj zapisanej ścieżki Java (QSettings)' i podaj ścieżkę ręcznie.</translation>
        </message>
        <message>
            <source>Using Java path from QSettings: {java}</source>
            <translation>Użycie ścieżki Java z QSettings: {java}</translation>
        </message>
        <message>
            <source>Java OK: version {java_ver}</source>
            <translation>Java OK: wersja {java_ver}</translation>
        </message>
        <message>
            <source>Download otp-1.5.0-shaded.jar from Maven Central (groupId=org.opentripplanner, artifactId=otp, version=1.5.0, classifier=shaded) and set the 'OpenTripPlanner 1.5.0 jar' parameter.</source>
            <translation>Pobierz otp-1.5.0-shaded.jar z Maven Central (groupId=org.opentripplanner, artifactId=otp, version=1.5.0, classifier=shaded) i ustaw parametr 'OpenTripPlanner 1.5.0 jar'.</translation>
        </message>
        <message>
            <source>GTFS folder is required.</source>
            <translation>Wymagany folder GTFS.</translation>
        </message>
        <message>
            <source>Discovered {len(gtfs_files)} GTFS feed(s): {', '.join(p.name for p in gtfs_files)}</source>
            <translation>Odkryto {len(gtfs_files)} feed(y) GTFS: {', '.join(p.name for p in gtfs_files)}</translation>
        </message>
        <message>
            <source>GTFS-RT TripUpdates URL is required.</source>
            <translation>Wymagany URL dla TripUpdates GTFS-RT.</translation>
        </message>
        <message>
            <source>GTFS-RT URL reachable (HTTP 200): {rt_url}</source>
            <translation>URL GTFS-RT osiągalny (HTTP 200): {rt_url}</translation>
        </message>
        <message>
            <source>GTFS-RT URL probe failed: {msg}. The run will continue, but if the feed has no TripUpdates the result will be static-like. URL: {rt_url}</source>
            <translation>Próba sondowania URL GTFS-RT nie powiodła się: {msg}. Uruchomienie będzie kontynuowane, ale jeśli feed nie ma TripUpdates, wynik będzie statyczny. URL: {rt_url}</translation>
        </message>
        <message>
            <source>Working directory is required.</source>
            <translation>Wymagany katalog roboczy.</translation>
        </message>
        <message>
            <source>Origin (lat, lon) sent to OTP: ({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})</source>
            <translation>Początek (lat, lon) wysłany do OTP: ({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})</translation>
        </message>
        <message>
            <source>Sampling interval ({interval_min} min) is longer than the RT horizon ({horizon_min} min).</source>
            <translation>Przedział próbkowania ({interval_min} min) jest dłuższy niż horyzont RT ({horizon_min} min).</translation>
        </message>
        <message>
            <source>Invalid measurement window: {e}</source>
            <translation>Nieprawidłowe okno pomiarowe: {e}</translation>
        </message>
        <message>
            <source>Measurement horizon was clamped to 23:59 — RunRealtimeAccessibility does not span calendar days.</source>
            <translation>Horyzont pomiarowy został ograniczony do 23:59 — RunRealtimeAccessibility nie obejmuje dni kalendarzowych.</translation>
        </message>
        <message>
            <source>Realtime snapshot anchored at {now.strftime('%Y-%m-%d %H:%M')}; window {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} ({len(time_list)} surface(s), horizon {horizon_min} min, every {interval_min} min).</source>
            <translation>Zrzut stanu w czasie rzeczywistym zakotwiczony na {now.strftime('%Y-%m-%d %H:%M')}; okno {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} ({len(time_list)} powierzchnia(y), horyzont {horizon_min} min, co {interval_min} min).</translation>
        </message>
        <message>
            <source>Output count raster path is required.</source>
            <translation>Wymagana ścieżka rastra licznika wyjściowego.</translation>
        </message>
        <message>
            <source>EXISTING_GRAPH_DIR does not contain Graph.obj: {existing_dir}. Point to the router directory (e.g. …/graphs/abc123/).</source>
            <translation>EXISTING_GRAPH_DIR nie zawiera Graph.obj: {existing_dir}. Wskaż katalog routera (np. …/graphs/abc123/).</translation>
        </message>
        <message>
            <source>Using existing graph: {router_dir} (router_id={router_id}); skipping build.</source>
            <translation>Używany istniejący graf: {router_dir} (id routera={router_id}); pomijanie budowania.</translation>
        </message>
        <message>
            <source>Router ID: {router_id}</source>
            <translation>ID routera: {router_id}</translation>
        </message>
        <message>
            <source>Graph cache hit — skipping build.</source>
            <translation>Cache grafu trafiony — pomijanie budowania.</translation>
        </message>
        <message>
            <source>Graph cache miss: expected {work_dir / 'graphs' / router_id}.
However, a graph was found at {_off_by_one.parent} — WORK_DIR appears to point to the 'graphs' subfolder rather than its parent.
Fix option A: set WORK_DIR to '{work_dir.parent}'.
Fix option B: set EXISTING_GRAPH_DIR to '{_off_by_one.parent}'.</source>
            <translation>Cache grafu nie trafiony: oczekiwano {work_dir / 'graphs' / router_id}.
Jednak znaleziono graf w {_off_by_one.parent} — WORK_DIR wydaje się wskazywać na podfolder 'graphs' zamiast na jego rodzica.
Opcja naprawcza A: ustaw WORK_DIR na '{work_dir.parent}'.
Opcja naprawcza B: ustaw EXISTING_GRAPH_DIR na '{_off_by_one.parent}'.</translation>
        </message>
        <message>
            <source>Building OTP graph (this can take minutes)…</source>
            <translation>Budowanie grafu OTP (może to trwać kilka minut)…</translation>
        </message>
        <message>
            <source>Port {port} is already in use. RunRealtimeAccessibility needs a freshly started OTP server so the GTFS-RT updater is loaded — it will not reuse a running server. Stop the OTP instance on port {port} (e.g. rerun the previous job once with KEEP_SERVER_ALIVE=False), or pick a different OTP_PORT, then run again.</source>
            <translation>Port {port} jest już zajęty. RunRealtimeAccessibility wymaga świeżo uruchomionego serwera OTP, aby załadować aktualizator GTFS-RT — nie użyje działającego serwera. Zatrzymaj instancję OTP na porcie {port} (np. ponownie uruchom poprzednie zadanie z KEEP_SERVER_ALIVE=False) lub wybierz inny PORT_OTP, a następnie uruchom ponownie.</translation>
        </message>
        <message>
            <source>Wrote GTFS-RT router-config.json to {router_dir} (feedId={feed_id}, frequencySec={polling_sec}, fuzzyTripMatching={fuzzy_matching}).</source>
            <translation>Zapisano router-config.json GTFS-RT do {router_dir} (feedId={feed_id}, frequencySec={polling_sec}, fuzzyTripMatching={fuzzy_matching}).</translation>
        </message>
        <message>
            <source>Starting OTP server on port {port}…</source>
            <translation>Uruchamianie serwera OTP na porcie {port}…</translation>
        </message>
        <message>
            <source>RT updater active — polling {rt_url} every {polling_sec}s</source>
            <translation>Aktualizator RT aktywny — sprawdzanie co {polling_sec}s z {rt_url}</translation>
        </message>
        <message>
            <source>Generating {len(time_list)} surface(s) for date={date_s}…</source>
            <translation>Generowanie {len(time_list)} powierzchni dla daty={date_s}…</translation>
        </message>
        <message>
            <source>OTP could not snap the origin point to any vertex in the graph.
Common causes:
- ORIGIN_POINT is outside the OSM coverage area (check the router polygon bbox logged above).
- OSM_PBF was empty or invalid (graph has no streets).
- Coordinates entered with swapped lat/lon — check the 'Origin (lat, lon) sent to OTP' line above.
Original error: {err_text}</source>
            <translation>OTP nie mogło przypisać punktu początkowego do żadnego wierzchołka w grafie.
Najczęstsze przyczyny:
- ORIGIN_POINT znajduje się poza obszarem pokrycia OSM (sprawdź poligon routera zapisany powyżej).
- OSM_PBF był pusty lub nieprawidłowy (graf nie ma ulic).
- Współrzędne podane ze zamienionymi lat/lon — sprawdź linię 'Origin (lat, lon) sent to OTP' powyżej.
Początkowy błąd: {err_text}</translation>
        </message>
        <message>
            <source>Surface count mismatch: expected {len(time_list)}, got {len(surfaces)}. Some surfaces may have failed silently. Check the OTP server log in {surfaces_dir.parent} for details.</source>
            <translation>Niezgodność liczby powierzchni: oczekiwano {len(time_list)}, otrzymano {len(surfaces)}. Niektóre powierzchnie mogły zawieść bezgłośnie. Sprawdź log serwera OTP w {surfaces_dir.parent} po szczegóły.</translation>
        </message>
        <message>
            <source>Generated {len(surfaces)} surface(s) in {surfaces_dir}.</source>
            <translation>Wygenerowano {len(surfaces)} powierzchnię/powierzchnie w {surfaces_dir}.</translation>
        </message>
        <message>
            <source>Debug VRT written: {vrt_path} (visual inspection only).</source>
            <translation>Zapisano debugowy plik VRT: {vrt_path} (tylko do wizualnej inspekcji).</translation>
        </message>
        <message>
            <source>VRT build failed (debug artifact only, pipeline continues): {e}</source>
            <translation>Budowanie VRT nie powiodło się (tylko artefakt debugowania, potok kontynuuje): {e}</translation>
        </message>
        <message>
            <source>Counting pixels with travel-time ≤ {threshold_min} min across {len(surfaces)} surface(s) → {out_count_path}</source>
            <translation>Liczenie pikseli z czasem podróży ≤ {threshold_min} min na {len(surfaces)} powierzchni/powierzchniach → {out_count_path}</translation>
        </message>
        <message>
            <source>Generating hex grid from count raster extent (cell size {cell_size} m)…</source>
            <translation>Generowanie siatki heksagonalnej z zasięgu rastra licznika (rozmiar komórki {cell_size} m)…</translation>
        </message>
        <message>
            <source>No pixels were accessible within the travel-time threshold. Check ORIGIN_POINT and TRAVEL_TIME_THRESHOLD, or supply a HEX_GRID layer manually.</source>
            <translation>W ramach progu czasu podróży nie znaleziono dostępnych pikseli. Sprawdź ORIGIN_POINT i TRAVEL_TIME_THRESHOLD lub podaj warstwę HEX_GRID ręcznie.</translation>
        </message>
        <message>
            <source>HEX_GRID is required when 'Generate hex grid' is unchecked. Supply a polygon layer or enable the 'Generate hex grid' option.</source>
            <translation>HEX_GRID jest wymagana, gdy opcja 'Generate hex grid' (Generuj siatkę heksagonalną) nie jest zaznaczona. Podaj warstwę poligonu lub włącz opcję 'Generate hex grid'.</translation>
        </message>
        <message>
            <source>Running zonal statistics on count raster…</source>
            <translation>Uruchamianie statystyk strefowych na rastrze licznika…</translation>
        </message>
        <message>
            <source>Classifying service-time categories…</source>
            <translation>Klasyfikacja kategorii czasu obsługi…</translation>
        </message>
        <message>
            <source>Statistics report saved to: {actual_path}</source>
            <translation>Raport statystyczny zapisany pod adresem: {actual_path}</translation>
        </message>
        <message>
            <source>Realtime pipeline complete: hex grid with service-time classification ready (analysis_type = realtime).</source>
            <translation>Potok rzeczywisty zakończony: gotowa siatka heksagonalna z klasyfikacją czasu obsługi (analysis_type = realtime).</translation>
        </message>
        <message>
            <source>Could not remove RT router-config.json at {rt_config_path}: {e}. Remove it manually before running a static analysis on this graph, or the live RT updater will silently apply.</source>
            <translation>Nie można usunąć router-config.json RT pod adresem {rt_config_path}: {e}. Usuń go ręcznie przed uruchomieniem analizy statycznej na tym grafie, lub żywy aktualizator RT zastosuje go bezgłośnie.</translation>
        </message>
        <message>
            <source>Could not read feed_info.txt from {gtfs.name}: {e}. Skipping it for feedId detection.</source>
            <translation>Nie można odczytać pliku feed_info.txt z {gtfs.name}: {e}. Pomijane dla wykrycia feedId.</translation>
        </message>
        <message>
            <source>Auto-detected feedId '{feed_id}' from feed_info.txt.</source>
            <translation>Automatycznie wykryto feedId '{feed_id}' z feed_info.txt.</translation>
        </message>
        <message>
            <source>Auto-detect found no feed_id in feed_info.txt (the column is often absent). Enter the feedId OTP assigns manually.</source>
            <translation>Wykrycie automatyczne nie znalazło feed_id w feed_info.txt (kolumna często jest nieobecna). Wprowadź ręcznie ID feedu, które przypisuje OTP.</translation>
        </message>
        <message>
            <source>GTFS-RT feedId is required. This GTFS has no feed_id in feed_info.txt, so OTP generates one at graph load. To discover it: enter any placeholder value and run once — the log line 'OTP loaded feed IDs: [...]' (and a mismatch warning) will show the real id — then rerun with that value. An unmatched feedId makes OTP silently ignore the RT feed.</source>
            <translation>Wymagane jest feedId GTFS-RT. Ten GTFS nie posiada feed_id w feed_info.txt, więc OTP generuje go podczas ładowania grafu. Aby go odkryć: wprowadź dowolną wartość zastępczą i uruchom raz — linia w dzienniku 'OTP loaded feed IDs: [...]' (i ostrzeżenie o niezgodności) pokaże rzeczywiste ID — a następnie uruchom ponownie z tą wartością. Niezgodne feedId sprawia, że OTP cicho ignoruje feed RT.</translation>
        </message>
        <message>
            <source>Could not fetch router diagnostic: {e}</source>
            <translation>Nie można pobrać diagnostyki routera: {e}</translation>
        </message>
        <message>
            <source>--- OTP router diagnostic ---</source>
            <translation>--- Diagnostyka routera OTP ---</translation>
        </message>
        <message>
            <source>hasTransit = {has_transit}; transitServiceStarts = {_epoch_to_local(transit_starts)} (local); transitServiceEnds = {_epoch_to_local(transit_ends)} (local)</source>
            <translation>hasTransit = {has_transit}; transitServiceStarts = {_epoch_to_local(transit_starts)} (lokalnie); transitServiceEnds = {_epoch_to_local(transit_ends)} (lokalnie)</translation>
        </message>
        <message>
            <source>Router center (lat, lon) = ({center_lat}, {center_lon})</source>
            <translation>Centrum routera (lat, lon) = ({center_lat}, {center_lon})</translation>
        </message>
        <message>
            <source>Router polygon bbox (lat, lon): ({min(lats):.4f}, {min(lons):.4f}) .. ({max(lats):.4f}, {max(lons):.4f})</source>
            <translation>Obramowanie poligonu routera (lat, lon): ({min(lats):.4f}, {min(lons):.4f}) .. ({max(lats):.4f}, {max(lons):.4f})</translation>
        </message>
        <message>
            <source>{flag} = {info[flag]}</source>
            <translation>{flag} = {info[flag]}</translation>
        </message>
        <message>
            <source>OTP loaded feed IDs: {feed_ids}</source>
            <translation>OTP załadowało ID feedów: {feed_ids}</translation>
        </message>
        <message>
            <source>Could not list OTP feed IDs: {e}</source>
            <translation>Nie można wypisać ID feedów OTP: {e}</translation>
        </message>
        <message>
            <source>-----------------------------</source>
            <translation>-----------------------------</translation>
        </message>
        <message>
            <source>The loaded OTP graph's transit service ({svc}) does not cover the measurement window {now:%Y-%m-%d %H:%M}–{end_h:02d}:{end_m:02d}. This is almost always a stale graph — EXISTING_GRAPH_DIR or the cached router was built from an older GTFS that does not cover today. Rebuild from the current GTFS (clear EXISTING_GRAPH_DIR, or use a fresh WORK_DIR so a new router_id is built) and rerun.</source>
            <translation>Usługa transportowa załadowanego grafu OTP ({svc}) nie obejmuje okna pomiarowego {now:%Y-%m-%d %H:%M}–{end_h:02d}:{end_m:02d}. Jest to prawie zawsze przestarzały graf — katalog EXISTING_GRAPH_DIR lub buforowany router został zbudowany z starszego GTFS, który nie obejmuje dzisiejszej daty. Zbuduj ponownie z aktualnego GTFS (wyczyść EXISTING_GRAPH_DIR lub użyj świeżego WORK_DIR, aby zbudować nowy router_id) i uruchom ponownie.</translation>
        </message>
        <message>
            <source>No OTP server log path available — RT effectiveness could not be verified. Check the /otp/routers/&lt;router&gt;/updaters endpoint.</source>
            <translation>Brak dostępnej ścieżki dziennika serwera OTP — nie można zweryfikować skuteczności RT. Sprawdź endpoint /otp/routers/&lt;router&gt;/updaters.</translation>
        </message>
        <message>
            <source>Could not read OTP log to verify RT: {e}</source>
            <translation>Nie można odczytać dziennika OTP w celu weryfikacji RT: {e}</translation>
        </message>
        <message>
            <source>GTFS-RT applied: {applied} trip update(s) took effect ({no_pattern} skipped). Surfaces reflect live conditions.</source>
            <translation>Zastosowano GTFS-RT: {applied} aktualizacja(e) podróży weszła w życie ({no_pattern} pominięto). Powierzchnie odzwierciedlają warunki w czasie rzeczywistym.</translation>
        </message>
        <message>
            <source>GTFS-RT applied. Note: {failed} trip update(s) were rejected by OTP's TripTimes validator (non-increasing times after delay propagation — known OTP 1.5 limitation, issues #1250/#2780/#2560). The remaining updates applied silently.</source>
            <translation>Zastosowano GTFS-RT. Uwaga: {failed} aktualizacja(e) podróży została odrzucona przez walidator TripTimes OTP (nierosnące czasy po propagacji opóźnienia — znane ograniczenie OTP 1.5, problemy #1250/#2780/#2560). Pozostałe aktualizacje zostały zastosowane cicho.</translation>
        </message>
        <message>
            <source>GTFS-RT updater is registered and running. No explicit application count is available (TimetableSnapshotSource logs success silently). Treating as effective.</source>
            <translation>Aktualizator GTFS-RT jest zarejestrowany i działa. Nie jest dostępna jawna liczba zastosowań (TimetableSnapshotSource loguje sukces cicho). Traktowane jako skuteczne.</translation>
        </message>
        <message>
            <source>GTFS-RT effectiveness could not be confirmed from the OTP log (no completed poll observed yet, or the feed was empty). The output is not prefixed RT-NOT-APPLIED_ — check the OTP server log or /otp/routers/&lt;router&gt;/updaters for updater status.</source>
            <translation>Nie można potwierdzić skuteczności GTFS-RT z dziennika OTP (jeszcze nie zaobserwowano zakończonego zapytania, lub feed był pusty). Wynik nie jest poprzedzony RT-NOT-APPLIED_ — sprawdź dziennik serwera OTP lub /otp/routers/&lt;router&gt;/updaters pod kątem statusu aktualizatora.</translation>
        </message>
        <message>
            <source>Pre-flight: first GTFS-RT poll applied {applied} update(s) — RT is live; generating surfaces.</source>
            <translation>Przed lotem: zastosowano pierwsze zapytanie GTFS-RT dla {applied} aktualizacji — RT jest aktywne; generowanie powierzchni.</translation>
        </message>
        <message>
            <source>Pre-flight: GTFS-RT updates matched (OTP validator rejected {failed} update(s) with non-increasing times — known OTP 1.5 limitation); RT is live. Generating surfaces.</source>
            <translation>Przed lotem: dopasowane aktualizacje GTFS-RT (walidator OTP odrzucił {failed} aktualizację(e) z nierosnącymi czasami — znane ograniczenie OTP 1.5); RT jest aktywne. Generowanie powierzchni.</translation>
        </message>
        <message>
            <source>Pre-flight: GTFS-RT updates matched (OTP rejected {failed} update(s) during validation — trip_id resolution succeeded); RT is live. Generating surfaces.</source>
            <translation>Przed lotem: dopasowane aktualizacje GTFS-RT (OTP odrzuciło {failed} aktualizację(e) podczas walidacji — rozstrzygnięcie trip_id zakończyło się sukcesem); RT jest aktywne. Generowanie powierzchni.</translation>
        </message>
        <message>
            <source>Pre-flight aborted before generating surfaces: OTP's first GTFS-RT poll applied 0 of {no_pattern} TripUpdates (trip_id not found in the static GTFS). The static feed and the live RT feed are different editions, so every surface would be static — {extra}. Use the official static edition covering today, downloaded close in time to the .pb, or run tools/rt_diagnose/compare_rt_vs_static.py to confirm the pairing.</source>
            <translation>Przedlotowe sprawdzenie anulowane przed wygenerowaniem powierzchni: pierwsze zapytanie GTFS-RT OTP zastosowało 0 z {no_pattern} TripUpdates (trip_id nie znaleziony w statycznym GTFS). Statyczny feed i żywy feed RT są różnymi edycjami, więc każda powierzchnia byłaby statyczna — {extra}. Użyj oficjalnej statycznej edycji obejmującej dzisiejszy dzień, pobranej blisko czasu .pb, lub uruchom narzędzia/rt_diagnose/compare_rt_vs_static.py, aby potwierdzić parowanie.</translation>
        </message>
        <message>
            <source>Pre-flight: no completed RT poll observed yet; proceeding (the post-run check will still verify whether RT took effect).</source>
            <translation>Przed lotem: nie zaobserwowano jeszcze ukończonego zapytania RT; kontynuowanie (sprawdzenie po wykonaniu nadal zweryfikuje, czy RT weszło w życie).</translation>
        </message>
        <message>
            <source>Today is {day_name} ({date_str}). Weekend transit schedules may differ significantly from weekday service.</source>
            <translation>Dziś jest {day_name} ({date_str}). Rozkłady jazdy weekendowe mogą znacznie różnić się od usług dni powszednich.</translation>
        </message>
        <message>
            <source>Could not read {gtfs_path.name} for service validation: {exc}</source>
            <translation>Nie można odczytać {gtfs_path.name} w celu walidacji usługi: {exc}</translation>
        </message>
        <message>
            <source>No calendar.txt/calendar_dates.txt in {gtfs_path.name} — cannot validate today's service; proceeding without the check.</source>
            <translation>Brak calendar.txt/calendar_dates.txt w {gtfs_path.name} — nie można zweryfikować dzisiejszej usługi; kontynuowanie bez sprawdzenia.</translation>
        </message>
        <message>
            <source>{gtfs_path.name}: {len(running)} service(s) active on {date_str}.</source>
            <translation>{gtfs_path.name}: {len(running)} usługa(y) aktywna(e) w dniu {date_str}.</translation>
        </message>
        <message>
            <source>No transit service is scheduled on {date_str} in the supplied GTFS ({range_hint}). RunRealtimeAccessibility measures live service and must run on a day the GTFS actually covers — and only makes sense run live, today.</source>
            <translation>Brak zaplanowanej usługi transportowej w dniu {date_str} w dostarczonym GTFS ({range_hint}). RunRealtimeAccessibility mierzy usługę na żywo i musi być uruchomiony w dniu, który faktycznie obejmuje GTFS — a ma sens tylko wtedy, gdy jest to dzisiaj.</translation>
        </message>
        <message>
            <source>{label} is required (parameter {key}).</source>
            <translation>Wymagany jest {label} (parametr {key}).</translation>
        </message>
        <message>
            <source>{label} not found at: {path} (parameter {key}).</source>
            <translation>{label} nie znaleziony pod adresem: {path} (parametr {key}).</translation>
        </message>
    </context>
    <context>
        <name>RunTemporalAccessibility</name>
        <message>
            <source>Run temporal accessibility</source>
            <translation>Uruchom analizę czasową</translation>
        </message>
        <message>
            <source>3 · Analysis</source>
            <translation>3 · Analiza</translation>
        </message>
        <message>
            <source>Runs the full temporal-accessibility pipeline against an OpenTripPlanner 1.5.0 instance: generates one travel-time surface per minute across the configured time window, stacks and counts surfaces below the travel-time threshold, and aggregates the result into a hexagonal grid with a 4-category service-time classification.

Requires user-provided Java 8 and otp-1.5.0-shaded.jar.

Note: maxWalkDistance may have no effect on surface extent in OTP analyst mode — the SPT is time-bounded (120 min ceiling), not distance-bounded. Use walk_speed to control how far the model walks within that time budget.</source>
            <translation>Uruchamia pełny potok analizy dostępności czasowej względem instancji OpenTripPlanner 1.5.0: generuje jedną powierzchnię czasu podróży na minutę w ramach skonfigurowanego okna czasowego, stosuje i liczy powierzchnie poniżej progu czasu podróży, a następnie agreguje wynik do siatki heksagonalnej z klasyfikacją czasu obsługi w 4 kategoriach.

Wymaga podania przez użytkownika Java 8 oraz otp-1.5.0-shaded.jar.

Uwaga: maxWalkDistance może nie mieć wpływu na rozpiętość powierzchni w trybie analityka OTP — SPT jest ograniczony czasowo (limit 120 min), a nie odległościowo. Użyj walk_speed, aby kontrolować, jak daleko model przemieszcza się w ramach tego budżetu czasowego.</translation>
        </message>
        <message>
            <source>OSM extract (.osm.pbf)</source>
            <translation>Ekstrakcja OSM (.osm.pbf)</translation>
        </message>
        <message>
            <source>GTFS folder (containing one or more .zip feeds)</source>
            <translation>Folder GTFS (zawierający jeden lub więcej feedów .zip)</translation>
        </message>
        <message>
            <source>Origin point (where travel-time analysis starts; OTP fromPlace)</source>
            <translation>Punkt początkowy (gdzie rozpoczyna się analiza czasu podróży; OTP fromPlace)</translation>
        </message>
        <message>
            <source>Hexagonal grid (polygon layer; leave blank when 'Generate hex grid' is checked)</source>
            <translation>Siatka heksagonalna (warstwa wielokątowa; zostaw puste, gdy zaznaczono 'Generuj siatkę heksagonalną')</translation>
        </message>
        <message>
            <source>Generate hex grid instead of using supplied layer</source>
            <translation>Generuj siatkę heksagonalną zamiast używać dostarczonej warstwy</translation>
        </message>
        <message>
            <source>Hex grid cell size (m)</source>
            <translation>Rozmiar komórki siatki heksagonalnej (m)</translation>
        </message>
        <message>
            <source>Analysis date</source>
            <translation>Data analizy</translation>
        </message>
        <message>
            <source>Window start time</source>
            <translation>Czas początkowy okna</translation>
        </message>
        <message>
            <source>Window end time</source>
            <translation>Czas końcowy okna</translation>
        </message>
        <message>
            <source>Sampling interval (minutes)</source>
            <translation>Interwał próbkowania (minuty)</translation>
        </message>
        <message>
            <source>Travel-time threshold (min)</source>
            <translation>Próg czasu podróży (min)</translation>
        </message>
        <message>
            <source>Arrive by (reverse routing — measure latest departure to arrive at destination by T)</source>
            <translation>Przybycie do (odwrotne routowanie — mierzy najpóźniejsze odjazdy, aby dotrzeć na cel przed czasem T)</translation>
        </message>
        <message>
            <source>Walk reluctance</source>
            <translation>Niechęć do chodzenia</translation>
        </message>
        <message>
            <source>Wait reluctance</source>
            <translation>Niechęć do czekania</translation>
        </message>
        <message>
            <source>Transfer penalty (s)</source>
            <translation>Kara za przesiadkę (s)</translation>
        </message>
        <message>
            <source>Minimum transfer time (s)</source>
            <translation>Minimalny czas przesiadki (s)</translation>
        </message>
        <message>
            <source>Maximum walk distance (m) — limited effect in OTP analyst mode</source>
            <translation>Maksymalna odległość piesza (m) — ograniczony wpływ w trybie analityka OTP</translation>
        </message>
        <message>
            <source>Walk speed (m/s)</source>
            <translation>Prędkość chodzenia (m/s)</translation>
        </message>
        <message>
            <source>Use Java path saved by 'Download Java Runtime Environment' (QSettings)</source>
            <translation>Użyj ścieżki Java zapisanej przez 'Pobierz środowisko uruchomieniowe Java' (QSettings)</translation>
        </message>
        <message>
            <source>Java 8 binary</source>
            <translation>Binarny plik Java 8</translation>
        </message>
        <message>
            <source>OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)</source>
            <translation>OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)</translation>
        </message>
        <message>
            <source>OTP heap for graph build (e.g. 2G)</source>
            <translation>Pamięć OTP do budowy grafu (np. 2G)</translation>
        </message>
        <message>
            <source>OTP heap for analyst server (e.g. 4G)</source>
            <translation>Pamięć OTP dla serwera analitycznego (np. 4G)</translation>
        </message>
        <message>
            <source>OTP server port</source>
            <translation>Port serwera OTP</translation>
        </message>
        <message>
            <source>Existing graph router directory (skip build)</source>
            <translation>Istniejący katalog routera grafu (pomijanie budowy)</translation>
        </message>
        <message>
            <source>Custom router-config.json (optional; overrides the auto-generated default and the GTFS-folder convention)</source>
            <translation>Własny plik router-config.json (opcjonalnie; nadpisuje domyślne automatycznie wygenerowane i konwencję folderu GTFS)</translation>
        </message>
        <message>
            <source>Keep OTP server alive after run</source>
            <translation>Utrzymaj serwer OTP przy życiu po uruchomieniu</translation>
        </message>
        <message>
            <source>Export statistics report</source>
            <translation>Eksportuj raport statystyczny</translation>
        </message>
        <message>
            <source>Report file (.xlsx or .csv)</source>
            <translation>Plik raportu (.xlsx lub .csv)</translation>
        </message>
        <message>
            <source>Excel files (*.xlsx);;CSV files (*.csv)</source>
            <translation>Pliki Excel (*.xlsx);;Pliki CSV (*.csv)</translation>
        </message>
        <message>
            <source>Working directory (intermediate surfaces, graph, cache)</source>
            <translation>Katalog roboczy (powierzchnie pośrednie, graf, pamięć podręczna)</translation>
        </message>
        <message>
            <source>Output hex grid (service-time + classification)</source>
            <translation>Wyjściowa siatka heksagonalna (czas usługi + klasyfikacja)</translation>
        </message>
        <message>
            <source>Output count raster</source>
            <translation>Wyjściowy raster liczbowy</translation>
        </message>
        <message>
            <source>No Java path saved in QSettings. Run 'Download Java Runtime Environment' first, or uncheck 'Use saved Java path (QSettings)' and supply the path manually.</source>
            <translation>Ścieżka Java nie została zapisana w QSettings. Uruchom najpierw 'Pobierz środowisko uruchomieniowe Java', lub odznacz 'Użyj zapisanej ścieżki Java (QSettings)' i podaj ścieżkę ręcznie.</translation>
        </message>
        <message>
            <source>Using Java path from QSettings: {java}</source>
            <translation>Używana ścieżka Java z QSettings: {java}</translation>
        </message>
        <message>
            <source>Java OK: version {java_ver}</source>
            <translation>Java OK: wersja {java_ver}</translation>
        </message>
        <message>
            <source>Download otp-1.5.0-shaded.jar from Maven Central (groupId=org.opentripplanner, artifactId=otp, version=1.5.0, classifier=shaded) and set the 'OpenTripPlanner 1.5.0 jar' parameter.</source>
            <translation>Pobierz otp-1.5.0-shaded.jar z Maven Central (groupId=org.opentripplanner, artifactId=otp, version=1.5.0, classifier=shaded) i ustaw parametr 'OpenTripPlanner 1.5.0 jar'.</translation>
        </message>
        <message>
            <source>GTFS folder is required.</source>
            <translation>Wymagany folder GTFS.</translation>
        </message>
        <message>
            <source>Discovered {len(gtfs_files)} GTFS feed(s): {', '.join(p.name for p in gtfs_files)}</source>
            <translation>Odkryto {len(gtfs_files)} plik(i) feedu GTFS: {', '.join(p.name for p in gtfs_files)}</translation>
        </message>
        <message>
            <source>Working directory is required.</source>
            <translation>Wymagany katalog roboczy.</translation>
        </message>
        <message>
            <source>Origin (lat, lon) sent to OTP: ({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})</source>
            <translation>Początek (szer, dług) wysłany do OTP: ({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})</translation>
        </message>
        <message>
            <source>Sampling interval ({interval_min} min) is longer than the analysis window.</source>
            <translation>Przedział próbkowania ({interval_min} min) jest dłuższy niż okno analizy.</translation>
        </message>
        <message>
            <source>Invalid time window: {e}</source>
            <translation>Nieprawidłowe okno czasowe: {e}</translation>
        </message>
        <message>
            <source>Sampling {len(time_list)} surfaces at {interval_min}-min interval ({time_list[0]}–{time_list[-1]}).</source>
            <translation>Próbkowanie {len(time_list)} powierzchni z interwałem co {interval_min}-min ({time_list[0]}–{time_list[-1]}).</translation>
        </message>
        <message>
            <source>Output count raster path is required.</source>
            <translation>Wymagana ścieżka rastra wyjściowego.</translation>
        </message>
        <message>
            <source>EXISTING_GRAPH_DIR does not contain Graph.obj: {existing_dir}. Point to the router directory (e.g. …/graphs/abc123/).</source>
            <translation>Katalog EXISTING_GRAPH_DIR nie zawiera Graph.obj: {existing_dir}. Wskaż katalog routera (np. …/graphs/abc123/).</translation>
        </message>
        <message>
            <source>Using existing graph: {router_dir} (router_id={router_id}); skipping build.</source>
            <translation>Używany istniejący graf: {router_dir} (id routera={router_id}); pomijanie budowania.</translation>
        </message>
        <message>
            <source>Router ID: {router_id}</source>
            <translation>ID Routera: {router_id}</translation>
        </message>
        <message>
            <source>Graph cache hit — skipping build.</source>
            <translation>Osiągnięto trafienie w pamięci podręcznej grafu — pomijanie budowania.</translation>
        </message>
        <message>
            <source>Graph cache miss: expected {work_dir / 'graphs' / router_id}.
However, a graph was found at {_off_by_one.parent} — WORK_DIR appears to point to the 'graphs' subfolder rather than its parent.
Fix option A: set WORK_DIR to '{work_dir.parent}'.
Fix option B: set EXISTING_GRAPH_DIR to '{_off_by_one.parent}'.</source>
            <translation>Brak trafienia w pamięci podręcznej grafu: oczekiwano {work_dir / 'graphs' / router_id}.
Jednak znaleziono graf w {_off_by_one.parent} — WORK_DIR wydaje się wskazywać na podfolder 'graphs', a nie na jego rodzica.
Opcja naprawcza A: ustaw WORK_DIR na '{work_dir.parent}'.
Opcja naprawcza B: ustaw EXISTING_GRAPH_DIR na '{_off_by_one.parent}'.</translation>
        </message>
        <message>
            <source>Building OTP graph (this can take minutes)…</source>
            <translation>Budowanie grafu OTP (może to zająć kilka minut)…</translation>
        </message>
        <message>
            <source>ARRIVE_BY=True with a reused OTP server: if surfaces fail with HTTP 500, restart the server by setting KEEP_SERVER_ALIVE=False for one run. Reverse routing may require more heap than forward routing — ensure OTP_XMX_SERVE is set to at least 4G.</source>
            <translation>ARRIVE_BY=True z ponownie używanym serwerem OTP: jeśli powierzchnie zawiodą z HTTP 500, uruchom ponownie serwer, ustawiając KEEP_SERVER_ALIVE=False dla jednego uruchomienia. Odwrotne routowanie może wymagać więcej pamięci niż routowanie w przód — upewnij się, że OTP_XMX_SERVE jest ustawione na co najmniej 4G.</translation>
        </message>
        <message>
            <source>Reusing OTP already running on port {port} (version {ver_str}).</source>
            <translation>Ponowne użycie OTP już działającego na porcie {port} (wersja {ver_str}).</translation>
        </message>
        <message>
            <source>Port {port} is held by a non-OTP process. Pick a different OTP_PORT or stop the conflicting service. Run TestOtpServer for details.</source>
            <translation>Port {port} zajęty przez proces niebędący OTP. Wybierz inny OTP_PORT lub zatrzymaj konfliktujący serwis. Uruchom TestOtpServer po szczegóły.</translation>
        </message>
        <message>
            <source>Starting OTP server on port {port}…</source>
            <translation>Uruchamianie serwera OTP na porcie {port}…</translation>
        </message>
        <message>
            <source>Generating {len(time_list)} surface(s) for date={date_s}…</source>
            <translation>Generowanie {len(time_list)} powierzchni dla daty={date_s}…</translation>
        </message>
        <message>
            <source>OTP could not snap the origin point to any vertex in the graph.
Common causes:
- ORIGIN_POINT is outside the OSM coverage area (check the router polygon bbox logged above).
- OSM_PBF was empty or invalid (graph has no streets).
- Coordinates entered with swapped lat/lon — check the 'Origin (lat, lon) sent to OTP' line above.
Original error: {err_text}</source>
            <translation>OTP nie udało się dopasować punktu początkowego do żadnego wierzchołka w grafie.
Typowe przyczyny:
- ORIGIN_POINT znajduje się poza obszarem pokrycia OSM (sprawdź poligon routera bbox zalogowany powyżej).
- OSM_PBF był pusty lub nieprawidłowy (graf nie ma ulic).
- Współrzędne podane ze zamienionym lat/lon — sprawdź linię 'Origin (lat, lon) sent to OTP' powyżej.
Początkowy błąd: {err_text}</translation>
        </message>
        <message>
            <source>Surface count mismatch: expected {len(time_list)}, got {len(surfaces)}. Some surfaces may have failed silently. Check the OTP server log in {surfaces_dir.parent} for details.</source>
            <translation>Niezgodność liczby powierzchni: oczekiwano {len(time_list)}, otrzymano {len(surfaces)}. Niektóre powierzchnie mogły zawieść bezgłośnie. Sprawdź log serwera OTP w {surfaces_dir.parent} po szczegóły.</translation>
        </message>
        <message>
            <source>Generated {len(surfaces)} surface(s) in {surfaces_dir}.</source>
            <translation>Wygenerowano {len(surfaces)} powierzchnię(e) w {surfaces_dir}.</translation>
        </message>
        <message>
            <source>Debug VRT written: {vrt_path} (visual inspection only).</source>
            <translation>Zapisano debug VRT: {vrt_path} (tylko do wizualnej inspekcji).</translation>
        </message>
        <message>
            <source>VRT build failed (debug artifact only, pipeline continues): {e}</source>
            <translation>Budowanie VRT nie powiodło się (tylko artefakt debugowania, potok kontynuuje): {e}</translation>
        </message>
        <message>
            <source>Counting pixels with travel-time ≤ {threshold_min} min across {len(surfaces)} surface(s) → {out_count_path}</source>
            <translation>Liczenie pikseli z czasem podróży ≤ {threshold_min} min na {len(surfaces)} powierzchniach → {out_count_path}</translation>
        </message>
        <message>
            <source>Generating hex grid from count raster extent (cell size {cell_size} m)…</source>
            <translation>Generowanie siatki sześciennej (hex grid) z zasięgu rastra liczbowego (rozmiar komórki {cell_size} m)…</translation>
        </message>
        <message>
            <source>No pixels were accessible within the travel-time threshold. Check ORIGIN_POINT and TRAVEL_TIME_THRESHOLD, or supply a HEX_GRID layer manually.</source>
            <translation>Nie znaleziono dostępnych pikseli w ramach progu czasu podróży. Sprawdź ORIGIN_POINT i TRAVEL_TIME_THRESHOLD lub podaj warstwę HEX_GRID ręcznie.</translation>
        </message>
        <message>
            <source>HEX_GRID is required when 'Generate hex grid' is unchecked. Supply a polygon layer or enable the 'Generate hex grid' option.</source>
            <translation>HEX_GRID jest wymagana, gdy opcja 'Generuj siatkę sześcienną' nie jest zaznaczona. Podaj warstwę wielokąta lub włącz opcję 'Generuj siatkę sześcienną'.</translation>
        </message>
        <message>
            <source>Running zonal statistics on count raster…</source>
            <translation>Uruchamianie statystyk strefowych na rastrze liczbowym…</translation>
        </message>
        <message>
            <source>Classifying service-time categories…</source>
            <translation>Klasyfikacja kategorii czasu obsługi…</translation>
        </message>
        <message>
            <source>Statistics report saved to: {actual_path}</source>
            <translation>Raport statystyczny zapisany w: {actual_path}</translation>
        </message>
        <message>
            <source>Pipeline complete: hex grid with service-time classification ready.</source>
            <translation>Potok zakończony: siatka sześcienna z klasyfikacją czasu obsługi gotowa.</translation>
        </message>
        <message>
            <source>Could not fetch router diagnostic: {e}</source>
            <translation>Nie można pobrać diagnostyki routera: {e}</translation>
        </message>
        <message>
            <source>--- OTP router diagnostic ---</source>
            <translation>--- Diagnostyka routera OTP ---</translation>
        </message>
        <message>
            <source>hasTransit = {has_transit}; transitServiceStarts = {_epoch_to_iso(transit_starts)} ({transit_starts}); transitServiceEnds = {_epoch_to_iso(transit_ends)} ({transit_ends})</source>
            <translation>hasTransit = {has_transit}; transitServiceStarts = {_epoch_to_iso(transit_starts)} ({transit_starts}); transitServiceEnds = {_epoch_to_iso(transit_ends)} ({transit_ends})</translation>
        </message>
        <message>
            <source>Router center (lat, lon) = ({center_lat}, {center_lon})</source>
            <translation>Centrum routera (lat, lon) = ({center_lat}, {center_lon})</translation>
        </message>
        <message>
            <source>Router polygon bbox (lat, lon): ({min(lats):.4f}, {min(lons):.4f}) .. ({max(lats):.4f}, {max(lons):.4f})</source>
            <translation>Bbox wielokąta routera (lat, lon): ({min(lats):.4f}, {min(lons):.4f}) .. ({max(lats):.4f}, {max(lons):.4f})</translation>
        </message>
        <message>
            <source>{flag} = {info[flag]}</source>
            <translation>{flag} = {info[flag]}</translation>
        </message>
        <message>
            <source>-----------------------------</source>
            <translation>-----------------------------</translation>
        </message>
        <message>
            <source>ANALYSIS_DATE is a {day_name} ({date_str}). Weekend transit schedules may differ significantly from weekday analyses.</source>
            <translation>DATA ANALIZY to {day_name} ({date_str}). Rozkłady jazdy weekendowe mogą znacznie różnić się od analiz dni powszednich.</translation>
        </message>
        <message>
            <source>No calendar.txt in {gtfs_path.name} — cannot validate analysis date against GTFS service range.</source>
            <translation>Brak pliku calendar.txt w {gtfs_path.name} — niemożliwa walidacja daty analizy względem zakresu usług GTFS.</translation>
        </message>
        <message>
            <source>{gtfs_path.name}: no services active on {date_str}. OTP may return all-unreachable surfaces for this date.</source>
            <translation>{gtfs_path.name}: brak aktywnych usług w dniu {date_str}. OTP może zwrócić wszystkie niedostępne powierzchnie dla tej daty.</translation>
        </message>
        <message>
            <source>{gtfs_path.name}: {active} service(s) active on {date_str}.</source>
            <translation>{gtfs_path.name}: {active} usługa(i) aktywne w dniu {date_str}.</translation>
        </message>
        <message>
            <source>Could not read {gtfs_path.name} for date validation: {exc}</source>
            <translation>Nie można odczytać {gtfs_path.name} w celu walidacji daty: {exc}</translation>
        </message>
        <message>
            <source>{label} is required (parameter {key}).</source>
            <translation>{label} jest wymagany (parametr {key}).</translation>
        </message>
        <message>
            <source>{label} not found at: {path} (parameter {key}).</source>
            <translation>{label} nie znaleziono pod adresem: {path} (parametr {key}).</translation>
        </message>
    </context>
    <context>
        <name>TestOtpServer</name>
        <message>
            <source>Test OTP server</source>
            <translation>Test serwera OTP</translation>
        </message>
        <message>
            <source>2 · Diagnostics</source>
            <translation>2 · Diagnostyka</translation>
        </message>
        <message>
            <source>Diagnostic checks for an OpenTripPlanner 1.5.0 setup: verifies the Java 8 binary, the OTP jar, and the port state (free / held by a foreign process / already serving OTP). All checks run independently and report through the algorithm log.</source>
            <translation>Sprawdzenia diagnostyczne dla konfiguracji OpenTripPlanner 1.5.0: weryfikuje binarny Java 8, plik jar OTP oraz stan portu (wolny / zajęty przez proces zewnętrzny / już obsługujący OTP). Wszystkie sprawdzenia działają niezależnie i raportują w dzienniku algorytmu.</translation>
        </message>
        <message>
            <source>Use Java path saved by 'Download Java Runtime Environment' (QSettings)</source>
            <translation>Użyj ścieżki Java zapisanej w 'Pobierz środowisko uruchomieniowe Java' (QSettings)</translation>
        </message>
        <message>
            <source>Java 8 binary</source>
            <translation>Binarny Java 8</translation>
        </message>
        <message>
            <source>OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)</source>
            <translation>Plik jar OpenTripPlanner 1.5.0 (otp-1.5.0-shaded.jar)</translation>
        </message>
        <message>
            <source>OTP server port</source>
            <translation>Port serwera OTP</translation>
        </message>
        <message>
            <source>All checks passed.</source>
            <translation>Wszystkie testy zaliczone.</translation>
        </message>
        <message>
            <source>One or more checks failed. Fix the items reported above before running 'Run temporal accessibility'.</source>
            <translation>Niektóre sprawdzenia się nie powiodły. Napraw elementy zgłoszone powyżej, zanim uruchomisz 'Uruchom dostępność czasową'.</translation>
        </message>
        <message>
            <source>No Java path saved in QSettings. Run 'Download Java Runtime Environment' first, or uncheck 'Use saved Java path' and supply the path manually.</source>
            <translation>Brak ścieżki Java zapisanej w QSettings. Uruchom najpierw 'Pobierz środowisko uruchomieniowe Java', lub odznacz 'Użyj zapisanej ścieżki Java' i podaj ścieżkę ręcznie.</translation>
        </message>
        <message>
            <source>Using Java path from QSettings: {java}</source>
            <translation>Używana ścieżka Java z QSettings: {java}</translation>
        </message>
        <message>
            <source>JAVA_PATH is empty. Either check 'Use saved Java path' or provide the path to the Java 8 binary.</source>
            <translation>JAVA_PATH jest pusty. Odznacz 'Użyj zapisanej ścieżki Java' lub podaj ścieżkę do binarnego Java 8.</translation>
        </message>
        <message>
            <source>Java OK: version {version}</source>
            <translation>Java OK: wersja {version}</translation>
        </message>
        <message>
            <source>OTP_JAR_PATH is empty.</source>
            <translation>OTP_JAR_PATH jest pusty.</translation>
        </message>
        <message>
            <source>OTP jar not found: {jar}. Download otp-1.5.0-shaded.jar from Maven Central (org.opentripplanner:otp:1.5.0, classifier 'shaded').</source>
            <translation>Nie znaleziono pliku jar OTP: {jar}. Pobierz otp-1.5.0-shaded.jar z Maven Central (org.opentripplanner:otp:1.5.0, klasyfikator 'shaded').</translation>
        </message>
        <message>
            <source>File is not a .jar: {jar}</source>
            <translation>Plik nie jest plikiem .jar: {jar}</translation>
        </message>
        <message>
            <source>OTP jar OK: {jar} ({size_mb:.1f} MB)</source>
            <translation>OTP jar OK: {jar} ({size_mb:.1f} MB)</translation>
        </message>
        <message>
            <source>Port {port}: OTP already serving here (version {ver_str}). RunTemporalAccessibility will reuse this server.</source>
            <translation>Port {port}: OTP już obsługuje go (wersja {ver_str}). Uruchomienie dostępności czasowej ponownie użyje tego serwera.</translation>
        </message>
        <message>
            <source>Port {port} is held by a non-OTP process (responds to TCP but not as an OTP /otp endpoint). Pick a different OTP_PORT or stop the conflicting service.</source>
            <translation>Port {port} jest zajęty przez proces niebędący OTP (odpowiada na TCP, ale nie jako punkt końcowy /otp). Wybierz inny PORT_OTP lub zatrzymaj konfliktujący serwis.</translation>
        </message>
        <message>
            <source>Port {port}: free.</source>
            <translation>Port {port}: wolny.</translation>
        </message>
    </context>
</TS>
