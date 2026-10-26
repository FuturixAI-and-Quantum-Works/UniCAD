from datetime import date

from unicad.drafting.sheets.model import (
    IssuedFor,
    PaperSize,
    Revision,
    SheetSpec,
    TitleBlockData,
)


def utility_100mwac(paper: PaperSize = PaperSize.A0) -> SheetSpec:
    tb = TitleBlockData(
        client="COAL INDIA LIMITED",
        project_title=(
            "SUPPLY OF PLANT AND EQUIPMENT INCLUDING TYPE TEST AND SPECIFIED SPARES FOR "
            "100 MWac SOLAR POWER PLANT TO BE SET UP IN GUJARAT"
        ),
        location="VILLAGE KHAVDA, DISTRICT KUTCH, GUJARAT",
        loa_no="CIL/EED/SOLAR/2021/20, Dated: 26-04-2021",
        drawing_title="AC SINGLE LINE DIAGRAM - 33 kV COLLECTION SYSTEM (PLANT 100 MWac)",
        drawing_number="UDEMO-CIL-202109-EE-004",
        revision="02",
        scale="NTS",
        sheet_no=4,
        sheet_count=18,
        paper=paper,
        drawn_by="AKS",
        checked_by="RVN",
        approved_by="MJD",
        drawn_date=date(2026, 9, 14),
        checked_date=date(2026, 9, 15),
        approved_date=date(2026, 9, 16),
        issued_for=IssuedFor.APPROVAL,
        consultant="GRIDLINE POWER CONSULTANTS PVT. LTD.",
    )
    return SheetSpec(
        paper=paper,
        title_block=tb,
        revisions=(
            Revision(rev="00", date=date(2026, 7, 6), description="FIRST SUBMISSION", by="AKS",
                     checked="RVN", approved="MJD"),
            Revision(rev="01", date=date(2026, 8, 12), description="REVISED AS PER OWNER COMMENTS ON IDT RATING AND ICOG PANEL",
                     by="AKS", checked="RVN", approved="MJD"),
            Revision(rev="02", date=date(2026, 9, 16), description="33 kV CABLE RUNS AND CT/PT DETAILS UPDATED",
                     by="AKS", checked="RVN", approved="MJD"),
        ),
        legend=(
            ("pv.module", "PV MODULE 540 Wp MONO PERC BIFACIAL"),
            ("dc.scb", "STRING MONITORING BOX 1500 Vdc, 11 IN 1 OUT"),
            ("inv.central", "POWER CONDITIONING UNIT, 3.125 MW (50 %%dC)"),
            ("tx.idt_4lv", "INVERTER DUTY TRANSFORMER 12.5 MVA, 33/0.66 kV, 4 LV WINDINGS"),
            ("sw.vcb", "36 kV, 630 A VACUUM CIRCUIT BREAKER, 25 kA FOR 1 SEC"),
            ("sw.isolator_es", "ISOLATOR WITH EARTH SWITCH"),
            ("tx.ct_3core", "CURRENT TRANSFORMER, 3 CORES (METERING, PROTECTION, PS)"),
            ("tx.pt", "POTENTIAL TRANSFORMER, 33 kV/SQRT3 / 110 V/SQRT3"),
            ("prot.lightning_arrester", "SURGE ARRESTER, 30 kV, 10 kA, TYPE 2"),
            ("inst.mfm", "MULTI-FUNCTION METER (kW, kVAr, kWh, kVArh)"),
            ("prot.relay", "PROTECTION RELAY WITH ANSI DEVICE NUMBER"),
            ("earth.earth", "EARTH CONNECTION"),
        ),
        parameters=(
            ("NOMINAL SYSTEM VOLTAGE", "33 kV"),
            ("MAXIMUM SYSTEM VOLTAGE", "36 kV"),
            ("DESIGN AMBIENT TEMPERATURE", "50 DEG C"),
            ("SHORT-CIRCUIT LEVEL", "25 kA FOR 1 SEC"),
            ("PHASE TO PHASE CLEARANCE", "320 mm"),
            ("PHASE TO EARTH CLEARANCE", "320 mm"),
            ("CREEPAGE DISTANCE", "31 mm/kV"),
            ("BASIC INSULATION LEVEL", "170 kVp"),
            ("POWER FREQUENCY WITHSTAND", "70 kV rms"),
            ("FREQUENCY", "50 Hz"),
            ("SYSTEM EARTHING", "EFFECTIVELY EARTHED"),
        ),
        notes=(
            "THIS SLD REPRESENTS THE 100 MWac SOLAR PLANT AT 33 kV.",
            "THE PLANT COMPRISES 8 BLOCKS OF 12.5 MWac EACH, EACH BLOCK WITH 4 NOS. 3.125 MW PCU AND ONE 5-WINDING IDT.",
            "33 kV MAIN HT SWITCHBOARD IS SPLIT INTO TWO SECTIONS OF 50 MWac EACH WITH A BUS TIE.",
            "ALL LT & HT POWER CABLE SIZES AND NUMBER OF RUNS ARE TENTATIVE AND WILL BE FINALISED DURING DETAILED ENGINEERING.",
            "RELAYS SHALL SUPPORT IEC 61850 AND METERS RS-485 COMMUNICATION TO SCADA.",
            "IDT IMPEDANCE AND CT/PT SIZING WILL BE SUBMITTED SEPARATELY.",
        ),
    )


def rooftop_500kwp(paper: PaperSize = PaperSize.A3) -> SheetSpec:
    tb = TitleBlockData(
        client="SUNRISE TEXTILES PVT. LTD.",
        project_title="500 kWp ROOFTOP SOLAR PV PLANT, GRID CONNECTED, NET METERING",
        location="PLOT 44, MIDC, SINNAR, NASHIK",
        loa_no="PO/SUN/2026/118, Dated: 02-06-2026",
        drawing_title="SINGLE LINE DIAGRAM - LT NET METERING",
        drawing_number="UDEMO-SUN-202611-EE-001",
        revision="00",
        scale="NTS",
        sheet_no=1,
        sheet_count=6,
        paper=paper,
        drawn_by="PRK",
        checked_by="SDL",
        approved_by="MJD",
        drawn_date=date(2026, 10, 12),
        checked_date=date(2026, 10, 13),
        approved_date=date(2026, 10, 14),
        issued_for=IssuedFor.APPROVAL,
    )
    return SheetSpec(
        paper=paper,
        title_block=tb,
        revisions=(
            Revision(rev="00", date=date(2026, 10, 14), description="FIRST SUBMISSION FOR DISCOM APPROVAL",
                     by="PRK", checked="SDL", approved="MJD"),
        ),
        legend=(
            ("pv.module", "PV MODULE 580 Wp"),
            ("inv.string", "STRING INVERTER 100 kW"),
            ("sw.mcb", "MINIATURE CIRCUIT BREAKER (ACDB)"),
            ("sw.mccb", "MOULDED CASE CIRCUIT BREAKER"),
            ("inst.kwh", "SOLAR GENERATION METER"),
            ("inst.net_meter", "BI-DIRECTIONAL NET METER"),
            ("prot.spd_ac", "SURGE PROTECTION DEVICE"),
            ("earth.earth", "EARTH CONNECTION"),
        ),
        parameters=(
            ("DC CAPACITY", "501.6 kWp"),
            ("AC CAPACITY", "500 kW"),
            ("GRID VOLTAGE", "415 V, 3 PH, 50 Hz"),
            ("INVERTERS", "5 x 100 kW"),
            ("MODULES", "864 x 580 Wp"),
        ),
        notes=(
            "ALL CABLES ARE ARMOURED ALUMINIUM XLPE UNLESS NOTED.",
            "MANUAL DISCONNECT SWITCH SHALL BE LOCKABLE AND ACCESSIBLE TO THE DISCOM.",
            "INVERTERS HAVE ANTI-ISLANDING PROTECTION AS PER IS 16169 / IEC 62116.",
            "EARTHING AS PER IS 3043.",
        ),
    )
