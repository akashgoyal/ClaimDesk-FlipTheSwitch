#!/usr/bin/env python3
"""Build a T&E expense workbook from the claim the room's agents agreed on.

The agents read bills and talk to people. This script does the arithmetic, so
a total can never come from a language model. It refuses to build a sheet when
the items of a bill do not add up to the total printed on that bill, when a
foreign amount has no rate, or when the same item is claimed twice.

Usage: build_expense_sheet.py claim.json expense-sheet.xlsx
Standard library only.
"""
import json
import sys
import zipfile
from collections import OrderedDict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.sax.saxutils import escape

CATEGORIES = ("flight", "train", "taxi", "hotel", "meal", "tax", "other")
STATUSES = ("approved", "rejected")
CENT = Decimal("0.01")


class ClaimError(ValueError):
    """The claim cannot become a sheet. The message says what to fix."""


def money(value, where):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ClaimError(f"{where}: {value!r} is not a number") from None
    if not amount.is_finite():
        raise ClaimError(f"{where}: {value!r} is not a number")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_day(value, where):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ClaimError(f"{where}: {value!r} is not a YYYY-MM-DD date") from None


def validate(claim):
    """Return (lines, totals) or raise ClaimError listing every problem."""
    problems = []
    for key in ("employee", "trip", "base_currency", "approved_by", "bills", "lines"):
        if not claim.get(key):
            problems.append(f"missing {key}")
    if problems:
        raise ClaimError("; ".join(problems))

    base = claim["base_currency"]
    seen = set()
    by_bill = {}
    lines = []
    for index, raw in enumerate(claim["lines"], 1):
        where = f"line {index} ({raw.get('bill', '?')})"
        try:
            for key in ("bill", "merchant", "description", "currency"):
                if not raw.get(key):
                    raise ClaimError(f"{where}: missing {key}")
            if raw.get("category") not in CATEGORIES:
                raise ClaimError(f"{where}: category must be one of {', '.join(CATEGORIES)}")
            if raw.get("status") not in STATUSES:
                raise ClaimError(f"{where}: status must be approved or rejected")
            day = parse_day(raw.get("date"), where)
            amount = money(raw.get("amount"), where)
            rate = Decimal("1")
            if raw["currency"] != base:
                if not raw.get("fx_rate"):
                    raise ClaimError(f"{where}: {raw['currency']} needs an fx_rate to {base}")
                rate = Decimal(str(raw["fx_rate"]))
            base_amount = (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        except ClaimError as err:
            problems.append(str(err))
            continue
        by_bill[raw["bill"]] = by_bill.get(raw["bill"], Decimal("0")) + amount
        if raw["status"] == "approved":
            key = (raw.get("invoice") or "", raw["description"], amount)
            if key[0] and key in seen:
                problems.append(f"{where}: {key[0]} / {raw['description']} is claimed twice")
            seen.add(key)
        lines.append({**raw, "day": day, "amount": amount, "base_amount": base_amount})

    for bill, printed in claim["bills"].items():
        printed = money(printed, f"bill {bill}")
        got = by_bill.get(bill)
        if got is None:
            problems.append(f"bill {bill}: printed total {printed} has no lines")
        elif got != printed:
            problems.append(f"bill {bill}: lines add up to {got}, the bill says {printed}")
    for bill in by_bill:
        if bill not in claim["bills"]:
            problems.append(f"bill {bill}: lines exist but no printed total was given")
    if problems:
        raise ClaimError("; ".join(problems))

    lines.sort(key=lambda line: (line["day"], line["bill"]))
    totals = OrderedDict((c, Decimal("0")) for c in CATEGORIES)
    for line in lines:
        if line["status"] == "approved":
            totals[line["category"]] += line["base_amount"]
    return lines, totals


# Minimal XLSX writing: three styles and inline strings are all a sheet needs.
BOLD, NUMBER, BOLD_NUMBER = 1, 2, 3


def column(index):
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def cell(ref, value, style=0):
    attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{attr}/>'
    if isinstance(value, Decimal):
        return f'<c r="{ref}"{attr}><v>{value}</v></c>'
    if isinstance(value, tuple):  # (formula, cached value)
        return f'<c r="{ref}"{attr}><f>{escape(value[0])}</f><v>{value[1]}</v></c>'
    return f'<c r="{ref}"{attr} t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def sheet_xml(rows, widths):
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, item in enumerate(row, 1):
            value, style = item if isinstance(item, list) else (item, 0)
            cells.append(cell(f"{column(c)}{r}", value, style))
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<cols>{cols}</cols><sheetData>{"".join(body)}</sheetData></worksheet>')


def write_xlsx(path, sheets):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1))
    files = {
        "[Content_Types].xml": f'{head}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'{overrides}</Types>',
        "_rels/.rels": f'{head}<Relationships xmlns="{pkg}"><Relationship Id="rId1" '
            f'Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'{head}<workbook {ns} xmlns:r="{rel}"><sheets>' + "".join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _, _) in enumerate(sheets, 1))
            + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": f'{head}<Relationships xmlns="{pkg}">' + "".join(
            f'<Relationship Id="rId{i}" Type="{rel}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="{rel}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": f'{head}<styleSheet {ns}><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/></cellXfs></styleSheet>',
    }
    for i, (_, rows, widths) in enumerate(sheets, 1):
        files[f"xl/worksheets/sheet{i}.xml"] = sheet_xml(rows, widths)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def build(claim, path):
    lines, totals = validate(claim)
    base = claim["base_currency"]
    title = [[f"Expense sheet: {claim['trip']}", BOLD]]
    meta = [f"Employee: {claim['employee']}    Approved by: {claim['approved_by']}"]
    header = ["Date", "Bill", "Category", "Merchant", "City", "Description", "Invoice", "Currency",
              "Amount", f"Amount ({base})", "Status", "Note"]
    rows = [title, meta, [], [[h, BOLD] for h in header]]
    first = len(rows) + 1
    grand = Decimal("0")
    for line in lines:
        claimed = line["status"] == "approved"
        if claimed:
            grand += line["base_amount"]
        rows.append([line["day"].isoformat(), line["bill"], line["category"], line["merchant"],
                     line.get("city", ""), line["description"], line.get("invoice", ""), line["currency"],
                     [line["amount"], NUMBER], [line["base_amount"] if claimed else Decimal("0.00"), NUMBER],
                     line["status"], line.get("note", "")])
    last = len(rows)
    rows.append([[f"Total claimed ({base})", BOLD]] + [""] * 8 + [[(f"SUM(J{first}:J{last})", grand), BOLD_NUMBER]])

    summary = [[["Category", BOLD], [f"Amount ({base})", BOLD]]]
    for category, amount in totals.items():
        if amount:
            summary.append([category, [amount, NUMBER]])
    summary.append([["Total", BOLD], [(f"SUM(B2:B{len(summary)})", grand), BOLD_NUMBER]])

    journey = [[["Date", BOLD], ["Where", BOLD], ["What happened", BOLD], ["Bills", BOLD]]]
    for step in claim.get("journey", []):
        journey.append([str(parse_day(step.get("date"), "journey")), step.get("where", ""),
                        step.get("event", ""), step.get("bills", "")])

    write_xlsx(path, [
        ("Expenses", rows, [12, 6, 10, 26, 10, 30, 16, 9, 12, 14, 10, 34]),
        ("Journey", journey, [12, 16, 56, 12]),
        ("Summary", summary, [16, 16]),
    ])
    return grand


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        claim = json.load(handle)
    try:
        grand = build(claim, argv[2])
    except ClaimError as err:
        print(f"REFUSED: {err}", file=sys.stderr)
        return 1
    print(f"OK: {argv[2]} total {grand} {claim['base_currency']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
