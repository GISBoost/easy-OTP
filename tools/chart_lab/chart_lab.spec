# PyInstaller spec for chart_lab. Build with:
#   cd tools\chart_lab
#   .venv\Scripts\python.exe -m PyInstaller chart_lab.spec --clean
#
# `pathex` below MUST include transit_charts/ and family_a_reconstruction/, not just this
# directory: chart_lab/paths.py wires them onto sys.path at RUNTIME (sys.path.insert), but
# PyInstaller's Analysis() only sees import/from statements via static source scanning - it
# never executes that sys.path.insert call, so without pathex it silently fails to find and
# bundle transit_charts/family_a at all, and the frozen build breaks with
# "ModuleNotFoundError: No module named 'transit_charts'" despite building without error.
#
# --onedir, not --onefile: faster startup and far easier to inspect/debug a first build (a
# onefile build unpacks itself to a temp dir on every launch, which just adds a step to
# reproducing any bundling problem). Reversible - a future milestone can switch to onefile
# once the bundle is known-good.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

_HERE = Path.cwd().resolve()
_TRANSIT_CHARTS = _HERE.parent / "transit_charts"
_FAMILY_A = _HERE.parent / "family_a_reconstruction"
_FAMILY_B = _HERE.parent / "family_b_realized"
_RT_DIAGNOSE = _HERE.parent / "rt_diagnose"
_REPO_ROOT = _HERE.parent.parent  # for easy_otp.core.gtfsrt_realizer (imports no QGIS)

datas = [
    (str(_HERE / "example_data"), "example_data"),
    (str(_HERE.parent.parent / "LICENSE"), "."),
]
# Gradio ships its static frontend (JS/CSS/templates) as package data, which PyInstaller's
# default import analysis does not pick up - it only follows Python imports, not files a
# package reads off disk at runtime. Missing these produces a build that launches but serves
# a blank/broken page in the browser.
#
# `include_py_files=True` for gradio specifically: gradio's own class-definition-time codegen
# (gradio/component_meta.py's create_or_modify_pyi, run via a metaclass on every *Events mixin
# class at import time) calls `inspect.getfile(cls)` then `Path.read_text()` on gradio's OWN
# .py source - a dev-time .pyi-stub-generation feature with no frozen-build guard. Without the
# raw .py source physically present next to the bundled bytecode, `import gradio` itself raises
# FileNotFoundError before the app ever gets a chance to run - confirmed by actually building
# and launching the frozen exe (see CL-6 verification), exactly the class of bug the PRD's
# "gradio may need a hook" warning was about, just a different failure mode than expected.
for pkg in ("gradio_client", "safehttpx", "groovy"):
    datas += collect_data_files(pkg)
datas += collect_data_files("gradio", include_py_files=True)

hiddenimports = (
    collect_submodules("transit_charts")
    + collect_submodules("family_a")
    + collect_submodules("gradio")
    + collect_submodules("gradio_client")
    # GL-1..GL-4: jobs.py imports these lazily inside the spawned child, which the static
    # scan can't always follow through a tool-name dispatch, so name them explicitly.
    + collect_submodules("chart_lab")
    + ["build_realized", "compare_rt_vs_static", "easy_otp.core.gtfsrt_realizer",
       "easy_otp.core.gtfsrt_recorder", "google.transit.gtfs_realtime_pb2", "tkinter"]
)

a = Analysis(
    ["chart_lab/app.py"],
    pathex=[str(_HERE), str(_TRANSIT_CHARTS), str(_FAMILY_A), str(_FAMILY_B), str(_RT_DIAGNOSE),
            str(_REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chart_lab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # shows Gradio's "Running on local URL" log; also surfaces a crash reason
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="chart_lab",
)
