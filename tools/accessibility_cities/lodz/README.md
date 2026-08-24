# Łódź — kopia dla spójności struktury

Łódź była pilotażem tego pipeline'u, zbudowanym wcześniej i osobno w
`tools/accessibility_lodz/` (tam też pełna dokumentacja: `RESEARCH_LOG.md`,
`HANDOFF.md`, `STUDENTS_ANALYSIS.md`, Metody A/C zmienności — nieobecne w
pozostałych 5 miastach, patrz `MULTI_CITY_ANALYSIS.md` §Ograniczenia).

Ten folder to **kopia kluczowych plików wynikowych** (nie osobny przebieg) —
żeby Łódź siedziała w tym samym miejscu i pod tą samą konwencją nazewnictwa
(`{miasto}.osm.pbf`, `{miasto}_gtfs.zip`, `{miasto}_hex500.gpkg`, `{miasto}_
service_accessibility.csv`, `{miasto}_uni_accessibility.csv`, `out/`) co
Warszawa/Kraków/Gdańsk/Poznań/Szczecin, do przeglądania i porównań w
`analyze_cross_city.py`. **Źródło prawdy pozostaje `tools/accessibility_lodz/`**
— jeśli coś tu wygląda inaczej niż tam, ufaj tamtemu folderowi i skopiuj
ponownie (`cp` z komentarzem w historii tego repo, nie edytuj tych kopii
bezpośrednio).
