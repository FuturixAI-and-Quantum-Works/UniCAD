"""Drawing-set assembly: plan the sheets of each drawing kind, number them, render and index the set."""

from unicad.sets.registry import BUILDERS, BuiltSheet, SheetBuilder, builder, register
from unicad.sets.render import SetIndex, load_profile, plan_set, render_set, render_sld_set
from unicad.sets.sld import SldBuilder, block_types, plan_sld_set

__all__ = [
    "BUILDERS",
    "BuiltSheet",
    "SetIndex",
    "SheetBuilder",
    "SldBuilder",
    "block_types",
    "builder",
    "load_profile",
    "plan_set",
    "plan_sld_set",
    "register",
    "render_set",
    "render_sld_set",
]
