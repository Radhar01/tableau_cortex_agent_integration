"""Build the 4-dashboard pharma workbook on top of the data sources in a .twb.

Keeps the Sales, Patientjourney and Payer data sources from the input workbook,
adds DIM_PAYER_PLAN to the Payer data source, adds parameters and calculated
fields, and replaces all worksheets/dashboards with:
  Summary Overview, Sales Performance, Patient Journey, Payer Access.

Usage: python build_pharma_workbook.py <input.twb> <output.twb>
"""
import html
import re
import sys
import uuid
import xml.dom.minidom
from pathlib import Path

DSID = {
    "S": "federated.1iccv2p0acwt2v12umskx08lnrhp",
    "P": "federated.1uvoo230vu7spa14svvjv1qskkln",
    "Y": "federated.0ixelgp0e8zj7z18bdrp4055wwsu",
}
DSCAP = {"S": "Sales", "P": "Patientjourney", "Y": "Payer"}
TAS = ["Cardiovascular", "Dermatology", "Immunology", "Metabolic", "Neurology", "Oncology", "Respiratory"]
METRICS = ["Net Sales ($M)", "TRx", "NBRx", "Market Share (%)"]
NL = "<run>Æ&#10;</run>"

# Theme: navy matches the chat panel header (frontend/index.html) so the report reads as one product.
NAVY, GRAY, GREEN, RED = "#002855", "#6B7280", "#15803D", "#B91C1C"
INK, TEXT, PAGE_BG, CARD_BORDER, TEAL = "#111827", "#374151", "#EEF2F6", "#DDE3EA", "#0F766E"
FONT = "Tableau Book"
BAR = "#3E6FA3"  # single-measure bars
PALETTES = {
    "TIER_NAME": {"Tier 2 - Preferred Brand": "#2E7D32", "Tier 3 - Non-Preferred Brand": "#F2B134",
                  "Tier 4 - Specialty": "#E8743B", "Not Covered": "#B71C1C"},
    "PA_OUTCOME": {"Approved": "#2E7D32", "Approved After Appeal": "#8BC34A",
                   "Denied - Appeal Not Pursued": "#EF9A9A", "Denied After Appeal": "#C62828"},
    "REP_STATUS": {"Active": "#4E79A7", "Departed": "#C62828"},
    "PAYER_NAME": {"Commercial": "#1F77B4", "Medicare Part D": "#7B4FA0", "Medicaid": "#17A589",
                   "Cash / Other": "#9E9E9E"},
    "REGION_NAME": {"Northeast": "#4E79A7", "Southeast": "#F28E2B", "Midwest": "#59A14F",
                    "Southwest": "#E15759", "West": "#76B7B2"},
}


def esc(s):
    return html.escape(str(s), quote=True).replace("&#x27;", "&apos;")


def guid():
    return "{" + str(uuid.uuid4()).upper() + "}"


# ---------------------------------------------------------------------------
# Data source edits
# ---------------------------------------------------------------------------
def ds_span(text, key):
    start = text.index(f"<datasource caption='{DSCAP[key]}'")
    return start, text.index("</datasource>", start) + len("</datasource>")


def edit_ds(text, key, fn):
    s, e = ds_span(text, key)
    return text[:s] + fn(text[s:e]) + text[e:]


PLAN_COLS = [  # remote, local, type, width
    ("PLAN_ID", "PLAN_ID (DIM_PAYER_PLAN)", "integer", None),
    ("PLAN_NAME", "PLAN_NAME", "string", 100),
    ("PLAN_TYPE", "PLAN_TYPE", "string", 50),
    ("PBM_NAME", "PBM_NAME", "string", 50),
    ("PLAN_LIVES_K", "PLAN_LIVES_K (DIM_PAYER_PLAN)", "integer", None),
    ("PAYER_ID", "PAYER_ID", "integer", None),
    ("PAYER_NAME", "PAYER_NAME", "string", 50),
    ("PAYER_TYPE", "PAYER_TYPE", "string", 30),
]
PLAN_CAPTIONS = {
    "PLAN_ID (DIM_PAYER_PLAN)": ("Plan Id (Dim Payer Plan)", "dimension", "ordinal"),
    "PLAN_NAME": ("Plan Name", "dimension", "nominal"),
    "PLAN_TYPE": ("Plan Type", "dimension", "nominal"),
    "PBM_NAME": ("Pbm Name", "dimension", "nominal"),
    "PLAN_LIVES_K (DIM_PAYER_PLAN)": ("Plan Lives K (Dim Payer Plan)", "measure", "quantitative"),
    "PAYER_ID": ("Payer Id", "dimension", "ordinal"),
    "PAYER_NAME": ("Payer Name", "dimension", "nominal"),
    "PAYER_TYPE": ("Payer Type", "dimension", "nominal"),
}


def add_payer_plan(block):
    if "DIM_PAYER_PLAN" in block:
        return block
    conn = re.search(r"<relation connection='([^']+)' name='DIM_PRODUCT'", block).group(1)
    oid = "DIM_PAYER_PLAN (PHARMA_DEMO.DIM_PAYER_PLAN)_" + uuid.uuid4().hex.upper()
    rel = (f"<relation connection='{conn}' name='DIM_PAYER_PLAN' "
           "table='[PHARMA_DEMO].[TABLEAU_CORTEX_PHARMA].[DIM_PAYER_PLAN]' type='table' />")
    # Tableau orders the dimension tables alphabetically and maps metadata ordinals in that
    # order, so DIM_PAYER_PLAN has to sit before DIM_PRODUCT everywhere.
    i = block.index(f"<relation connection='{conn}' name='DIM_PRODUCT'")
    block = block[:i] + rel + "\n          " + block[i:]

    maps = "".join(f"          <map key='[{loc}]' value='[DIM_PAYER_PLAN].[{rem}]' />\n" for rem, loc, _, _ in PLAN_COLS)
    i = block.index("        </cols>")
    block = block[:i] + maps + block[i:]

    ordinal = max(int(o) for o in re.findall(r"<ordinal>(\d+)</ordinal>", block))
    recs = []
    for rem, loc, typ, width in PLAN_COLS:
        ordinal += 1
        if typ == "string":
            detail = (f"<local-type>string</local-type>\n            <aggregation>Count</aggregation>\n"
                      f"            <width>{width}</width>\n            <contains-null>true</contains-null>\n"
                      "            <collation flag='0' name='binary' />\n            <attributes>\n"
                      "              <attribute datatype='string' name='DebugRemoteType'>&quot;SQL_VARCHAR&quot;</attribute>\n"
                      "              <attribute datatype='string' name='DebugWireType'>&quot;SQL_C_CHAR&quot;</attribute>\n"
                      "              <attribute datatype='string' name='TypeIsVarchar'>&quot;true&quot;</attribute>\n"
                      "            </attributes>")
            rtype = 129
        else:
            detail = ("<local-type>integer</local-type>\n            <aggregation>Sum</aggregation>\n"
                      "            <precision>38</precision>\n            <scale>0</scale>\n"
                      "            <contains-null>true</contains-null>\n            <attributes>\n"
                      "              <attribute datatype='string' name='DebugRemoteType'>&quot;SQL_DECIMAL&quot;</attribute>\n"
                      "              <attribute datatype='string' name='DebugWireType'>&quot;SQL_C_NUMERIC&quot;</attribute>\n"
                      "            </attributes>")
            rtype = 131
        recs.append(
            "          <metadata-record class='column'>\n"
            f"            <remote-name>{rem}</remote-name>\n            <remote-type>{rtype}</remote-type>\n"
            f"            <local-name>[{loc}]</local-name>\n            <parent-name>[DIM_PAYER_PLAN]</parent-name>\n"
            f"            <remote-alias>{rem}</remote-alias>\n            <ordinal>{ordinal}</ordinal>\n"
            f"            {detail}\n            <object-id>[{oid}]</object-id>\n          </metadata-record>\n")
    i = block.rindex("          <metadata-record class='column'>", 0, block.index("<parent-name>[DIM_PRODUCT]</parent-name>"))
    block = block[:i] + "".join(recs) + block[i:]
    seq = iter(range(1, 10000))
    block = re.sub(r"<ordinal>\d+</ordinal>", lambda _: f"<ordinal>{next(seq)}</ordinal>", block)

    cols = []
    for _, loc, typ, _ in PLAN_COLS:
        cap, role, t = PLAN_CAPTIONS[loc]
        cols.append(f"      <column caption='{cap}' datatype='{typ}' name='[{loc}]' role='{role}' type='{t}' />\n")
    i = block.index("      <column caption='DIM_DATE' datatype='table'")
    block = block[:i] + "".join(cols) + block[i:]
    tcol = (f"      <column caption='DIM_PAYER_PLAN' datatype='table' name='[__tableau_internal_object_id__].[{oid}]' "
            "role='measure' type='quantitative' />\n")
    i = block.index("      <layout ")
    block = block[:i] + tcol + block[i:]

    obj = (f"          <object caption='DIM_PAYER_PLAN' id='{oid}'>\n            <properties context=''>\n"
           f"              {rel}\n            </properties>\n          </object>\n")
    i = block.index("          <object caption='DIM_PRODUCT'")
    block = block[:i] + obj + block[i:]
    fact = re.search(r"object-id='(FACT_PAYER_ACCESS [^']+)'", block).group(1)
    relx = ("          <relationship>\n            <expression op='='>\n"
            "              <expression op='[PLAN_ID]' />\n              <expression op='[PLAN_ID (DIM_PAYER_PLAN)]' />\n"
            f"            </expression>\n            <first-end-point object-id='{fact}' />\n"
            f"            <second-end-point is-db-set-unique-key='true' object-id='{oid}' unique-key='true' />\n"
            "          </relationship>\n")
    i = block.index("        </relationships>")
    return block[:i] + relx + block[i:]


# ---------------------------------------------------------------------------
# Parameters and calculated fields
# ---------------------------------------------------------------------------
def param_xml(name, caption, default, members):
    mem = "".join(f"<member value='{esc(chr(34) + m + chr(34))}' />" for m in members)
    q = esc('"' + default + '"')
    return (f"<column caption='{esc(caption)}' datatype='string' name='[{name}]' param-domain-type='list' "
            f"role='measure' type='nominal' value='{q}'><calculation class='tableau' formula='{q}' />"
            f"<members>{mem}</members></column>")


PARAMS = {
    "TA Param": param_xml("TA Param", "Therapeutic Area", "All", ["All"] + TAS),
    "Metric Param": param_xml("Metric Param", "Trend Metric", METRICS[0], METRICS),
}
PARAM_DS = ("    <datasource hasconnection='false' inline='true' name='Parameters' version='18.1'>\n"
            "      <aliases enabled='yes' />\n" + "".join(f"      {p}\n" for p in PARAMS.values()) +
            "    </datasource>\n")

CALCS = {"S": {}, "P": {}, "Y": {}}
FMT_PCT, FMT_INT, FMT_1, FMT_USD = "p0.0%", "n#,##0", "n#,##0.0", 'c"$"#,##0'
# Display units in Tableau's own notation: ,, = millions, , = thousands.
FMT_USD_M, FMT_K = 'c"$"#,##0,,.0M;-"$"#,##0,,.0M', "n#,##0.0,K;-#,##0.0,K"
TC_COLS = "<table-calc ordering-type='Columns' />"


def calc(ds, name, formula, dt="real", role="measure", typ="quantitative", fmt=None, tc=""):
    CALCS[ds][name] = dict(formula=formula, dt=dt, role=role, typ=typ, fmt=fmt, tc=tc)


def dcalc(ds, name, formula, dt="boolean"):
    calc(ds, name, formula, dt, "dimension", "nominal")


def scalc(ds, name, formula):
    calc(ds, name, formula, "string", "measure", "nominal")


def money(e):
    return (f"IF {e} >= 1000000000 THEN '$' + STR(ROUND({e} / 1000000000, 2)) + 'B' "
            f"ELSEIF {e} >= 1000000 THEN '$' + STR(ROUND({e} / 1000000, 1)) + 'M' "
            f"ELSE '$' + STR(ROUND({e} / 1000, 1)) + 'K' END")


def count(e):
    return (f"IF {e} >= 1000000 THEN STR(ROUND({e} / 1000000, 2)) + 'M' "
            f"ELSEIF {e} >= 10000 THEN STR(ROUND({e} / 1000, 1)) + 'K' ELSE STR(ROUND({e}, 0)) END")


def pct(e):
    return f"STR(ROUND({e} * 100, 1)) + '%'"


def delta(ds, prefix, v, unit, good_up=True):
    """Two string calcs, '<prefix> Good' / '<prefix> Bad', so the KPI tile can colour the change."""
    mag = {"pct": f"STR(ROUND(ABS({v}) * 100, 1)) + '%'",
           "pts": f"STR(ROUND(ABS({v}), 1)) + ' pts'",
           "days": f"STR(ROUND(ABS({v}), 1)) + ' days'"}[unit]
    text = f"IIF({v} >= 0, '▲ ', '▼ ') + {mag} + ' vs PYTD'"
    good = f"{v} >= 0" if good_up else f"{v} <= 0"
    scalc(ds, f"{prefix} Good", f"IIF({good}, {text}, '')")
    scalc(ds, f"{prefix} Bad", f"IIF(NOT ({good}), {text}, '')")


def ytd_pair(ds, name, agg, field, fmt):
    calc(ds, f"{name} YTD", f"{agg}(IF [Is YTD] THEN [{field}] END)", fmt=fmt)
    calc(ds, f"{name} PYTD", f"{agg}(IF [Is PYTD] THEN [{field}] END)", fmt=fmt)


for k in CALCS:
    calc(k, "Latest Month", "{ FIXED : MAX([DATE_KEY]) }", "date", "dimension", "ordinal")
    dcalc(k, "Is YTD", "YEAR([DATE_KEY]) = YEAR([Latest Month]) AND [DATE_KEY] <= [Latest Month]")
    dcalc(k, "Is PYTD", "YEAR([DATE_KEY]) = YEAR([Latest Month]) - 1 AND MONTH([DATE_KEY]) <= MONTH([Latest Month])")
    dcalc(k, "TA Filter", "[Parameters].[TA Param] = 'All' OR [THERAPEUTIC_AREA] = [Parameters].[TA Param]")

# ---- Sales -----------------------------------------------------------------
S = "S"
calc(S, "Total Net Sales", "SUM([NET_SALES])", fmt=FMT_USD_M)
ytd_pair(S, "Net Sales", "SUM", "NET_SALES", FMT_USD_M)
ytd_pair(S, "TRx", "SUM", "TRX", FMT_K)
ytd_pair(S, "NBRx", "SUM", "NBRX", FMT_INT)
ytd_pair(S, "Calls", "SUM", "CALLS", FMT_K)
for n in ("Net Sales", "TRx", "NBRx"):
    calc(S, f"{n} YoY Pct", f"([{n} YTD] - [{n} PYTD]) / [{n} PYTD]", fmt=FMT_PCT)
    delta(S, f"{n} Delta", f"[{n} YoY Pct]", "pct")
calc(S, "Net Sales YTD Label", money("[Net Sales YTD]"), "string", "measure", "nominal")
calc(S, "TRx YTD Label", count("[TRx YTD]"), "string", "measure", "nominal")
calc(S, "NBRx YTD Label", count("[NBRx YTD]"), "string", "measure", "nominal")
calc(S, "Market Share", "SUM([TRX]) / SUM([MARKET_TRX])", fmt=FMT_PCT)
calc(S, "Market Share YTD", "SUM(IF [Is YTD] THEN [TRX] END) / SUM(IF [Is YTD] THEN [MARKET_TRX] END)", fmt=FMT_PCT)
calc(S, "Market Share PYTD", "SUM(IF [Is PYTD] THEN [TRX] END) / SUM(IF [Is PYTD] THEN [MARKET_TRX] END)", fmt=FMT_PCT)
calc(S, "Share Change Pts", "([Market Share YTD] - [Market Share PYTD]) * 100", fmt="n+0.0;-0.0")
calc(S, "Market Share YTD Label", pct("[Market Share YTD]"), "string", "measure", "nominal")
delta(S, "Share Delta", "[Share Change Pts]", "pts")
calc(S, "GTN Pct YTD", "SUM(IF [Is YTD] THEN [GROSS_TO_NET_DEDUCTIONS] END) / SUM(IF [Is YTD] THEN [GROSS_SALES] END)", fmt=FMT_PCT)
calc(S, "GTN Pct PYTD", "SUM(IF [Is PYTD] THEN [GROSS_TO_NET_DEDUCTIONS] END) / SUM(IF [Is PYTD] THEN [GROSS_SALES] END)", fmt=FMT_PCT)
calc(S, "GTN Change Pts", "([GTN Pct YTD] - [GTN Pct PYTD]) * 100", fmt="n+0.0;-0.0")
calc(S, "GTN YTD Label", pct("[GTN Pct YTD]"), "string", "measure", "nominal")
delta(S, "GTN Delta", "[GTN Change Pts]", "pts", good_up=False)
calc(S, "NBRx per 100 Calls YTD", "[NBRx YTD] / [Calls YTD] * 100", fmt=FMT_1)
calc(S, "Selected Metric",
     "CASE [Parameters].[Metric Param] WHEN 'Net Sales ($M)' THEN SUM([NET_SALES]) / 1000000 "
     "WHEN 'TRx' THEN SUM([TRX]) WHEN 'NBRx' THEN SUM([NBRX]) "
     "WHEN 'Market Share (%)' THEN [Market Share] * 100 END", fmt=FMT_1)
calc(S, "Selected Metric 3M Avg", "WINDOW_AVG([Selected Metric], -2, 0)", fmt=FMT_1, tc=TC_COLS)
calc(S, "Quarter YoY Growth",
     "(SUM([NET_SALES]) - LOOKUP(SUM([NET_SALES]), -4)) / ABS(LOOKUP(SUM([NET_SALES]), -4))", fmt=FMT_PCT, tc=TC_COLS)
calc(S, "Vacancy Cutoff", "{ FIXED : MIN([REP_DEPARTURE_DATE]) }", "date", "dimension", "ordinal")
calc(S, "NBRx Pre Window",
     "IF [DATE_KEY] <= [Vacancy Cutoff] AND [DATE_KEY] > DATEADD('month', -8, [Vacancy Cutoff]) THEN [NBRX] END",
     "integer", fmt=FMT_INT)
calc(S, "NBRx Post Window", "IF [DATE_KEY] > [Vacancy Cutoff] THEN [NBRX] END", "integer", fmt=FMT_INT)
calc(S, "NBRx Change Post Vacancy",
     "(SUM([NBRx Post Window]) / COUNTD(IF [DATE_KEY] > [Vacancy Cutoff] THEN [DATE_KEY] END)) / "
     "(SUM([NBRx Pre Window]) / COUNTD(IF [DATE_KEY] <= [Vacancy Cutoff] AND [DATE_KEY] > "
     "DATEADD('month', -8, [Vacancy Cutoff]) THEN [DATE_KEY] END)) - 1", fmt=FMT_PCT)

# ---- Patient journey ---------------------------------------------------------
P = "P"
calc(P, "Patients", "COUNTD([PATIENT_ID])", fmt=FMT_INT)
calc(P, "Patients YTD", "COUNTD(IF [Is YTD] THEN [PATIENT_ID] END)", fmt=FMT_INT)
calc(P, "Patients PYTD", "COUNTD(IF [Is PYTD] THEN [PATIENT_ID] END)", fmt=FMT_INT)
calc(P, "Patients YoY Pct", "([Patients YTD] - [Patients PYTD]) / [Patients PYTD]", fmt=FMT_PCT)
calc(P, "Patients YTD Label", count("[Patients YTD]"), "string", "measure", "nominal")
delta(P, "Patients Delta", "[Patients YoY Pct]", "pct")
calc(P, "Abandonment Rate", "AVG([ABANDONED_FLAG])", fmt=FMT_PCT)
ytd_pair(P, "Abandonment", "AVG", "ABANDONED_FLAG", FMT_PCT)
calc(P, "Abandonment Change Pts", "([Abandonment YTD] - [Abandonment PYTD]) * 100", fmt="n+0.0;-0.0")
calc(P, "Abandonment YTD Label", pct("[Abandonment YTD]"), "string", "measure", "nominal")
delta(P, "Abandonment Delta", "[Abandonment Change Pts]", "pts", good_up=False)
calc(P, "PA Denial YTD", "SUM(IF [Is YTD] THEN [PA_DENIED_FLAG] END) / SUM(IF [Is YTD] THEN [PA_REQUIRED_FLAG] END)", fmt=FMT_PCT)
calc(P, "PA Denial PYTD", "SUM(IF [Is PYTD] THEN [PA_DENIED_FLAG] END) / SUM(IF [Is PYTD] THEN [PA_REQUIRED_FLAG] END)", fmt=FMT_PCT)
calc(P, "PA Denial Change Pts", "([PA Denial YTD] - [PA Denial PYTD]) * 100", fmt="n+0.0;-0.0")
calc(P, "PA Denial YTD Label", pct("[PA Denial YTD]"), "string", "measure", "nominal")
delta(P, "PA Denial Delta", "[PA Denial Change Pts]", "pts", good_up=False)
ytd_pair(P, "Days to Fill", "AVG", "DAYS_RX_TO_FIRST_FILL", FMT_1)
calc(P, "Days to Fill Change", "[Days to Fill YTD] - [Days to Fill PYTD]", fmt="n+0.0;-0.0")
calc(P, "Days to Fill YTD Label", "STR(ROUND([Days to Fill YTD], 1)) + ' days'", "string", "measure", "nominal")
delta(P, "Days to Fill Delta", "[Days to Fill Change]", "days", good_up=False)
for d in ("90D", "180D", "365D"):
    calc(P, f"Persistence {d}", f"AVG([PERSISTENT_{d}_FLAG])", fmt="p0%")
calc(P, "Persistence 180D Label", pct("[Persistence 180D]"), "string", "measure", "nominal")
dcalc(P, "Mature Cohort", "[RX_WRITTEN_DATE] <= DATEADD('month', -15, [Latest Month])")
calc(P, "1 Rx Written", "COUNTD([PATIENT_ID])", fmt=FMT_INT)
calc(P, "2 PA Cleared", "SUM(IIF([PA_DENIED_FLAG] = 0 OR [APPEAL_APPROVED_FLAG] = 1, 1, 0))", fmt=FMT_INT)
calc(P, "3 First Fill", "SUM([FIRST_FILL_FLAG])", fmt=FMT_INT)
calc(P, "4 On Therapy 90D", "SUM([PERSISTENT_90D_FLAG])", fmt=FMT_INT)
calc(P, "5 On Therapy 180D", "SUM([PERSISTENT_180D_FLAG])", fmt=FMT_INT)
calc(P, "6 On Therapy 365D", "SUM([PERSISTENT_365D_FLAG])", fmt=FMT_INT)
calc(P, "HCP Abandonment Rate", "{ FIXED [HCP_ID] : AVG([ABANDONED_FLAG]) }", fmt=FMT_PCT)
calc(P, "Overall Abandonment Rate", "{ FIXED : AVG([ABANDONED_FLAG]) }", fmt=FMT_PCT)
dcalc(P, "HCP Risk Segment",
      "IF [HCP Abandonment Rate] >= 1.25 * [Overall Abandonment Rate] THEN 'High abandonment' "
      "ELSEIF [HCP Abandonment Rate] <= 0.75 * [Overall Abandonment Rate] THEN 'Low abandonment' "
      "ELSE 'Typical' END", "string")
calc(P, "High Abandonment HCPs", "COUNTD(IF [HCP Risk Segment] = 'High abandonment' THEN [HCP_ID] END)", fmt=FMT_INT)
calc(P, "High Abandonment HCPs Label", "STR([High Abandonment HCPs])", "string", "measure", "nominal")

# ---- Payer ---------------------------------------------------------------------
Y = "Y"
dcalc(Y, "Is Latest Month", "[DATE_KEY] = [Latest Month]")
calc(Y, "Coverage Pct Latest",
     "SUM(IF [Is Latest Month] THEN [COVERED_LIVES_K] END) / SUM(IF [Is Latest Month] THEN [PLAN_LIVES_K] END)", fmt=FMT_PCT)
calc(Y, "Coverage Latest Label", pct("[Coverage Pct Latest]"), "string", "measure", "nominal")
calc(Y, "Claim Approval Rate", "SUM([CLAIMS_APPROVED]) / SUM([CLAIMS_SUBMITTED])", fmt=FMT_PCT)
calc(Y, "Claim Rejection Rate", "SUM([CLAIMS_REJECTED]) / SUM([CLAIMS_SUBMITTED])", fmt=FMT_PCT)
for n, num, den in (("Claim Approval", "CLAIMS_APPROVED", "CLAIMS_SUBMITTED"), ("PA Approval", "PA_APPROVED", "PA_REQUESTS")):
    calc(Y, f"{n} YTD", f"SUM(IF [Is YTD] THEN [{num}] END) / SUM(IF [Is YTD] THEN [{den}] END)", fmt=FMT_PCT)
    calc(Y, f"{n} PYTD", f"SUM(IF [Is PYTD] THEN [{num}] END) / SUM(IF [Is PYTD] THEN [{den}] END)", fmt=FMT_PCT)
    calc(Y, f"{n} Change Pts", f"([{n} YTD] - [{n} PYTD]) * 100", fmt="n+0.0;-0.0")
    calc(Y, f"{n} YTD Label", pct(f"[{n} YTD]"), "string", "measure", "nominal")
    delta(Y, f"{n} Delta", f"[{n} Change Pts]", "pts")
ytd_pair(Y, "Rebates", "SUM", "ESTIMATED_REBATE_USD", FMT_USD_M)
calc(Y, "Rebates YoY Pct", "([Rebates YTD] - [Rebates PYTD]) / [Rebates PYTD]", fmt=FMT_PCT)
calc(Y, "Rebates YTD Label", money("[Rebates YTD]"), "string", "measure", "nominal")
delta(Y, "Rebates Delta", "[Rebates YoY Pct]", "pct", good_up=False)
ytd_pair(Y, "Avg Copay", "AVG", "AVG_PATIENT_COPAY", 'c"$"#,##0.00')
calc(Y, "Avg Copay YoY Pct", "([Avg Copay YTD] - [Avg Copay PYTD]) / [Avg Copay PYTD]", fmt=FMT_PCT)
calc(Y, "Avg Copay YTD Label", "'$' + STR(ROUND([Avg Copay YTD], 2))", "string", "measure", "nominal")
delta(Y, "Avg Copay Delta", "[Avg Copay YoY Pct]", "pct", good_up=False)
calc(Y, "Avg Rebate Pct", "AVG([REBATE_PCT])", fmt=FMT_PCT)
dcalc(Y, "Restrictions",
      "IIF([PA_REQUIRED_FLAG] = 1, 'PA ', '') + IIF([STEP_THERAPY_FLAG] = 1, 'ST ', '') + "
      "IIF([QUANTITY_LIMIT_FLAG] = 1, 'QL', '')", "string")
calc(Y, "Access Score",
     "IF [TIER_NAME] = 'Not Covered' THEN 0 ELSE (CASE [TIER_NAME] WHEN 'Tier 2 - Preferred Brand' THEN 100 "
     "WHEN 'Tier 3 - Non-Preferred Brand' THEN 70 ELSE 55 END) - 10 * [PA_REQUIRED_FLAG] "
     "- 10 * [STEP_THERAPY_FLAG] - 5 * [QUANTITY_LIMIT_FLAG] END", fmt=FMT_1)
calc(Y, "Company Access Score", "{ FIXED : AVG(IF [Is Latest Month] THEN [Access Score] END) }", fmt=FMT_1)
calc(Y, "Plan Access Score", "AVG(IF [Is Latest Month] THEN [Access Score] END)", fmt=FMT_1)
calc(Y, "Access Score vs Company", "[Plan Access Score] - AVG([Company Access Score])", fmt="n+0.0;-0.0")
calc(Y, "Weighted PA Turnaround", "SUM([AVG_PA_TURNAROUND_DAYS] * [PA_REQUESTS]) / SUM([PA_REQUESTS])", fmt=FMT_1)


def calc_xml(name, c):
    fmt = f" default-format='{esc(c['fmt'])}'" if c["fmt"] else ""
    inner = f"<calculation class='tableau' formula='{esc(c['formula'])}'" + (f">{c['tc']}</calculation>" if c["tc"] else " />")
    return f"<column datatype='{c['dt']}'{fmt} name='[{esc(name)}]' role='{c['role']}' type='{c['typ']}'>{inner}</column>"


# ---------------------------------------------------------------------------
# Field references and worksheets
# ---------------------------------------------------------------------------
class F:
    def __init__(self, ds, col, deriv="None", kind="nk", prefix="none", inst=None, tc=""):
        self.ds, self.col, self.deriv, self.kind, self.tc = ds, col, deriv, kind, tc
        self.inst = inst or f"{prefix}:{col}:{kind}"

    @property
    def typ(self):
        return {"nk": "nominal", "ok": "ordinal", "qk": "quantitative"}[self.kind]

    @property
    def full(self):
        return f"[{DSID[self.ds]}].[{self.inst}]"

    def inst_xml(self):
        x = (f"<column-instance column='[{esc(self.col)}]' derivation='{self.deriv}' name='[{esc(self.inst)}]' "
             f"pivot='key' type='{self.typ}'")
        return x + (f">{self.tc}</column-instance>" if self.tc else " />")


AGG = {"Sum": "sum", "Avg": "avg", "Count": "cnt", "CountD": "ctd", "Min": "min", "Max": "max"}


def D(ds, col):
    return F(ds, col)


def A(ds, col, agg):
    return F(ds, col, agg, "qk", AGG[agg])


def U(ds, col, kind="qk"):
    tc = CALCS[ds][col]["tc"] if col in CALCS[ds] else ""
    return F(ds, col, "User", kind, "usr", tc=tc)


def MON(ds, col):
    return F(ds, col, "Month-Trunc", "qk", "tmn")


def PCT(ds, col, agg, along, n=1):
    p = AGG[agg]
    tc = f"<table-calc ordering-field='{esc(along.full)}' ordering-type='Field' type='PctTotal' />"
    return F(ds, col, agg, "qk", p, inst=f"pcto:{p}:{col}:qk:{n}", tc=tc)


def MN(ds):
    return f"[{DSID[ds]}].[:Measure Names]"


def MV(ds):
    return f"[{DSID[ds]}].[Multiple Values]"


BASE = {}


def closure(ds, names):
    seen, params, stack = set(), set(), list(names)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in CALCS[ds]:
            f = CALCS[ds][n]["formula"]
            params |= set(re.findall(r"\[Parameters\]\.\[([^\]]+)\]", f))
            f = re.sub(r"\[Parameters\]\.\[[^\]]+\]", "", f)
            stack += re.findall(r"\[([^\]]+)\]", f)
        elif n not in BASE[ds]:
            raise KeyError(f"{DSCAP[ds]}: unknown field [{n}]")
    return seen, params


def diverging(full, colors=("#B91C1C", "#F4A6A6", "#EDEDED", "#9FD3A8", "#15803D")):
    c = "".join(f"<color>{x}</color>" for x in colors)
    return (f"<encoding attr='color' center='0.0' field='{esc(full)}' type='custom-interpolated'>"
            f"<color-palette custom='true' name='' type='ordered-diverging'>{c}</color-palette></encoding>")


def sequential(full, colors=("#E3EEF9", "#9CC3E6", "#4A90C8", "#1F4E79")):
    c = "".join(f"<color>{x}</color>" for x in colors)
    return (f"<encoding attr='color' field='{esc(full)}' type='custom-interpolated'>"
            f"<color-palette custom='true' name='' type='ordered-sequential'>{c}</color-palette></encoding>")


def palette(f):
    maps = "".join(f"<map to='{col}'><bucket>{esc(chr(34) + k + chr(34))}</bucket></map>"
                   for k, col in PALETTES[f.col].items())
    return f"<encoding attr='color' field='{esc(f.full)}' type='palette'>{maps}</encoding>"


def run(text, size=None, color=None, bold=False):
    a = (" bold='true'" if bold else "") + (f" fontcolor='{color}'" if color else "") + (f" fontsize='{size}'" if size else "")
    return f"<run{a}>{esc(text)}</run>"


def frun(full, size=None, color=None, bold=False):
    a = (" bold='true'" if bold else "") + (f" fontcolor='{color}'" if color else "") + (f" fontsize='{size}'" if size else "")
    return f"<run{a}><![CDATA[<{full}>]]></run>"


def shelf(items):
    items = [i if isinstance(i, str) else i.full for i in items]
    if not items:
        return None
    return items[0] if len(items) == 1 else "(" + " / ".join(items) + ")"


WORKSHEETS = []

# Applied to every worksheet: one font, no gridlines or row dividers, quiet axes.
BASE_STYLE = (
    f"<style-rule element='worksheet'><format attr='font-family' value='{FONT}' />"
    f"<format attr='font-size' value='9' /><format attr='color' value='{TEXT}' />"
    "<format attr='display-field-labels' value='false' /></style-rule>"
    "<style-rule element='gridline'><format attr='line-visibility' scope='rows' value='off' />"
    "<format attr='line-visibility' scope='cols' value='off' /></style-rule>"
    "<style-rule element='zeroline'><format attr='stroke-color' scope='rows' value='#D1D5DB' />"
    "<format attr='stroke-color' scope='cols' value='#D1D5DB' /></style-rule>"
    "<style-rule element='table-div'><format attr='line-visibility' scope='rows' value='off' />"
    "<format attr='line-visibility' scope='cols' value='off' /></style-rule>"
)
AXIS_STYLE = "<format attr='tick-color' value='#00000000' /><format attr='line-visibility' value='off' />"
LABEL_STYLE = f"<format attr='font-size' value='9' /><format attr='color' value='{TEXT}' />"


def tooltip_runs(items):
    """[(label, field)] -> tooltip lines 'Label:  value' (first item is the bold heading)."""
    runs = []
    for i, (label, f) in enumerate(items):
        full = f if isinstance(f, str) else f.full
        if i == 0:
            runs += [frun(full, 11, INK, True)]
        else:
            runs += [NL, run(f"{label}:  ", 9, GRAY), frun(full, 9, INK, True)]
    return runs


def pane_xml(mark, enc, label_runs=None, show_labels=False, mark_color=None, attrs="", tooltip=None):
    order = ("color", "size", "text", "lod", "tooltip", "geometry")
    parts = []
    for key in order:
        for f in enc.get(key, []):
            parts.append(f"<{key} column='{esc(f if isinstance(f, str) else f.full)}' />")
    encs = f"<encodings>{''.join(parts)}</encodings>" if parts else ""
    tip = (f"<customized-tooltip><formatted-text>{''.join(tooltip_runs(tooltip))}</formatted-text></customized-tooltip>"
           if tooltip else "")
    label = tip + (f"<customized-label><formatted-text>{''.join(label_runs)}</formatted-text></customized-label>" if label_runs else "")
    fmts = ""
    if show_labels:
        fmts += "<format attr='mark-labels-show' value='true' /><format attr='mark-labels-cull' value='true' />"
    if mark_color:
        fmts += f"<format attr='mark-color' value='{mark_color}' />"
    style = f"<style><style-rule element='mark'>{fmts}</style-rule></style>" if fmts else ""
    return (f"<pane selection-relaxation-option='selection-relaxation-allow'{attrs}><view><breakdown value='auto' /></view>"
            f"<mark class='{mark}' />{encs}{label}{style}</pane>")


def worksheet(name, ds, title_runs, mark="Automatic", rows=(), cols=(), color=None, size=None, text=(), lod=(),
              filters=(), sorts=(), label_runs=None, show_labels=False, mark_color=None, mark_styles=(),
              panes=None, rows_expr=None, cols_expr=None, mapsrc=False, geometry=None, axis_style="",
              mn_members=None, extra=(), tooltip=None, hide_axes=(), axis_titles=(), formats=()):
    """hide_axes: [(field, 'rows'|'cols')]; axis_titles: [(field, scope, title)];
    formats: [(element, field, text-format)] for axes/labels/tooltips."""
    in_view = {f.inst for f in list(rows) + list(cols) + list(text) + list(lod) + [color, size] if isinstance(f, F)}
    tip_fields = [f for _, f in (tooltip or []) if isinstance(f, F) and f.inst not in in_view]
    fields = [f for f in list(rows) + list(cols) + list(text) + list(lod) + list(extra) + tip_fields if isinstance(f, F)]
    fields += [f for f in (color, size) if isinstance(f, F)]
    filters = [("true", D(ds, "TA Filter"))] + list(filters)
    fields += [flt[1] for flt in filters if isinstance(flt[1], F)]
    fields += [s[2] for s in sorts]
    if mn_members:
        fields += mn_members
    insts = {}
    for f in fields:
        insts.setdefault(f.inst, f)
    names, params = closure(ds, {f.col for f in insts.values()})
    title_text = "".join(title_runs)
    params |= set(re.findall(r"\[Parameters\]\.\[([^\]]+)\]", title_text))

    deps = ""
    if params:
        deps += "<datasource-dependencies datasource='Parameters'>" + "".join(PARAMS[p] for p in sorted(params)) + "</datasource-dependencies>"
    cols_xml = "".join(calc_xml(n, CALCS[ds][n]) if n in CALCS[ds] else BASE[ds][n] for n in sorted(names))
    deps += (f"<datasource-dependencies datasource='{DSID[ds]}'>{cols_xml}"
             + "".join(f.inst_xml() for f in insts.values()) + "</datasource-dependencies>")

    flt_xml, slices = "", []
    for flt in filters:
        kind, f = flt[0], flt[1]
        if kind == "mn":
            col = MN(ds)
            mem = "".join(f"<groupfilter function='member' level='[:Measure Names]' member='{esc(chr(34) + m.full + chr(34))}' />"
                          for m in mn_members)
            flt_xml += (f"<filter class='categorical' column='{esc(col)}'><groupfilter function='union' user:op='manual'>"
                        f"{mem}</groupfilter></filter>")
            slices.append(col)
            continue
        lvl = f"[{esc(f.inst)}]"
        if kind == "true":
            g = (f"<groupfilter function='member' level='{lvl}' member='true' user:ui-domain='database' "
                 "user:ui-enumeration='inclusive' user:ui-marker='enumerate' />")
        elif kind == "in":
            mem = "".join(f"<groupfilter function='member' level='{lvl}' member='{esc(chr(34) + v + chr(34))}' />" for v in flt[2])
            g = (f"<groupfilter function='union' user:ui-domain='database' user:ui-enumeration='inclusive' "
                 f"user:ui-marker='enumerate'>{mem}</groupfilter>")
        else:  # except
            mem = "".join(f"<groupfilter function='member' level='{lvl}' member='{esc(v if v == '%null%' else chr(34) + v + chr(34))}' />"
                          for v in flt[2])
            g = (f"<groupfilter function='except' user:ui-domain='database' user:ui-enumeration='exclusive' "
                 f"user:ui-marker='enumerate'><groupfilter function='level-members' level='{lvl}' />{mem}</groupfilter>")
        flt_xml += f"<filter class='categorical' column='{esc(f.full)}'>{g}</filter>"
        slices.append(f.full)

    sort_xml = ""
    if mn_members:
        b = "".join(f"<bucket>{esc(chr(34) + m.full + chr(34))}</bucket>" for m in mn_members)
        sort_xml += f"<manual-sort column='{esc(MN(ds))}' direction='ASC'><dictionary>{b}</dictionary></manual-sort>"
    for _, dim, using, direction in sorts:
        sort_xml += f"<computed-sort column='{esc(dim.full)}' direction='{direction}' using='{esc(using.full)}' />"

    srcs = f"<datasource caption='{DSCAP[ds]}' name='{DSID[ds]}' />" + ("<datasource name='Parameters' />" if params else "")
    view = (f"<view><datasources>{srcs}</datasources>"
            + ("<mapsources><mapsource name='Tableau' /></mapsources>" if mapsrc else "")
            + deps + flt_xml + sort_xml
            + "<slices>" + "".join(f"<column>{esc(s)}</column>" for s in slices) + "</slices>"
            + "<aggregation value='true' /></view>")

    style_rules = BASE_STYLE
    if mark_styles:
        style_rules += f"<style-rule element='mark'>{''.join(mark_styles)}</style-rule>"
    axis_rules = axis_style + AXIS_STYLE
    full = lambda f: f if isinstance(f, str) else f.full  # noqa: E731
    for f, scope in hide_axes:
        axis_rules += f"<format attr='display' class='0' field='{esc(full(f))}' scope='{scope}' value='false' />"
    for f, scope, t in axis_titles:
        axis_rules += f"<format attr='title' class='0' field='{esc(full(f))}' scope='{scope}' value='{esc(t)}' />"
    for element, f, fmt in formats:
        if element == "axis":
            axis_rules += f"<format attr='text-format' field='{esc(f.full)}' value='{esc(fmt)}' />"
    style_rules += f"<style-rule element='axis'>{axis_rules}</style-rule>"
    for element, base in (("label", LABEL_STYLE), ("tooltip", "")):
        fx = base + "".join(f"<format attr='text-format' field='{esc(f.full)}' value='{esc(fmt)}' />"
                            for el, f, fmt in formats if el == element)
        if fx:
            style_rules += f"<style-rule element='{element}'>{fx}</style-rule>"
    style = f"<style>{style_rules}</style>"

    if panes is None:
        enc = {"color": [color] if color else [], "size": [size] if size else [], "text": list(text),
               "lod": list(lod), "tooltip": tip_fields, "geometry": [geometry] if geometry else []}
        panes = pane_xml(mark, enc, label_runs, show_labels, mark_color, tooltip=tooltip)
    r = rows_expr or shelf(rows)
    c = cols_expr or shelf(cols)
    rows_xml = f"<rows>{esc(r)}</rows>" if r else "<rows />"
    cols_xml2 = f"<cols>{esc(c)}</cols>" if c else "<cols />"
    WORKSHEETS.append((name, (
        f"<worksheet name='{esc(name)}'><layout-options><title><formatted-text>{title_text}</formatted-text></title></layout-options>"
        f"<table>{view}{style}<panes>{panes}</panes>{rows_xml}{cols_xml2}</table>"
        f"<simple-id uuid='{guid()}' /></worksheet>")))


def title(t, sub=None):
    runs = [run(t, 10, INK, True)]
    if sub:
        runs += [run("   " + sub, 8, GRAY)]
    return runs


def kpi(name, ds, caption, value, good=None, bad=None, sub=None):
    text = [U(ds, value, "nk")] + [U(ds, x, "nk") for x in (good, bad) if x]
    runs = [run(caption, 8, GRAY, True), NL, frun(text[0].full, 18, INK, True), NL]
    if good:
        runs += [frun(U(ds, good, "nk").full, 9, GREEN, True), frun(U(ds, bad, "nk").full, 9, RED, True)]
    if sub:
        runs += [run(sub, 8, GRAY)]
    worksheet(name, ds, [run(caption)], mark="Text", text=text, label_runs=runs)


# ---- KPI tiles ---------------------------------------------------------------
def build_kpis():
    kpi("KPI Net Sales", S, "NET SALES · YTD", "Net Sales YTD Label", "Net Sales Delta Good", "Net Sales Delta Bad")
    kpi("KPI TRx", S, "TOTAL RX (TRx) · YTD", "TRx YTD Label", "TRx Delta Good", "TRx Delta Bad")
    kpi("KPI NBRx", S, "NEW-TO-BRAND RX · YTD", "NBRx YTD Label", "NBRx Delta Good", "NBRx Delta Bad")
    kpi("KPI Market Share", S, "TRx MARKET SHARE · YTD", "Market Share YTD Label", "Share Delta Good", "Share Delta Bad")
    kpi("KPI GTN", S, "GROSS-TO-NET · YTD", "GTN YTD Label", "GTN Delta Good", "GTN Delta Bad")
    kpi("KPI New Patients", P, "NEW PATIENTS · YTD", "Patients YTD Label", "Patients Delta Good", "Patients Delta Bad")
    kpi("KPI Abandonment", P, "ABANDONMENT RATE · YTD", "Abandonment YTD Label", "Abandonment Delta Good", "Abandonment Delta Bad")
    kpi("KPI PA Denial", P, "PA DENIAL RATE · YTD", "PA Denial YTD Label", "PA Denial Delta Good", "PA Denial Delta Bad")
    kpi("KPI Days to Fill", P, "RX → FIRST FILL · YTD", "Days to Fill YTD Label", "Days to Fill Delta Good", "Days to Fill Delta Bad")
    kpi("KPI Persistence", P, "180-DAY PERSISTENCE", "Persistence 180D Label", sub="eligible patients, all periods")
    kpi("KPI High Risk HCPs", P, "HIGH-ABANDONMENT HCPs", "High Abandonment HCPs Label", sub="≥ 1.25× average abandonment")
    kpi("KPI Coverage", Y, "COVERED LIVES", "Coverage Latest Label", sub="share of plan lives, latest month")
    kpi("KPI Claim Approval", Y, "CLAIM APPROVAL · YTD", "Claim Approval YTD Label", "Claim Approval Delta Good", "Claim Approval Delta Bad")
    kpi("KPI PA Approval", Y, "PA APPROVAL · YTD", "PA Approval YTD Label", "PA Approval Delta Good", "PA Approval Delta Bad")
    kpi("KPI Rebates", Y, "REBATES PAID · YTD", "Rebates YTD Label", "Rebates Delta Good", "Rebates Delta Bad")
    kpi("KPI Avg Copay", Y, "AVG PATIENT COPAY · YTD", "Avg Copay YTD Label", "Avg Copay Delta Good", "Avg Copay Delta Bad")


def build_charts():
    yoy, ns = U(S, "Net Sales YoY Pct"), U(S, "Net Sales YTD")
    prod, region, terr = D(S, "PRODUCT_NAME"), D(S, "REGION_NAME"), D(S, "TERRITORY_NAME")
    mon_s = MON(S, "DATE_KEY")

    # ---- Summary -------------------------------------------------------------
    q, qsales, growth = D(S, "QUARTER_LABEL"), U(S, "Total Net Sales"), U(S, "Quarter YoY Growth")
    worksheet("Quarterly Net Sales", S, title("Quarterly net sales", "label = growth vs same quarter last year"),
              mark="Bar", cols=[q], rows=[qsales], color=growth, text=[growth], show_labels=True,
              mark_styles=[diverging(growth.full)], axis_titles=[(qsales, "rows", "Net sales")],
              tooltip=[("", q), ("Net sales", qsales), ("vs same quarter last year", growth)])
    worksheet("Net Sales Map", S, title("Net sales growth by state", "YTD vs PYTD"), mark="Multipolygon",
              rows_expr=f"[{DSID[S]}].[Latitude (generated)]", cols_expr=f"[{DSID[S]}].[Longitude (generated)]",
              color=yoy, lod=[D(S, "STATE"), ns], mapsrc=True,
              geometry=f"[{DSID[S]}].[Geometry (generated)]", mark_styles=[diverging(yoy.full)],
              tooltip=[("", D(S, "STATE")), ("Net sales YTD", ns), ("YoY growth", yoy)])
    ta = D(S, "THERAPEUTIC_AREA")
    worksheet("Therapeutic Area Treemap", S, title("Net sales YTD by therapeutic area", "colour = YoY growth"),
              mark="Square", size=ns, color=yoy, text=[ta, U(S, "Net Sales YTD Label", "nk")], show_labels=True,
              mark_styles=[diverging(yoy.full)], tooltip=[("", ta), ("Net sales YTD", ns), ("YoY growth", yoy)])
    score = [ns, yoy, U(S, "Market Share YTD"), U(S, "Share Change Pts"),
             U(S, "GTN Pct YTD"), U(S, "NBRx per 100 Calls YTD")]
    worksheet("Product Scorecard", S, title("Product scorecard", "YTD"), mark="Text",
              rows=[prod], cols=[MN(S)], text=[MV(S)], filters=[("mn", None)], mn_members=score)

    # ---- Sales ---------------------------------------------------------------
    m, ma = U(S, "Selected Metric"), U(S, "Selected Metric 3M Avg")
    trend_tip = [("", mon_s), ("This month", m), ("3-month average", ma)]
    trend_panes = (
        "<pane selection-relaxation-option='selection-relaxation-allow'><view><breakdown value='auto' /></view>"
        "<mark class='Automatic' /></pane>"
        + pane_xml("Bar", {}, mark_color="#A9C6E8", attrs=f" id='1' y-axis-name='{esc(m.full)}'", tooltip=trend_tip)
        + pane_xml("Line", {}, mark_color=NAVY, attrs=f" id='2' y-axis-name='{esc(ma.full)}'", tooltip=trend_tip))
    worksheet("Sales Trend", S, [run("Monthly ", 10, INK, True), frun("[Parameters].[Metric Param]", 10, INK, True),
                                 run("   bars = month, line = 3-month moving average", 8, GRAY)],
              cols=[mon_s], rows_expr=f"({m.full} + {ma.full})", extra=[m, ma], panes=trend_panes,
              axis_style=(f"<encoding attr='space' class='0' field='{esc(ma.full)}' field-type='quantitative' "
                          "fold='true' scope='rows' synchronized='true' type='space' />"),
              hide_axes=[(ma, "rows")], axis_titles=[(m, "rows", ""), (mon_s, "cols", "")])
    worksheet("Sales by Product", S, title("Net sales YTD by product", "colour = YoY growth · click a bar to filter"),
              mark="Bar", rows=[prod], cols=[ns], color=yoy, text=[ns, yoy], show_labels=True,
              label_runs=[frun(ns.full, 9, INK, True), run("   "), frun(yoy.full, 9, GRAY)],
              sorts=[("computed", prod, ns, "DESC")], mark_styles=[diverging(yoy.full)], hide_axes=[(ns, "cols")],
              tooltip=[("", prod), ("Net sales YTD", ns), ("YoY growth", yoy)])
    share = U(S, "Market Share YTD")
    worksheet("Share Heatmap", S, title("TRx market share YTD", "region × product"), mark="Square",
              rows=[region], cols=[prod], color=share, text=[share], show_labels=True,
              mark_styles=[sequential(share.full)],
              tooltip=[("", prod), ("Region", region), ("TRx share YTD", share)])
    calls, nbrx = U(S, "Calls YTD"), U(S, "NBRx YTD")
    mpi = A(S, "MARKET_POTENTIAL_INDEX", "Avg")
    worksheet("Territory Productivity", S, title("Field productivity by territory", "size = market potential"),
              mark="Circle", cols=[calls], rows=[nbrx], color=region, size=mpi, lod=[terr], mark_styles=[palette(region)],
              axis_titles=[(calls, "cols", "Rep calls YTD"), (nbrx, "rows", "NBRx YTD")],
              tooltip=[("", terr), ("Region", region), ("Rep calls YTD", calls), ("NBRx YTD", nbrx),
                       ("NBRx per 100 calls", U(S, "NBRx per 100 Calls YTD")), ("Market potential index", mpi)])
    chg, status = U(S, "NBRx Change Post Vacancy"), D(S, "REP_STATUS")
    worksheet("Rep Vacancy Impact", S, title("Northeast NBRx after rep departures", "8 months after vs 8 before"),
              mark="Bar", rows=[terr], cols=[chg], color=status, text=[chg], show_labels=True,
              filters=[("in", D(S, "REGION_NAME"), ["Northeast"])], sorts=[("computed", terr, chg, "ASC")],
              mark_styles=[palette(status)], hide_axes=[(chg, "cols")],
              tooltip=[("", terr), ("Rep status", status), ("NBRx change", chg)])

    # ---- Patient journey -----------------------------------------------------
    funnel = [U(P, n) for n in ("1 Rx Written", "2 PA Cleared", "3 First Fill", "4 On Therapy 90D",
                                "5 On Therapy 180D", "6 On Therapy 365D")]
    worksheet("Patient Funnel", P, title("Patient journey funnel", "cohort with Rx written 15+ months ago"),
              mark="Bar", rows=[MN(P)], cols=[MV(P)], text=[MV(P)], show_labels=True, mark_color=TEAL,
              filters=[("true", D(P, "Mature Cohort")), ("mn", None)], mn_members=funnel, hide_axes=[(MV(P), "cols")])
    outcome, payer, mon_p = D(P, "PA_OUTCOME"), D(P, "PAYER_NAME"), MON(P, "DATE_KEY")
    pa_pct = PCT(P, "PATIENT_ID", "Count", outcome)
    worksheet("PA Outcome by Payer", P, title("Prior-auth outcomes by payer channel", "share of patients needing PA"),
              mark="Bar", rows=[payer], cols=[pa_pct], color=outcome, text=[pa_pct], show_labels=True,
              filters=[("except", outcome, ["Not Required"])], mark_styles=[palette(outcome)],
              hide_axes=[(pa_pct, "cols")], formats=[("label", pa_pct, "p0%"), ("tooltip", pa_pct, "p0.0%")],
              tooltip=[("", payer), ("PA outcome", outcome), ("Share of PA patients", pa_pct)])
    rate = U(P, "Abandonment Rate")
    worksheet("Abandonment Trend", P, title("Monthly abandonment rate by payer channel"), mark="Line",
              cols=[mon_p], rows=[rate], color=payer, mark_styles=[palette(payer)],
              axis_titles=[(rate, "rows", "Abandonment rate"), (mon_p, "cols", "")],
              tooltip=[("", payer), ("Month", mon_p), ("Abandonment rate", rate)])
    pers = [U(P, "Persistence 90D"), U(P, "Persistence 180D"), U(P, "Persistence 365D")]
    worksheet("Persistence by Specialty", P, title("Persistence by prescriber specialty", "90 / 180 / 365 days"),
              mark="Square", rows=[D(P, "HCP_SPECIALTY")], cols=[MN(P)], color=MV(P), text=[MV(P)], show_labels=True,
              filters=[("mn", None)], mn_members=pers, mark_styles=[sequential(MV(P))])
    reason, cnt = D(P, "DISCONTINUATION_REASON"), A(P, "PATIENT_ID", "Count")
    reason_pct = PCT(P, "PATIENT_ID", "Count", reason, 2)
    worksheet("Discontinuation Reasons", P, title("Why patients discontinue", "share of discontinuations"),
              mark="Bar", rows=[reason], cols=[cnt], text=[reason_pct], show_labels=True, mark_color=BAR,
              filters=[("except", reason, ["Not applicable"])], sorts=[("computed", reason, cnt, "DESC")],
              hide_axes=[(cnt, "cols")], formats=[("label", reason_pct, "p0%"), ("tooltip", reason_pct, "p0.0%")],
              tooltip=[("", reason), ("Patients", cnt), ("Share", reason_pct)])

    # ---- Payer ----------------------------------------------------------------
    tier, plan, ypayer, yprod = D(Y, "TIER_NAME"), D(Y, "PLAN_NAME"), D(Y, "PAYER_NAME"), D(Y, "PRODUCT_NAME")
    restr = D(Y, "Restrictions")
    worksheet("Formulary Matrix", Y, title("Formulary status, latest month", "PA = prior auth · ST = step therapy · QL = quantity limit"),
              mark="Square", rows=[ypayer, plan], cols=[yprod], color=tier, text=[restr], show_labels=True,
              filters=[("true", D(Y, "Is Latest Month"))], mark_styles=[palette(tier)],
              tooltip=[("", plan), ("Payer channel", ypayer), ("Product", yprod), ("Tier", tier), ("Restrictions", restr)])
    score_f, vs = U(Y, "Plan Access Score"), U(Y, "Access Score vs Company")
    worksheet("Plan Access Score", Y, title("Plan access score", "colour = vs company average · click a bar to filter"),
              mark="Bar", rows=[plan], cols=[score_f], color=vs, text=[score_f], show_labels=True,
              sorts=[("computed", plan, score_f, "DESC")], mark_styles=[diverging(vs.full)], hide_axes=[(score_f, "cols")],
              tooltip=[("", plan), ("Access score", score_f), ("vs company average", vs)])
    mon_y, rej = MON(Y, "DATE_KEY"), U(Y, "Claim Rejection Rate")
    worksheet("Claim Rejection Trend", Y, title("Monthly claim rejection rate by payer channel"), mark="Line",
              cols=[mon_y], rows=[rej], color=ypayer, mark_styles=[palette(ypayer)],
              axis_titles=[(rej, "rows", "Claim rejection rate"), (mon_y, "cols", "")],
              tooltip=[("", ypayer), ("Month", mon_y), ("Claim rejection rate", rej)])
    reb, appr = U(Y, "Avg Rebate Pct"), U(Y, "Claim Approval Rate")
    lives = A(Y, "PLAN_LIVES_K (DIM_PAYER_PLAN)", "Avg")
    worksheet("Rebate vs Approval", Y, title("Rebate rate vs claim approval by plan", "YTD · size = plan lives"),
              mark="Circle", cols=[reb], rows=[appr], color=ypayer, size=lives, text=[plan], show_labels=True,
              filters=[("true", D(Y, "Is YTD"))], mark_styles=[palette(ypayer)],
              axis_titles=[(reb, "cols", "Average rebate"), (appr, "rows", "Claim approval rate")],
              tooltip=[("", plan), ("Payer channel", ypayer), ("Average rebate", reb), ("Claim approval", appr),
                       ("Plan lives (K)", lives)])
    pbm, tat = D(Y, "PBM_NAME"), U(Y, "Weighted PA Turnaround")
    worksheet("PA Turnaround by PBM", Y, title("PA turnaround by PBM", "days, weighted by PA volume, YTD"),
              mark="Bar", rows=[pbm], cols=[tat], text=[tat], show_labels=True, mark_color=BAR,
              filters=[("true", D(Y, "Is YTD")), ("except", pbm, ["%null%"])], sorts=[("computed", pbm, tat, "DESC")],
              hide_axes=[(tat, "cols")], formats=[("label", tat, 'n0.0" days"'), ("tooltip", tat, 'n0.0" days"')],
              tooltip=[("", pbm), ("Average PA turnaround", tat)])


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------
W, H = 1500, 950
CHAT_WIDTH = 380
CHAT_TREX = Path(__file__).resolve().parent.parent / "tableau_extension" / "cortex_chat_pharma.trex"


def chat_extension():
    """(extension id, url, manifest XML) from the .trex used for the dashboards' chat panel."""
    text = CHAT_TREX.read_text(encoding="utf-8")
    ext_id = re.search(r'<dashboard-extension id="([^"]+)"', text).group(1)
    url = html.unescape(re.search(r"<url>([^<]+)</url>", text).group(1))
    inner = re.search(r"<manifest[^>]*>(.*)</manifest>", text, re.S).group(1)
    return ext_id, url, inner


def zstyle(kind=None):
    """Zone styles: 'card' (white panel, thin border), 'header' (navy band), 'plain' (inside a card)."""
    border = {"card": f"<format attr='border-color' value='{CARD_BORDER}' /><format attr='border-style' value='solid' />"
                      "<format attr='border-width' value='1' />"}.get(kind,
              "<format attr='border-color' value='#000000' /><format attr='border-style' value='none' />"
              "<format attr='border-width' value='0' />")
    margin, padding, bg = {"card": (5, 6, "#FFFFFF"), "plain": (0, 0, "#FFFFFF"), "header": (0, 10, NAVY),
                           "page": (0, 0, PAGE_BG)}.get(kind, (0, 0, None))
    bg_fmt = f"<format attr='background-color' value='{bg}' />" if bg else ""
    return (f"<zone-style>{border}<format attr='margin' value='{margin}' /><format attr='padding' value='{padding}' />"
            f"{bg_fmt}</zone-style>")


def V(*kids, style=None):
    return ("vert", kids, style)


def Hz(*kids, style=None):
    return ("horz", kids, style)


def build_zones(tree):
    counter = [0]
    ext_id, url, _ = chat_extension()

    def nid():
        counter[0] += 1
        return counter[0]

    def u(v, total):
        return int(round(v * 100000 / total))

    def place(node, x, y, w, h, fixed=None):
        kind = node[0]
        geo = f"h='{u(h, H)}' id='{nid()}' w='{u(w, W)}' x='{u(x, W)}' y='{u(y, H)}'"
        fx = f" fixed-size='{fixed}' is-fixed='true'" if fixed else ""
        if kind in ("vert", "horz"):
            kids, style = node[1], node[2]
            span = h if kind == "vert" else w
            fixed_total = sum(k[1] for k in kids if k[0] == "fix")
            weights = sum(k[1] for k in kids if k[0] == "w")
            pos, inner = (y if kind == "vert" else x), ""
            for sz_kind, sz, child in kids:
                size = sz if sz_kind == "fix" else (span - fixed_total) * sz / weights
                if kind == "vert":
                    inner += place(child, x, pos, w, size, sz if sz_kind == "fix" else None)
                else:
                    inner += place(child, pos, y, size, h, sz if sz_kind == "fix" else None)
                pos += size
            return f"<zone {geo}{fx} param='{kind}' type-v2='layout-flow'>{inner}{zstyle(style)}</zone>"
        if kind == "sheet":
            st = " show-title='false'" if node[1].startswith("KPI ") else ""
            return f"<zone {geo}{fx} name='{esc(node[1])}'{st}>{zstyle(node[2])}</zone>"
        if kind == "text":
            return f"<zone {geo}{fx} type-v2='text'><formatted-text>{node[1]}</formatted-text>{zstyle(node[2])}</zone>"
        if kind == "param":
            return f"<zone {geo}{fx} mode='compact' param='[Parameters].[{node[1]}]' type-v2='paramctrl'>{zstyle('plain')}</zone>"
        if kind == "legend":
            return (f"<zone {geo}{fx} leg-item-layout='horz' name='{esc(node[1])}' pane-specification-id='0' "
                    f"param='{esc(node[2])}' show-title='false' type-v2='color'>{zstyle('plain')}</zone>")
        if kind == "chat":
            return (f"<zone forceUpdate='true' {geo}{fx} param='{esc(f'[{ext_id}].[1.0.0].[{url}]')}' type-v2='dashboard-object'>"
                    f"<add-in add-in-id='{esc(ext_id)}' extension-url='{esc(url)}' extension-version='1.0.0' "
                    f"instance-id='{uuid.uuid4().hex.upper()}'><instance-settings /><type-settings><dashboard />"
                    f"</type-settings></add-in>{zstyle('card')}</zone>")
        raise ValueError(kind)

    body = place(V(*tree), 0, 0, W, H)
    return (f"<zones><zone h='100000' id='{nid()}' type-v2='layout-basic' w='100000' x='0' y='0'>{body}"
            f"{zstyle('page')}</zone></zones>")


def sheet(n, weight=1):
    return ("w", weight, ("sheet", n, "card"))


def with_legend(n, field, weight=1):
    """Chart and its colour legend share one card."""
    return ("w", weight, V(("w", 1, ("sheet", n, "plain")), ("fix", 26, ("legend", n, field)), style="card"))


def page(title_text, params, kpis, row_a, row_b):
    """Navy header band; body = content column (filters, KPI cards, two chart rows) + chat column."""
    head = (f"<run bold='true' fontcolor='#FFFFFF' fontsize='16'>{esc(title_text)}</run>"
            f"<run fontcolor='#C9D6E8' fontsize='9'>{esc('    ' + SUB)}</run>")
    filter_bar = Hz(*[("fix", 250, ("param", p)) for p in params],
                    ("w", 1, ("text", run("Filters apply to every chart and to the Cortex Chat panel", 8, GRAY), "plain")),
                    style="card")
    content = V(("fix", 46, filter_bar),
                ("fix", 100, Hz(*[("w", 1, ("sheet", k, "card")) for k in kpis])),
                ("w", 1, row_a), ("w", 1, row_b), style=None)
    return [("fix", 52, ("text", head, "header")),
            ("w", 1, Hz(("w", 1, content), ("fix", CHAT_WIDTH, ("chat",)), style=None))]


SUB = "YTD = Jan to latest data month (Sep 2026) vs the same months last year"
DASHBOARDS = [
    ("Summary Overview", ["TA Param"], page(
        "Pharma Commercial Overview", ["TA Param"],
        ["KPI Net Sales", "KPI Market Share", "KPI New Patients", "KPI Abandonment", "KPI Claim Approval", "KPI Rebates"],
        Hz(sheet("Quarterly Net Sales", 1.2), sheet("Net Sales Map", 1)),
        Hz(sheet("Therapeutic Area Treemap", 1), sheet("Product Scorecard", 1.4)))),
    ("Sales Performance", ["TA Param", "Metric Param"], page(
        "Sales Performance", ["TA Param", "Metric Param"],
        ["KPI Net Sales", "KPI TRx", "KPI NBRx", "KPI Market Share", "KPI GTN"],
        Hz(sheet("Sales Trend", 1.45), sheet("Sales by Product", 1)),
        Hz(sheet("Share Heatmap", 1.25),
           with_legend("Territory Productivity", f"[{DSID[S]}].[none:REGION_NAME:nk]"),
           with_legend("Rep Vacancy Impact", f"[{DSID[S]}].[none:REP_STATUS:nk]", 0.9)))),
    ("Patient Journey", ["TA Param"], page(
        "Patient Journey", ["TA Param"],
        ["KPI New Patients", "KPI Abandonment", "KPI PA Denial", "KPI Days to Fill", "KPI Persistence", "KPI High Risk HCPs"],
        Hz(sheet("Patient Funnel", 1), with_legend("PA Outcome by Payer", f"[{DSID[P]}].[none:PA_OUTCOME:nk]", 1.15)),
        Hz(with_legend("Abandonment Trend", f"[{DSID[P]}].[none:PAYER_NAME:nk]", 1.2),
           sheet("Persistence by Specialty", 1), sheet("Discontinuation Reasons", 1)))),
    ("Payer Access", ["TA Param"], page(
        "Payer & Market Access", ["TA Param"],
        ["KPI Coverage", "KPI Claim Approval", "KPI PA Approval", "KPI Rebates", "KPI Avg Copay"],
        Hz(with_legend("Formulary Matrix", f"[{DSID[Y]}].[none:TIER_NAME:nk]", 1.45), sheet("Plan Access Score", 1)),
        Hz(with_legend("Claim Rejection Trend", f"[{DSID[Y]}].[none:PAYER_NAME:nk]", 1.15),
           with_legend("Rebate vs Approval", f"[{DSID[Y]}].[none:PAYER_NAME:nk]"),
           sheet("PA Turnaround by PBM", 0.8)))),
]


def dashboard_xml(name, params, tree):
    deps = "".join(PARAMS[p] for p in params)
    return (f"<dashboard name='{esc(name)}'><style />"
            f"<size maxheight='{H}' maxwidth='{W}' minheight='{H}' minwidth='{W}' sizing-mode='fixed' />"
            f"<datasources><datasource name='Parameters' /></datasources>"
            f"<datasource-dependencies datasource='Parameters'>{deps}</datasource-dependencies>"
            f"{build_zones(tree)}<simple-id uuid='{guid()}' /></dashboard>")


def referenced_extensions():
    """Workbook-level record of the chat extension, as Tableau writes it after adding one."""
    _, _, manifest = chat_extension()
    views = "".join(f"<referenced-view instances='1' viewId='{esc(n)}' />" for n, _, _ in DASHBOARDS)
    return (f"<referenced-extensions><referenced-extension><manifest manifest-version='0.1'>{manifest}</manifest>"
            f"<referenced-views>{views}</referenced-views></referenced-extension></referenced-extensions>")


def action(caption, dashboard, source, exclude=None):
    ex = f"<param name='exclude' value='{esc(exclude)}' />" if exclude else ""
    return (f"<action caption='{esc(caption)}' name='[Action_{uuid.uuid4().hex.upper()}]'>"
            "<activation auto-clear='true' type='on-select' />"
            f"<source dashboard='{esc(dashboard)}' type='sheet' worksheet='{esc(source)}' />"
            f"<command command='tsc:tsl-filter'>{ex}<param name='special-fields' value='all' />"
            f"<param name='target' value='{esc(dashboard)}' /></command></action>")


CARDS = ("<cards><edge name='left'><strip size='160'><card type='pages' /><card type='filters' /><card type='marks' />"
         "</strip></edge><edge name='top'><strip size='2147483647'><card type='columns' /></strip>"
         "<strip size='2147483647'><card type='rows' /></strip><strip size='31'><card type='title' /></strip></edge></cards>")


def main(src, out):
    text = open(src, encoding="utf-8").read()
    text = edit_ds(text, "Y", add_payer_plan)
    text = edit_ds(text, "S", lambda b: b.replace(
        "<column caption='State' datatype='string' name='[STATE]' role='dimension' type='nominal' />",
        "<column caption='State' datatype='string' name='[STATE]' role='dimension' semantic-role='[State].[Name]' type='nominal' />"))

    for k in DSID:
        s, e = ds_span(text, k)
        block = text[s:e]
        BASE[k] = {}
        for m in re.finditer(r"^      (<column caption='[^']*' datatype='(\w+)' name='\[([^\]]+)\]'[^>]*/>)$", block, re.M):
            if m.group(2) != "table":
                BASE[k][m.group(3)] = m.group(1)
        # a calc named like an existing field's caption stops Tableau from loading the workbook
        captions = {c.lower() for c in re.findall(r"<column caption='([^']*)'[^>]*/>", block)}
        clash = sorted(n for n in CALCS[k] if n.lower() in captions)
        if clash:
            raise SystemExit(f"{DSCAP[k]}: calculated field name(s) {clash} match an existing field caption; rename them")
        # fields left at their defaults only appear as metadata records
        for loc, typ in re.findall(r"<local-name>\[([^\]]+)\]</local-name>.*?<local-type>(\w+)</local-type>", block, re.S):
            if loc not in BASE[k]:
                role, t = (("measure", "quantitative") if typ in ("integer", "real")
                           else ("dimension", "ordinal" if typ == "date" else "nominal"))
                BASE[k][loc] = f"<column datatype='{typ}' name='[{loc}]' role='{role}' type='{t}' />"
        # drop this script's calcs from an earlier run (in our format or as Tableau re-saved them),
        # then add the current ones; any other calculated fields are left alone
        for calc_name in CALCS[k]:
            block = re.sub(r"[ \t]*<column [^>]*name='\[" + re.escape(esc(calc_name)) + r"\]'[^>]*>\s*"
                           r"<calculation class='tableau'.*?</column>\r?\n?", "", block, flags=re.S)
        calcs = "".join(f"      {calc_xml(n, c)}\n" for n, c in CALCS[k].items())
        # columns must come before any <group> (Tableau adds one for filter actions) and <layout>
        i = min(block.index(tag) for tag in ("      <group ", "      <layout ") if tag in block)
        text = text[:s] + block[:i] + calcs + block[i:] + text[e:]

    # manual-sort / computed-sort elements are only valid with this format flag
    if "<Extensions />" not in text:  # needed once a dashboard holds an extension
        text = text.replace("  </document-format-change-manifest>", "    <Extensions />\n  </document-format-change-manifest>", 1)
    if "<SortTagCleanup />" not in text:
        text = text.replace("  </document-format-change-manifest>", "    <SortTagCleanup />\n  </document-format-change-manifest>", 1)
    if "name='Parameters'" not in text:
        text = text.replace("  <datasources>\n", "  <datasources>\n" + PARAM_DS, 1)

    build_kpis()
    build_charts()
    names =[n for n, _ in WORKSHEETS]
    assert len(names) == len(set(names)), "duplicate worksheet names"
    for dname, _, tree in DASHBOARDS:
        for leaf in re.findall(r"\('sheet', '([^']+)'", repr(tree)):
            assert leaf in names, f"{dname}: missing sheet {leaf}"

    head = text[: text.index("</datasources>") + len("</datasources>")]
    actions = ("<actions>" + action("Filter by product", "Sales Performance", "Sales by Product")
               + action("Filter by state", "Summary Overview", "Net Sales Map",
                        exclude="KPI Claim Approval,KPI Rebates")
               + action("Filter by plan", "Payer Access", "Plan Access Score") + "</actions>")
    worksheets = "<worksheets>" + "".join(x for _, x in WORKSHEETS) + "</worksheets>"
    dashboards = "<dashboards>" + "".join(dashboard_xml(n, p, t) for n, p, t in DASHBOARDS) + "</dashboards>"
    windows = "<windows saved-dpi-scale-factor='1.5' source-height='106'>"
    for i, (dname, _, tree) in enumerate(DASHBOARDS):
        mx = " maximized='true'" if i == 0 else ""
        vps = "".join(f"<viewpoint name='{esc(s)}'><zoom type='entire-view' /></viewpoint>"
                      for s in dict.fromkeys(re.findall(r"\('sheet', '([^']+)'", repr(tree))))
        windows += (f"<window class='dashboard'{mx} name='{esc(dname)}'><viewpoints>{vps}</viewpoints><active id='-1' />"
                    "<device-preview><device is-portrait='true' name='Generic Phone' type='Phone' /></device-preview>"
                    f"<simple-id uuid='{guid()}' /></window>")
    for n in names:
        windows += f"<window class='worksheet' hidden='true' name='{esc(n)}'>{CARDS}<simple-id uuid='{guid()}' /></window>"
    windows += "</windows>"
    tail = ("\n  <mapsources><mapsource name='Tableau' /></mapsources>\n  " + actions + "\n  " + worksheets
            + "\n  " + dashboards + "\n  " + windows + "\n  " + referenced_extensions() + "\n</workbook>\n")
    result = head + tail
    xml.dom.minidom.parseString(result.encode("utf-8"))  # well-formedness check
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(result)
    calc_count = sum(len(v) for v in CALCS.values())
    print(f"wrote {out}: {len(names)} worksheets, {len(DASHBOARDS)} dashboards, {calc_count} calculated fields")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
