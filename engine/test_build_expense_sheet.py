import copy
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_expense_sheet import ClaimError, build, validate  # noqa: E402


def line(bill, date, category, description, amount, invoice, status="approved", **extra):
    return {"bill": bill, "date": date, "category": category, "merchant": "M", "description": description,
            "amount": amount, "invoice": invoice, "currency": "INR", "status": status, **extra}


# The sample Pune trip in samples/, as the agents should end up describing it.
TRIP = {
    "employee": "Alex Mehta", "trip": "Pune client visit", "base_currency": "INR", "approved_by": "manager",
    "bills": {"B1": "6240.00", "B2": "780.00", "B3": "11268.00", "B4": "420.00", "B5": "420.00", "B6": "6510.00"},
    "lines": [
        line("B1", "2026-10-13", "flight", "DEL to PNQ", "6240.00", "AIR-58K2-TD"),
        line("B2", "2026-10-13", "taxi", "Airport to hotel", "780.00", "CAB-1093"),
        line("B3", "2026-10-15", "hotel", "Room, 2 nights", "8400.00", "HTL-7Q42-KX"),
        line("B3", "2026-10-14", "meal", "Restaurant dinner", "1860.00", "HTL-7Q42-KX", note="Over limit, approved"),
        line("B3", "2026-10-15", "tax", "GST on room", "1008.00", "HTL-7Q42-KX"),
        line("B4", "2026-10-14", "taxi", "Hotel to client office", "420.00", "CAB-1147"),
        line("B5", "2026-10-14", "taxi", "Hotel to client office", "420.00", "CAB-1147", status="rejected",
             note="Duplicate of B4"),
        line("B6", "2026-10-16", "flight", "PNQ to DEL", "6510.00", "AIR-61M7-TD"),
    ],
    "journey": [{"date": "2026-10-13", "where": "Delhi to Pune", "event": "Flew out", "bills": "B1, B2"}],
}


class ValidateTests(unittest.TestCase):
    def test_sample_trip_total_excludes_the_duplicate(self):
        _, totals = validate(TRIP)
        self.assertEqual(sum(totals.values()), Decimal("25218.00"))

    def test_lines_are_chronological(self):
        lines, _ = validate(TRIP)
        days = [item["day"] for item in lines]
        self.assertEqual(days, sorted(days))

    def refused(self, claim, text):
        with self.assertRaises(ClaimError) as caught:
            validate(claim)
        self.assertIn(text, str(caught.exception))

    def test_refuses_lines_that_do_not_match_the_printed_total(self):
        claim = copy.deepcopy(TRIP)
        claim["lines"][2]["amount"] = "8500.00"
        self.refused(claim, "bill B3: lines add up to 11368.00, the bill says 11268.00")

    def test_refuses_the_same_item_claimed_twice(self):
        claim = copy.deepcopy(TRIP)
        claim["lines"][6]["status"] = "approved"
        self.refused(claim, "CAB-1147 / Hotel to client office is claimed twice")

    def test_refuses_foreign_currency_without_a_rate(self):
        claim = copy.deepcopy(TRIP)
        claim["lines"][0]["currency"] = "USD"
        self.refused(claim, "USD needs an fx_rate to INR")

    def test_converts_foreign_currency_with_a_rate(self):
        claim = copy.deepcopy(TRIP)
        claim["bills"]["B1"] = "100.00"
        claim["lines"][0].update(currency="USD", amount="100.00", fx_rate="83.5")
        _, totals = validate(claim)
        self.assertEqual(totals["flight"], Decimal("14860.00"))

    def test_refuses_a_bill_with_no_lines(self):
        claim = copy.deepcopy(TRIP)
        claim["bills"]["B9"] = "10.00"
        self.refused(claim, "bill B9: printed total 10.00 has no lines")

    def test_refuses_a_line_with_no_printed_total(self):
        claim = copy.deepcopy(TRIP)
        del claim["bills"]["B6"]
        self.refused(claim, "bill B6: lines exist but no printed total")

    def test_refuses_a_missing_approver(self):
        claim = copy.deepcopy(TRIP)
        claim["approved_by"] = ""
        self.refused(claim, "missing approved_by")

    def test_refuses_a_bad_date_and_reports_every_problem(self):
        claim = copy.deepcopy(TRIP)
        claim["lines"][0]["date"] = "13/10/2026"
        claim["lines"][1]["category"] = "fun"
        with self.assertRaises(ClaimError) as caught:
            validate(claim)
        self.assertIn("not a YYYY-MM-DD date", str(caught.exception))
        self.assertIn("category must be one of", str(caught.exception))


class WorkbookTests(unittest.TestCase):
    def test_workbook_has_three_valid_sheets_and_the_right_total(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sheet.xlsx")
            self.assertEqual(build(TRIP, path), Decimal("25218.00"))
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                for name in archive.namelist():
                    ET.fromstring(archive.read(name))
                expenses = archive.read("xl/worksheets/sheet1.xml").decode()
                self.assertIn("<f>SUM(J5:J12)</f><v>25218.00</v>", expenses)
                self.assertIn("xl/worksheets/sheet3.xml", archive.namelist())

    def test_text_is_escaped(self):
        claim = copy.deepcopy(TRIP)
        claim["employee"] = "A & B <co>"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sheet.xlsx")
            build(claim, path)
            with zipfile.ZipFile(path) as archive:
                ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))


if __name__ == "__main__":
    unittest.main()
