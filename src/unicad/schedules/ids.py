"""Cable identification in the positional style of Indian EPC schedules: block, inverter, combiner, structure row, structure, input."""

from dataclasses import dataclass
from functools import lru_cache

from unicad.arrays.geometry import frame
from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.domain.project import Project
from unicad.rules.topology import instance_tag

COMBINERS = frozenset({NodeKind.SCB, NodeKind.DC_FUSE_BOX})
GENERATORS = frozenset({NodeKind.INVERTER, NodeKind.PCS})

SCHEME_FIELDS = "inverter tag, combiner tag, structure row, structure no., input no., cable size"
SCHEME_EXAMPLE = "INV-01-SCB-02-R003-0027-IP05-4 sqmm"


def scheme_note(example: str = SCHEME_EXAMPLE) -> str:
    """How a string cable id reads, shown on `example` (the project's own first string cable id where it has one)."""
    return (
        f"String cable id {example} reads {SCHEME_FIELDS}. "
        "Inverter and combiner are the network tags of the equipment fed (no combiner where the string goes straight to the inverter); "
        "R and structure no. are the table's row rank and position in the block; IP is the input at the combiner or MPPT."
    )


SCHEME = scheme_note()


@dataclass(frozen=True)
class StringId:
    inverter: str
    combiner: str
    row: int | None
    structure: int | None
    input_no: int

    @property
    def stem(self) -> str:
        """Tags of the equipment the string feeds, '-' joined: the part every string of one combiner shares."""
        return "-".join(x for x in (self.inverter, self.combiner) if x)

    def text(self, size_mm2: float | None = None) -> str:
        parts = [self.stem]
        if self.row is not None and self.structure is not None:
            parts += [f"R{self.row:03d}", f"{self.structure:04d}"]
        parts.append(f"IP{self.input_no:02d}")
        out = "-".join(parts)
        return out if size_mm2 is None else f"{out}-{size_mm2:g} sqmm"


def indices(instance_id: str) -> tuple[int, ...]:
    suffix = instance_id.partition("@")[2]
    return tuple(int(i) for i in suffix.split(".")) if suffix else ()


class Ids:
    def __init__(self, project: Project, expanded: ElectricalNetwork):
        self.net = expanded
        self.project = project
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
        """Tag of the block (a top-level group holding a transformer, e.g. 'BLOCK-06') of the inverter downstream of `node_id`, or ''
        when there is none."""
        _, gen = self._roles(node_id)
        if gen is None:
            return ""
        net = self.project.network
        base, _, suffix = gen.partition("@")
        gid = net.node(base).group
        if gid is None:
            return ""
        while (g := net.group_by_id[gid]).within is not None:
            gid = g.within
        if not any(n.group == g.id and n.kind is NodeKind.TRANSFORMER for n in net.nodes):
            return ""
        path = tuple(int(i) for i in suffix.split(".")) if suffix else ()
        return g.tag.format(*path[: int(g.count > 1)])

    def string_id(self, string_id: str) -> StringId:
        combiner, gen = self._roles(string_id)
        spath = indices(string_id)
        row = structure = None
        table = self._first_table.get(string_id)
        if table in self.table_position:
            row, structure = self.table_position[table]
        return StringId(
            inverter=instance_tag(self.project.network, gen) if gen else instance_tag(self.project.network, string_id),
            combiner=instance_tag(self.project.network, combiner) if combiner else "",
            row=row, structure=structure, input_no=spath[-1] if spath else 1,
        )  # fmt: skip
