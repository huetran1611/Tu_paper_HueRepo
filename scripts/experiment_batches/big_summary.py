import csv
import math
import re
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

results_dir = Path("collected_big_instance_dd227")
detail_csv = Path("big_instance_dd227_all_runs.csv")
best_csv = Path("big_instance_dd227_best_summary.csv")
xlsx_path = Path("big_instance_dd227_summary.xlsx")

def natural_key(value):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", str(value))]

def read_value(text, label):
    match = re.search(rf"^{re.escape(label)}:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
    return match.group(1) if match else ""

def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return ""

def read_elapsed_seconds(text):
    value = read_value(text, "Mean elapsed time")
    value = value.removesuffix(" seconds").strip()
    return as_float(value)

def extract_section(text, start_label, end_label=None):
    if start_label not in text:
        return ""
    section = text.split(start_label, 1)[1]
    if end_label and end_label in section:
        section = section.split(end_label, 1)[0]
    return section.strip()

def route_customers(route_text):
    customers = []
    for line in route_text.splitlines():
        if ":" not in line:
            continue
        body = line.split(":", 1)[1].split("|", 1)[0]
        for token in re.findall(r"\d+", body):
            customer = int(token)
            if customer != 0:
                customers.append(customer)
    return customers

rows = []
result_files = sorted(
    results_dir.rglob("*.Dd227.run*.txt"),
    key=lambda path: natural_key(str(path)),
)
for path in result_files:
    if not re.search(r"\.Dd227\.run[12345]\.txt$", path.name):
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    scenario = read_value(text, "Scenario")
    instance = read_value(text, "Instance")
    run = read_value(text, "Run")
    customer_count = int(instance.split(".", 1)[0])
    best_obj = as_float(read_value(text, "Improved solution cost"))
    elapsed_seconds = read_elapsed_seconds(text)
    feasibility = read_value(text, "Final solution feasibility")
    truck_routes = extract_section(text, "Truck Routes:\n", "\nDrone Routes:")
    drone_routes = extract_section(text, "Drone Routes:\n", "\nTotal validation:")
    drone_customers = set(route_customers(drone_routes))
    drone_ratio = len(drone_customers) / customer_count if customer_count else ""
    rows.append({
        "scenario": scenario,
        "instance": instance,
        "run": int(run) if run else "",
        "best_obj": best_obj,
        "elapsed_seconds": elapsed_seconds,
        "feasibility": feasibility,
        "drone_customer_count": len(drone_customers),
        "customer_count": customer_count,
        "drone_ratio": drone_ratio,
        "truck_routes": truck_routes,
        "drone_routes": drone_routes,
        "result_file": str(path),
    })

if len(rows) != 320:
    raise SystemExit(f"Expected 320 parsed rows, found {len(rows)}")

detail_fields = [
    "scenario", "instance", "run", "best_obj", "elapsed_seconds", "feasibility",
    "drone_customer_count", "customer_count", "drone_ratio",
    "truck_routes", "drone_routes", "result_file",
]
with detail_csv.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=detail_fields)
    writer.writeheader()
    writer.writerows(rows)

best_rows = []
groups = defaultdict(list)
for row in rows:
    groups[(row["scenario"], row["instance"])].append(row)
for (scenario, instance), group in sorted(groups.items(), key=lambda item: (item[0][0], natural_key(item[0][1]))):
    if len(group) != 5 or {row["run"] for row in group} != set(range(1, 6)):
        raise ValueError(f"Expected five distinct runs for {scenario}/{instance}")
    feasible = [row for row in group if row["feasibility"] == "FEASIBLE" and isinstance(row["best_obj"], float)]
    candidates = feasible or [row for row in group if isinstance(row["best_obj"], float)]
    best = min(candidates, key=lambda row: row["best_obj"]) if candidates else group[0]
    numeric_objs = [row["best_obj"] for row in group if isinstance(row["best_obj"], float)]
    numeric_elapsed = [row["elapsed_seconds"] for row in group if isinstance(row["elapsed_seconds"], float)]
    best_rows.append({
        "scenario": scenario,
        "instance": instance,
        "best_run": best["run"],
        "best_obj": best["best_obj"],
        "best_elapsed_seconds": best["elapsed_seconds"],
        "mean_obj_5_runs": statistics.fmean(numeric_objs) if numeric_objs else "",
        "mean_elapsed_seconds_5_runs": statistics.fmean(numeric_elapsed) if numeric_elapsed else "",
        "feasible_runs": sum(row["feasibility"] == "FEASIBLE" for row in group),
        "drone_customer_count": best["drone_customer_count"],
        "customer_count": best["customer_count"],
        "drone_ratio": best["drone_ratio"],
        "truck_routes": best["truck_routes"],
        "drone_routes": best["drone_routes"],
        "result_file": best["result_file"],
    })

best_fields = [
    "scenario", "instance", "best_run", "best_obj", "best_elapsed_seconds",
    "mean_obj_5_runs", "mean_elapsed_seconds_5_runs",
    "feasible_runs", "drone_customer_count", "customer_count",
    "drone_ratio", "truck_routes", "drone_routes", "result_file",
]
with best_csv.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=best_fields)
    writer.writeheader()
    writer.writerows(best_rows)

def col_name(index):
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result

def cell_xml(row_index, col_index, value, style=0):
    ref = f"{col_name(col_index)}{row_index}"
    style_attr = f' s="{style}"' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escape(str(value))}</t></is></c>'

def worksheet(headers, data_rows):
    wrapped = {i for i, h in enumerate(headers, 1) if h in {"truck_routes", "drone_routes"}}
    xml_rows = []
    for row_index, values in enumerate([headers, *data_rows], 1):
        cells = "".join(
            cell_xml(row_index, col_index, value, 1 if row_index == 1 else (2 if col_index in wrapped else 0))
            for col_index, value in enumerate(values, 1)
        )
        xml_rows.append(f'<row r="{row_index}">{cells}</row>')
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{55 if h in {"truck_routes", "drone_routes"} else 18}" customWidth="1"/>'
        for i, h in enumerate(headers, 1)
    )
    last = f"{col_name(len(headers))}{len(data_rows) + 1}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
        f'<autoFilter ref="A1:{last}"/></worksheet>'
    )

sheets = [
    ("Best", worksheet(best_fields, [[row[field] for field in best_fields] for row in best_rows])),
    ("All Runs", worksheet(detail_fields, [[row[field] for field in detail_fields] for row in rows])),
]
content_types = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
    + '</Types>'
)
workbook = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
    + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
    + '</sheets></workbook>'
)
workbook_rels = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
    + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '</Relationships>'
)
styles = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf></cellXfs>'
    '</styleSheet>'
)
with zipfile.ZipFile(xlsx_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", content_types)
    z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    z.writestr("xl/workbook.xml", workbook)
    z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
    z.writestr("xl/styles.xml", styles)
    for i, (_, xml) in enumerate(sheets, 1):
        z.writestr(f"xl/worksheets/sheet{i}.xml", xml)

print(f"Wrote {best_csv}, {detail_csv}, and {xlsx_path}")
