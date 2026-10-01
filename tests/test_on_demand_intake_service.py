import unittest

from app.services.on_demand_intake_service import parse_address


class OnDemandAddressParsingTests(unittest.TestCase):
    def test_common_malformed_source_layouts_use_shared_parser(self):
        cases = [
            (
                "444 S Broad St  Brevard, NC 28712",
                ("444 S Broad St", "Brevard", "NC", "28712"),
            ),
            (
                "1015 Barbara Jean Lane, Wingate, NC 28174 ,  Wingate ,   28174",
                ("1015 Barbara Jean Lane", "Wingate", "NC", "28174"),
            ),
            (
                "105 Chestnut Cir Lake Lure, NC 28746",
                ("105 Chestnut Cir", "Lake Lure", "NC", "28746"),
            ),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                parsed = parse_address(raw)
                actual = tuple(parsed[field] for field in (
                    "address_1", "city", "state", "postal_code",
                ))
                self.assertEqual(actual, expected)

    def test_street_suffix_and_unit_text_are_not_mistaken_for_a_city(self):
        cases = [
            "7221 Waverly Walk Ave, Charlotte, NC 28277",
            "123 Main St Suite A, Raleigh, NC 27601",
        ]

        expected_streets = ["7221 Waverly Walk Ave", "123 Main St Suite A"]
        for raw, expected_street in zip(cases, expected_streets):
            with self.subTest(raw=raw):
                self.assertEqual(parse_address(raw)["address_1"], expected_street)

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
