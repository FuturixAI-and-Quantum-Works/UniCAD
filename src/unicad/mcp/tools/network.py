import difflib
from collections import Counter
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.domain.network import SPEC_KINDS, AttrValue, Edge, Group, Node, Service
from unicad.domain.project import Project
from unicad.mcp.advisories import advisories
from unicad.mcp.errors import guarded
from unicad.mcp.inference import infer_service
from unicad.mcp.schema import LayingArg, NodeKindArg, field_hints, values
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Edit, Workspace

READ_ONLY = ToolAnnotations(readOnlyHint=True)
LIST_CAP = 40


def repeat_depth(project: Project, group_id: str | None, own_count: int) -> int:
    """Number of tag placeholders an item needs: one per repeating group around it, plus its own count."""
    depth, seen = int(own_count > 1), set()
    while group_id is not None and group_id not in seen and (g := project.network.group_by_id.get(group_id)):
        seen.add(group_id)
        depth += g.count > 1
        group_id = g.within
    return depth


def default_tag(item_id: str, depth: int) -> str:
    return item_id.upper().replace("_", "-") + "".join(f"-{{{i}:02d}}" for i in range(depth))


def parse_terminal(project: Project, role: str, terminal: str) -> tuple[str, str]:
    node_id, sep, port = terminal.rpartition(":")
    if not sep or not node_id or not port:
        raise ValueError(f"{role} {terminal!r} must look like 'node_id:port', e.g. 'inv:ac'")
    if node_id not in project.network.node_by_id:
        near = difflib.get_close_matches(node_id, list(project.network.node_by_id), n=3)
        hint = f"; did you mean {near}" if near else f"; existing nodes: {list(project.network.node_by_id)[:15]}"
        raise ValueError(f"{role} {terminal!r} refers to unknown node {node_id!r}{hint}")
    return node_id, port


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def network_add_group(
        project_id: ProjectIdArg,
        id: Annotated[str, Field(description="Unique id (letters, digits, '_', '-', '.'), e.g. 'inv_sec'")],
        count: Annotated[int, Field(ge=1, description="How many identical copies of everything in this group")] = 1,
        tag: Annotated[str | None, Field(description="Drawing tag; needs one {0:02d}-style placeholder per repeating group around it, including this one when count > 1. Default is derived from id.")] = None,
        within: Annotated[str | None, Field(description="Id of the enclosing group")] = None,
        title: Annotated[str | None, Field(description="Box title on the SLD, e.g. '100 kW STRING INVERTER SECTION'")] = None,
    ) -> dict[str, Any]:
        """Add a repeated block of equipment (e.g. 5 identical inverter sections). Model identical equipment once and give the group a count.

        Example: network_add_group(project_id="p1", id="inv_sec", count=5, title="100 kW INVERTER SECTION")."""

        def change(p: Project) -> Edit:
            depth = repeat_depth(p, within, count)
            if within is not None and within not in p.network.group_by_id:
                raise ValueError(f"group {id!r} is within unknown group {within!r}; groups: {list(p.network.group_by_id)}")
            if depth == 0 and tag is None:
                use_tag = id.upper().replace("_", "-")
            else:
                use_tag = tag or default_tag(id, depth)
            new = Group(id=id, tag=use_tag, count=count, within=within, title=title)
            existing = p.network.group_by_id.get(id)
            if existing is not None:
                if existing == new:
                    return Edit(p, (id,))
                raise ValueError(f"group {id!r} already exists with different content; change it with network_update")
            return Edit(ws.rebuild(p, groups=[*p.network.groups, new]), (id,), {"added": [id]})

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def network_add_node(
        project_id: ProjectIdArg,
        id: Annotated[str, Field(description="Unique id for this equipment template, e.g. 'inv'")],
        kind: NodeKindArg,
        spec: Annotated[str | None, Field(description="Catalog id from catalog_search (module, inverter, breaker, ...)")] = None,
        count: Annotated[int, Field(ge=1, description="Instances per instance of its group, e.g. 10 strings per inverter")] = 1,
        group: Annotated[str | None, Field(description="Id of the group it repeats with")] = None,
        tag: Annotated[str | None, Field(description="Drawing tag with {0:02d}-style placeholders (see network_add_group); default derived from id")] = None,
        voltage_v: Annotated[float | None, Field(gt=0, description="Nominal voltage in volts of the circuit it sits on, e.g. 415")] = None,
        modules_in_series: Annotated[int | None, Field(gt=0, description="Modules per string; pv_string only")] = None,
        ports: Annotated[list[str] | None, Field(description="Override port names; defaults depend on kind (inverter: dc, ac)")] = None,
        attributes: Annotated[dict[str, AttrValue] | None, Field(description="Free label/value pairs shown on drawings, e.g. {\"rating_a\": 250}")] = None,
        title: Annotated[str | None, Field(description="Box title on the SLD")] = None,
        symbol: Annotated[str | None, Field(description="Override the drawing symbol key")] = None,
    ) -> dict[str, Any]:
        """Add an equipment node. Ports are named per kind: pv_string out; inverter dc, ac; transformer lv, hv; most others in, out.

        Example: network_add_node(project_id="p1", id="inv", kind="inverter", spec="growatt-max-100ktl3-x-lv", group="inv_sec")."""

        def change(p: Project) -> Edit:
            if group is not None and group not in p.network.group_by_id:
                raise ValueError(f"node {id!r} is in unknown group {group!r}; groups: {list(p.network.group_by_id)}")
            use_tag = tag or default_tag(id, repeat_depth(p, group, count))
            new = Node(
                id=id, tag=use_tag, kind=kind, spec=spec, symbol=symbol, count=count, group=group, voltage_v=voltage_v,
                modules_in_series=modules_in_series, ports=tuple(ports) if ports is not None else None,
                attributes=attributes or {}, title=title,
            )  # fmt: skip
            existing = p.network.node_by_id.get(id)
            if existing is not None:
                if existing == new:
                    return Edit(p, (id,))
                raise ValueError(f"node {id!r} already exists with different content; change it with network_update")
            updated = ws.rebuild(p, nodes=[*p.network.nodes, new])
            return Edit(updated, (id,), {"added": [id], "ports": list(new.port_names)}, advisories(updated, [id]))

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def network_connect(
        project_id: ProjectIdArg,
        src: Annotated[str, Field(description="Source terminal 'node_id:port'; power flows from src to dst (generation towards grid)")],
        dst: Annotated[str, Field(description="Destination terminal 'node_id:port'")],
        service: Annotated[Service | None, Field(description=f"Circuit type ({values(Service)}); inferred from the ports and nodes when unambiguous, else required")] = None,
        cable: Annotated[str | None, Field(description="Cable catalog id from catalog_search(kind='cable')")] = None,
        length_m: Annotated[float | None, Field(gt=0, description="Route length in metres, per instance")] = None,
        runs: Annotated[int, Field(ge=1, description="Parallel cables per connection")] = 1,
        laying: LayingArg = None,
        derating: Annotated[float | None, Field(gt=0, le=1, description="Installation derating factor, e.g. 0.8")] = None,
        remarks: Annotated[str | None, Field(description="Free text shown in the cable schedule remarks")] = None,
        edge_id: Annotated[str | None, Field(description="Id for the connection; default 'e_<src>_<dst>'")] = None,
    ) -> dict[str, Any]:
        """Connect two nodes. Instances pair up by group: equal counts connect one to one, a count of 1 fans in or out.

        Example: network_connect(project_id="p1", src="inv:ac", dst="acdb:in", cable="polycab-...", length_m=25)."""

        def change(p: Project) -> Edit:
            (sn, sp), (dn, dp) = parse_terminal(p, "src", src), parse_terminal(p, "dst", dst)
            svc = service or infer_service(p, ws.catalog, (sn, sp), (dn, dp))
            if svc is None:
                raise ValueError(f"cannot infer the service for {src} -> {dst}; pass service (dc, lv_ac, mv_ac, hv_ac, earth, control)")
            new = Edge(
                id=edge_id or f"e_{sn}_{dn}", src=f"{sn}:{sp}", dst=f"{dn}:{dp}", service=svc, cable=cable, runs=runs,
                length_m=length_m, laying=laying, derating=derating, remarks=remarks,
            )  # fmt: skip
            extra = {"edge_id": new.id, "service": svc.value, "service_inferred": service is None}
            existing = p.network.edge_by_id.get(new.id)
            if existing is not None:
                if existing == new:
                    return Edit(p, (new.id,), extra)
                raise ValueError(f"connection {new.id!r} already exists with different content; change it with network_update or pass another edge_id")
            updated = ws.rebuild(p, edges=[*p.network.edges, new])
            return Edit(updated, (new.id,), extra | {"added": [new.id]}, advisories(updated, [new.id]))

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def network_update(
        project_id: ProjectIdArg,
        id: Annotated[str, Field(description="Id of an existing node, connection (edge) or group")],
        fields: Annotated[
            dict[str, Any],
            Field(
                description="Fields to change; null clears a field. For `attributes` the given keys are merged (null removes a key). Enum-valued fields: kind (node), service and laying (edge).",
                json_schema_extra={"properties": field_hints()},
            ),
        ],
    ) -> dict[str, Any]:
        """Change fields of an existing node, connection or group. The id itself cannot change.

        Node fields: tag, kind, spec, symbol, count, group, voltage_v (V), modules_in_series, ports, attributes, title.
        Edge fields: src, dst, service, cable, runs, length_m (m), laying, derating, remarks. Group fields: tag, count, within, title.
        Example: network_update(project_id="p1", id="e_inv_acdb", fields={"length_m": 30}).
        Changing a group count can invalidate member tags; update those tags in the same session."""

        def change(p: Project) -> Edit:
            net = p.network
            for model, items, key in ((Node, net.nodes, "nodes"), (Edge, net.edges, "edges"), (Group, net.groups, "groups")):
                current = next((i for i in items if i.id == id), None)
                if current is not None:
                    break
            else:
                raise ValueError(f"no node, connection or group {id!r}")
            allowed = set(model.model_fields) - {"id"}
            if unknown := sorted(set(fields) - allowed):
                raise ValueError(f"{model.__name__.lower()} {id!r} has no field(s) {unknown}; fields: {sorted(allowed)}")
            data = current.model_dump(mode="json")
            for k, v in fields.items():
                if k == "attributes" and isinstance(v, dict):
                    merged = {**data["attributes"], **v}
                    data["attributes"] = {a: b for a, b in merged.items() if b is not None}
                else:
                    data[k] = v
            new = model.model_validate(data)
            updated = ws.rebuild(p, **{key: [new if i.id == id else i for i in items]})
            return Edit(updated, (id,), {"updated": id, "fields": sorted(fields)}, advisories(updated, [id]))

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def network_remove(
        project_id: ProjectIdArg,
        ids: Annotated[list[str], Field(min_length=1, description="Ids of nodes, connections (edges) or groups to remove")],
        include_members: Annotated[bool, Field(description="When removing a group, also remove the nodes and groups inside it")] = False,
    ) -> dict[str, Any]:
        """Remove nodes, connections or groups. Removing a node also removes its connections; the reply lists everything removed.

        Example: network_remove(project_id="p1", ids=["scb"])."""

        def change(p: Project) -> Edit:
            net = p.network
            nodes, edges, groups = set(), set(), set()
            for i in ids:
                if i in net.node_by_id:
                    nodes.add(i)
                elif i in net.edge_by_id:
                    edges.add(i)
                elif i in net.group_by_id:
                    groups.add(i)
                else:
                    raise ValueError(f"no node, connection or group {i!r}")
            if groups:
                inside = set(groups)
                grew = True
                while grew:
                    grew = False
                    for g in net.groups:
                        if g.within in inside and g.id not in inside:
                            inside.add(g.id)
                            grew = True
                members_n = {n.id for n in net.nodes if n.group in inside}
                members_g = inside - groups
                if (members_n - nodes or members_g - groups) and not include_members:
                    raise ValueError(
                        f"group(s) {sorted(groups)} still contain nodes {sorted(members_n - nodes)} and groups {sorted(members_g)}; "
                        "remove those first or pass include_members=true"
                    )
                nodes |= members_n
                groups |= members_g
            cascaded = {e.id for e in net.edges if e.src_node in nodes or e.dst_node in nodes} - edges
            gone_edges = edges | cascaded
            updated = ws.rebuild(
                p,
                groups=[g for g in net.groups if g.id not in groups],
                nodes=[n for n in net.nodes if n.id not in nodes],
                edges=[e for e in net.edges if e.id not in gone_edges],
            )
            removed = {"nodes": sorted(nodes), "edges": sorted(edges), "groups": sorted(groups)}
            return Edit(updated, (), {"removed": removed, "cascaded_edges": sorted(cascaded)})

        return ws.mutate(project_id, change)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def network_summary(project_id: ProjectIdArg, revision: RevisionArg = None) -> dict[str, Any]:
        """Counts by node kind (templates and real instances), connections by service, voltage zones, unconnected ports and nodes missing a catalog spec.

        Example: network_summary(project_id="p1")."""
        p, rev = ws.load(project_id, revision)
        net = p.network
        kinds: dict[str, dict[str, int]] = {}
        for n in net.nodes:
            k = kinds.setdefault(n.kind.value, {"templates": 0, "instances": 0})
            k["templates"] += 1
            k["instances"] += net.instance_count(n.id)
        zones = [
            {"voltage_v": z.voltage_v, "nodes": len(z.node_ids), "edges": len(z.edge_ids), "sample_nodes": list(z.node_ids[:6])}
            for z in net.voltage_zones()
        ]
        open_ports = net.unconnected_ports()
        no_spec = [n.id for n in net.nodes if n.kind in SPEC_KINDS and n.spec is None]
        return {
            "project_id": project_id,
            "revision": rev,
            "groups": [{"id": g.id, "count": g.count, "within": g.within} for g in net.groups],
            "node_kinds": kinds,
            "total_instances": sum(v["instances"] for v in kinds.values()),
            "edges_by_service": dict(Counter(e.service.value for e in net.edges)),
            "voltage_zones": zones,
            "unconnected_ports": {"total": len(open_ports), "ports": list(open_ports[:LIST_CAP])},
            "nodes_without_spec": {"total": len(no_spec), "ids": no_spec[:LIST_CAP]},
        }
