"""PrepareStudentLayer: joins GUS NSP 2021 Excel data to census-tract geometry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant

if TYPE_CHECKING:
    from qgis.core import QgsProcessingContext, QgsProcessingFeedback


class PrepareStudentLayer(QgsProcessingAlgorithm):
    EXCEL_FILE = "EXCEL_FILE"
    EXCEL_SHEET = "EXCEL_SHEET"
    POPULATION_COLUMN = "POPULATION_COLUMN"
    GEOMETRY_LAYER = "GEOMETRY_LAYER"
    KEY_FIELD = "KEY_FIELD"
    OUTPUT_FIELD_NAME = "OUTPUT_FIELD_NAME"
    OUTPUT = "OUTPUT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "preparestudentlayer"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Prepare student layer")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "analysis"

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Reads a GUS NSP 2021 Excel file and joins census-tract population "
            "data to a polygon geometry layer.\n\n"
            "Handles three observed states of GUS Excel files:\n"
            "  'raw'   — multi-row header, short symbols; region code forward-filled "
            "from preceding 'rejon statystyczny' rows.\n"
            "  'wrong' — full 7-char keys, but population values are strings with "
            "'-' as suppression markers.\n"
            "  'done'  — clean, numeric values, minimum processing.\n\n"
            "Output: a polygon layer with the original geometry attributes plus one "
            "added Double field (default 'pop20_29') — ready for use as "
            "POPULATION_LAYER in the Population overlay (R1b) algorithm.\n\n"
            "Requires openpyxl. If the automatic install at QGIS startup failed "
            "(e.g. SSL unavailable in QGIS 3.22), install manually from the "
            "OSGeo4W Shell: python -m pip install openpyxl — then restart QGIS.\n\n"
            "Input file: download the 'Ludnosc w rejonach statystycznych "
            "i obwodach spisowych' table from the GUS NSP 2021 results page "
            "(stat.gov.pl/spisy-powszechne/nsp-2021/)."
        )

    def createInstance(self):  # noqa: N802 — Qt API name
        return PrepareStudentLayer()

    def initAlgorithm(self, config=None):  # noqa: N802 — Qt API name
        self.addParameter(
            QgsProcessingParameterFile(
                self.EXCEL_FILE,
                self.tr("GUS NSP 2021 Excel file"),
                behavior=QgsProcessingParameterFile.File,
                fileFilter=self.tr("Excel files (*.xlsx)"),
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.EXCEL_SHEET,
                self.tr("Sheet name (empty = first sheet)"),
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.POPULATION_COLUMN,
                self.tr("Population column name in Excel header"),
                defaultValue="pop20-29",
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.GEOMETRY_LAYER,
                self.tr("Census tract geometry layer"),
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.KEY_FIELD,
                self.tr("Join key field in geometry layer"),
                parentLayerParameterName=self.GEOMETRY_LAYER,
                defaultValue="OBWOD",
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.OUTPUT_FIELD_NAME,
                self.tr("Output field name"),
                defaultValue="pop20_29",
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Output layer"),
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(  # noqa: N802 — Qt API name
        self,
        parameters: dict,
        context: "QgsProcessingContext",
        feedback: "QgsProcessingFeedback",
    ) -> dict:
        import json        # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        from pathlib import Path

        from ..core.dependencies import _get_python_executable, ensure_openpyxl  # noqa: PLC0415

        if not ensure_openpyxl():
            raise QgsProcessingException(self.tr(
                "openpyxl is not available. If the automatic install at QGIS "
                "startup failed, install manually from the OSGeo4W Shell: "
                "python -m pip install openpyxl — then restart QGIS."
            ))

        excel_path = self.parameterAsFile(parameters, self.EXCEL_FILE, context)
        excel_sheet = self.parameterAsString(parameters, self.EXCEL_SHEET, context).strip()
        pop_col = self.parameterAsString(parameters, self.POPULATION_COLUMN, context).strip()
        geom_layer = self.parameterAsVectorLayer(parameters, self.GEOMETRY_LAYER, context)
        key_field = self.parameterAsString(parameters, self.KEY_FIELD, context)
        out_field_name = (
            self.parameterAsString(parameters, self.OUTPUT_FIELD_NAME, context).strip() or
            "pop20_29"
        )

        feedback.setProgress(0)

        # --- Step 1: Load Excel sheet (subprocess to avoid QGIS libxml2 DLL conflict) ---
        feedback.pushInfo(self.tr("Loading Excel file: {}".format(excel_path)))
        reader = Path(__file__).parent.parent / "core" / "xlsx_reader.py"
        args_json = json.dumps(
            {"path": excel_path, "sheet": excel_sheet or None},
            ensure_ascii=False,
        )
        proc = subprocess.run(  # nosec S603 S607
            [_get_python_executable(), str(reader), args_json],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if proc.returncode != 0:
            raise QgsProcessingException(self.tr(
                "Excel reader subprocess failed (exit {}):\n{}".format(
                    proc.returncode, (proc.stderr or "")[-500:]
                )
            ))
        data = json.loads(proc.stdout)
        if "error" in data:
            raise QgsProcessingException(self.tr(data["error"]))

        rows = data["rows"]
        sheet_names: list[str] = data["sheet_names"]
        sheet_name_used: str = data["sheet_used"]
        if len(sheet_names) > 1 and not excel_sheet:
            feedback.pushInfo(self.tr(
                "Multi-sheet workbook; using first sheet '{}'. "
                "All sheets: {}.".format(sheet_name_used, ", ".join(sheet_names))
            ))

        feedback.setProgress(5)
        if feedback.isCanceled():
            return {}

        # --- Step 2: Header detection (two-pass: Symbol/Struktura may be on a
        #     different row than the population sub-header in raw GUS exports) ---
        struct_row_idx = None
        pop_row_idx = None
        col_symbol = col_struktura = col_population = None

        for i, row in enumerate(rows[:30]):
            row_strs = [str(v).strip() if v is not None else "" for v in row]
            if struct_row_idx is None and "Struktura" in row_strs:
                sym_col = next(
                    (j for j, s in enumerate(row_strs)
                     if s == "Symbol" or s.startswith("Symbol ")),
                    None,
                )
                if sym_col is not None:
                    struct_row_idx = i
                    col_symbol = sym_col
                    col_struktura = row_strs.index("Struktura")
            if col_population is None and pop_col in row_strs:
                pop_row_idx = i
                col_population = row_strs.index(pop_col)
            if struct_row_idx is not None and col_population is not None:
                break

        if struct_row_idx is None:
            raise QgsProcessingException(self.tr(
                "Could not detect header row. Searched rows 0-29 for columns "
                "'Symbol' and 'Struktura'. "
                "Check that the sheet '{}' is correct.".format(sheet_name_used)
            ))
        if col_population is None:
            nearby: set[str] = set()
            for row in rows[max(0, struct_row_idx - 1): struct_row_idx + 3]:
                nearby.update(
                    str(v).strip() for v in row if v is not None and str(v).strip()
                )
            raise QgsProcessingException(self.tr(
                "Column '{}' not found in header. "
                "Available columns near row {}: {}.".format(
                    pop_col, struct_row_idx, ", ".join(sorted(nearby))
                )
            ))

        header_row_idx = max(struct_row_idx, pop_row_idx)

        feedback.pushInfo(self.tr(
            "Header: Symbol/Struktura at row {} (0-based), '{}' at row {}. "
            "Columns: Symbol={}, Struktura={}, {}={}.".format(
                struct_row_idx, pop_col, pop_row_idx,
                col_symbol, col_struktura, pop_col, col_population
            )
        ))
        feedback.setProgress(10)

        # --- Step 3: Single-pass extraction ---
        excel_data: dict[str, float] = {}
        duplicates: list[str] = []
        dash_count = 0
        tract_count = 0
        current_rejon: str | None = None

        data_rows = rows[header_row_idx + 1:]
        n_data = max(len(data_rows), 1)

        for i, row in enumerate(data_rows):
            if feedback.isCanceled():
                return {}

            row_num = header_row_idx + 2 + i  # 1-based for user-facing messages

            val_symbol = row[col_symbol] if col_symbol < len(row) else None
            val_struktura = row[col_struktura] if col_struktura < len(row) else None
            val_pop = row[col_population] if col_population < len(row) else None

            if val_struktura is None:
                continue

            str_struktura = str(val_struktura).strip()

            if str_struktura == "rejon statystyczny":
                if isinstance(val_symbol, (int, float)):
                    rej_str = str(int(val_symbol))
                    current_rejon = rej_str.zfill(6) if len(rej_str) == 5 else rej_str
                else:
                    current_rejon = str(val_symbol).strip() if val_symbol is not None else ""
                continue

            if str_struktura != "obwód spisowy":
                continue

            # Build join key
            if isinstance(val_symbol, (int, float)):
                sym_str = str(int(val_symbol))
                sym = sym_str.zfill(7) if len(sym_str) == 6 else sym_str
            else:
                sym = str(val_symbol).strip() if val_symbol is not None else ""
            if len(sym) >= 7:
                key = sym
            else:
                if current_rejon is None:
                    raise QgsProcessingException(self.tr(
                        "Row {}: census tract '{}' encountered without a preceding "
                        "'rejon statystyczny' row. Cannot build join key.".format(
                            row_num, sym
                        )
                    ))
                key = current_rejon + sym

            # Coerce population value
            if val_pop is None:
                pop_value = 0.0
            elif isinstance(val_pop, (int, float)):
                pop_value = float(val_pop)
            else:
                str_pop = str(val_pop).strip()
                if str_pop == "-":
                    pop_value = 0.0
                    dash_count += 1
                elif str_pop == "":
                    pop_value = 0.0
                else:
                    try:
                        pop_value = float(str_pop)
                    except ValueError:
                        raise QgsProcessingException(self.tr(
                            "Row {}: cannot interpret '{}' as a number in column '{}'. "
                            "Expected a number, an empty cell, or '-'.".format(
                                row_num, val_pop, pop_col
                            )
                        ))

            tract_count += 1
            if key in excel_data:
                excel_data[key] += pop_value
                duplicates.append(key)
            else:
                excel_data[key] = pop_value

            if i % 500 == 0:
                feedback.setProgress(10 + int(30 * i / n_data))

        feedback.setProgress(40)

        # --- Step 4: Validate duplicates ---
        if duplicates:
            unique_dupes = sorted(set(duplicates))
            feedback.pushWarning(self.tr(
                "{} OBWOD symbol(s) appeared more than once; population values summed "
                "(GUS records split census tracts under the same symbol at "
                "administrative boundaries): {}{}.".format(
                    len(unique_dupes),
                    ", ".join(unique_dupes[:5]),
                    "..." if len(unique_dupes) > 5 else "",
                )
            ))

        feedback.pushInfo(self.tr(
            "Excel extraction: {} tract rows, {} unique keys, "
            "{} '-' values converted to 0.".format(
                tract_count, len(excel_data), dash_count
            )
        ))
        feedback.setProgress(45)

        # --- Step 5: Validate KEY_FIELD, build sink, join ---
        geom_fields = geom_layer.fields()
        if geom_fields.indexFromName(key_field) < 0:
            available = [f.name() for f in geom_fields]
            raise QgsProcessingException(self.tr(
                "Geometry layer has no field '{}'. Available fields: {}.".format(
                    key_field, ", ".join(available)
                )
            ))

        key_field_obj = geom_fields.field(geom_fields.indexFromName(key_field))
        key_is_numeric = key_field_obj.type() in (
            QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.Double
        )

        out_fields = QgsFields()
        for f in geom_fields:
            out_fields.append(QgsField(f))
        out_fields.append(QgsField(out_field_name, QVariant.Double))

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            out_fields,
            geom_layer.wkbType(),
            geom_layer.sourceCrs(),
        )

        total_geom = geom_layer.featureCount()
        n_geom = max(total_geom, 1)
        matched_count = 0
        unmatched_geom = 0
        leading_zero_warned = False
        pop_values: list[float] = []

        for feat_idx, feat in enumerate(geom_layer.getFeatures()):
            if feedback.isCanceled():
                return {}

            raw_key = feat[key_field]

            if raw_key is None:
                str_key = ""
            elif key_is_numeric:
                int_val = int(raw_key)
                str_key = str(int_val)
                if not leading_zero_warned and len(str_key) < 7:
                    feedback.pushWarning(self.tr(
                        "Key field '{}' is numeric; leading zeros may be lost when "
                        "converting to string. Consider storing '{}' as a text field "
                        "to preserve keys like '0123456'.".format(
                            key_field, key_field
                        )
                    ))
                    leading_zero_warned = True
            else:
                str_key = str(raw_key).strip()

            out_feat = QgsFeature(out_fields)
            out_feat.setGeometry(feat.geometry())

            attrs = list(feat.attributes())
            if str_key in excel_data:
                pop_val = excel_data[str_key]
                attrs.append(pop_val)
                pop_values.append(pop_val)
                matched_count += 1
            else:
                attrs.append(None)
                unmatched_geom += 1

            out_feat.setAttributes(attrs)
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

            if feat_idx % 100 == 0:
                feedback.setProgress(50 + int(45 * feat_idx / n_geom))

        if matched_count == 0:
            raise QgsProcessingException(self.tr(
                "None of the {} Excel rows match the geometry layer. "
                "Check that you provided the correct file for this region.".format(
                    len(excel_data)
                )
            ))

        # --- Step 6: Final report ---
        excel_not_in_geom = len(excel_data) - matched_count
        pop_min = min(pop_values) if pop_values else 0.0
        pop_max = max(pop_values) if pop_values else 0.0
        pop_sum = sum(pop_values) if pop_values else 0.0

        feedback.pushInfo(self.tr(
            "--- PrepareStudentLayer complete ---\n"
            "Excel tract rows:              {}\n"
            "Geometry features:             {}\n"
            "Matched (both sets):           {}\n"
            "Excel keys not in geometry:    {}\n"
            "Geometry features unmatched:   {} ({} = NULL)\n"
            "'-' values converted to 0:     {}\n"
            "{} stats:  min={:.1f}  max={:.1f}  sum={:.1f}".format(
                tract_count,
                total_geom,
                matched_count,
                excel_not_in_geom,
                unmatched_geom, out_field_name,
                dash_count,
                out_field_name, pop_min, pop_max, pop_sum,
            )
        ))

        feedback.setProgress(100)
        return {self.OUTPUT: dest_id}
