"""Cable identification in the positional style of Indian EPC schedules: block, inverter, combiner, structure row, structure, input."""

from functools import lru_cache

from unicad.arrays.geometry import frame
from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.domain.project import Project

COMBINERS = frozenset({NodeKind.SCB, NodeKind.DC_FUSE_BOX})
GENERATORS = frozenset({NodeKind.INVERTER, NodeKind.PCS})

SCHEME = (
    "String cable id: B<block>-I<inverter>-S<combiner>-R<structure row>-<structure no.>-IP<combiner input>-<size> sqmm; "
    "block, inverter, combiner and input are the network instance indices (block 01 where the network has no block level), "
    "structure row is the rank of the table's row across the block, structure no. the table's position in the block."
)


def indices(instance_id: str) -> tuple[int, ...]:
    suffix = instance_id.partition("@")[2]
    return tuple(int(i) for i in suffix.split(".")) if suffix else ()


class Ids:
    def __init__(self, project: Project, expanded: ElectricalNetwork):
        self.net = expanded
        self._succ: dict[str, list[str]] = {n.id: [] for n in expanded.nodes}
        for e in expanded.edges:
            if e.is_power:
                self._succ[e.src_node].append(e.dst_node)
        self._roles = lru_cache(maxsize=None)(self._roles_uncached)
        self.table_position: dict[str, tuple[int, int]] = {}
        layout = project.array_layout
        self._first_table: dict[str, str] = {}
        if layout is not None:
            for b in layout.blocks:
                if not b.tables:
                    continue
                (_, _), (vx, vy) = frame(b.tables[0].azimuth_deg)
                rows = sorted({round(t.origin.x * vx + t.origin.y * vy, 2) for t in b.tables})
                for k, t in enumerate(b.tables, start=1):
                    rank = rows.index(round(t.origin.x * vx + t.origin.y * vy, 2)) + 1
                    self.table_position[t.id] = (rank, k)
            self._first_table = {a.string: a.spans[0].table for a in layout.strings}

    def _roles_uncached(self, node_id: str) -> tuple[str | None, str | None]:
        """(nearest combiner, generator) on the way downstream from `node_id`."""
        kind = self.net.node(node_id).kind
        if kind in GENERATORS:
            return None, node_id
        for s in self._succ[node_id]:
            combiner, generator = self._roles(s)
            if generator is not None:
                return (node_id if kind in COMBINERS else combiner), generator
        return None, None

    def block_of(self, node_id: str) -> str:
        """'B01' style block label of the inverter downstream of `node_id`, or '' when there is none."""
        _, gen = self._roles(node_id)
        if gen is None:
            return ""
        path = indices(gen)
        return f"B{path[0] if len(path) >= 2 else 1:02d}"

    def string_stem(self, string_id: str) -> str:
        combiner, gen = self._roles(string_id)
        gpath = indices(gen) if gen else ()
        block = gpath[0] if len(gpath) >= 2 else 1
        inverter = gpath[-1] if gpath else 1
        cpath = indices(combiner) if combiner else ()
        smb = cpath[len(gpath)] if len(cpath) > len(gpath) else 1
        spath = indices(string_id)
        ip = spath[-1] if spath else 1
        parts = [f"B{block:02d}", f"I{inverter}", f"S{smb:02d}"]
        table = self._first_table.get(string_id)
        if table in self.table_position:
            rank, structure = self.table_position[table]
            parts += [f"R{rank:03d}", f"{structure:04d}"]
        parts.append(f"IP{ip:02d}")
        return "-".join(parts)
