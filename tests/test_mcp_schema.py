"""The tool input schemas an agent reads: every enum-valued parameter declares its allowed values, every unit-suffixed number names its unit."""

import asyncio
import re
import tempfile
import typing
from pathlib import Path

import pytest

from unicad.arrays.spec import FillOrder, MountingKind, PitchMethod
from unicad.domain.layout import Orientation
from unicad.domain.network import Laying, NodeKind, Service
from unicad.domain.project import IssuedFor, Segment
from unicad.domain.site import FeatureKind
from unicad.drafting.sheets.model import PaperSize
from unicad.ingest.site_dxf import UNIT_NAMES
from unicad.ingest.site_import import FORMATS
from unicad.mcp import Config, build_server, schema
from unicad.mcp.tools.catalog import KINDS
from unicad.rules import REGISTRY, families
from unicad.sets import ALL_KINDS


def names(enum) -> set[str]:
    return {e.value for e in enum}


NODE_KINDS = names(NodeKind)
SERVICES = names(Service)
LAYINGS = names(Laying)
DRAWING_KINDS = {"all", *ALL_KINDS}
COORDINATE_SYSTEMS = {"lonlat", "latlon", "local"}

ENUMS: dict[tuple[str, str], set[str]] = {
    ("network_add_node", "kind"): NODE_KINDS,
    ("network_connect", "service"): SERVICES,
    ("network_connect", "laying"): LAYINGS,
    ("catalog_search", "kind"): set(KINDS),
    ("drawings_render", "kinds"): DRAWING_KINDS,
    ("drawings_render", "formats"): {"dxf", "pdf", "dwg"},
    ("drawings_preview", "kinds"): DRAWING_KINDS,
    ("cable_schedule", "format"): {"xlsx", "dxf", "pdf"},
    ("cable_schedule", "lengths"): {"auto", "network", "estimated"},
    ("cable_schedule", "detail"): {"auto", "full", "summary"},
    ("bom", "format"): {"xlsx", "dxf", "pdf"},
    ("bom", "lengths"): {"auto", "network", "estimated"},
    ("company_preview_sheet", "paper"): names(PaperSize),
    ("lps_design", "method"): {"ese", "rod"},
    ("lps_design", "level"): {"I", "II", "III", "IV"},
    ("project_create", "segment"): names(Segment),
    ("project_issue", "issued_for"): names(IssuedFor),
    ("site_set_feature", "kind"): names(FeatureKind),
    ("site_set_feature", "coordinate_system"): COORDINATE_SYSTEMS,
    ("site_import", "coordinate_system"): COORDINATE_SYSTEMS,
    ("layout_place", "orientation"): names(Orientation),
    ("layout_place", "mounting"): names(MountingKind),
    ("layout_place", "pitch_method"): names(PitchMethod),
    ("layout_place", "fill"): names(FillOrder),
    ("layout_place", "regions"): {"auto", "ground", "roofs"},
    ("validate", "families"): set(families()),
    ("explain_finding", "rule_id"): set(REGISTRY),
    ("site_import", "format"): set(FORMATS),
    ("site_import", "units"): set(UNIT_NAMES),
}

NETWORK_UPDATE_FIELDS = {"kind": NODE_KINDS, "service": SERVICES, "laying": LAYINGS}

FREE_TEXT = {
    "bom.out_dir", "cable_schedule.out_dir", "catalog_get.id", "catalog_search.manufacturer", "catalog_search.text", "company_assign.profile_id", "company_get.profile_id",
    "company_onboard.answers", "company_onboard.dxf_path", "company_preview_sheet.profile_id", "company_update.profile_id", "design_strings.inverter", "design_strings.module",
    "drawings_preview.sheet", "explain_finding.subject", "layout_blocks.block", "layout_place.module", "network_add_group.id", "network_add_group.tag",
    "network_add_group.title", "network_add_group.within", "network_add_node.group", "network_add_node.id", "network_add_node.ports", "network_add_node.spec",
    "network_add_node.symbol", "network_add_node.tag", "network_add_node.title", "network_autobuild.battery", "network_autobuild.inverter", "network_autobuild.module",
    "network_autobuild.pcs", "network_connect.cable", "network_connect.dst", "network_connect.edge_id", "network_connect.remarks", "network_connect.src", "network_remove.ids",
    "network_update.id", "project_create.address", "project_create.client", "project_create.client_code", "project_create.name", "project_create.site", "project_create.state",
    "project_create.utility", "project_diff.from_revision", "project_diff.to_revision", "project_issue.approved", "project_issue.checked", "project_issue.date", "project_issue.drawn", "project_issue.remarks", "project_issue.rev", "project_snapshot.label", "site_import.file_path", "site_import.source_crs",
    "site_remove_feature.ids", "site_set_feature.id", "site_set_feature.name",
}  # fmt: skip


def schemas() -> dict[str, dict]:
    async def go():
        server = build_server(Config(workspace=Path(tempfile.mkdtemp())))
        return {t.name: t.input_schema for t in await server.list_tools()}

    return asyncio.run(go())


SCHEMAS = schemas()


def resolve(prop: dict, defs: dict) -> tuple[set[str] | None, bool]:
    """(allowed values, or None when unconstrained; whether the value is a string)."""
    if "$ref" in prop:
        return resolve(defs[prop["$ref"].rsplit("/", 1)[1]], defs)
    if "enum" in prop:
        return {v for v in prop["enum"] if v is not None}, True
    if "anyOf" in prop:
        parts = [resolve(o, defs) for o in prop["anyOf"] if o.get("type") != "null"]
        allowed = [a for a, _ in parts]
        return (None if any(a is None for a in allowed) else set().union(*allowed)), any(s for _, s in parts)
    if prop.get("type") == "array":
        return resolve(prop.get("items", {}), defs)
    return None, prop.get("type") == "string"


def lookup(tool: str, param: str) -> tuple[dict, dict]:
    sch = SCHEMAS[tool]
    return sch["properties"][param], sch.get("$defs", {})


@pytest.mark.parametrize("tool, param, expected", [pytest.param(*key, expected, id=".".join(key)) for key, expected in ENUMS.items()])
def test_enum_valued_parameter_declares_exactly_the_values_the_tool_accepts(tool, param, expected):
    prop, defs = lookup(tool, param)
    allowed, _ = resolve(prop, defs)
    assert allowed == expected, f"{tool}.{param} is not an enum of {sorted(expected)}"


LISTED = [k for k in ENUMS if len(ENUMS[k]) <= 12 or k == ("network_add_node", "kind")]


@pytest.mark.parametrize("tool, param", LISTED)
def test_description_lists_the_allowed_values(tool, param):
    prop, defs = lookup(tool, param)
    allowed, _ = resolve(prop, defs)
    text = prop.get("description", "")
    missing = sorted(v for v in allowed if v not in text)
    assert not missing, f"{tool}.{param} description omits {missing}"


def test_network_update_fields_declare_the_enum_valued_fields_and_units():
    prop, _ = lookup("network_update", "fields")
    hints = prop["properties"]
    for field, expected in NETWORK_UPDATE_FIELDS.items():
        assert {v for v in hints[field]["enum"] if v is not None} == expected
    assert "metres" in hints["length_m"]["description"] and "volts" in hints["voltage_v"]["description"]
    assert prop["additionalProperties"] is True


def test_every_other_string_parameter_is_declared_free_text():
    found, undeclared = set(), []
    for tool, sch in SCHEMAS.items():
        for param, prop in sch["properties"].items():
            allowed, is_string = resolve(prop, sch.get("$defs", {}))
            if allowed is None and is_string and param not in ("project_id", "revision"):
                found.add(f"{tool}.{param}")
                if (tool, param) not in ENUMS and f"{tool}.{param}" not in FREE_TEXT:
                    undeclared.append(f"{tool}.{param}")
    assert not undeclared, f"string parameters that are neither enums nor declared free text: {undeclared}"
    assert not (FREE_TEXT - found), f"declared free text that is no longer a plain string: {sorted(FREE_TEXT - found)}"


def test_shared_schema_aliases_match_the_code_that_validates_them():
    assert set(typing.get_args(schema.CatalogKind)) == set(KINDS)
    assert set(typing.get_args(schema.DrawingKind)) == DRAWING_KINDS
    assert set(typing.get_args(schema.RuleFamily)) == set(families())
    assert set(typing.get_args(schema.SiteFormatName)) == set(FORMATS)
    assert set(typing.get_args(schema.DrawingUnit)) == set(UNIT_NAMES)
    assert set(typing.get_args(schema.RegionMode)) == {"auto", "ground", "roofs"}


UNIT_PATTERNS = {
    "_m": r"\bm\b|metre|meter", "_mm": r"\bmm\b", "_kw": r"kW", "_kwp": r"kWp", "_kwh": r"kWh", "_kva": r"kVA", "_v": r"\bV\b|volt", "_ka": r"kA",
    "_deg": r"deg|°", "_pct": r"%", "_s": r"\bs\b|second", "_us": r"µs|\bus\b|microsecond", "_ohm": r"ohm", "_kv": r"kV", "_c": r"°C",
}  # fmt: skip
def unit_cases():
    for tool, sch in SCHEMAS.items():
        for param, prop in sch["properties"].items():
            numeric = "number" in str(prop) or "integer" in str(prop)
            suffix = next((s for s in sorted(UNIT_PATTERNS, key=len, reverse=True) if param.endswith(s)), None)
            if numeric and suffix:
                yield pytest.param(tool, param, UNIT_PATTERNS[suffix], id=f"{tool}.{param}")


@pytest.mark.parametrize("tool, param, pattern", list(unit_cases()))
def test_unit_suffixed_number_names_its_unit_in_the_description(tool, param, pattern):
    prop, _ = lookup(tool, param)
    text = prop.get("description", "") + " " + " ".join(o.get("description", "") for o in prop.get("anyOf", []))
    assert re.search(pattern, text), f"{tool}.{param}: {text!r} does not give the unit"
