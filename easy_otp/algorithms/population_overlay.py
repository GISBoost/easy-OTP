"""PopulationOverlay: areal interpolation of demographic data onto a hex grid.

Ports the QGIS model ``ludnosc_studentow.model3`` as a proper Processing
algorithm.  The only deviation from the reference model is step 6: the
reference uses FIELD_TYPE=1 (Integer) with FIELD_PRECISION=0, which rounds
sub-0.5 person fragments to zero.  This implementation uses FIELD_TYPE=0
(Double) with FIELD_PRECISION=2 to preserve fractional counts.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingMultiStepFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)


class PopulationOverlay(QgsProcessingAlgorithm):
    HEX_GRID = "HEX_GRID"
    POPULATION_LAYER = "POPULATION_LAYER"
    POPULATION_FIELD = "POPULATION_FIELD"
    OUTPUT = "OUTPUT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "populationoverlay"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Population overlay")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "analysis"

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Overlays a demographic polygon layer on a hexagonal grid using "
            "areal interpolation weighted by surface area.\n\n"
            "Each hexagon receives a 'num_students' field (Float) with the "
            "estimated number of persons from the chosen population field. "
            "The algorithm splits census polygons by hex edges, computes the "
            "area-weighted population of each piece, then sums those pieces "
            "per hexagon.\n\n"
            "The hex grid must be in a projected CRS with metric units "
            "(e.g. EPSG:2180, EPSG:3857). If the population layer has a "
            "different CRS it is reprojected automatically before processing."
        )

    def createInstance(self):  # noqa: N802 — Qt API name
        return PopulationOverlay()

    def initAlgorithm(self, config=None):  # noqa: N802 — Qt API name
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.HEX_GRID,
                self.tr("Hex grid"),
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.POPULATION_LAYER,
                self.tr("Population layer"),
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.POPULATION_FIELD,
                self.tr("Population field"),
                parentLayerParameterName=self.POPULATION_LAYER,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="pop20_29",
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Output (hex grid with num_students)"),
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(  # noqa: N802 — Qt API name
        self,
        parameters: dict,
        context,
        feedback,
    ) -> dict:
        import processing  # noqa: PLC0415 — available only inside the QGIS interpreter

        hex_grid = self.parameterAsVectorLayer(parameters, self.HEX_GRID, context)
        pop_layer = self.parameterAsVectorLayer(parameters, self.POPULATION_LAYER, context)
        pop_field = self.parameterAsString(parameters, self.POPULATION_FIELD, context)

        # --- Validate inputs ---

        hex_crs = hex_grid.sourceCrs()
        if hex_crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "Hex grid must be in a projected CRS with metric units "
                "(e.g. EPSG:2180, EPSG:3857). Got: {}.".format(hex_crs.authid())
            ))

        if pop_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            raise QgsProcessingException(self.tr(
                "Population layer must be polygonal, got '{}'.".format(
                    QgsWkbTypes.geometryDisplayString(pop_layer.geometryType())
                )
            ))

        pop_fields = pop_layer.fields()
        if pop_fields.indexFromName(pop_field) < 0:
            raise QgsProcessingException(self.tr(
                "Population layer has no field '{}'.".format(pop_field)
            ))

        field_obj = pop_fields.field(pop_field)
        if field_obj.type() not in (
            QVariant.Int, QVariant.UInt, QVariant.Double, QVariant.LongLong
        ):
            raise QgsProcessingException(self.tr(
                "Field '{}' must be numeric (Int or Float), got '{}'.".format(
                    pop_field, field_obj.typeName()
                )
            ))

        if hex_grid.fields().indexFromName("num_students") >= 0:
            raise QgsProcessingException(self.tr(
                "Output field 'num_students' already exists in HEX_GRID. "
                "Remove it or rename it before running PopulationOverlay."
            ))

        # --- Optional reproject (outside the 7-step feedback) ---

        pop_source = parameters[self.POPULATION_LAYER]
        if pop_layer.sourceCrs() != hex_crs:
            feedback.pushInfo(self.tr(
                "Reprojecting population layer from {} to {}.".format(
                    pop_layer.sourceCrs().authid(), hex_crs.authid()
                )
            ))
            reproject_result = processing.run(
                "native:reprojectlayer",
                {
                    "INPUT": pop_source,
                    "TARGET_CRS": hex_crs,
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context,
                feedback=feedback,
                is_child_algorithm=True,
            )
            pop_source = reproject_result["OUTPUT"]

        # --- 7-step areal interpolation ---

        multi_feedback = QgsProcessingMultiStepFeedback(7, feedback)
        outputs: dict = {}

        # Step 1 — area of each census polygon
        multi_feedback.setCurrentStep(0)
        if multi_feedback.isCanceled():
            return {}
        outputs["step1"] = processing.run(
            "native:fieldcalculator",
            {
                "FIELD_LENGTH": 10,
                "FIELD_NAME": "area",
                "FIELD_PRECISION": 2,
                "FIELD_TYPE": 0,   # Double
                "FORMULA": "$area",
                "INPUT": pop_source,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        # Step 2 — population density (persons / m²)
        multi_feedback.setCurrentStep(1)
        if multi_feedback.isCanceled():
            return {}
        outputs["step2"] = processing.run(
            "native:fieldcalculator",
            {
                "FIELD_LENGTH": 10,
                "FIELD_NAME": "_eo_density",
                "FIELD_PRECISION": 6,
                "FIELD_TYPE": 0,   # Double
                "FORMULA": '"{}"/"area"'.format(pop_field),
                "INPUT": outputs["step1"]["OUTPUT"],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        # Step 3 — split census polygons by hex grid edges
        multi_feedback.setCurrentStep(2)
        if multi_feedback.isCanceled():
            return {}
        outputs["step3"] = processing.run(
            "native:splitwithlines",
            {
                "INPUT": outputs["step2"]["OUTPUT"],
                "LINES": parameters[self.HEX_GRID],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        # Step 4 — area of each split piece
        multi_feedback.setCurrentStep(3)
        if multi_feedback.isCanceled():
            return {}
        outputs["step4"] = processing.run(
            "native:fieldcalculator",
            {
                "FIELD_LENGTH": 10,
                "FIELD_NAME": "_eo_part_area",
                "FIELD_PRECISION": 2,
                "FIELD_TYPE": 0,   # Double
                "FORMULA": "$area",
                "INPUT": outputs["step3"]["OUTPUT"],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        # Step 5 — one representative point per piece (carries piece attributes)
        multi_feedback.setCurrentStep(4)
        if multi_feedback.isCanceled():
            return {}
        outputs["step5"] = processing.run(
            "native:pointonsurface",
            {
                "ALL_PARTS": False,
                "INPUT": outputs["step4"]["OUTPUT"],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        # Step 6 — population count for each piece = part_area × density
        # FIX vs reference: FIELD_TYPE=0 (Double) preserves fractional counts;
        # the reference uses FIELD_TYPE=1 (Integer) which truncates < 0.5 persons.
        multi_feedback.setCurrentStep(5)
        if multi_feedback.isCanceled():
            return {}
        outputs["step6"] = processing.run(
            "native:fieldcalculator",
            {
                "FIELD_LENGTH": 10,
                "FIELD_NAME": "_eo_part_pop",
                "FIELD_PRECISION": 2,
                "FIELD_TYPE": 0,   # Double — bug fix (reference uses Integer=1)
                "FORMULA": '"_eo_part_area"*"_eo_density"',
                "INPUT": outputs["step5"]["OUTPUT"],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        # Step 7 — sum weighted points per hexagon → num_students
        multi_feedback.setCurrentStep(6)
        if multi_feedback.isCanceled():
            return {}
        outputs["step7"] = processing.run(
            "native:countpointsinpolygon",
            {
                "CLASSFIELD": None,
                "FIELD": "num_students",
                "POINTS": outputs["step6"]["OUTPUT"],
                "POLYGONS": parameters[self.HEX_GRID],
                "WEIGHT": "_eo_part_pop",
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=multi_feedback,
            is_child_algorithm=True,
        )

        result_layer = context.getMapLayer(outputs["step7"]["OUTPUT"])
        zero_count = sum(
            1 for feat in result_layer.getFeatures()
            if not feat["num_students"]
        )
        if zero_count:
            feedback.pushInfo(self.tr(
                "{} hexagon(s) have num_students = 0 "
                "(not covered by the population layer).".format(zero_count)
            ))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            result_layer.fields(), result_layer.wkbType(), result_layer.sourceCrs(),
        )
        for feat in result_layer.getFeatures():
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}
