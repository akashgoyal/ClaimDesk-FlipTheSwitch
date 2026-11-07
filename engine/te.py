#!/usr/bin/env python3
"""The T&E department's rules engine and system of record.

Agents read bills and talk to people. This does the arithmetic, applies the
policy and keeps the case file, so a limit or a total never comes from a model.
Every command works on one case in TE_HOME (default ~/.te-desk). Standard
library only.

  te.py intake --employee USER          (starts a claim and names its private room)
  te.py open --case EXP-1 --employee USER --request TR-2041 --purpose "..."
  te.py add-bills --case EXP-1 < bills.json
  te.py set-journey --case EXP-1 < journey.json
  te.py show --case EXP-1
  te.py entitlement --employee USER --city Pune --days 3 [--estimate 20000]
  te.py policy-text
  te.py policy --case EXP-1      te.py gst --case EXP-1
  te.py risk --case EXP-1        te.py resolve-card --case EXP-1 --txn T-1 --as personal
  te.py route --case EXP-1       te.py decide --case EXP-1 --by USER --reject L5 --approve-rest
  te.py settle --case EXP-1      te.py sheet --case EXP-1 --out expense-sheet.xlsx
  te.py post --case EXP-1 --out DIR
  te.py report
  te.py queue                    te.py next --by USER
  te.py ask --case EXP-1 --question "..."   te.py questions --case EXP-1
  te.py answer --case EXP-1 --id Q1 --text "..." --by USER
  te.py status --employee USER   te.py request-approval --case EXP-1
  te.py escalate
"""
import argparse
import csv
import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_expense_sheet import BOLD, BOLD_NUMBER, NUMBER, write_xlsx  # noqa: E402

CENT = Decimal("0.01")
CATEGORIES = ("flight", "train", "taxi", "hotel", "meal", "mileage", "tax", "other")
LINE_FIELDS = ("bill", "date", "merchant", "city", "category", "description", "invoice", "currency", "amount")


class Refused(Exception):
    """The engine won't do this. The message says what to fix."""


def home():
    return os.environ.get("TE_HOME") or os.path.expanduser("~/.te-desk")


def path(*parts):
    return os.path.join(home(), *parts)


def load_json(*parts):
    with open(path(*parts), encoding="utf-8") as handle:
        return json.load(handle)


def save_json(data, *parts):
    target = path(*parts)
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(temp, target)


def rows(name):
    with open(path("data", name), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def policy():
    return load_json("data", "policy.json")


def money(value, where="amount"):
    try:
        amount = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        raise Refused(f"{where}: {value!r} is not a number") from None
    if not amount.is_finite():
        raise Refused(f"{where}: {value!r} is not a number")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def day(value, where="date"):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise Refused(f"{where}: {value!r} is not a YYYY-MM-DD date") from None


def employee_by(field, value):
    for row in rows("employees.csv"):
        if row[field] == value:
            return row
    raise Refused(f"no employee with {field} {value!r} in the employee master")


def load_case(case_id):
    file = path("cases", f"{case_id}.json")
    if not os.path.exists(file):
        raise Refused(f"no case {case_id}")
    return load_json("cases", f"{case_id}.json")


def log(case, action, detail="", by="engine"):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    case["audit"].append({"at": stamp, "by": by, "action": action, "detail": detail})


def save_case(case):
    save_json(case, "cases", f"{case['id']}.json")
    write_tracker()


def now():
    return datetime.now(timezone.utc)


def all_cases():
    if not os.path.isdir(path("cases")):
        return []
    names = sorted(f for f in os.listdir(path("cases")) if f.endswith(".json"))
    return [load_json("cases", name) for name in names]


def new_case(person, by):
    os.makedirs(path("cases"), exist_ok=True)
    existing = [f for f in os.listdir(path("cases")) if f.endswith(".json")]
    case_id = f"EXP-{len(existing) + 1:04d}"
    case = {
        "id": case_id, "employee_id": person["employee_id"], "request": None, "purpose": None, "destination": None, "start": None, "end": None,
        "budget": None, "bills": {}, "lines": [], "journey": [], "card_resolutions": {}, "approvals": [], "settlement": None, "posted": False, "audit": [],
        "room": f"claim-{case_id.lower()}", "questions": [], "queued_at": None, "started_at": None, "approval_request": None, "escalations": {},
    }
    log(case, "opened", "", by=by)
    return case


# Where a claim stands. The stage is worked out from the case, never set by hand,
# so the tracker can't disagree with what actually happened.
STAGES = ("Waiting for bills", "Queued", "Checking", "Waiting for employee", "Review", "Approval", "Settled", "Posted")
STAGE_FOR_EMPLOYEE = {
    "Waiting for bills": "We're waiting for your bills.",
    "Queued": "We have your bills, and the claim is in the finance queue.",
    "Checking": "The team is checking your trip.",
    "Waiting for employee": "We need an answer from you.",
    "Review": "It's being checked against policy, tax and risk rules.",
    "Approval": "It's waiting for approval.",
    "Settled": "It's approved and settled, and is being posted to the books.",
    "Posted": "It's complete.",
}


def open_questions(case):
    return [q for q in case.get("questions", []) if q.get("answer") is None]


def stage(case):
    if case["posted"]:
        return "Posted"
    if case["settlement"]:
        return "Settled"
    if open_questions(case):
        return "Waiting for employee"
    if case.get("approval_request") or case["approvals"]:
        return "Approval"
    if case["journey"]:
        return "Review"
    if case.get("started_at"):
        return "Checking"
    if case["lines"]:
        return "Queued"
    return "Waiting for bills"


def next_approval(case):
    done = {a["role"] for a in case["approvals"]}
    return next((s for s in route(case) if s["role"] not in done), None)


def minutes_waiting(case):
    """Minutes the current approval request has been open, or None."""
    request = case.get("approval_request")
    if not request:
        return None
    return (now() - datetime.fromisoformat(request["at"])).total_seconds() / 60


def is_overdue(case):
    waited = minutes_waiting(case)
    return waited is not None and waited >= policy().get("approval_expiry_minutes", 1440)


def waiting_on(case):
    current = stage(case)
    person = employee_by("employee_id", case["employee_id"])
    if current in ("Waiting for bills", "Waiting for employee"):
        return person["username"]
    if current == "Queued":
        return "finance"
    if current in ("Checking", "Review"):
        return "agents"
    if current == "Settled":
        return "accountant"
    if current == "Approval":
        step = next_approval(case)
        return step["username"] if step else "clerk"
    return ""


def case_total(case):
    if case["settlement"]:
        return case["settlement"]["total_expense"]
    if not case["lines"]:
        return ""
    return str(claim_total(case))


def tracker_rows():
    out = []
    for case in all_cases():
        person = employee_by("employee_id", case["employee_id"])
        trail = case["audit"]
        opened, updated = trail[0]["at"] if trail else "", trail[-1]["at"] if trail else ""
        seconds = int((datetime.fromisoformat(updated) - datetime.fromisoformat(opened)).total_seconds()) if trail else 0
        out.append({"case": case["id"], "employee": person["name"], "username": person["username"], "department": person["department"],
                    "purpose": case["purpose"] or "", "destination": case["destination"] or "", "stage": stage(case), "waiting_on": waiting_on(case),
                    "total": case_total(case), "opened": opened, "updated": updated, "elapsed_seconds": seconds, "last_event": trail[-1]["action"] if trail else "",
                    "overdue": is_overdue(case), "escalated": bool(case.get("escalations")), "room": case.get("room") or ""})
    return out


TRACKER_FIELDS = ("case", "employee", "department", "purpose", "destination", "stage", "waiting_on", "total", "opened", "updated", "room")


def write_tracker():
    """tracker.csv for people, tracker.json for the live board. Rebuilt on every change."""
    cases = tracker_rows()
    temp = path("tracker.csv.tmp")
    with open(temp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACKER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cases)
    os.replace(temp, path("tracker.csv"))
    save_json({"updated": now().isoformat(timespec="seconds"), "stages": list(STAGES), "cases": cases}, "tracker.json")


# GSTIN: 15 characters whose last is a base-36 checksum of the first 14.
GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum(first_14):
    total = 0
    for index, char in enumerate(first_14):
        product = GSTIN_CHARS.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return GSTIN_CHARS[(36 - total % 36) % 36]


def gstin_valid(value):
    value = (value or "").strip().upper()
    if len(value) != 15 or any(c not in GSTIN_CHARS for c in value):
        return False
    if not value[:2].isdigit() or not 1 <= int(value[:2]) <= 38:
        return False
    return gstin_checksum(value[:14]) == value[14]


# Opening a case and recording what the bills say.

def cmd_intake(args):
    """Start a claim before any bills arrive, and name its private room."""
    person = employee_by("username", args.employee)
    for case in all_cases():
        if case["employee_id"] == person["employee_id"] and stage(case) == "Waiting for bills":
            return f"{case['id']} is already waiting for bills from {person['name']}. Private room: {case['room']}"
    case = new_case(person, args.employee)
    save_case(case)
    return f"Started {case['id']} for {person['name']}. Private room: {case['room']}"


def cmd_open(args):
    person = employee_by("username", args.employee)
    if args.case:
        case = load_case(args.case)
        if case["employee_id"] != person["employee_id"]:
            raise Refused(f"{args.case} belongs to another employee")
        if case["lines"]:
            raise Refused(f"{args.case} already has bills")
    else:
        case = new_case(person, args.employee)
    request = next((r for r in rows("preapprovals.csv") if r["request_id"] == args.request), None)
    if args.request and not request:
        raise Refused(f"no pre-trip approval {args.request}")
    if request and request["employee_id"] != person["employee_id"]:
        raise Refused(f"{args.request} belongs to another employee")
    purpose = args.purpose or (request or {}).get("purpose")
    if not purpose:
        raise Refused("a business purpose is required")
    case.update(request=args.request, purpose=purpose, destination=(request or {}).get("destination") or args.city,
                start=(request or {}).get("start") or args.start, end=(request or {}).get("end") or args.end, budget=(request or {}).get("budget"))
    if not (case["destination"] and case["start"] and case["end"]):
        raise Refused("give --request, or --city, --start and --end")
    log(case, "trip recorded", purpose, by=args.employee)
    save_case(case)
    return f"Opened {case['id']} for {person['name']} ({person['grade']}, {person['cost_center']}): {purpose}"


def fx_rate(currency, on):
    base = policy()["base_currency"]
    if currency == base:
        return Decimal("1")
    for row in rows("fx_rates.csv"):
        if row["currency"] == currency and row["month"] == on[:7]:
            return Decimal(row["rate_to_inr"])
    raise Refused(f"no {currency} rate for {on[:7]} in fx_rates.csv")


def cmd_add_bills(args):
    case = load_case(args.case)
    if not case["start"]:
        raise Refused(f"{args.case} has no trip details yet, so run te.py open first")
    payload = json.load(sys.stdin)
    problems, lines = [], []
    for raw in payload.get("lines", []):
        where = f"{raw.get('bill', '?')} {raw.get('description', '')}".strip()
        try:
            for field in LINE_FIELDS:
                if raw.get(field) in (None, ""):
                    raise Refused(f"{where}: missing {field}")
            if str(raw["amount"]).upper() == "UNREADABLE":
                raise Refused(f"{where}: amount is unreadable, so ask the employee")
            if raw["category"] not in CATEGORIES:
                raise Refused(f"{where}: category must be one of {', '.join(CATEGORIES)}")
            day(raw["date"], where)
            amount = money(raw["amount"], where)
            rate = fx_rate(raw["currency"], raw["date"])
        except Refused as err:
            problems.append(str(err))
            continue
        line = {**raw, "amount": str(amount), "base_amount": str((amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)), "status": "pending"}
        lines.append(line)
    bills = {bill: str(money(total, f"bill {bill}")) for bill, total in payload.get("bills", {}).items()}
    added = {}
    for line in lines:
        added[line["bill"]] = added.get(line["bill"], Decimal("0")) + Decimal(line["amount"])
    for bill, total in bills.items():
        if bill not in added:
            problems.append(f"bill {bill}: printed total {total} has no lines")
        elif added[bill] != Decimal(total):
            problems.append(f"bill {bill}: lines add up to {added[bill]}, the bill says {total}")
    for bill in added:
        if bill not in bills:
            problems.append(f"bill {bill}: no printed total was given")
    if problems:
        raise Refused("; ".join(problems))
    start = len(case["lines"])
    for offset, line in enumerate(lines, 1):
        line["id"] = f"L{start + offset}"
    case["lines"].extend(lines)
    case["bills"].update(bills)
    log(case, "bills recorded", f"{len(bills)} bills, {len(lines)} lines")
    case["queued_at"] = case.get("queued_at") or now().isoformat(timespec="seconds")
    save_case(case)
    return f"{args.case}: recorded {len(bills)} bills and {len(lines)} lines. Every bill reconciles to its printed total. It's now in the finance queue."


def cmd_set_journey(args):
    case = load_case(args.case)
    unanswered = open_questions(case)
    if unanswered:
        raise Refused(f"the employee hasn't answered: {', '.join(q['id'] for q in unanswered)}")
    case["journey"] = json.load(sys.stdin)
    log(case, "journey recorded", f"{len(case['journey'])} entries")
    save_case(case)
    return f"{args.case}: journey recorded."


def format_lines(case):
    out = []
    for line in case["lines"]:
        out.append(f"{line['id']} {line['bill']} {line['date']} {line['category']} {line['merchant']} | {line['description']} | {line['invoice']} | {line['currency']} {line['amount']} | {line['status']}")
    return out


def cmd_show(args):
    case = load_case(args.case)
    person = employee_by("employee_id", case["employee_id"])
    head = [f"{case['id']}: {person['name']} ({person['grade']}, {person['department']}, {person['cost_center']})",
            f"Employee username: {person['username']}. Private room: {case.get('room') or 'none'}. Stage: {stage(case)}",
            f"Purpose: {case['purpose']}. {case['destination']}, {case['start']} to {case['end']}. Budget: {case['budget'] or 'none'}"]
    answers = [f"{q['id']} asked: {q['question']} | Answer: {q['answer'] if q['answer'] is not None else 'WAITING'}" for q in case.get("questions", [])]
    return "\n".join(head + format_lines(case) + answers)


def cmd_policy_text(_args):
    data = policy()
    keep = {k: data[k] for k in ("company", "city_tiers", "grades", "mileage_per_km", "submission_days", "bill_required_above", "approval_matrix")}
    return json.dumps(keep, indent=1)


def cmd_entitlement(args):
    person = employee_by("username", args.employee)
    data = policy()
    tier = data["city_tiers"].get(args.city)
    if not tier:
        raise Refused(f"{args.city} has no tier in the policy, so ask finance")
    grade = data["grades"][person["grade"]]
    per_diem = Decimal(grade["per_diem"][tier]) * args.days
    out = [f"{person['name']}, {person['grade']}, {args.city} (tier {tier}), {args.days} days:",
           f"Hotel up to {grade['hotel_per_night'][tier]} per night before tax",
           f"Meals up to {grade['meal_limit']} each",
           f"Per diem {grade['per_diem'][tier]} a day, {per_diem} in total",
           f"Flights: {', '.join(grade['flight_cabins'])}"]
    if args.estimate is not None:
        chain = approval_chain(Decimal(str(args.estimate)))
        out.append(f"An estimate of {args.estimate} needs approval from: {', '.join(chain)}")
    return "\n".join(out)


# Checks. Each returns findings without changing an approval.

def policy_findings(case):
    data = policy()
    person = employee_by("employee_id", case["employee_id"])
    grade = data["grades"][person["grade"]]
    tier = data["city_tiers"].get(case["destination"])
    findings = []
    for line in case["lines"]:
        amount, verdict, why = Decimal(line["base_amount"]), "OK", "Within policy"
        category = line["category"]
        if category == "hotel":
            nights = int(line.get("nights") or 1)
            limit = Decimal(grade["hotel_per_night"][tier]) if tier else None
            per_night = (amount / nights).quantize(CENT)
            if limit is None:
                verdict, why = "EXCEPTION", f"{case['destination']} has no tier in the policy"
            elif per_night > limit:
                verdict, why = "EXCEPTION", f"{per_night} a night is over the {limit} limit for {person['grade']}, tier {tier}"
            else:
                why = f"{per_night} a night, limit {limit}"
        elif category == "meal":
            limit = Decimal(grade["meal_limit"])
            if amount > limit:
                verdict, why = "EXCEPTION", f"{amount} is {amount - limit} over the {limit} meal limit"
            else:
                why = f"Limit {limit}"
        elif category == "flight":
            cabin = (line.get("cabin") or "").lower()
            if not cabin:
                verdict, why = "EXCEPTION", "Cabin isn't recorded"
            elif cabin not in grade["flight_cabins"]:
                verdict, why = "EXCEPTION", f"{cabin} isn't allowed for {person['grade']}"
            else:
                why = f"{cabin}, allowed"
        elif category == "mileage":
            km, allowed = line.get("km"), None
            if km:
                allowed = (Decimal(str(km)) * Decimal(data["mileage_per_km"])).quantize(CENT)
            if allowed is None or amount > allowed:
                verdict, why = "EXCEPTION", f"Mileage should be {allowed if allowed is not None else 'recorded in km'}"
        last_day = day(case["end"])
        if (date.fromisoformat(line["date"]) - last_day).days > 1:
            verdict, why = "EXCEPTION", f"Dated after the trip ended on {case['end']}"
        findings.append({"line": line["id"], "bill": line["bill"], "category": category, "amount": str(amount), "verdict": verdict, "why": why})
    return findings


def cmd_policy(args):
    case = load_case(args.case)
    data = policy()
    findings = policy_findings(case)
    out = [f"{f['line']} {f['bill']} {f['category']} {f['amount']}: {f['verdict']}. {f['why']}" for f in findings]
    duplicates = duplicate_lines(case)
    total = sum((Decimal(line["base_amount"]) for line in case["lines"] if line["id"] not in duplicates), Decimal("0"))
    if case["budget"] and total > Decimal(case["budget"]):
        over = total - Decimal(case["budget"])
        out.append(f"BUDGET: bills total {total} before per diem and without duplicates, {over} over the pre-approved {case['budget']}")
    age = (date.today() - day(case["end"])).days
    if age > data["submission_days"]:
        out.append(f"LATE: submitted {age} days after the trip; the policy allows {data['submission_days']}")
    log(case, "policy checked", f"{sum(f['verdict'] != 'OK' for f in findings)} exceptions")
    save_case(case)
    return "\n".join(out)


def gst_findings(case):
    data = policy()
    findings = []
    for line in case["lines"]:
        if line["category"] != "tax":
            continue
        gst_on = line.get("gst_on") or ""
        amount, gstin = Decimal(line["base_amount"]), line.get("vendor_gstin") or ""
        if gst_on in data["gst"]["credit_blocked_on"]:
            eligible, why = False, f"Credit isn't available on {gst_on} under the sample rules"
        elif not gstin:
            eligible, why = False, "The bill shows no supplier GSTIN"
        elif not gstin_valid(gstin):
            eligible, why = False, f"Supplier GSTIN {gstin} fails the format or checksum test"
        elif not line.get("billed_to_company"):
            eligible, why = False, "The invoice isn't made out to the company. Ask the supplier to re-issue it"
        else:
            eligible, why = True, f"Supplier GSTIN {gstin} is valid and the invoice is made out to the company"
        findings.append({"line": line["id"], "bill": line["bill"], "amount": str(amount), "eligible": eligible, "why": why})
    return findings


def cmd_gst(args):
    case = load_case(args.case)
    findings = gst_findings(case)
    if not findings:
        return "No GST lines on this claim."
    out = [f"{f['line']} {f['bill']} {f['amount']}: {'CREDIT' if f['eligible'] else 'NO CREDIT'}. {f['why']}" for f in findings]
    credit = sum((Decimal(f["amount"]) for f in findings if f["eligible"]), Decimal("0"))
    lost = sum((Decimal(f["amount"]) for f in findings if not f["eligible"]), Decimal("0"))
    out.append(f"Credit available {credit}. Credit lost {lost}. These are sample rules, not tax advice.")
    log(case, "GST checked", f"credit {credit}, lost {lost}")
    save_case(case)
    return "\n".join(out)


def card_match(case):
    """Match each bill's total to one card charge. Returns (bill->txn, charges with no bill)."""
    start, end = day(case["start"]), day(case["end"])
    charges = [t for t in rows("card_statement.csv") if t["employee_id"] == case["employee_id"]
               and -1 <= (date.fromisoformat(t["date"]) - start).days and (date.fromisoformat(t["date"]) - end).days <= 3]
    used, matched = set(), {}
    for bill, total in case["bills"].items():
        dates = [date.fromisoformat(line["date"]) for line in case["lines"] if line["bill"] == bill]
        for charge in charges:
            if charge["txn_id"] in used or Decimal(charge["amount"]) != Decimal(total):
                continue
            if any(abs((date.fromisoformat(charge["date"]) - d).days) <= 3 for d in dates):
                matched[bill] = charge["txn_id"]
                used.add(charge["txn_id"])
                break
    return matched, [c for c in charges if c["txn_id"] not in used]


def duplicate_lines(case):
    """Lines repeating an earlier invoice and amount in this claim or a posted claim."""
    data = policy()
    history = {}
    for record in load_json("ledger.json"):
        for old in record["lines"]:
            history[(old["invoice"], Decimal(old["amount"]))] = record["case"]
    seen, duplicates = {}, {}
    for line in case["lines"]:
        key = (line["invoice"], Decimal(line["amount"]))
        if key in history:
            duplicates[line["id"]] = f"already paid on {history[key]}"
        elif key in seen:
            duplicates[line["id"]] = f"same invoice and amount as {seen[key]}"
        else:
            seen[key] = line["id"]
    return duplicates


def cmd_risk(args):
    case = load_case(args.case)
    data = policy()
    out = []
    for line_id, why in duplicate_lines(case).items():
        out.append(f"DUPLICATE {line_id}: {why}")
    matched, unmatched = card_match(case)
    for bill, txn in matched.items():
        out.append(f"CARD {bill} matches company card charge {txn}")
    for charge in unmatched:
        state = case["card_resolutions"].get(charge["txn_id"])
        if state:
            out.append(f"RESOLVED {charge['txn_id']} {charge['merchant']} {charge['amount']}: {state['as']}")
        else:
            out.append(f"NO BILL {charge['txn_id']} {charge['date']} {charge['merchant']} {charge['amount']}: ask the employee whether it was business or personal")
    for line in case["lines"]:
        amount = Decimal(line["base_amount"])
        if amount >= Decimal(data["round_amount_flag_above"]) and amount % 500 == 0 and line["category"] in ("meal", "taxi", "other"):
            out.append(f"ROUND {line['id']}: {amount} is a round amount, which is worth a second look")
    log(case, "risk checked", f"{len(out)} findings")
    save_case(case)
    return "\n".join(out) or "No risk findings."


def cmd_resolve_card(args):
    case = load_case(args.case)
    _, unmatched = card_match(case)
    charge = next((c for c in unmatched if c["txn_id"] == args.txn), None)
    if not charge:
        raise Refused(f"{args.txn} isn't an unmatched card charge on this case")
    case["card_resolutions"][args.txn] = {"as": args.as_, "amount": charge["amount"], "merchant": charge["merchant"], "note": args.note or ""}
    if args.as_ == "business":
        line_id = f"L{len(case['lines']) + 1}"
        case["lines"].append({"id": line_id, "bill": f"NB-{args.txn}", "date": charge["date"], "merchant": charge["merchant"], "city": case["destination"],
                              "category": "other", "description": f"Card charge with no bill: {args.note or 'no reason given'}", "invoice": args.txn,
                              "currency": policy()["base_currency"], "amount": charge["amount"], "base_amount": charge["amount"], "status": "pending"})
        case["bills"][f"NB-{args.txn}"] = charge["amount"]
    log(case, "card charge resolved", f"{args.txn} as {args.as_}: {args.note or ''}", by=args.by or "journey")
    save_case(case)
    return f"{args.txn} recorded as {args.as_}."


# Approvals.

def per_diem(case):
    person = employee_by("employee_id", case["employee_id"])
    data = policy()
    tier = data["city_tiers"].get(case["destination"])
    if not tier:
        return 0, Decimal("0"), Decimal("0")
    rate = Decimal(data["grades"][person["grade"]]["per_diem"][tier])
    days = (day(case["end"]) - day(case["start"])).days + 1
    return days, rate, (rate * days).quantize(CENT)


def approval_chain(total):
    for level in policy()["approval_matrix"]:
        if level["up_to"] is None or total <= Decimal(level["up_to"]):
            return level["chain"]
    raise Refused("the approval matrix has no level for this amount")


def claim_total(case):
    duplicates = duplicate_lines(case)
    bills = sum((Decimal(line["base_amount"]) for line in case["lines"] if line["id"] not in duplicates and line["status"] != "rejected"), Decimal("0"))
    return bills + per_diem(case)[2]


def holder(role, case):
    moved = case.get("escalations", {}).get(role)
    if moved:
        return moved["to"]
    person = employee_by("employee_id", case["employee_id"])
    if role == "manager":
        return person["manager_username"]
    for row in rows("employees.csv"):
        if row["role"] == role:
            return row["username"]
    raise Refused(f"nobody holds the {role} role in the employee master")


def route(case):
    return [{"role": role, "username": holder(role, case)} for role in approval_chain(claim_total(case))]


def cmd_route(args):
    case = load_case(args.case)
    chain, done = route(case), {a["role"] for a in case["approvals"]}
    total = claim_total(case)
    parts = [f"{step['role']} ({step['username']}): {'done' if step['role'] in done else 'waiting'}" for step in chain]
    return f"{args.case} total {total} including per diem. Approvals: " + "; ".join(parts)


def unresolved_charges(case):
    _, unmatched = card_match(case)
    return [c["txn_id"] for c in unmatched if c["txn_id"] not in case["card_resolutions"]]


def cmd_decide(args):
    case = load_case(args.case)
    if case["posted"]:
        raise Refused(f"{args.case} is already posted")
    if not case["lines"]:
        raise Refused(f"{args.case} has no bills")
    open_charges = unresolved_charges(case)
    if open_charges:
        raise Refused(f"resolve these card charges first: {', '.join(open_charges)}")
    unanswered = open_questions(case)
    if unanswered:
        raise Refused(f"the employee hasn't answered: {', '.join(q['id'] for q in unanswered)}")
    person = employee_by("employee_id", case["employee_id"])
    if args.by == person["username"] and not policy()["allow_self_approval"]:
        raise Refused(f"{args.by} submitted this claim and can't approve it")
    chain = route(case)
    done = {a["role"] for a in case["approvals"]}
    step = next((s for s in chain if s["role"] not in done), None)
    if step is None:
        raise Refused("every approval is already recorded")
    if step["username"] != args.by:
        raise Refused(f"the next approval is {step['role']}, which is {step['username']}, not {args.by}")
    known = {line["id"]: line for line in case["lines"]}
    rejected = [x for x in (args.reject or "").split(",") if x]
    approved = [x for x in (args.approve or "").split(",") if x]
    for line_id in rejected + approved:
        if line_id not in known:
            raise Refused(f"no line {line_id}")
    if set(rejected) & set(approved):
        raise Refused("a line can't be both approved and rejected")
    duplicates = duplicate_lines(case)
    for line_id in approved:
        if line_id in duplicates:
            raise Refused(f"{line_id} is a duplicate ({duplicates[line_id]}) and can't be approved")
    rejected = sorted(set(rejected) | set(duplicates))
    for line_id in rejected:
        known[line_id]["status"] = "rejected"
    for line_id in approved:
        known[line_id]["status"] = "approved"
    if args.approve_rest:
        for line in case["lines"]:
            if line["status"] == "pending":
                line["status"] = "approved"
    pending = [line["id"] for line in case["lines"] if line["status"] == "pending"]
    if pending:
        raise Refused(f"these lines still need a decision: {', '.join(pending)}. Nothing was recorded.")
    case["approvals"].append({"role": step["role"], "by": args.by, "note": args.note or ""})
    case["approval_request"] = None
    log(case, f"{step['role']} approved", f"rejected: {', '.join(rejected) or 'none'}. {args.note or ''}".strip(), by=args.by)
    chain = route(case)
    done = {a["role"] for a in case["approvals"]}
    remaining = [s for s in chain if s["role"] not in done]
    save_case(case)
    next_step = f"Next: {remaining[0]['role']} ({remaining[0]['username']})." if remaining else "All approvals are done."
    return f"{args.case}: {step['role']} approval recorded for {args.by}. {next_step}"


# Settlement, the sheet and the books.

def require_approved(case):
    done = {a["role"] for a in case["approvals"]}
    missing = [s["role"] for s in route(case) if s["role"] not in done]
    if missing:
        raise Refused(f"still needs approval from: {', '.join(missing)}")
    if unresolved_charges(case):
        raise Refused("card charges are unresolved")


def settlement(case):
    require_approved(case)
    matched, _ = card_match(case)
    card_bills = set(matched)
    days, rate, diem = per_diem(case)
    approved_card, approved_cash, rejected_card = Decimal("0"), Decimal("0"), Decimal("0")
    for line in case["lines"]:
        amount = Decimal(line["base_amount"])
        is_card = line["bill"] in card_bills or line["bill"].startswith("NB-")
        if line["status"] == "approved":
            if is_card:
                approved_card += amount
            else:
                approved_cash += amount
        elif is_card:
            rejected_card += amount
    personal = sum((Decimal(r["amount"]) for r in case["card_resolutions"].values() if r["as"] == "personal"), Decimal("0"))
    request = case.get("request")
    advance = sum((Decimal(a["amount"]) for a in rows("advances.csv")
                   if a["employee_id"] == case["employee_id"] and a["status"] == "open" and (not request or a["request_id"] == request)), Decimal("0"))
    reimbursable = approved_cash + diem
    net = reimbursable - advance - personal - rejected_card
    return {"days": days, "per_diem_rate": str(rate), "per_diem": str(diem), "approved_card": str(approved_card), "approved_cash": str(approved_cash),
            "rejected_card": str(rejected_card), "personal": str(personal), "advance": str(advance), "reimbursable": str(reimbursable),
            "total_expense": str(approved_card + approved_cash + diem), "net": str(net)}


def describe_net(net):
    return f"Pay employee {net}" if net >= 0 else f"Employee returns {-net}"


def cmd_settle(args):
    case = load_case(args.case)
    if case["posted"]:
        raise Refused(f"{args.case} is already posted, so it can't be settled again")
    result = settlement(case)
    case["settlement"] = result
    log(case, "settled", result["net"])
    save_case(case)
    net = Decimal(result["net"])
    return "\n".join([
        f"Bills approved, on company card: {result['approved_card']}", f"Bills approved, paid by employee: {result['approved_cash']}",
        f"Per diem, {result['days']} days at {result['per_diem_rate']}: {result['per_diem']}", f"TOTAL EXPENSE: {result['total_expense']}",
        f"Reimbursable: {result['reimbursable']}", f"Less advance: {result['advance']}", f"Less personal card charges: {result['personal']}",
        f"Less rejected items paid on card: {result['rejected_card']}", f"NET: {describe_net(net)}"])


def cmd_sheet(args):
    case = load_case(args.case)
    result = case["settlement"] or settlement(case)
    person = employee_by("employee_id", case["employee_id"])
    base = policy()["base_currency"]
    matched, _ = card_match(case)
    approvers = ", ".join(f"{a['role']}: {a['by']}" for a in case["approvals"])
    findings = {f["line"]: f for f in policy_findings(case)}
    duplicates = duplicate_lines(case)
    header = ["Line", "Date", "Bill", "Category", "Merchant", "Description", "Invoice", "Paid by", "Currency", "Amount", f"Approved ({base})", "Status", "Policy note"]
    sheet = [[[f"Expense claim {case['id']}: {case['purpose']}", BOLD]],
             [f"{person['name']} ({person['employee_id']}), {person['department']}, {person['cost_center']}    Approved by {approvers}"], [],
             [[h, BOLD] for h in header]]
    first = len(sheet) + 1
    approved_total = Decimal("0")
    for line in sorted(case["lines"], key=lambda item: (item["date"], item["bill"], item["id"])):
        ok = line["status"] == "approved"
        value = Decimal(line["base_amount"]) if ok else Decimal("0.00")
        approved_total += value
        note = duplicates.get(line["id"]) or (findings[line["id"]]["why"] if findings[line["id"]]["verdict"] != "OK" else "")
        paid = "Company card" if line["bill"] in matched or line["bill"].startswith("NB-") else "Employee"
        sheet.append([line["id"], line["date"], line["bill"], line["category"], line["merchant"], line["description"], line["invoice"], paid, line["currency"],
                      [Decimal(line["amount"]), NUMBER], [value, NUMBER], line["status"], note])
    last = len(sheet)
    sheet.append([[f"Bills approved ({base})", BOLD]] + [""] * 9 + [[(f"SUM(K{first}:K{last})", approved_total), BOLD_NUMBER]])
    journey = [[["Date", BOLD], ["Where", BOLD], ["What happened", BOLD], ["Bills", BOLD]]]
    for step in case["journey"]:
        journey.append([step.get("date", ""), step.get("where", ""), step.get("event", ""), step.get("bills", "")])
    settle_rows = [[["Item", BOLD], [base, BOLD]]]
    for label, key in (("Bills approved, on company card", "approved_card"), ("Bills approved, paid by employee", "approved_cash"),
                       (f"Per diem, {result['days']} days at {result['per_diem_rate']}", "per_diem"), ("Total expense", "total_expense"), ("Reimbursable", "reimbursable"),
                       ("Less advance", "advance"), ("Less personal card charges", "personal"), ("Less rejected items paid on card", "rejected_card")):
        settle_rows.append([label, [Decimal(result[key]), NUMBER]])
    net = Decimal(result["net"])
    settle_rows.append([[describe_net(net).rsplit(" ", 1)[0], BOLD], [abs(net), BOLD_NUMBER]])
    audit = [[["When", BOLD], ["Who", BOLD], ["What", BOLD], ["Detail", BOLD]]] + [[e["at"], e["by"], e["action"], e["detail"]] for e in case["audit"]]
    write_xlsx(args.out, [
        ("Expenses", sheet, [6, 12, 8, 10, 26, 34, 16, 13, 9, 12, 14, 10, 40]),
        ("Journey", journey, [14, 18, 60, 16]), ("Settlement", settle_rows, [38, 14]), ("Audit trail", audit, [24, 14, 22, 50]),
    ])
    log(case, "sheet built", args.out)
    save_case(case)
    return f"OK: {args.out}. Total expense {result['total_expense']} {base}. {describe_net(net)}."


def journal_entries(case, result):
    data = policy()
    gl = data["gl"]
    person = employee_by("employee_id", case["employee_id"])
    credit = {f["line"]: f["eligible"] for f in gst_findings(case)}
    debits = {}
    for line in case["lines"]:
        if line["status"] != "approved":
            continue
        if line["category"] == "tax":
            account = gl["gst_input"] if credit.get(line["id"]) else gl["gst_expense"]
        else:
            account = gl.get(line["category"], gl["other"])
        debits[account] = debits.get(account, Decimal("0")) + Decimal(line["base_amount"])
    if Decimal(result["per_diem"]):
        debits[gl["per_diem"]] = debits.get(gl["per_diem"], Decimal("0")) + Decimal(result["per_diem"])
    credits = {}
    charged = Decimal(result["approved_card"]) + Decimal(result["rejected_card"]) + Decimal(result["personal"])
    if charged:
        credits[gl["card_clearing"]] = charged
    if Decimal(result["advance"]):
        credits[gl["advances"]] = Decimal(result["advance"])
    net = Decimal(result["net"])
    if net > 0:
        credits[gl["employee_payable"]] = net
    elif net < 0:
        debits[gl["employee_receivable"]] = -net
    total_debit, total_credit = sum(debits.values(), Decimal("0")), sum(credits.values(), Decimal("0"))
    if total_debit != total_credit:
        raise Refused(f"the journal doesn't balance: debits {total_debit}, credits {total_credit}")
    entries = [(account, "debit", amount) for account, amount in sorted(debits.items())] + [(account, "credit", amount) for account, amount in sorted(credits.items())]
    return entries, person, total_debit


def cmd_post(args):
    case = load_case(args.case)
    if case["posted"]:
        raise Refused(f"{args.case} is already posted")
    result = settlement(case)
    case["settlement"] = result
    entries, person, total = journal_entries(case, result)
    os.makedirs(args.out, exist_ok=True)
    journal_path = os.path.join(args.out, f"{case['id']}-journal.csv")
    with open(journal_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case", "account", "side", "amount", "cost_center", "employee_id"])
        for account, side, amount in entries:
            writer.writerow([case["id"], account, side, amount, person["cost_center"], person["employee_id"]])
    net = Decimal(result["net"])
    payment_path = None
    if net > 0:
        payment_path = os.path.join(args.out, f"{case['id']}-payment-SAMPLE.csv")
        with open(payment_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["SAMPLE FILE. NOT SENT TO ANY BANK."])
            writer.writerow(["case", "employee_id", "name", "amount", "currency"])
            writer.writerow([case["id"], person["employee_id"], person["name"], net, policy()["base_currency"]])
    exceptions = sum(f["verdict"] != "OK" for f in policy_findings(case))
    ledger = load_json("ledger.json")
    ledger.append({"case": case["id"], "employee_id": case["employee_id"], "department": person["department"], "posted": date.today().isoformat(),
                   "total": result["total_expense"], "exceptions": exceptions,
                   "lines": [{"invoice": line["invoice"], "amount": line["amount"], "category": line["category"]} for line in case["lines"] if line["status"] == "approved"]})
    save_json(ledger, "ledger.json")
    case["posted"] = True
    log(case, "posted", journal_path)
    save_case(case)
    lines = [f"Posted {case['id']}. The journal balances at {total}: {journal_path}"]
    for account, side, amount in entries:
        lines.append(f"{side} {account} {amount}")
    lines.append(f"Sample payment file: {payment_path}" if payment_path else describe_net(net))
    return "\n".join(lines)


def cmd_report(_args):
    ledger = load_json("ledger.json")
    by_department, by_category, exceptions, total = {}, {}, 0, Decimal("0")
    for record in ledger:
        amount = Decimal(record["total"])
        total += amount
        exceptions += record["exceptions"]
        by_department[record["department"]] = by_department.get(record["department"], Decimal("0")) + amount
        billed = Decimal("0")
        for line in record["lines"]:
            billed += Decimal(line["amount"])
            by_category[line["category"]] = by_category.get(line["category"], Decimal("0")) + Decimal(line["amount"])
        # What a claim's total has beyond its bills is its per diem.
        if amount != billed:
            by_category["per_diem"] = by_category.get("per_diem", Decimal("0")) + amount - billed
    out = [f"{len(ledger)} claims posted, {total} in total, {exceptions} policy exceptions."]
    out += [f"Department {name}: {value}" for name, value in sorted(by_department.items())]
    out += [f"Category {name}: {value}" for name, value in sorted(by_category.items())]
    posted = {r["case"] for r in ledger}
    open_advances = [a for a in rows("advances.csv") if a["status"] == "open"]
    settled_requests = set()
    if os.path.isdir(path("cases")):
        for name in os.listdir(path("cases")):
            case = load_json("cases", name)
            if case["id"] in posted and case.get("request"):
                settled_requests.add(case["request"])
    still_open = [a for a in open_advances if a["request_id"] not in settled_requests]
    out.append("Open advances: " + (", ".join(f"{a['advance_id']} {a['amount']}" for a in still_open) or "none"))
    return "\n".join(out)


# The queue, questions for the employee, and approvals that go quiet.

def cmd_queue(_args):
    cases = tracker_rows()
    active = [c for c in cases if c["stage"] != "Posted"]
    out = ["| Claim | Employee | Purpose | Stage | Waiting on | Total |", "|---|---|---|---|---|---|"]
    for c in active:
        flag = " (OVERDUE)" if c["overdue"] else " (escalated)" if c["escalated"] else ""
        out.append(f"| {c['case']} | {c['employee']} | {c['purpose'] or '-'} | {c['stage']}{flag} | {c['waiting_on'] or '-'} | {c['total'] or '-'} |")
    if not active:
        out = ["No open claims."]
    queued = sum(c["stage"] == "Queued" for c in cases)
    out.append(f"\n{len(active)} open, {queued} queued, {len(cases) - len(active)} posted. Tracker file: {path('tracker.csv')}")
    return "\n".join(out)


def cmd_next(args):
    if args.case:
        case = load_case(args.case)
        if stage(case) != "Queued":
            raise Refused(f"{args.case} is at {stage(case)}, so it can't be started")
    else:
        queued = sorted((c for c in all_cases() if stage(c) == "Queued"), key=lambda c: c["queued_at"] or "")
        if not queued:
            raise Refused("nothing is queued")
        case = queued[0]
    person = employee_by("employee_id", case["employee_id"])
    if person["username"] == args.by and not policy()["allow_self_approval"]:
        raise Refused(f"{args.by} submitted {case['id']} and can't start it")
    case["started_at"] = now().isoformat(timespec="seconds")
    log(case, "started", "", by=args.by)
    save_case(case)
    left = sum(stage(c) == "Queued" for c in all_cases())
    return f"Started {case['id']} for {person['name']}: {case['purpose']}. {left} still queued."


def cmd_ask(args):
    case = load_case(args.case)
    if case["posted"] or case["settlement"]:
        raise Refused(f"{args.case} is already settled")
    question = {"id": f"Q{len(case['questions']) + 1}", "question": args.question, "answer": None, "by": None}
    case["questions"].append(question)
    log(case, "question for employee", f"{question['id']}: {args.question}")
    save_case(case)
    return f"{args.case}: recorded {question['id']}. Now ask the concierge to put it to the employee."


def cmd_questions(args):
    case = load_case(args.case)
    person = employee_by("employee_id", case["employee_id"])
    head = f"{case['id']}: employee username {person['username']}, private room {case.get('room') or 'none'}"
    waiting = open_questions(case)
    if not waiting:
        return head + "\nNo open questions."
    return "\n".join([head] + [f"{q['id']}: {q['question']}" for q in waiting])


def cmd_answer(args):
    case = load_case(args.case)
    person = employee_by("employee_id", case["employee_id"])
    if args.by != person["username"]:
        raise Refused(f"{args.case} belongs to {person['username']}, so {args.by} can't answer for it")
    question = next((q for q in case.get("questions", []) if q["id"] == args.id), None)
    if not question:
        raise Refused(f"no question {args.id} on {args.case}")
    if question["answer"] is not None:
        raise Refused(f"{args.id} is already answered")
    question.update(answer=args.text, by=args.by)
    log(case, "employee answered", f"{args.id}: {args.text}", by=args.by)
    save_case(case)
    left = open_questions(case)
    return f"{args.case}: recorded the answer to {args.id}. " + (f"Still open: {', '.join(q['id'] for q in left)}." if left else "No open questions remain.")


def cmd_status(args):
    """One employee's claims, and nobody else's."""
    person = employee_by("username", args.employee)
    mine = [c for c in all_cases() if c["employee_id"] == person["employee_id"]]
    if not mine:
        return f"{person['name']} has no claims."
    out = []
    for case in mine:
        line = f"{case['id']}: {case['purpose'] or 'New claim'}. {STAGE_FOR_EMPLOYEE[stage(case)]}"
        if case["settlement"]:
            line += f" {describe_net(Decimal(case['settlement']['net']))}."
        out.append(line)
    return "\n".join(out)


def cmd_request_approval(args):
    case = load_case(args.case)
    if not case["lines"]:
        raise Refused(f"{args.case} has no bills")
    unanswered = open_questions(case)
    if unanswered:
        raise Refused(f"the employee hasn't answered: {', '.join(q['id'] for q in unanswered)}")
    open_charges = unresolved_charges(case)
    if open_charges:
        raise Refused(f"resolve these card charges first: {', '.join(open_charges)}")
    step = next_approval(case)
    if step is None:
        return f"{args.case}: every approval is already recorded."
    case["approval_request"] = {"role": step["role"], "username": step["username"], "at": now().isoformat(timespec="seconds")}
    log(case, "approval requested", f"{step['role']} from {step['username']}")
    save_case(case)
    limit = policy().get("approval_expiry_minutes", 1440)
    return f"{args.case}: {step['role']} approval requested from {step['username']}. It escalates after {limit} minutes with no answer."


def cmd_escalate(_args):
    """Move every overdue approval up. Switch has no timer, so this runs whenever the queue is looked at."""
    out = []
    routes = policy().get("escalation", {})
    for case in all_cases():
        if not is_overdue(case):
            continue
        request = case["approval_request"]
        target_role = routes.get(request["role"])
        person = employee_by("employee_id", case["employee_id"])
        target = holder(target_role, case) if target_role else None
        waited = int(minutes_waiting(case))
        if not target or target in (request["username"], person["username"]):
            out.append(f"OVERDUE {case['id']}: {request['role']} approval from {request['username']} has waited {waited} minutes, and there's nobody to move it to.")
            continue
        case["escalations"][request["role"]] = {"from": request["username"], "to": target, "at": now().isoformat(timespec="seconds")}
        case["approval_request"] = {"role": request["role"], "username": target, "at": now().isoformat(timespec="seconds")}
        log(case, "approval escalated", f"{request['role']} moved from {request['username']} to {target} after {waited} minutes")
        save_case(case)
        out.append(f"ESCALATED {case['id']}: {request['role']} approval moved from {request['username']} to {target} after {waited} minutes.")
    return "\n".join(out) or "No approvals are overdue."


def cmd_setup(args):
    """Create TE_HOME from the bundled sample data. Keeps existing cases unless --reset."""
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if args.reset and os.path.isdir(home()):
        shutil.rmtree(home())
    os.makedirs(path("cases"), exist_ok=True)
    os.makedirs(path("data"), exist_ok=True)
    for name in os.listdir(source):
        if name != "ledger_seed.json":
            shutil.copy(os.path.join(source, name), path("data", name))
    if not os.path.exists(path("ledger.json")):
        shutil.copy(os.path.join(source, "ledger_seed.json"), path("ledger.json"))
    if args.employee or args.approver or args.finance_head:
        people = rows("employees.csv")
        fields = list(people[0])
        for row in people:
            if row["employee_id"] == "E1042" and args.employee:
                row["username"] = args.employee
            if row["employee_id"] == "E1007" and args.approver:
                row["username"] = args.approver
            if args.approver:
                row["manager_username"] = args.approver
            if args.finance_head and row["role"] == "finance_head":
                row["role"] = "manager"
        if args.finance_head:
            people.append({**people[1], "employee_id": "E1001", "name": "Sara Khan", "username": args.finance_head, "department": "Finance", "role": "finance_head"})
        with open(path("data", "employees.csv"), "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(people)
    if args.solo or args.expiry_minutes is not None:
        data = policy()
        if args.solo:
            data["allow_self_approval"] = True
        if args.expiry_minutes is not None:
            data["approval_expiry_minutes"] = args.expiry_minutes
        save_json(data, "data", "policy.json")
    write_tracker()
    return f"Set up {home()}" + (" in solo mode, which allows self-approval" if args.solo else "")


def parser():
    top = argparse.ArgumentParser(description="T&E rules engine")
    sub = top.add_subparsers(dest="command", required=True)

    def add(name, func, *flags):
        command = sub.add_parser(name)
        command.set_defaults(func=func)
        for flag, options in flags:
            command.add_argument(flag, **options)

    case = ("--case", {"required": True})
    add("setup", cmd_setup, ("--employee", {}), ("--approver", {}), ("--finance-head", {}), ("--expiry-minutes", {"type": int}), ("--solo", {"action": "store_true"}), ("--reset", {"action": "store_true"}))
    add("intake", cmd_intake, ("--employee", {"required": True}))
    add("open", cmd_open, ("--case", {}), ("--employee", {"required": True}), ("--request", {}), ("--purpose", {}), ("--city", {}), ("--start", {}), ("--end", {}))
    add("add-bills", cmd_add_bills, case)
    add("set-journey", cmd_set_journey, case)
    add("show", cmd_show, case)
    add("policy-text", cmd_policy_text)
    add("entitlement", cmd_entitlement, ("--employee", {"required": True}), ("--city", {"required": True}), ("--days", {"type": int, "required": True}), ("--estimate", {"type": float}))
    add("policy", cmd_policy, case)
    add("gst", cmd_gst, case)
    add("risk", cmd_risk, case)
    add("resolve-card", cmd_resolve_card, case, ("--txn", {"required": True}), ("--as", {"dest": "as_", "choices": ["personal", "business"], "required": True}), ("--note", {}), ("--by", {}))
    add("route", cmd_route, case)
    add("decide", cmd_decide, case, ("--by", {"required": True}), ("--approve", {}), ("--reject", {}), ("--approve-rest", {"action": "store_true"}), ("--note", {}))
    add("settle", cmd_settle, case)
    add("sheet", cmd_sheet, case, ("--out", {"required": True}))
    add("post", cmd_post, case, ("--out", {"required": True}))
    add("report", cmd_report)
    add("queue", cmd_queue)
    add("next", cmd_next, ("--by", {"required": True}), ("--case", {}))
    add("ask", cmd_ask, case, ("--question", {"required": True}))
    add("questions", cmd_questions, case)
    add("answer", cmd_answer, case, ("--id", {"required": True}), ("--text", {"required": True}), ("--by", {"required": True}))
    add("status", cmd_status, ("--employee", {"required": True}))
    add("request-approval", cmd_request_approval, case)
    add("escalate", cmd_escalate)
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(args.func(args))
    except Refused as err:
        print(f"REFUSED: {err}", file=sys.stderr)
        return 1
    except FileNotFoundError as err:
        print(f"REFUSED: {err.filename} is missing. Run te.py setup first.", file=sys.stderr)
        return 1
    except json.JSONDecodeError as err:
        print(f"REFUSED: the input isn't valid JSON: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
