"""Module temperature and string voltage calculations."""

from unicad.domain.catalog import ModuleSpec
from unicad.domain.project import DesignBasis, NoctThermal, PvsystThermal
from unicad.rules.model import Calc, Quantity
from unicad.rules.thresholds import STC_IRRADIANCE, STC_TEMP

VMP_COEFF = "Vmp temperature coefficient taken as the module's Pmax coefficient (catalog has no Vmp coefficient); this overstates the Vmp swing, which is conservative for both MPPT window checks."
COLD_CELL = "Coldest cell temperature taken as ambient_min_c (no irradiance heating), the worst case for open-circuit voltage."


def cell_temp_min(basis: DesignBasis) -> Calc:
    return Calc(
        value=basis.ambient_min_c,
        unit="degC",
        formula="Tcell_min = Tamb_min",
        inputs=(Quantity(name="Tamb_min", value=basis.ambient_min_c, unit="degC", source="design_basis.ambient_min_c"),),
        assumptions=(COLD_CELL,),
    )


def cell_temp_max(basis: DesignBasis, module: ModuleSpec) -> Calc | None:
    """Cell temperature at ambient_max_c and 1000 W/m2. None when the NOCT model has no NOCT value."""
    ta = Quantity(name="Tamb_max", value=basis.ambient_max_c, unit="degC", source="design_basis.ambient_max_c")
    g = STC_IRRADIANCE.value
    gq = Quantity(name="G", value=g, unit="W/m2", source=f"threshold.{STC_IRRADIANCE.name}")
    model = basis.cell_temp
    if isinstance(model, NoctThermal):
        noct, src = (model.noct_c, "design_basis.cell_temp.noct_c") if model.noct_c is not None else (module.noct_c, module.id)
        if noct is None:
            return None
        return Calc(
            value=basis.ambient_max_c + (noct - 20.0) / 800.0 * g,
            unit="degC",
            formula="Tcell_max = Tamb_max + (NOCT - 20) / 800 x G",
            inputs=(ta, Quantity(name="NOCT", value=noct, unit="degC", source=src), gq),
            assumptions=(STC_IRRADIANCE.assumption,),
        )
    assert isinstance(model, PvsystThermal)
    area = module.length * module.width / 1e6
    eta = module.pmax / (area * 1000.0)
    assumptions = [STC_IRRADIANCE.assumption, "Module efficiency eta = Pmax / (length x width x 1000 W/m2) from the catalog."]
    if model.uv > 0:
        assumptions.append("Wind speed taken as 0 m/s (no wind cooling), the worst case.")
    return Calc(
        value=basis.ambient_max_c + model.absorptance * g * (1 - eta) / model.uc,
        unit="degC",
        formula="Tcell_max = Tamb_max + alpha x G x (1 - eta) / (Uc + Uv x v), v = 0",
        inputs=(
            ta,
            gq,
            Quantity(name="alpha", value=model.absorptance, source="design_basis.cell_temp.absorptance"),
            Quantity(name="eta", value=round(eta, 5), source=module.id),
            Quantity(name="Uc", value=model.uc, unit="W/m2K", source="design_basis.cell_temp.uc"),
            Quantity(name="Uv", value=model.uv, unit="W/m2K/(m/s)", source="design_basis.cell_temp.uv"),
        ),
        assumptions=tuple(assumptions),
    )


def string_voltage(module: ModuleSpec, n: int, which: str, cell: Calc) -> Calc:
    """String voltage of `n` modules at a cell temperature: which is 'voc' or 'vmp'."""
    v, coeff, cname, extra = (
        (module.voc, module.temp_coeff_voc, "beta_Voc", ())
        if which == "voc"
        else (module.vmp, module.temp_coeff_pmax, "gamma_Pmax", (VMP_COEFF,))
    )
    sym = "Voc" if which == "voc" else "Vmp"
    t = cell.value
    return Calc(
        value=n * v * (1 + coeff / 100.0 * (t - STC_TEMP.value)),
        unit="V",
        formula=f"N x {sym} x (1 + {cname} / 100 x (Tcell - 25))",
        inputs=(
            Quantity(name="N", value=n, source="network.modules_in_series"),
            Quantity(name=sym, value=v, unit="V", source=module.id),
            Quantity(name=cname, value=coeff, unit="%/degC", source=module.id),
            Quantity(name="Tcell", value=round(t, 3), unit="degC", source=cell.formula),
            *cell.inputs,
        ),
        assumptions=(*cell.assumptions, *extra),
    )
