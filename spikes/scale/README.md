# Scale spike: can file-based DXF + LibreCAD handle utility-scale drawings?

Run on 2026-10-26 with ezdxf 1.4.5, Python 3.15, and LibreCAD 2.2.1.6 (official x86_64 AppImage, extracted).

```sh
uv venv && uv pip install ezdxf pillow
python stress.py sld 330 24 sld.dxf                  # 330 inverters x 24 strings
python stress.py layout 3215 nested layout_nested.dxf # ~180k modules as 3215 table blocks
python stress.py layout 3215 flat layout_flat.dxf     # ~180k individual module inserts
librecad dxf2png -r 4000x3000 -o sld.png sld.dxf
librecad dxf2pdf -a -p 841x594 -o sld.pdf sld.dxf
```

| Case | Modelspace entities | DXF size | ezdxf build + save | ezdxf read-back | LibreCAD dxf2png | LibreCAD dxf2pdf |
| --- | --- | --- | --- | --- | --- | --- |
| SLD, 330 inv / 7,920 strings | 25,113 | 5.2 MB | 1.1 s | 1.0 s | 0.4 s | 1.6 s |
| Layout, 180k modules nested in blocks | 3,215 | 0.4 MB | 0.1 s | 0.06 s | 1.7 s | 5.3 s |
| Layout, 180k flat inserts | 180,040 | 20 MB | 5.2 s | 3.9 s | 1.4 s | 5.0 s |

The PNGs were checked by eye (`sld_small.png`, `layout_flat_small.png`). Every inverter column and table row is drawn.

Findings:

- Size is not the limit. A 100 MWp site renders in a few seconds.
- A whole-site PNG can't be read at any practical resolution. LibreCAD's `dxf2png` has no viewport option, so the agent needs cropped region previews from a separate renderer, and SLDs must be split into sheets.
- Nesting modules inside table blocks shrinks the file 50x. The layout engine should place tables, not modules.
- LibreCAD's console exporters need a display (Xwayland worked here). A headless server or CI box needs `xvfb-run`.
