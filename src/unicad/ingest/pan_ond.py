"""Read and write PVsyst .PAN (module) and .OND (inverter) text files as ModuleSpec / InverterSpec.

PVsyst publishes no format specification. The structure handled here (2-space indented ``key=value`` lines, blocks that open
with ``Key=TypeName`` and close with ``End of ...``) is taken from real files; units are those PVsyst stores:
PNom W, muISC mA/degC, muVocSpec mV/degC, muPmpReq %/degC, Width/Height m, and in .OND PNomConv/PMaxOUT kW.
A missing or unmappable field is reported through IncompleteSpec, never guessed.
"""

import re
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path

from unicad.domain.catalog import CellTech, InverterSpec, ModuleSpec, Source

_TECH = {
    "mtSiPoly": CellTech.POLY,
    "mtSiMono": None,
    "mtCdTe": CellTech.THIN_FILM,
    "mtCIS": CellTech.THIN_FILM,
    "mtAmorphous": CellTech.THIN_FILM,
    "mtSiAmorph": CellTech.THIN_FILM,
    "mtuCSi": CellTech.THIN_FILM,
}
_TECH_OUT = {
    CellTech.POLY: "mtSiPoly",
    CellTech.THIN_FILM: "mtCdTe",
}


class IncompleteSpec(ValueError):
    def __init__(self, missing: list[str], partial: dict):
        super().__init__(f"missing or unmappable fields: {', '.join(missing)}")
        self.missing = missing
        self.partial = partial


@dataclass
class PvsystNode:
    type: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    children: dict[str, "PvsystNode"] = field(default_factory=dict)

    def text(self, key: str) -> str | None:
        return self.fields.get(key)

    def number(self, key: str) -> float | None:
        raw = self.fields.get(key)
        if raw is None:
            return None
        try:
            return float(raw.split(",")[0])
        except ValueError:
            return None

    def numbers(self, key: str) -> list[float]:
        return [float(p) for p in self.fields.get(key, "").split(",") if re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", p)]


@dataclass
class PvsystFile:
    """root is the PVObject_ block; kind is its type (pvModule for .PAN, pvGInverter for .OND)."""

    root: PvsystNode

    @property
    def kind(self) -> str | None:
        return self.root.type

    @property
    def version(self) -> str | None:
        return self.root.text("Version")

    @property
    def commercial(self) -> PvsystNode:
        return self.root.children.get("PVObject_Commercial", PvsystNode())


def parse_pvsyst(data: str | bytes | Path) -> PvsystFile:
    """Parse the text of a .PAN/.OND file, its bytes (BOM and latin-1 tolerated) or a path to one."""
    if isinstance(data, Path):
        data = data.read_bytes()
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            data = data.decode("latin-1")
    data = data.lstrip("\ufeff")

    lines = [ln.rstrip() for ln in data.splitlines() if ln.strip()]
    root = PvsystNode()
    stack: list[tuple[int, PvsystNode]] = [(-1, root)]
    for i, line in enumerate(lines):
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if body.startswith("End of "):
            if len(stack) > 1:
                stack.pop()
            continue
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        node = stack[-1][1]
        key, sep, value = body.partition("=")
        key, value = key.strip(), value.strip()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        deeper = (len(nxt) - len(nxt.lstrip(" "))) > indent
        if deeper:
            child = PvsystNode(type=value or None)
            node.children[key] = child
            stack.append((indent, child))
        elif sep:
            node.fields[key] = value
        else:
            node.fields.setdefault(key, "")
    if "PVObject_" not in root.children:
        raise ValueError("not a PVsyst component file: no PVObject_ block")
    return PvsystFile(root.children["PVObject_"])


def _slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9._]+", "-", "-".join(parts).lower()).strip("-")


def _source(f: PvsystFile, name: str | None) -> Source:
    c = f.commercial
    bits = [name or "PVsyst component file", f"PVsyst file version {f.version}" if f.version else None, c.text("DataSource")]
    return Source(kind="pan_ond", ref="; ".join(b for b in bits if b))


def _manufacturer_model(f: PvsystFile) -> tuple[str | None, str | None]:
    c = f.commercial
    return c.text("Manufacturer"), c.text("Model")


def module_from_pan(
    pan: PvsystFile | str | bytes | Path,
    *,
    tech: CellTech | None = None,
    id: str | None = None,
    source_name: str | None = None,
) -> ModuleSpec:
    """tech is required for mtSiMono files, which do not say PERC, TOPCon, HJT or IBC."""
    f = pan if isinstance(pan, PvsystFile) else parse_pvsyst(pan)
    if f.kind != "pvModule":
        raise ValueError(f"expected a pvModule (.PAN) file, got {f.kind}")
    r = f.root
    manufacturer, model = _manufacturer_model(f)
    isc, voc = r.number("Isc"), r.number("Voc")
    mu_isc, mu_voc = r.number("muISC"), r.number("muVocSpec")
    height, width = f.commercial.number("Height"), f.commercial.number("Width")
    cells_s, cells_p = r.number("NCelS"), r.number("NCelP") or 1
    technol = r.text("Technol")
    resolved_tech = tech or _TECH.get(technol or "")
    bifaciality = r.number("BifacialityFactor") or 0.0
    fields = dict(
        manufacturer=manufacturer, model=model, pmax=r.number("PNom"), voc=voc, isc=isc, vmp=r.number("Vmp"), imp=r.number("Imp"),
        temp_coeff_isc=mu_isc / (10 * isc) if mu_isc is not None and isc else None,
        temp_coeff_voc=mu_voc / (10 * voc) if mu_voc is not None and voc else None,
        temp_coeff_pmax=r.number("muPmpReq"),
        length=max(height, width) * 1000 if height and width else None,
        width=min(height, width) * 1000 if height and width else None,
        cells=round(cells_s * cells_p) if cells_s else None,
        tech=resolved_tech,
    )
    if gaps := [k for k, v in fields.items() if v is None]:
        raise IncompleteSpec(gaps, fields)
    max_v = r.number("VMaxIEC") or r.number("VMaxUL")
    return ModuleSpec(
        id=id or _slug(manufacturer, model), source=_source(f, source_name), bifacial=bifaciality > 0,
        bifaciality=bifaciality if bifaciality > 0 else None, **({"max_system_voltage": max_v} if max_v else {}), **fields,
    )


def inverter_from_ond(
    ond: PvsystFile | str | bytes | Path,
    *,
    topology: str | None = None,
    id: str | None = None,
    source_name: str | None = None,
) -> InverterSpec:
    """topology is required: an .OND file does not say string, central, micro or hybrid.

    ac_rated_kva is PNomConv (kW). ac_max_kva is sqrt(3) x VOutConv x IMaxAC when the file has IMaxAC, else PMaxOUT (kW).
    IMaxDC is the whole-inverter DC current and is stored as max_dc_input_current.
    """
    f = ond if isinstance(ond, PvsystFile) else parse_pvsyst(ond)
    if f.kind != "pvGInverter":
        raise ValueError(f"expected a pvGInverter (.OND) file, got {f.kind}")
    conv = f.root.children.get("Converter", PvsystNode())
    manufacturer, model = _manufacturer_model(f)
    v_out, i_max_ac = conv.number("VOutConv"), conv.number("IMaxAC")
    p_nom, p_max = conv.number("PNomConv"), conv.number("PMaxOUT")
    phase_mode = conv.text("MonoTri")
    ac_max = sqrt(3) * v_out * i_max_ac / 1000 if v_out and i_max_ac and phase_mode == "Tri" else p_max
    fields = dict(
        manufacturer=manufacturer, model=model, topology=topology, ac_rated_kva=p_nom, ac_max_kva=ac_max, ac_voltage=v_out,
        phases={"Tri": 3, "Mono": 1}.get(phase_mode or ""), dc_max_voltage=conv.number("VAbsMax"), mppt_min=conv.number("VMppMin"),
        mppt_max=conv.number("VMPPMax"), mppt_count=round(n) if (n := f.root.number("NbMPPT")) else None,
    )
    if gaps := [k for k, v in fields.items() if v is None]:
        raise IncompleteSpec(gaps, fields)
    if fields["ac_max_kva"] < fields["ac_rated_kva"]:
        fields["ac_max_kva"] = fields["ac_rated_kva"]
    eff = conv.number("EfficMax")
    inputs = f.root.number("NbInputs")
    return InverterSpec(
        id=id or _slug(manufacturer, model), source=_source(f, source_name),
        dc_input_count=round(inputs) if inputs else None,
        max_dc_input_current=conv.number("IMaxDC") or None, max_efficiency=eff / 100 if eff else None, **fields,
    )


_PAN_TEMPLATE = """PVObject_=pvModule
  Version=7.2
  Flags=$00900243

  PVObject_Commercial=pvCommercial
    Manufacturer={manufacturer}
    Model={model}
    DataSource=UniCAD catalog export
    Width={width:.4f}
    Height={height:.4f}
  End of PVObject pvCommercial

  Technol={technol}
  NCelS={cells_s}
  NCelP={cells_p}
  GRef=1000
  TRef=25.0
  PNom={pmax:.3f}
  BifacialityFactor={bifaciality:.3f}
  Isc={isc:.4f}
  Voc={voc:.4f}
  Imp={imp:.4f}
  Vmp={vmp:.4f}
  muISC={mu_isc:.4f}
  muVocSpec={mu_voc:.4f}
  muPmpReq={gamma:.4f}
  VMaxIEC={vmax:.1f}
End of PVObject pvModule
"""


def module_to_pan(spec: ModuleSpec) -> str:
    """Write the fields ModuleSpec holds in PVsyst layout. The one-diode model parameters are absent, so PVsyst cannot simulate from it.

    Mono technologies are all written as mtSiMono, so pass tech= to module_from_pan to read one back.
    """
    technol = _TECH_OUT.get(spec.tech, "mtSiMono")
    return _PAN_TEMPLATE.format(
        manufacturer=spec.manufacturer, model=spec.model, width=spec.width / 1000, height=spec.length / 1000, technol=technol,
        cells_s=spec.cells // 2 if spec.cells % 2 == 0 else spec.cells, cells_p=2 if spec.cells % 2 == 0 else 1, pmax=spec.pmax, bifaciality=spec.bifaciality or 0.0, isc=spec.isc, voc=spec.voc, imp=spec.imp, vmp=spec.vmp,
        mu_isc=spec.temp_coeff_isc * 10 * spec.isc, mu_voc=spec.temp_coeff_voc * 10 * spec.voc, gamma=spec.temp_coeff_pmax,
        vmax=spec.max_system_voltage,
    )


_OND_TEMPLATE = """PVObject_=pvGInverter
  Version=7.2
  Flags=$00381562

  PVObject_Commercial=pvCommercial
    Manufacturer={manufacturer}
    Model={model}
    DataSource=UniCAD catalog export
  End of PVObject pvCommercial
  Transfo=Without

  Converter=TConverter
    PNomConv={p_nom:.4f}
    PMaxOUT={p_max:.4f}
    VOutConv={v_out:.2f}
    VMppMin={vmpp_min:.1f}
    VMPPMax={vmpp_max:.1f}
    VAbsMax={vabs:.1f}
    EfficMax={eff:.4f}
    MonoTri={mono_tri}
    IMaxDC={imax_dc:.2f}
    IMaxAC={imax_ac:.4f}
  End of TConverter
  NbInputs={inputs}
  NbMPPT={mppt}
End of PVObject pvGInverter
"""


def inverter_to_ond(spec: InverterSpec) -> str:
    """Write the converter fields InverterSpec holds. Efficiency curves are absent, so PVsyst cannot simulate from it.

    IMaxDC is written only if the spec has a total DC current; IMaxAC is derived from ac_max_kva and ac_voltage.
    """
    total = spec.max_dc_input_current or (spec.max_input_current_per_mppt or 0) * spec.mppt_count
    factor = sqrt(3) if spec.phases == 3 else 1
    text = _OND_TEMPLATE.format(
        manufacturer=spec.manufacturer, model=spec.model, p_nom=spec.ac_rated_kva, p_max=spec.ac_max_kva, v_out=spec.ac_voltage,
        vmpp_min=spec.mppt_min, vmpp_max=spec.mppt_max, vabs=spec.dc_max_voltage, eff=(spec.max_efficiency or 0) * 100,
        mono_tri="Tri" if spec.phases == 3 else "Mono", imax_dc=total, imax_ac=spec.ac_max_kva * 1000 / (factor * spec.ac_voltage),
        inputs=spec.dc_input_count or 0, mppt=spec.mppt_count,
    )
    return "\n".join(ln for ln in text.splitlines() if not (ln.strip().startswith(("IMaxDC=0.0", "EfficMax=0.00")) or ln.strip() == "NbInputs=0")) + "\n"
