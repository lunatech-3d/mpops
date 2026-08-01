import unittest

from app.services.matterport_email_parser import parse_matterport_payment_email


HEADER = """Dear LunaTech 3D,

A USD 1,540.78 payment was sent to you today by ACH and covers the following:

Amount      Type     Document number        Document date
"""


class MatterportEmailParserTests(unittest.TestCase):
    def test_valid_email_extracts_header_and_invoice(self):
        result = parse_matterport_payment_email(
            "Date: Fri, 24 Jul 2026 09:30:00 -0400\n"
            "Subject: Matterport payment notification\n" + HEADER +
            "USD 326.04  Invoice  AP-rec1ZrtnPyo5sE9a5   7/24/2026\n"
        )
        self.assertEqual(result["header"]["payment_amount_cents"], 154078)
        self.assertEqual(result["header"]["payment_method"], "ACH")
        self.assertEqual(result["header"]["payment_date"], "2026-07-24")
        self.assertEqual(result["header"]["payer_name"], "Matterport")
        self.assertEqual(result["header"]["source_email_subject"],
                         "Matterport payment notification")
        self.assertEqual(result["rows"][0]["document_number"], "AP-rec1ZrtnPyo5sE9a5")

    def test_multiple_invoices(self):
        text = HEADER + """USD 326.04  Invoice  AP-rec1   7/24/2026
USD 297.06  Invoice  AP-rec2   7/6/2026
USD 420.60  Invoice  AP-rec3   7/21/2026
"""
        result = parse_matterport_payment_email(text)
        self.assertEqual(result["summary"]["valid_count"], 3)
        self.assertEqual(result["summary"]["importable_total_cents"], 104370)
        self.assertEqual(result["rows"][1]["document_date"], "2026-07-06")
        self.assertEqual(result["rows"][2]["document_type"], "Invoice")

    def test_unknown_but_present_invoice_is_valid_without_job_lookup(self):
        result = parse_matterport_payment_email(
            HEADER + "USD 25.00 Invoice TIPALTI-ON-DEMAND-42 7/24/2026\n")
        self.assertEqual(result["rows"][0]["status"], "Valid")
        self.assertEqual(result["rows"][0]["document_number"],
                         "TIPALTI-ON-DEMAND-42")

    def test_malformed_email(self):
        with self.assertRaisesRegex(ValueError, "payment amount and payment method"):
            parse_matterport_payment_email("This is not a payment notification")

    def test_missing_amount_is_invalid(self):
        result = parse_matterport_payment_email(
            HEADER + "USD          Invoice  AP-rec1   7/24/2026\n")
        self.assertEqual(result["rows"][0]["status"], "Invalid")
        self.assertIn("Amount is required", result["rows"][0]["message"])

    def test_missing_invoice_number_is_invalid(self):
        result = parse_matterport_payment_email(
            HEADER + "USD 326.04  Invoice  7/24/2026\n")
        self.assertEqual(result["rows"][0]["status"], "Invalid")
        self.assertIn("Invoice number is required", result["rows"][0]["message"])

    def test_duplicate_invoice_is_not_importable(self):
        result = parse_matterport_payment_email(
            HEADER + "USD 10.00 Invoice AP-rec1 7/24/2026\n"
                     "USD 20.00 Invoice ap-REC1 7/24/2026\n")
        self.assertEqual(result["summary"]["duplicate_count"], 1)
        self.assertEqual(result["summary"]["importable_total_cents"], 1000)

    def test_extra_blank_lines(self):
        result = parse_matterport_payment_email(
            HEADER + "\n\nUSD 10.00 Invoice AP-rec1 7/24/2026\n\n")
        self.assertEqual(result["summary"]["valid_count"], 1)

    def test_windows_crlf(self):
        text = (HEADER + "USD 10.00 Invoice AP-rec1 7/24/2026\n").replace("\n", "\r\n")
        self.assertEqual(parse_matterport_payment_email(text)["summary"]["valid_count"], 1)

    def test_unix_lf(self):
        result = parse_matterport_payment_email(
            HEADER + "USD 10.00 Invoice AP-rec1 7/24/2026\n")
        self.assertEqual(result["summary"]["valid_count"], 1)


if __name__ == "__main__":
    unittest.main()
