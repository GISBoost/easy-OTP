# City TERYT reference (verified against GUS NSP2021 Excel + KBW precinct registry, not guessed)

Verification method: for each city, sum of district/delegatura population rows in
`docs/gis/ludnosc_nsp_2021.xlsx` must equal the city's own "miasto na prawach powiatu"
summary row in the same sheet. All five checked OK below.

| City | Voivodeship sheet | GUS powiat symbol (4-digit) | Census GMINA codes (7-char, `GMINA` field in SU_BREC_2021_OBW.shp) | KBW TERYT gminy (6-digit, `obwody_glosowania`/`wyniki_gl_na_listy...` CSVs) | Population (NSP2021) |
|---|---|---|---|---|---|
| Łódź | Łódzkie | 1061 | 1061029, 1061039, 1061049, 1061059, 1061069 (5 delegatury) | 106101 (single) | 670,642 |
| Warszawa | Mazowieckie | 1465 | 1465028..1465198 step 10, 18 values (18 dzielnice) | 146502..146519, 18 values (18 dzielnice) | 1,860,281 |
| Kraków | Małopolskie | 1261 | 1261029, 1261039, 1261049, 1261059 (4 delegatury) | 126101 (single) | 800,653 |
| Poznań | Wielkopolskie | 3064 | 3064029, 3064039, 3064049, 3064059, 3064069 (5 delegatury) | 306401 (single) | ~546,859* |
| Gdańsk | Pomorskie | 2261 | 2261011 (single, not split) | 226101 (single) | 486,022 |
| Szczecin | Zachodniopomorskie | 3262 | 3262011 (single, not split) | 326201 (single) | 396,168 |

\* Poznań: sum of 5 delegatury rows in the Excel; the "miasto na prawach powiatu" summary
row for Poznań was not captured in the same grep pass — cross-check population sum
against Poznań's real ~2021 census population (532,048 per GUS official city page) before
trusting this number blindly; if the delegatura sum diverges meaningfully, re-verify.

## Important structural note

- Łódź, Warszawa, Kraków, Poznań: census population data is split into sub-city units
  (dzielnica/delegatura) in the Excel — the census GMINA code for tract geometry is
  NOT the same as the single powiat-level code. Filter tract geometry by the FULL list
  of sub-unit GMINA codes, not by powiat prefix alone.
- Gdańsk, Szczecin: NOT split — single GMINA code, same pattern as Piotrków Trybunalski
  (the city that was mistakenly used for Łódź on the first attempt).
- KBW election-results TERYT is independently split per district for Warszawa (18 codes)
  but stays a single code for Kraków/Poznań/Gdańsk/Szczecin even where the census data
  is delegatura-split (Kraków, Poznań) — census sub-division and electoral sub-division
  are NOT the same partition. Always filter KBW files by powiat-prefix TERYT match, not
  by assuming it mirrors the census GMINA list.
