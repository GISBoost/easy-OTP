"""Per-city configuration for the multi-city accessibility pipeline (POI services
+ university/student accessibility, Lodz method generalized to 5 more cities).

geofabrik_region: voivodeship extract to clip from (download.geofabrik.de/europe/poland/)
voivodeship_sheet: sheet name in docs/gis/ludnosc_nsp_2021.xlsx for age-2029 extraction
osm_admin_level: Overpass area admin_level that worked for Lodz (6) -- verified per
    city before trusting (see fetch_osm_services.py's fallback to 8 if 6 returns 0).
universities: name -> regex (case-insensitive) matched against OSM name/operator tags.
    Chosen per Michal's instruction: polytechnic + university + medical university
    where all 3 exist as separate institutions; substituted with the closest local
    equivalent otherwise (see comments per city).
"""

CITIES = {
    "warszawa": {
        "display_name": "Warszawa",
        "geofabrik_region": "mazowieckie",
        "voivodeship_sheet": "Mazowieckie",
        "osm_admin_level": "6",
        "universities": {
            "Politechnika Warszawska": r"politechnik\w*\s*warszawsk",
            "Uniwersytet Warszawski": r"uniwersytet\w*\s*warszawsk",
            "Warszawski Uniwersytet Medyczny": r"uniwersytet\w*\s*medyczn",
        },
    },
    "krakow": {
        "display_name": "Kraków",
        "geofabrik_region": "malopolskie",
        "voivodeship_sheet": "Małopolskie",
        "osm_admin_level": "6",
        # Krakow's medical school is Collegium Medicum UJ, not a separate
        # "Uniwersytet Medyczny" institution -- substituted per instructions.
        "universities": {
            "Politechnika Krakowska": r"politechnik\w*\s*krakowsk",
            "Uniwersytet Jagielloński": r"jagiell",
            "Collegium Medicum UJ": r"collegium\s*medicum",
        },
    },
    "gdansk": {
        "display_name": "Gdańsk",
        "geofabrik_region": "pomorskie",
        "voivodeship_sheet": "Pomorskie",
        "osm_admin_level": "6",
        "universities": {
            "Politechnika Gdańska": r"politechnik\w*\s*gda[nń]sk",
            "Uniwersytet Gdański": r"uniwersytet\w*\s*gda[nń]sk",
            "Gdański Uniwersytet Medyczny": r"uniwersytet\w*\s*medyczn",
        },
    },
    "poznan": {
        "display_name": "Poznań",
        "geofabrik_region": "wielkopolskie",
        "voivodeship_sheet": "Wielkopolskie",
        "osm_admin_level": "6",
        # Poznan's main university is UAM (Adam Mickiewicz), not "Uniwersytet
        # Poznański" -- substituted per instructions.
        "universities": {
            "Politechnika Poznańska": r"politechnik\w*\s*pozna[nń]sk",
            "Uniwersytet im. Adama Mickiewicza": r"adama?\s*mickiewicza|\buam\b",
            "Uniwersytet Medyczny im. K. Marcinkowskiego": r"uniwersytet\w*\s*medyczn",
        },
    },
    "szczecin": {
        "display_name": "Szczecin",
        "geofabrik_region": "zachodniopomorskie",
        "voivodeship_sheet": "Zachodniopomorskie",
        "osm_admin_level": "6",
        # Szczecin has no "Politechnika" -- ZUT (technical university) is the
        # closest local equivalent, substituted per instructions.
        "universities": {
            "Zachodniopomorski Uniwersytet Technologiczny": r"zachodniopomorski\w*\s*uniwersytet\w*\s*technologiczn|\bzut\b",
            "Uniwersytet Szczeciński": r"uniwersytet\w*\s*szczeci[nń]sk",
            "Pomorski Uniwersytet Medyczny": r"uniwersytet\w*\s*medyczn",
        },
    },
    "gzm": {
        # Metropolitan association of 41 municipalities, not a single city --
        # no ses_income_lodz/gzm.gpkg exists (unlike the other cities here),
        # so prepare_osm_pbf.py falls back to gzm_boundary.geojson (Nominatim
        # relation 8269826, verified 2026-08-29) instead of an SES gpkg bbox.
        # universities/osm_admin_level intentionally omitted -- irrelevant to
        # the isochrone pipeline, this entry only exists for geofabrik_region.
        "display_name": "Górnośląsko-Zagłębiowska Metropolia",
        "geofabrik_region": "slaskie",
        # bbox clip would pull in a lot of extra Śląskie territory outside
        # the 41-municipality union (Rybnik, Żory etc. sit right next to it)
        # -- see prepare_osm_pbf.py's clip_method handling.
        "clip_method": "polygon",
    },
    "kielce": {
        "display_name": "Kielce",
        "geofabrik_region": "swietokrzyskie",
    },
}
