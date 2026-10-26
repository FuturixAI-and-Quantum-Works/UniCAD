from unicad.catalog.store import Catalog
from unicad.domain.network import EARTH_PORT, SIGNAL_PORTS, NodeKind, Service
from unicad.domain.project import Project

_DC_KINDS = frozenset(
    {NodeKind.PV_STRING, NodeKind.PV_ARRAY_BLOCK, NodeKind.SCB, NodeKind.DC_FUSE_BOX, NodeKind.DC_ISOLATOR, NodeKind.DC_SPD, NodeKind.BATTERY}
)
_LV_KINDS = frozenset({NodeKind.LT_PANEL, NodeKind.ACDB})
_MV_KINDS = frozenset({NodeKind.VCB, NodeKind.RMU, NodeKind.HT_PANEL})
LV_MAX_V = 1000.0
MV_MAX_V = 33000.0


def service_for_voltage(voltage_v: float) -> Service:
    """Conventional bands: up to 1 kV LV, up to 33 kV MV, above that HV."""
    if voltage_v <= LV_MAX_V:
        return Service.LV_AC
    return Service.MV_AC if voltage_v <= MV_MAX_V else Service.HV_AC


def infer_service(project: Project, catalog: Catalog, src: tuple[str, str], dst: tuple[str, str]) -> Service | None:
    """The cable service of a connection when the ends make it unambiguous, else None."""
    net = project.network
    ends = [(net.node_by_id[n], p) for n, p in (src, dst)]
    ports = {p for _, p in ends}
    if EARTH_PORT in ports:
        return Service.EARTH
    if ports & SIGNAL_PORTS:
        return Service.CONTROL
    if "dc" in ports or any(n.kind in _DC_KINDS for n, _ in ends):
        return Service.DC
    for node, port in ends:
        if node.kind in (NodeKind.INVERTER, NodeKind.PCS) and port == "ac":
            return Service.LV_AC
        if node.kind in (NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER):
            if port == "lv":
                return Service.LV_AC
            if node.spec:
                return service_for_voltage(catalog.get(node.spec).hv_kv * 1000)
    for node, _ in ends:
        if node.voltage_v is not None:
            return service_for_voltage(node.voltage_v)
        if node.kind is NodeKind.POI_GRID:
            return service_for_voltage(project.design_basis.grid.voltage_v)
        if node.kind in _LV_KINDS:
            return Service.LV_AC
        if node.kind in _MV_KINDS:
            return Service.MV_AC
    return None
