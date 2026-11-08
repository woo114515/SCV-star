"""Field groups shared by the BC cache, model and tests."""

from scv_star.data.bc_schema import SCALAR_FIELDS, UNIT_FIELDS

CATEGORICAL_FIELDS = ("unit_type", "alliance", "display_type")
BOOLEAN_FIELDS = (
    "is_flying",
    "is_burrowed",
    "is_selected",
    "is_powered",
    "is_blip",
)
NUMERIC_FIELDS = tuple(
    name for name in UNIT_FIELDS if name not in CATEGORICAL_FIELDS and name not in BOOLEAN_FIELDS
)
SCALAR_INPUT_FIELDS = tuple(SCALAR_FIELDS) + ("loop",)
ENTITY_CATEGORICAL_INDEX = tuple(UNIT_FIELDS.index(name) for name in CATEGORICAL_FIELDS)
ENTITY_BOOLEAN_INDEX = tuple(UNIT_FIELDS.index(name) for name in BOOLEAN_FIELDS)
ENTITY_NUMERIC_INDEX = tuple(UNIT_FIELDS.index(name) for name in NUMERIC_FIELDS)
SCALAR_DIM = len(SCALAR_INPUT_FIELDS)
TARGET_KINDS = ("none", "point", "unit")
