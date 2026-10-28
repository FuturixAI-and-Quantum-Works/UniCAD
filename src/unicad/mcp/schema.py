"""Enum-valued tool parameters as the tool input schema declares them, so a client sees the allowed values and never guesses.

Each alias is `Annotated[<enum or Literal>, Field(description=...)]` whose description lists the values and, where the value is a
quantity, its unit. Tool modules import the alias instead of typing `str`; tests/test_mcp_schema.py holds the table of every
enum-valued parameter and the check that the schema exposes exactly the values the code accepts.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from unicad.arrays.spec import FillOrder, MountingKind, PitchMethod
from unicad.domain.layout import Orientation
from unicad.domain.network import Laying, NodeKind, Service
from unicad.domain.project import LightningProtectionLevel, Segment
from unicad.domain.site import FeatureKind
from unicad.drafting.sheets.model import PaperSize


def values(enum: type[StrEnum]) -> str:
    return ", ".join(e.value for e in enum)


CatalogKind = Literal["module", "inverter", "battery", "pcs", "transformer", "cable", "protection", "meter"]
DrawingKind = Literal["all", "register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom"]
RuleFamily = Literal["structure", "pv_strings", "ratio", "protection", "cables", "vdrop", "earthing", "lps", "layout", "drawings"]
SiteFormatName = Literal["kml", "kmz", "geojson", "dxf", "coords"]
DrawingUnit = Literal["m", "mm", "cm", "ft", "in", "km", "yd", "dm"]
LpsMethod = Literal["ese", "rod"]
RegionMode = Literal["auto", "ground", "roofs"]

LayingArg = Annotated[Laying | None, Field(description=f"Laying method, which sets the ampacity table: {values(Laying)}")]
ServiceArg = Annotated[Service | None, Field(description=f"Circuit type: {values(Service)}")]
NodeKindArg = Annotated[NodeKind, Field(description=f"Equipment kind: {values(NodeKind)}")]
SegmentArg = Annotated[Segment, Field(description=f"Project segment: {values(Segment)}")]
FeatureKindArg = Annotated[FeatureKind, Field(description=f"Site feature kind: {values(FeatureKind)}")]
MountingArg = Annotated[MountingKind, Field(description=f"Mounting structure: {values(MountingKind)}")]
PitchMethodArg = Annotated[PitchMethod, Field(description=f"Row pitch method: {values(PitchMethod)}. solstice is shade-free 09:00-15:00 solar time on the winter solstice; explicit uses pitch_m, gcr uses gcr, gap uses row_gap_m")]
FillOrderArg = Annotated[FillOrder, Field(description=f"With a target: {values(FillOrder)}. rows fills the front rows first, nearest fills closest to fill_from")]
OrientationArg = Annotated[Orientation, Field(description=f"Module orientation on the table: {values(Orientation)}")]
RegionModeArg = Annotated[RegionMode, Field(description="Where to place: auto (roofs for rooftop mountings, else ground), ground or roofs")]
CatalogKindArg = Annotated[CatalogKind | None, Field(description="Catalog kind: module, inverter, battery, pcs, transformer, cable, protection or meter")]
DrawingKindsArg = Annotated[list[DrawingKind], Field(description="Drawing kinds in set order, or 'all' for the complete set: all, register, sld, layout, earthing, lps, strings, cable_schedule, bom")]
RuleFamiliesArg = Annotated[list[RuleFamily] | None, Field(description="Rule families to run; omit for all: structure, pv_strings, ratio, protection, cables, vdrop, earthing, lps, layout, drawings")]
SiteFormatArg = Annotated[SiteFormatName | None, Field(description="Site file format: kml, kmz, geojson, dxf or coords; guessed from the extension when omitted")]
DrawingUnitArg = Annotated[DrawingUnit | None, Field(description="Drawing unit of a DXF without a usable $INSUNITS: m, mm, cm, ft, in, km, yd or dm")]
PaperArg = Annotated[PaperSize, Field(description=f"Paper size: {values(PaperSize)}")]
LpsMethodArg = Annotated[LpsMethod, Field(description="ese (early streamer emission, NF C 17-102) or rod (conventional air terminal, rolling sphere)")]
LpsLevelArg = Annotated[LightningProtectionLevel, Field(description="Lightning protection level from the IS/IEC 62305-2 risk assessment: I, II, III or IV")]

EDGE_UNITS = {
    "length_m": "Route length of one instance, in metres",
    "runs": "Parallel cables per connection, number",
    "derating": "Installation derating factor, 0 to 1",
}
NODE_UNITS = {"voltage_v": "Nominal voltage of the circuit, in volts", "count": "Instances per instance of its group, number"}
NODE_RATINGS = {
    "busbar": "Node field (busbar, acdb, lt_panel, ht_panel, scb): {rating_a, material 'al'|'cu', phases e.g. '3P+N', fault_ka, fault_s}",
    "ct": "Node field (ct): {primary_a, secondary_a, cores: [{accuracy_class e.g. '0.2S' or 'PS', burden_va, purpose 'metering'|'protection'}]}",
    "pt": "Node field (pt): {primary_v, secondary_v (line-to-line), phase_to_neutral (ratio over V3), cores: [{accuracy_class, burden_va, purpose}]}",
    "relay": "Node field (relay): {functions: ANSI numbers e.g. ['50', '51', '67', '67N', '27', '59', '50N', '51N'], protocol e.g. 'IEC 61850'}",
    "spd": "Node field (ac_spd, dc_spd): {spd_type '1'|'2'|'1+2'|'3', imax_ka, in_ka, uc_v}",
    "long_time_setting_a": "Node field (breaker): Ir, the trip unit's long-time (overload) setting in A, at most the frame rating; cables are checked against it",
    "mppt": "Node field (pv_string): MPPT number (1-based) each instance lands on within its inverter, one per instance",
    "symbol": "Node field: drawing symbol key; network_symbols lists the valid keys",
}


def field_hints() -> dict[str, dict]:
    """JSON-schema `properties` for the free-form `fields` object of network_update: the enum-valued fields of nodes and edges with their allowed values, and units."""
    out: dict[str, dict] = {
        "kind": {"type": "string", "enum": [e.value for e in NodeKind], "description": "Node field: equipment kind"},
        "service": {"type": "string", "enum": [e.value for e in Service], "description": "Edge field: circuit type"},
        "laying": {"type": ["string", "null"], "enum": [*(e.value for e in Laying), None], "description": "Edge field: laying method"},
    }
    for k, text in {**EDGE_UNITS, **NODE_UNITS, **NODE_RATINGS}.items():
        out[k] = {"description": text}
    return out
