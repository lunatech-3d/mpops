import unittest
from app.services.tipalti_parser import mark_imported_duplicates, parse_tipalti_text


class TipaltiParserTests(unittest.TestCase):
    def test_related_invoices_table_format(self):
        text = ("Invoice date    Invoice number    Invoice subject    Invoice amount    Amount submitted\r\n"
                "Jul 13, 2026    AP-xxxxx    Address...    USD 100.00    USD 100.00\r\n")
        result = parse_tipalti_text(text)
        self.assertTrue(result["headers_detected"])
        self.assertEqual(result["summary"]["valid_count"], 1)
        self.assertEqual(result["rows"][0]["document_number"], "AP-xxxxx")
        self.assertEqual(result["rows"][0]["description_raw"], "Address...")
        self.assertEqual(result["rows"][0]["amount_received_cents"], 10000)

    def test_full_payment_details_page_with_vertical_browser_table(self):
        text = """Payment Details\r
Status: Paid\r
Value date: Jul 22, 2026\r
Payer reference code: ap-recuq1iwiwoxi\r
Transaction reference: 104230140689214\r
Amount submitted:\r
USD6,089.60\r
Transaction fee:\r
USD0.00\r
Net amount:\r
USD6,089.60\r
Amount paid:\r
USD6,089.60\r
\r
Related Invoices\r
\r
Invoice date\r
Invoice number\r
Invoice subject\r
Invoice amount\r
Amount submitted\r
\r
Jul 13, 2026\r
AP-rec-1\r
6370 Wilcox Rd...\r
USD 908.96\r
USD 908.96\r
\r
Jul 14, 2026\r
AP-rec-2\r
Second invoice\r
USD1,100.00\r
USD1,100.00\r
"""
        result = parse_tipalti_text(text)
        self.assertEqual(result["summary"], {
            "row_count": 2, "valid_count": 2, "duplicate_count": 0,
            "invalid_count": 0, "importable_total_cents": 200896,
        })
        self.assertEqual(result["rows"][0]["source_row_number"], 23)
        self.assertEqual(result["rows"][1]["document_date"], "2026-07-14")

    def test_malformed_payment_details_clipboard_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Related Invoices"):
            parse_tipalti_text("Payment Details\nStatus: Paid\nAmount submitted:\nUSD10.00")
        with self.assertRaisesRegex(ValueError, "header"):
            parse_tipalti_text("Payment Details\nRelated Invoices\nInvoice date\nInvoice subject\nJul 13, 2026\nSomething")

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
        result = parse_tipalti_text("Document #\tDocument Date\tAmount\n\n ABC \t7/24/2026\t10\nabc\t7/24/2026\t20\n")
        self.assertEqual(result["summary"], {"row_count": 2, "valid_count": 1, "duplicate_count": 1, "invalid_count": 0, "importable_total_cents": 1000})
        self.assertEqual(result["rows"][1]["message"], "Duplicate document number in pasted data")

    def test_dates_and_invalid_fields(self):
        text = "Document Number\tDocument Date\tAmount\n\t02/30/2026\t\nOK\tJuly 24, 2026\t10.001"
        result = parse_tipalti_text(text)
        self.assertEqual(result["summary"]["invalid_count"], 2)
        self.assertIn("Document number is required", result["rows"][0]["message"])
        self.assertIn("Document date is invalid", result["rows"][0]["message"])
        self.assertIn("two decimal", result["rows"][1]["message"])

    def test_missing_document_date_is_invalid(self):
        result = parse_tipalti_text("Document Number\tAmount\nAP-1\t10")
        self.assertEqual(result["rows"][0]["status"], "Invalid")
        self.assertIn("Document date is required", result["rows"][0]["message"])

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
        result = parse_tipalti_text("Document Number\tDocument Date\tAmount\nABC\t7/24/2026\t1")
        mark_imported_duplicates(result, {"abc"})
        self.assertEqual(result["summary"]["duplicate_count"], 1)
        self.assertEqual(result["summary"]["importable_total_cents"], 0)


if __name__ == "__main__": unittest.main()
