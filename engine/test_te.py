import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import te  # noqa: E402

VALID = te.gstin_checksum("27AAALR0001A1Z")
HOTEL_GSTIN = "27AAALR0001A1Z" + VALID
AIR_GSTIN = "07AAASK0002B1Z" + te.gstin_checksum("07AAASK0002B1Z")


def line(bill, date, category, description, amount, invoice, merchant="M", **extra):
    return {"bill": bill, "date": date, "merchant": merchant, "city": "Pune", "category": category,
            "description": description, "invoice": invoice, "currency": "INR", "amount": amount, **extra}


# The sample trip in samples/, as the reader should record it.
BILLS = {
    "bills": {"B1": "6240.00", "B2": "780.00", "B3": "11268.00", "B4": "420.00", "B5": "420.00", "B6": "6510.00"},
    "lines": [
        line("B1", "2026-10-13", "flight", "DEL to PNQ", "5571.43", "AIR-58K2-TD", cabin="economy"),
        line("B1", "2026-10-13", "tax", "GST", "668.57", "AIR-58K2-TD", gst_on="flight", vendor_gstin=AIR_GSTIN, billed_to_company=True),
        line("B2", "2026-10-13", "taxi", "Airport to hotel", "780.00", "CAB-1093"),
        line("B3", "2026-10-15", "hotel", "Room, 2 nights", "8400.00", "HTL-7Q42-KX", nights=2),
        line("B3", "2026-10-14", "meal", "Dinner", "1860.00", "HTL-7Q42-KX"),
        line("B3", "2026-10-15", "tax", "GST on room", "1008.00", "HTL-7Q42-KX", gst_on="hotel", vendor_gstin=HOTEL_GSTIN, billed_to_company=False),
        line("B4", "2026-10-14", "taxi", "Hotel to client", "420.00", "CAB-1147"),
        line("B5", "2026-10-14", "taxi", "Hotel to client", "420.00", "CAB-1147"),
        line("B6", "2026-10-16", "flight", "PNQ to DEL", "5812.50", "AIR-61M7-TD", cabin="economy"),
        line("B6", "2026-10-16", "tax", "GST", "697.50", "AIR-61M7-TD", gst_on="flight", vendor_gstin=AIR_GSTIN, billed_to_company=True),
    ],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["TE_HOME"] = self.home
        self.addCleanup(shutil.rmtree, self.home)
        self.ok("setup")

    def run_te(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(stdin) if stdin is not None else "")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = te.main(list(argv))
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv, stdin=None):
        code, out, err = self.run_te(*argv, stdin=stdin)
        self.assertEqual(code, 0, err)
        return out

    def refused(self, text, *argv, stdin=None):
        code, _, err = self.run_te(*argv, stdin=stdin)
        self.assertEqual(code, 1)
        self.assertIn(text, err)

    def opened(self, bills=BILLS):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        case = "EXP-0001"
        self.ok("add-bills", "--case", case, stdin=bills)
        return case

    def approved(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal", "--note", "Coffee with family")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest", "--note", "Dinner approved")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        return case


class Gstin(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertTrue(te.gstin_valid(HOTEL_GSTIN))
        wrong = HOTEL_GSTIN[:14] + ("0" if HOTEL_GSTIN[14] != "0" else "1")
        self.assertFalse(te.gstin_valid(wrong))
        self.assertFalse(te.gstin_valid("27ABC"))
        self.assertFalse(te.gstin_valid("99AAALR0001A1Z" + te.gstin_checksum("99AAALR0001A1Z")))

    def test_known_good_checksum(self):
        # A widely published format example.
        self.assertEqual(te.gstin_checksum("27AAPFU0939F1Z"), "V")


class Intake(Base):
    def test_refuses_bill_that_does_not_reconcile(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        bad = json.loads(json.dumps(BILLS))
        bad["lines"][0]["amount"] = "5000.00"
        self.refused("bill B1: lines add up to 5668.57, the bill says 6240.00", "add-bills", "--case", "EXP-0001", stdin=bad)
        self.assertIn("EXP-0001", self.ok("show", "--case", "EXP-0001"))
        self.assertEqual(te.load_case("EXP-0001")["lines"], [])

    def test_refuses_unreadable_amount(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        bad = json.loads(json.dumps(BILLS))
        bad["lines"][2]["amount"] = "UNREADABLE"
        self.refused("is unreadable", "add-bills", "--case", "EXP-0001", stdin=bad)

    def test_unknown_employee_and_other_persons_request_are_refused(self):
        self.refused("no employee", "open", "--employee", "nobody", "--purpose", "x", "--city", "Pune", "--start", "2026-10-01", "--end", "2026-10-02")
        self.refused("another employee", "open", "--employee", "employee-maya", "--request", "TR-2041")

    def test_foreign_currency_uses_the_rate(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin={
            "bills": {"B1": "100.00"},
            "lines": [{**line("B1", "2026-10-14", "other", "Visa fee", "100.00", "V-1"), "currency": "USD"}]})
        self.assertEqual(te.load_case("EXP-0001")["lines"][0]["base_amount"], "8350.00")

    def test_missing_rate_is_refused(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.refused("no GBP rate", "add-bills", "--case", "EXP-0001", stdin={
            "bills": {"B1": "100.00"}, "lines": [{**line("B1", "2026-10-14", "other", "Fee", "100.00", "V-1"), "currency": "GBP"}]})


class Checks(Base):
    def test_policy_flags_the_dinner_and_budget_only(self):
        out = self.ok("policy", "--case", self.opened())
        self.assertIn("meal 1860.00: EXCEPTION", out)
        self.assertIn("360.00 over the 1500 meal limit", out)
        self.assertEqual(out.count("EXCEPTION"), 1)
        self.assertIn("BUDGET", out)

    def test_policy_flags_business_class(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin={"bills": {"B1": "9000"}, "lines": [line("B1", "2026-10-13", "flight", "DEL to PNQ", "9000", "A", cabin="business")]})
        self.assertIn("business isn't allowed", self.ok("policy", "--case", "EXP-0001"))

    def test_policy_flags_hotel_over_limit_per_night(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin={"bills": {"B1": "12000"}, "lines": [line("B1", "2026-10-15", "hotel", "Room", "12000", "H", nights=2)]})
        self.assertIn("6000.00 a night is over the 5000 limit", self.ok("policy", "--case", "EXP-0001"))

    def test_gst(self):
        out = self.ok("gst", "--case", self.opened())
        self.assertIn("Credit available 1366.07. Credit lost 1008.00", out)
        self.assertIn("isn't made out to the company", out)

    def test_bad_gstin_loses_credit(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin={"bills": {"B1": "100"}, "lines": [
            line("B1", "2026-10-13", "tax", "GST", "100", "A", gst_on="hotel", vendor_gstin="27AAALR0001A1Z0" if VALID != "0" else "27AAALR0001A1Z1", billed_to_company=True)]})
        self.assertIn("fails the format or checksum", self.ok("gst", "--case", "EXP-0001"))

    def test_risk_finds_duplicate_and_card_charge_with_no_bill(self):
        out = self.ok("risk", "--case", self.opened())
        self.assertIn("DUPLICATE L8", out)
        self.assertIn("NO BILL T-9003", out)
        self.assertIn("CARD B3 matches company card charge T-9002", out)

    def test_risk_finds_a_bill_paid_on_an_earlier_claim(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin={"bills": {"B1": "620"}, "lines": [line("B1", "2026-10-14", "taxi", "Cab", "620", "CAB-0871")]})
        self.assertIn("already paid on EXP-0902", self.ok("risk", "--case", "EXP-0001"))


class Approvals(Base):
    def test_route_needs_two_levels_over_the_limit(self):
        case = self.opened()
        out = self.ok("route", "--case", case)
        self.assertIn("total 28418.00", out)
        self.assertIn("manager (manager-tom): waiting", out)
        self.assertIn("finance_head (manager-tom): waiting", out)

    def test_small_claim_needs_only_the_manager(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin={"bills": {"B1": "500"}, "lines": [line("B1", "2026-10-14", "taxi", "Cab", "500", "C")]})
        self.assertNotIn("finance_head", self.ok("route", "--case", "EXP-0001"))

    def test_employee_cannot_approve_their_own_claim(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.refused("can't approve it", "decide", "--case", case, "--by", "employee-alex", "--approve-rest")

    def test_stranger_cannot_approve(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.refused("not employee-maya", "decide", "--case", case, "--by", "employee-maya", "--approve-rest")

    def test_no_approval_with_an_open_card_charge(self):
        self.refused("resolve these card charges first: T-9003", "decide", "--case", self.opened(), "--by", "manager-tom", "--approve-rest")

    def test_every_line_needs_a_decision(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.refused("still need a decision", "decide", "--case", case, "--by", "manager-tom", "--reject", "L5")
        self.assertEqual(te.load_case(case)["approvals"], [])

    def test_duplicate_is_rejected_automatically_and_cannot_be_approved(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.refused("can't be approved", "decide", "--case", case, "--by", "manager-tom", "--approve", "L8")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        status = {item["id"]: item["status"] for item in te.load_case(case)["lines"]}
        self.assertEqual(status["L8"], "rejected")
        self.assertEqual(status["L7"], "approved")

    def test_settlement_refused_until_all_approvals_are_in(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        self.refused("still needs approval from: finance_head", "settle", "--case", case)
        self.refused("still needs approval from: finance_head", "post", "--case", case, "--out", self.home)

    def test_self_approval_works_only_in_solo_mode(self):
        self.ok("setup", "--solo")
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        data = te.policy()
        self.assertTrue(data["allow_self_approval"])


class Settlement(Base):
    def test_the_sample_trip(self):
        out = self.ok("settle", "--case", self.approved())
        self.assertIn("TOTAL EXPENSE: 28418.00", out)
        self.assertIn("on company card: 24018.00", out)
        self.assertIn("Reimbursable: 4400.00", out)
        self.assertIn("NET: Pay employee 1150.00", out)

    def test_rejected_card_item_is_recovered(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--reject", "L5", "--approve-rest")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        out = self.ok("settle", "--case", case)
        self.assertIn("rejected items paid on card: 1860.00", out)
        self.assertIn("NET: Employee returns 710.00", out)

    def test_business_card_charge_without_a_bill_needs_a_decision(self):
        case = self.opened()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "business", "--note", "Client coffee")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        out = self.ok("settle", "--case", case)
        self.assertIn("TOTAL EXPENSE: 29668.00", out)
        self.assertIn("NET: Pay employee 2400.00", out)


class Books(Base):
    def test_journal_balances_and_files_are_written(self):
        case = self.approved()
        out_dir = os.path.join(self.home, "out")
        out = self.ok("post", "--case", case, "--out", out_dir)
        self.assertIn("balances at 28418.00", out)
        with open(os.path.join(out_dir, f"{case}-journal.csv"), encoding="utf-8") as handle:
            entries = list(csv.DictReader(handle))
        side = {(e["account"], e["side"]): Decimal(e["amount"]) for e in entries}
        self.assertEqual(side[("1401", "debit")], Decimal("1366.07"))
        self.assertEqual(side[("6199", "debit")], Decimal("1008.00"))
        self.assertEqual(side[("6101", "debit")], Decimal("11383.93"))
        self.assertEqual(side[("6103", "debit")], Decimal("1200.00"))
        self.assertEqual(side[("2210", "credit")], Decimal("25268.00"))
        self.assertEqual(side[("1310", "credit")], Decimal("2000.00"))
        self.assertEqual(side[("2205", "credit")], Decimal("1150.00"))
        self.assertTrue(all(e["cost_center"] == "CC-410" for e in entries))
        with open(os.path.join(out_dir, f"{case}-payment-SAMPLE.csv"), encoding="utf-8") as handle:
            self.assertIn("NOT SENT TO ANY BANK", handle.read())

    def test_cannot_settle_after_posting(self):
        case = self.approved()
        self.ok("settle", "--case", case)
        self.ok("post", "--case", case, "--out", self.home)
        self.refused("already posted", "settle", "--case", case)

    def test_cannot_post_twice_and_duplicate_is_caught_on_the_next_claim(self):
        case = self.approved()
        self.ok("post", "--case", case, "--out", self.home)
        self.refused("already posted", "post", "--case", case, "--out", self.home)
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0002", stdin={"bills": {"B1": "780"}, "lines": [line("B1", "2026-10-13", "taxi", "Cab", "780", "CAB-1093")]})
        self.assertIn("already paid on EXP-0001", self.ok("risk", "--case", "EXP-0002"))

    def test_sheet_has_four_tabs_and_the_audit_trail(self):
        case = self.approved()
        self.ok("settle", "--case", case)
        path = os.path.join(self.home, "s.xlsx")
        self.assertIn("Pay employee 1150.00", self.ok("sheet", "--case", case, "--out", path))
        with zipfile.ZipFile(path) as archive:
            self.assertIn("xl/worksheets/sheet4.xml", archive.namelist())
            self.assertIn("25218.00", archive.read("xl/worksheets/sheet1.xml").decode())
            self.assertIn("manager approved", archive.read("xl/worksheets/sheet4.xml").decode())

    def test_report_after_posting(self):
        case = self.approved()
        before = self.ok("report")
        self.assertIn("AD-311", before)
        self.ok("post", "--case", case, "--out", self.home)
        after = self.ok("report")
        self.assertIn("3 claims posted", after)
        self.assertIn("Department Sales: 35738.00", after)
        self.assertIn("Category per_diem: 3200.00", after)
        self.assertIn("Open advances: none", after)


class Entitlement(Base):
    def test_entitlement_and_estimate(self):
        out = self.ok("entitlement", "--employee", "employee-alex", "--city", "Pune", "--days", "3", "--estimate", "30000")
        self.assertIn("Hotel up to 5000", out)
        self.assertIn("2400 in total", out)
        self.assertIn("manager, finance_head", out)

    def test_unknown_city(self):
        self.refused("no tier", "entitlement", "--employee", "employee-alex", "--city", "Goa", "--days", "1")


class Setup(Base):
    def test_usernames_can_be_changed(self):
        self.ok("setup", "--employee", "priya", "--approver", "sam")
        self.assertEqual(te.employee_by("employee_id", "E1042")["username"], "priya")
        self.assertEqual(te.employee_by("employee_id", "E1042")["manager_username"], "sam")


# A clean claim from a second employee, as in samples/second-claim/.
HARBOUR_GSTIN = "27AAALH0003C1Z" + te.gstin_checksum("27AAALH0003C1Z")
SECOND_BILLS = {
    "bills": {"B1": "4480.00", "B2": "550.00", "B3": "6720.00", "B4": "4480.00"},
    "lines": [
        line("B1", "2026-10-20", "flight", "DEL to BOM", "4000.00", "AIR-72P3-TD", cabin="economy"),
        line("B1", "2026-10-20", "tax", "GST", "480.00", "AIR-72P3-TD", gst_on="flight", vendor_gstin=AIR_GSTIN, billed_to_company=True),
        line("B2", "2026-10-20", "taxi", "Airport to hotel", "550.00", "CAB-2210"),
        line("B3", "2026-10-21", "hotel", "Room, 1 night", "6000.00", "HBR-3381", nights=1),
        line("B3", "2026-10-21", "tax", "GST on room", "720.00", "HBR-3381", gst_on="hotel", vendor_gstin=HARBOUR_GSTIN, billed_to_company=True),
        line("B4", "2026-10-21", "flight", "BOM to DEL", "4000.00", "AIR-74Q9-TD", cabin="economy"),
        line("B4", "2026-10-21", "tax", "GST", "480.00", "AIR-74Q9-TD", gst_on="flight", vendor_gstin=AIR_GSTIN, billed_to_company=True),
    ],
}


class Flow(Base):
    """The claim's path: private room, queue, questions, approval, escalation."""

    def started(self):
        self.ok("intake", "--employee", "employee-alex")
        self.ok("open", "--case", "EXP-0001", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin=BILLS)
        self.ok("next", "--by", "manager-tom")
        return "EXP-0001"

    def board(self, case="EXP-0001"):
        with open(os.path.join(self.home, "tracker.json"), encoding="utf-8") as handle:
            return next(c for c in json.load(handle)["cases"] if c["case"] == case)

    def test_intake_names_the_private_room_and_does_not_repeat(self):
        self.assertIn("claim-exp-0001", self.ok("intake", "--employee", "employee-alex"))
        self.assertEqual(self.board()["stage"], "Waiting for bills")
        self.assertIn("already waiting", self.ok("intake", "--employee", "employee-alex"))
        self.assertEqual(len(te.all_cases()), 1)

    def test_unknown_employee_cannot_start_a_claim(self):
        self.refused("no employee", "intake", "--employee", "stranger")

    def test_bills_need_trip_details_first(self):
        self.ok("intake", "--employee", "employee-alex")
        self.refused("no trip details", "add-bills", "--case", "EXP-0001", stdin=BILLS)

    def test_case_cannot_be_opened_by_another_employee(self):
        self.ok("intake", "--employee", "employee-alex")
        self.refused("belongs to another employee", "open", "--case", "EXP-0001", "--employee", "employee-maya", "--purpose", "x", "--city", "Pune", "--start", "2026-10-13", "--end", "2026-10-14")

    def test_stages_follow_the_work(self):
        self.ok("intake", "--employee", "employee-alex")
        self.ok("open", "--case", "EXP-0001", "--employee", "employee-alex", "--request", "TR-2041")
        self.assertEqual(self.board()["stage"], "Waiting for bills")
        self.ok("add-bills", "--case", "EXP-0001", stdin=BILLS)
        self.assertEqual(self.board()["stage"], "Queued")
        self.assertEqual(self.board()["waiting_on"], "finance")
        self.ok("next", "--by", "manager-tom")
        self.assertEqual(self.board()["stage"], "Checking")
        self.ok("ask", "--case", "EXP-0001", "--question", "Where did you stay on the 15th?")
        self.assertEqual(self.board()["stage"], "Waiting for employee")
        self.assertEqual(self.board()["waiting_on"], "employee-alex")
        self.ok("answer", "--case", "EXP-0001", "--id", "Q1", "--text", "With family", "--by", "employee-alex")
        self.ok("resolve-card", "--case", "EXP-0001", "--txn", "T-9003", "--as", "personal")
        self.ok("set-journey", "--case", "EXP-0001", stdin=[{"date": "2026-10-13", "where": "Pune", "event": "Arrived", "bills": "B1"}])
        self.assertEqual(self.board()["stage"], "Review")
        self.ok("request-approval", "--case", "EXP-0001")
        self.assertEqual(self.board()["stage"], "Approval")
        self.assertEqual(self.board()["waiting_on"], "manager-tom")
        self.ok("decide", "--case", "EXP-0001", "--by", "manager-tom", "--approve-rest")
        self.ok("decide", "--case", "EXP-0001", "--by", "manager-tom", "--approve-rest")
        self.assertEqual(self.board()["waiting_on"], "clerk")
        self.ok("settle", "--case", "EXP-0001")
        self.assertEqual(self.board()["stage"], "Settled")
        self.ok("post", "--case", "EXP-0001", "--out", self.home)
        self.assertEqual(self.board()["stage"], "Posted")
        self.assertEqual(self.board()["total"], "28418.00")

    def test_next_takes_the_oldest_and_refuses_an_empty_queue(self):
        self.refused("nothing is queued", "next", "--by", "manager-tom")
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin=BILLS)
        self.ok("open", "--employee", "employee-maya", "--request", "TR-2055")
        self.ok("add-bills", "--case", "EXP-0002", stdin=SECOND_BILLS)
        self.assertIn("EXP-0001", self.ok("next", "--by", "manager-tom"))
        self.assertIn("EXP-0002", self.ok("next", "--by", "manager-tom"))
        self.refused("nothing is queued", "next", "--by", "manager-tom")

    def test_employee_cannot_start_their_own_claim(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("add-bills", "--case", "EXP-0001", stdin=BILLS)
        self.refused("can't start it", "next", "--by", "employee-alex")

    def test_only_the_claims_employee_can_answer(self):
        case = self.started()
        self.ok("ask", "--case", case, "--question", "Where did you stay?")
        self.refused("can't answer for it", "answer", "--case", case, "--id", "Q1", "--text", "Fine", "--by", "employee-maya")
        self.refused("can't answer for it", "answer", "--case", case, "--id", "Q1", "--text", "Fine", "--by", "manager-tom")
        self.ok("answer", "--case", case, "--id", "Q1", "--text", "With family", "--by", "employee-alex")
        self.refused("already answered", "answer", "--case", case, "--id", "Q1", "--text", "Again", "--by", "employee-alex")
        self.assertIn("With family", self.ok("show", "--case", case))

    def test_open_question_blocks_the_next_steps(self):
        case = self.started()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.ok("ask", "--case", case, "--question", "Where did you stay?")
        self.refused("hasn't answered", "set-journey", "--case", case, stdin=[])
        self.refused("hasn't answered", "request-approval", "--case", case)
        self.refused("hasn't answered", "decide", "--case", case, "--by", "manager-tom", "--approve-rest")

    def test_status_shows_only_that_employees_claims(self):
        self.ok("open", "--employee", "employee-alex", "--request", "TR-2041")
        self.ok("open", "--employee", "employee-maya", "--request", "TR-2055")
        mine = self.ok("status", "--employee", "employee-alex")
        self.assertIn("EXP-0001", mine)
        self.assertNotIn("EXP-0002", mine)
        self.assertNotIn("Delivery review", mine)

    def test_tracker_csv_lists_every_claim(self):
        self.started()
        self.ok("open", "--employee", "employee-maya", "--request", "TR-2055")
        with open(os.path.join(self.home, "tracker.csv"), encoding="utf-8", newline="") as handle:
            rows_ = list(csv.DictReader(handle))
        self.assertEqual([r["case"] for r in rows_], ["EXP-0001", "EXP-0002"])
        self.assertEqual(rows_[0]["stage"], "Checking")
        self.assertIn("EXP-0001", self.ok("queue"))

    def test_nothing_overdue_before_the_limit(self):
        case = self.started()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.ok("request-approval", "--case", case)
        self.assertIn("No approvals are overdue", self.ok("escalate"))
        self.assertFalse(self.board()["overdue"])

    def expired(self):
        self.ok("setup", "--approver", "manager-tom", "--finance-head", "finance-sara", "--expiry-minutes", "0")
        case = self.started()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.ok("request-approval", "--case", case)
        return case

    def test_overdue_approval_moves_up_and_the_old_approver_is_refused(self):
        case = self.expired()
        self.assertTrue(self.board()["overdue"])
        self.assertIn("ESCALATED EXP-0001", self.ok("escalate"))
        self.assertTrue(self.board()["escalated"])
        self.assertEqual(self.board()["waiting_on"], "finance-sara")
        self.refused("which is finance-sara, not manager-tom", "decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        self.ok("decide", "--case", case, "--by", "finance-sara", "--approve-rest")
        self.ok("decide", "--case", case, "--by", "finance-sara", "--approve-rest")
        self.ok("settle", "--case", case)
        trail = [e["action"] for e in te.load_case(case)["audit"]]
        self.assertIn("approval escalated", trail)

    def test_escalation_with_nobody_above_only_reports(self):
        self.ok("setup", "--approver", "manager-tom", "--expiry-minutes", "0")
        case = self.started()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.ok("request-approval", "--case", case)
        self.assertIn("nobody to move it to", self.ok("escalate"))
        self.assertEqual(self.board()["waiting_on"], "manager-tom")

    def test_two_different_approvers_are_both_required(self):
        self.ok("setup", "--approver", "manager-tom", "--finance-head", "finance-sara")
        case = self.started()
        self.ok("resolve-card", "--case", case, "--txn", "T-9003", "--as", "personal")
        self.refused("which is manager-tom", "decide", "--case", case, "--by", "finance-sara", "--approve-rest")
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        self.refused("which is finance-sara", "decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        self.ok("decide", "--case", case, "--by", "finance-sara", "--approve-rest")
        self.assertEqual(te.next_approval(te.load_case(case)), None)

    def test_clean_second_claim_needs_one_approval_and_no_questions(self):
        self.ok("open", "--employee", "employee-maya", "--request", "TR-2055")
        case = "EXP-0001"
        self.ok("add-bills", "--case", case, stdin=SECOND_BILLS)
        self.ok("next", "--by", "manager-tom")
        policy = self.ok("policy", "--case", case)
        self.assertNotIn("EXCEPTION", policy)
        self.assertNotIn("BUDGET", policy)
        self.assertIn("Credit lost 0", self.ok("gst", "--case", case))
        risk = self.ok("risk", "--case", case)
        self.assertNotIn("NO BILL", risk)
        self.assertNotIn("DUPLICATE", risk)
        self.ok("decide", "--case", case, "--by", "manager-tom", "--approve-rest")
        settled = self.ok("settle", "--case", case)
        self.assertIn("TOTAL EXPENSE: 18230.00", settled)
        self.assertIn("NET: Pay employee 2550.00", settled)
        self.assertIn("balances", self.ok("post", "--case", case, "--out", self.home))


if __name__ == "__main__":
    unittest.main()
