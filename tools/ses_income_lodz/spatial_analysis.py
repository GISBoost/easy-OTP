"""Spatial-autocorrelation layer of the SES analysis: for each variable used in the H1-H5
hypotheses, test whether it is spatially clustered within each city (Global Moran's I,
k-nearest-neighbor row-standardized weights, permutation test for significance -- no
esda/libpysal dependency, plain numpy+sklearn which are already installed).

This answers a different question than the H1-H5 point correlations: not "do X and Y move
together at the same tract", but "do similar values of X sit next to each other in space".
A variable with strong positive Moran's I is smooth/patchy across the city (few large zones
of similar value); one with I near 0 is spatially noisy (values bounce tract-to-tract with
no geographic pattern). This matters for interpreting H1-H5: income_index_pln is uniform
within a voting precinct by construction (Step 3 of METHODOLOGY.md), so it is expected to be
strongly clustered; if a demographic variable is comparatively unclustered, weak point
correlation with income is partly a scale-mismatch (MAUP) artifact, not proof of "no effect".

Usage: python spatial_analysis.py
"""
import warnings

import geopandas as gpd
import numpy as np
from sklearn.neighbors import NearestNeighbors

warnings.filterwarnings("ignore")

CITIES = ["lodz", "krakow", "warszawa", "poznan", "gdansk", "szczecin"]
VARS = ["income_index_pln", "fam_pct_matki_samotne", "pis_proc", "fam_avg_children",
        "hh_avg_size", "hh_pct_jednoosobowe"]
K = 8
N_PERM = 299
RNG = np.random.default_rng(42)


def morans_i(values, nn_idx):
    n = len(values)
    x = values - values.mean()
    denom = (x ** 2).sum()
    neigh_sum = x[nn_idx].sum(axis=1)  # sum of x_j over each point's k neighbors
    numer = (x * neigh_sum).sum() / K  # row-standardized weight = 1/K per neighbor
    w_row = 1.0 / K
    s0 = n * K * w_row  # = n since row-standardized (each row sums to 1)
    return (n / s0) * (numer / denom)


def permutation_p(values, nn_idx, observed):
    n = len(values)
    count_ge = 0
    for _ in range(N_PERM):
        perm = RNG.permutation(values)
        i_perm = morans_i(perm, nn_idx)
        if abs(i_perm) >= abs(observed):
            count_ge += 1
    return (count_ge + 1) / (N_PERM + 1)


results = {v: {} for v in VARS}

HAS_PIS_ON_GLOSOWANIA = {"warszawa", "poznan", "gdansk", "szczecin"}

for city in CITIES:
    gdf = gpd.read_file(f"{city}.gpkg", layer="obwody_spisowe")
    if city in HAS_PIS_ON_GLOSOWANIA:
        glos = gpd.read_file(f"{city}.gpkg", layer="obwody_glosowania")
        pis_by_precinct = dict(zip(glos["number"].astype(int).astype(str), glos["pis_proc"]))
        def _lookup(pn):
            if pn is None or gpd.pd.isna(pn):
                return None
            return pis_by_precinct.get(str(int(float(pn))))
        gdf["pis_proc"] = gdf["precinct_nr"].apply(_lookup)
    for c in VARS:
        if c not in gdf.columns:
            results[c][city] = None
            continue
        vals = gpd.pd.to_numeric(gdf[c], errors="coerce")
        mask = vals.notna() & gdf.geometry.notna()
        sub = gdf.loc[mask].copy()
        sub["_v"] = vals.loc[mask].astype(float)
        if len(sub) < 30:
            results[c][city] = None
            continue
        centroids = np.column_stack([sub.geometry.centroid.x, sub.geometry.centroid.y])
        nn = NearestNeighbors(n_neighbors=K + 1).fit(centroids)
        _, idx = nn.kneighbors(centroids)
        nn_idx = idx[:, 1:]  # drop self
        values = sub["_v"].to_numpy()
        obs = morans_i(values, nn_idx)
        p = permutation_p(values, nn_idx, obs)
        results[c][city] = (round(obs, 3), p, len(sub))
    print(f"{city}: done")

print()
COLW = 16
print(f"{'Zmienna':28s} " + " ".join(f"{c:>{COLW}s}" for c in CITIES))
for v in VARS:
    cells = []
    for city in CITIES:
        r = results[v][city]
        if r is None:
            cells.append("n/a".rjust(COLW))
        else:
            i_val, p, n = r
            sig = "***" if p < 0.01 else ("*" if p < 0.05 else "")
            cells.append(f"{i_val:+.3f}{sig} (n={n})".rjust(COLW))
    print(f"{v:28s} " + " ".join(cells))
print("\n*** p<0.01, * p<0.05 (permutation test, 299 permutacji) wg wskaznikow globalnego I Morana")
