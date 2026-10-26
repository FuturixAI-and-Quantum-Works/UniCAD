"""Move a finished drawing onto the company's layer standard."""

from ezdxf.document import Drawing

from unicad.drafting.sheets.model import CompanyProfile


def apply_layer_map(doc: Drawing, profile: CompanyProfile) -> list[str]:
    """Rename UniCAD layers to the company layers in `profile.layer_map`, on every entity of modelspace and blocks.

    A mapped layer that nothing uses is not created. Returns warnings for layers whose company linetype the document lacks."""
    mapping = {src: spec for src, spec in profile.layer_map.items() if src in doc.layers}
    if not mapping:
        return []
    warnings: list[str] = []
    for spec in mapping.values():
        if spec.name in doc.layers:
            continue
        linetype = spec.linetype if spec.linetype in doc.linetypes else "CONTINUOUS"
        if linetype != spec.linetype:
            warnings.append(f"layer {spec.name!r}: linetype {spec.linetype!r} is not in the drawing, CONTINUOUS used")
        doc.layers.add(spec.name, color=spec.colour, linetype=linetype, lineweight=spec.lineweight)
    spaces = [doc.modelspace(), *(b for b in doc.blocks if not b.name.startswith("*"))]
    for space in spaces:
        for e in space:
            spec = mapping.get(e.dxf.get("layer", "0"))
            if spec is not None:
                e.dxf.layer = spec.name
    keep = {spec.name for spec in mapping.values()}
    for src in mapping:
        if src not in keep:
            doc.layers.remove(src)
    return warnings
