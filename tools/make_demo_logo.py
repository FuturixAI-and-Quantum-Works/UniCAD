import math
from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parent.parent / "profiles" / "demo-logo.dxf"


def main() -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 10)
    msp.add_circle((0, 0), 5)
    for i in range(12):
        a = math.radians(i * 30)
        msp.add_line((6.5 * math.cos(a), 6.5 * math.sin(a)), (8.5 * math.cos(a), 8.5 * math.sin(a)))
    doc.saveas(OUT)


if __name__ == "__main__":
    main()
