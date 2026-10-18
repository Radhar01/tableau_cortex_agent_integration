"""Re-flow the four pharma dashboards in a .twbx to a clean, consistent layout.

Only zone geometry changes (x / y / w / h / fixed-size) plus the fit mode of charts listed in
FIT_WIDTH; sheets, the chat extension, styles, data sources and actions are left untouched.
The dashboard's own canvas size (e.g. 1700 x 800) is kept and the layout is computed for it.

Usage: python relayout_dashboards.py <in.twbx> <out.twbx>
"""
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

HEADER, FILTER_BAR, KPI_ROW, LEGEND, PARAM = 46, 44, 96, 26, 250
CHAT_WIDTH = 420

# Relative widths of the charts in each row (keyed by the first sheet in a card), and row heights.
WEIGHTS = {
    "Quarterly Net Sales": 1.25, "Net Sales Map": 1,
    "Therapeutic Area Treemap": 0.8, "Product Scorecard": 1.7,  # scorecard has 6 measure columns
    "Sales Trend": 1.4, "Sales by Product": 1,
    "Share Heatmap": 1.2, "Territory Productivity": 1.05, "Rep Vacancy Impact": 0.95,
    "Patient Funnel": 1, "PA Outcome by Payer": 1.15,
    "Abandonment Trend": 1.25, "Persistence by Specialty": 1, "Discontinuation Reasons": 1.05,
    "Formulary Matrix": 1.45, "Plan Access Score": 1,
    "Claim Rejection Trend": 1.15, "Rebate vs Approval": 1, "PA Turnaround by PBM": 0.85,
}
ROW_WEIGHTS = {"Sales Performance": (1, 1.1)}
# Too many categories to label inside an 800px-tall canvas: fit the width and scroll vertically.
FIT_WIDTH = {"Sales Performance": ["Rep Vacancy Impact"]}


def first_sheet(z):
    for e in z.iter("zone"):
        if e.get("name") and not e.get("type-v2"):
            return e.get("name")
    return None


def plan(dashboard, W, H):
    """Return {zone id: (x, y, w, h, fixed)} in Tableau's 0-100000 units."""
    out = {}
    u = lambda v, total: int(round(v * 100000 / total))

    def put(z, x, y, w, h, fixed=None):
        out[z.get("id")] = (u(x, W), u(y, H), u(w, W), u(h, H), fixed)

    def sizes(kids, span, fixed_of, weight_of):
        fixed_total = sum(fixed_of(k) or 0 for k in kids)
        weights = sum(weight_of(k) for k in kids if not fixed_of(k)) or 1
        return [(fixed_of(k), fixed_of(k) or (span - fixed_total) * weight_of(k) / weights) for k in kids]

    def card(z, x, y, w, h):
        """A sheet, or a vertical card holding a sheet and its colour legend."""
        put(z, x, y, w, h)
        if z.get("type-v2") == "layout-flow":
            kids = z.findall("zone")
            pos = y
            for k, (fx, size) in zip(kids, sizes(kids, h, lambda k: LEGEND if k.get("type-v2") == "color" else None,
                                                  lambda k: 1)):
                put(k, x, pos, w, size, fx)
                pos += size

    root = dashboard.find("zones").find("zone")                     # layout-basic
    put(root, 0, 0, W, H)
    page = root.find("zone")                                         # vert: header + body
    put(page, 0, 0, W, H)
    header, body = page.findall("zone")
    put(header, 0, 0, W, HEADER, HEADER)
    bh = H - HEADER
    put(body, 0, HEADER, W, bh)
    content, chat = body.findall("zone")
    cw = W - CHAT_WIDTH
    put(content, 0, HEADER, cw, bh)
    put(chat, cw, HEADER, CHAT_WIDTH, bh, CHAT_WIDTH)

    filters, kpis, row_a, row_b = content.findall("zone")
    y = HEADER
    put(filters, 0, y, cw, FILTER_BAR, FILTER_BAR)
    x = 0
    for k, (fx, size) in zip(filters.findall("zone"), sizes(
            filters.findall("zone"), cw, lambda k: PARAM if k.get("type-v2") == "paramctrl" else None, lambda k: 1)):
        put(k, x, y, size, FILTER_BAR, fx)
        x += size
    y += FILTER_BAR
    put(kpis, 0, y, cw, KPI_ROW, KPI_ROW)
    tiles = kpis.findall("zone")
    for i, k in enumerate(tiles):
        put(k, cw * i / len(tiles), y, cw / len(tiles), KPI_ROW)
    y += KPI_ROW

    rows_h = H - y
    wa, wb = ROW_WEIGHTS.get(dashboard.get("name"), (1, 1))
    for row, rh in ((row_a, rows_h * wa / (wa + wb)), (row_b, rows_h * wb / (wa + wb))):
        put(row, 0, y, cw, rh)
        x = 0
        kids = row.findall("zone")
        for k, (_, size) in zip(kids, sizes(kids, cw, lambda k: None, lambda k: WEIGHTS.get(first_sheet(k), 1))):
            card(k, x, y, size, rh)
            x += size
        y += rh
    return out


def apply(text, name, geometry):
    start = text.index(f"<dashboard name='{name}'")
    end = text.index("</dashboard>", start)
    block = text[start:end]

    def fix(m):
        tag = m.group(0)
        zid = re.search(r" id='(\d+)'", tag).group(1)
        if zid not in geometry:
            return tag
        x, y, w, h, fixed = geometry[zid]
        for attr, val in (("x", x), ("y", y), ("w", w), ("h", h)):
            tag = re.sub(rf" {attr}='-?\d+'", f" {attr}='{val}'", tag)
        tag = re.sub(r" fixed-size='\d+'| is-fixed='true'", "", tag)
        if fixed:
            tag = tag.replace("<zone ", f"<zone fixed-size='{int(round(fixed))}' is-fixed='true' ", 1)
        return tag

    return text[:start] + re.sub(r"<zone [^>]*>", fix, block) + text[end:]


def set_fit(text, dashboard, sheet, fit):
    start = text.index(f"<window class='dashboard'", text.index("<windows"))
    w = re.search(rf"<window class='dashboard'[^>]*name='{re.escape(dashboard)}'.*?</window>", text[start:], re.S)
    s, e = start + w.start(), start + w.end()
    block = re.sub(rf"(<viewpoint name='{re.escape(sheet)}'>\s*<zoom type=')[^']*(')", rf"\g<1>{fit}\2", text[s:e])
    return text[:s] + block + text[e:]


def main(src, dst):
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        twb = next(n for n in names if n.endswith(".twb"))
        files = {n: z.read(n) for n in names}
    text = files[twb].decode("utf-8")
    root = ET.fromstring(files[twb])
    for d in root.find("dashboards").findall("dashboard"):
        size = d.find("size")
        W, H = int(size.get("maxwidth")), int(size.get("maxheight"))
        text = apply(text, d.get("name"), plan(d, W, H))
        for sheet in FIT_WIDTH.get(d.get("name"), []):
            text = set_fit(text, d.get("name"), sheet, "fit-width")
        print(f"{d.get('name')}: re-laid out for {W}x{H}, chat {CHAT_WIDTH}px")
    ET.fromstring(text.encode("utf-8"))  # still well-formed
    files[twb] = text.encode("utf-8")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, files[n])
    print(f"wrote {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
