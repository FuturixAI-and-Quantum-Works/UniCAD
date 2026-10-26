"""Drawing-set assembly: plan the sheets of each drawing kind, number them, render and index the set."""

from unicad.sets.registry import BUILDERS, BuiltSheet, SheetBuilder, builder, register
from unicad.sets.render import SetIndex, load_profile, plan_set, render_set, render_sld_set
from unicad.sets.sld import SldBuilder, block_types, plan_sld_set
from unicad.sets.plans import EarthingBuilder, LayoutBuilder, LpsBuilder
from unicad.sets.strings import StringsBuilder
from unicad.sets.schedules import BomBuilder, ScheduleBuilder
from unicad.sets.register import RegisterBuilder

ALL_KINDS = ("register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom")
"""Every drawing kind in set order (which is also drawing-number block order)."""

__all__ = [
    "ALL_KINDS",
    "BUILDERS",
    "BomBuilder",
    "EarthingBuilder",
    "LayoutBuilder",
    "LpsBuilder",
    "RegisterBuilder",
    "ScheduleBuilder",
    "StringsBuilder",
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
