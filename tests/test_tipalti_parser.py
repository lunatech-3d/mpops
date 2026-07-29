import unittest
from app.services.tipalti_parser import mark_imported_duplicates, parse_tipalti_text


class TipaltiParserTests(unittest.TestCase):
    def test_tab_headers_aliases_reordered_and_normalized(self):
        result = parse_tipalti_text(" Amount Paid \tMemo\tInvoice #\tType\tInvoice Date\n$1,234.56\t Work \t 001-A \t Invoice \t7/24/2026\n")
        self.assertTrue(result["headers_detected"])
        row = result["rows"][0]
        self.assertEqual((row["document_number"], row["document_date"], row["amount_received_cents"]), ("001-A", "2026-07-24", 123456))
        self.assertEqual(result["summary"]["importable_total_cents"], 123456)

    def test_comma_delimited_quoted_fields(self):
        result = parse_tipalti_text('Reference,Payment Type,Date,Details,Net Amount\nMP-1,Invoice,"Jul 24, 2026",Capture,"$1,234.56"')
        self.assertEqual(result["rows"][0]["document_date"], "2026-07-24")
        self.assertEqual(result["rows"][0]["amount_received_cents"], 123456)

    def test_headerless_fallback_and_ambiguous_rejection(self):
        result = parse_tipalti_text("MP-1\tInvoice\t07/24/2026\tCapture\t152.50")
        self.assertFalse(result["headers_detected"]); self.assertEqual(result["summary"]["valid_count"], 1)
        with self.assertRaisesRegex(ValueError, "header row"):
            parse_tipalti_text("MP-1,Invoice,152.50")

    def test_blank_rows_ignored_and_duplicates_marked(self):
        result = parse_tipalti_text("Document #\tAmount\n\n ABC \t10\nabc\t20\n")
        self.assertEqual(result["summary"], {"row_count": 2, "valid_count": 1, "duplicate_count": 1, "invalid_count": 0, "importable_total_cents": 1000})
        self.assertEqual(result["rows"][1]["message"], "Duplicate document number in pasted data")

    def test_dates_and_invalid_fields(self):
        text = "Document Number\tDocument Date\tAmount\n\t02/30/2026\t\nOK\tJuly 24, 2026\t10.001"
        result = parse_tipalti_text(text)
        self.assertEqual(result["summary"]["invalid_count"], 2)
        self.assertIn("Document number is required", result["rows"][0]["message"])
        self.assertIn("Document date is invalid", result["rows"][0]["message"])
        self.assertIn("two decimal", result["rows"][1]["message"])

    def test_currency_variations_and_negative_rejection(self):
        for value, cents in (("152.50", 15250), ("$152.50", 15250), ("1,234.56", 123456), ("$1,234.56", 123456)):
            with self.subTest(value=value):
                result = parse_tipalti_text(f"D\tInvoice\t\tX\t{value}")
                self.assertEqual(result["rows"][0]["amount_received_cents"], cents)
        for value in ("(152.50)", "-$152.50"):
            result = parse_tipalti_text(f"D\tInvoice\t\tX\t{value}")
            self.assertEqual(result["rows"][0]["status"], "Invalid")
            self.assertIn("Negative", result["rows"][0]["message"])

    def test_existing_duplicate_overlay(self):
        result = parse_tipalti_text("Document Number\tAmount\nABC\t1")
        mark_imported_duplicates(result, {"abc"})
        self.assertEqual(result["summary"]["duplicate_count"], 1)
        self.assertEqual(result["summary"]["importable_total_cents"], 0)


if __name__ == "__main__": unittest.main()
