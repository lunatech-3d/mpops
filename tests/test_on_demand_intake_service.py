import unittest

from app.services.on_demand_intake_service import parse_address


class OnDemandAddressParsingTests(unittest.TestCase):
    def test_space_delimited_zip_plus_four_in_separate_component(self):
        parsed = parse_address(
            "7221 Waverly Walk Ave, Charlotte, NC, 28277 8030"
        )

        self.assertEqual(parsed, {
            "address_1": "7221 Waverly Walk Ave",
            "address_2": None,
            "city": "Charlotte",
            "state": "NC",
            "postal_code": "28277-8030",
            "country": None,
        })

    def test_zip_plus_four_variants_are_normalized_with_country(self):
        for source_zip in ("28277-8030", "28277 8030", "282778030"):
            with self.subTest(source_zip=source_zip):
                parsed = parse_address(
                    f"7221 Waverly Walk Ave, Charlotte, NC, {source_zip}, USA"
                )
                self.assertEqual(parsed["postal_code"], "28277-8030")
                self.assertEqual(parsed["country"], "USA")

    def test_space_delimited_zip_plus_four_beside_state(self):
        parsed = parse_address(
            "7221 Waverly Walk Ave, Charlotte, NC 28277 8030"
        )

        self.assertEqual(parsed["address_1"], "7221 Waverly Walk Ave")
        self.assertEqual(parsed["city"], "Charlotte")
        self.assertEqual(parsed["state"], "NC")
        self.assertEqual(parsed["postal_code"], "28277-8030")

    def test_space_delimited_zip_plus_four_beside_state_with_country(self):
        parsed = parse_address(
            "7221 Waverly Walk Ave, Charlotte, NC 28277 8030, USA"
        )

        self.assertEqual(parsed["address_1"], "7221 Waverly Walk Ave")
        self.assertEqual(parsed["city"], "Charlotte")
        self.assertEqual(parsed["state"], "NC")
        self.assertEqual(parsed["postal_code"], "28277-8030")
        self.assertEqual(parsed["country"], "USA")


if __name__ == "__main__":
    unittest.main()
