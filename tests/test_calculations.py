"""Deterministic synthetic fixtures, not finance or inventory business records."""

import copy
import unittest
from decimal import localcontext

from assistant_app.calculations import CalculationError, SCOPE, summarize_rows, validate_rows


SOURCES = [{"id": "fixture-source-1", "text": "隔离原记录夹具"}]


def row(row_id="row-1", value="0.1", domain="expense", dimension=None, period="2026-10"):
    field = "currency" if domain == "expense" else "unit"
    return {"id": row_id, "label": "工程夹具项目", "value": value,
            field: dimension or ("NGN" if domain == "expense" else "件"),
            "period": period, "source_id": SOURCES[0]["id"], "source_position": "原表第2行"}


class CalculationContracts(unittest.TestCase):
    def test_decimal_precision_and_input_preservation(self):
        rows = [row(value="0.1"), row("row-2", "0.2")]
        before = copy.deepcopy(rows)
        normalized = validate_rows("expense", rows, SOURCES)
        with localcontext() as context:
            context.prec = 2
            summary = summarize_rows("expense", normalized)
        self.assertEqual(summary["groups"][0]["total"], "0.3")
        self.assertEqual(summary["groups"][0]["row_ids"], ["row-1", "row-2"])
        self.assertEqual(summary["scope"], SCOPE)
        self.assertEqual(rows, before)
        self.assertIsNot(normalized[0], rows[0])

    def test_fractional_precision_and_explicit_negative_adjustments(self):
        rows = validate_rows("expense", [row(value="1.230000"), row("negative", "-0.200000")], SOURCES)
        self.assertEqual(rows[0]["value"], "1.230000")
        self.assertEqual(summarize_rows("expense", rows)["groups"][0]["total"], "1.030000")
        self.assertEqual(validate_rows("expense", [row(value="+0.10")], SOURCES)[0]["value"], "0.10")

    def test_currency_and_period_never_merge_or_convert(self):
        rows = [row("a", "10", dimension="NGN"), row("b", "2", dimension="USD"),
                row("c", "3", dimension="NGN", period="2026-11")]
        summary = summarize_rows("expense", validate_rows("expense", rows, SOURCES))
        self.assertEqual(summary["groups"], [
            {"currency": "NGN", "period": "2026-10", "total": "10", "row_ids": ["a"]},
            {"currency": "USD", "period": "2026-10", "total": "2", "row_ids": ["b"]},
            {"currency": "NGN", "period": "2026-11", "total": "3", "row_ids": ["c"]}])
        self.assertEqual(summary["row_count"], 3)

    def test_unit_and_period_never_merge(self):
        rows = [row("a", "0.1", "inventory", "kg"), row("b", "0.2", "inventory", "kg"),
                row("c", "5", "inventory", "件"), row("d", "2", "inventory", "kg", "2026-11")]
        summary = summarize_rows("inventory", validate_rows("inventory", rows, SOURCES))
        self.assertEqual([(g["unit"], g["period"], g["total"]) for g in summary["groups"]],
                         [("kg", "2026-10", "0.3"), ("件", "2026-10", "5"), ("kg", "2026-11", "2")])
        self.assertTrue(all("currency" not in group for group in summary["groups"]))

    def test_different_items_with_same_unit_and_period_never_merge(self):
        rows = [row("apple", "3", "inventory", "件"), row("screw", "5", "inventory", "件")]
        rows[0]["label"], rows[1]["label"] = "苹果", "螺丝"
        summary = summarize_rows("inventory", validate_rows("inventory", rows, SOURCES))
        self.assertEqual(summary["groups"], [
            {"label": "苹果", "unit": "件", "period": "2026-10", "total": "3", "row_ids": ["apple"]},
            {"label": "螺丝", "unit": "件", "period": "2026-10", "total": "5", "row_ids": ["screw"]}])

    def test_missing_extra_or_wrong_domain_fields_rejected(self):
        for key in row():
            value = row()
            del value[key]
            with self.subTest(missing=key), self.assertRaises(CalculationError):
                validate_rows("expense", [value], SOURCES)
        value = row()
        value["approved"] = True
        with self.assertRaises(CalculationError):
            validate_rows("expense", [value], SOURCES)
        with self.assertRaises(CalculationError):
            validate_rows("inventory", [row()], SOURCES)
        for domain in ("travel", "general", None, [], 1):
            with self.assertRaises(CalculationError):
                summarize_rows(domain, [])

    def test_illegal_and_missing_numbers_are_never_zero(self):
        for value in (None, True, False, 0, 0.1, "", " ", "NaN", "Infinity", "-Infinity", "1e3", ".2", "1.",
                      "1,000", "1 000", "１", "1\n", "1234567890123456789", "0.1234567"):
            with self.subTest(value=value), self.assertRaises(CalculationError):
                validate_rows("expense", [row(value=value)], SOURCES)
            with self.assertRaises(CalculationError):
                summarize_rows("expense", [row(value=value)])

    def test_duplicate_or_unsafe_ids_rejected(self):
        with self.assertRaises(CalculationError):
            validate_rows("expense", [row(), row(value="10")], SOURCES)
        for row_id in (None, True, "", "a" * 81, "../a", "a b", "行1", "a\n"):
            with self.assertRaises(CalculationError):
                validate_rows("expense", [row(row_id)], SOURCES)

    def test_unconfirmed_dimensions_period_or_location_rejected(self):
        for field in ("label", "currency", "period", "source_id", "source_position"):
            for value in (None, "", " ", 1, True):
                item = row()
                item[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(CalculationError):
                    validate_rows("expense", [item], SOURCES)
        for field in ("currency", "period", "source_position"):
            for value in ("unknown", "待核", "未知", "2026-10（待核）", "TBD"):
                item = row()
                item[field] = value
                with self.assertRaises(CalculationError):
                    validate_rows("expense", [item], SOURCES)
        with self.assertRaises(CalculationError):
            validate_rows("inventory", [row(domain="inventory", dimension="待核")], SOURCES)

    def test_sources_must_belong_to_current_matter(self):
        with self.assertRaises(CalculationError):
            validate_rows("expense", [row()], [{"id": "other-matter-source"}])
        with self.assertRaises(CalculationError):
            validate_rows("expense", [row()], [])
        for sources in (None, {}, [None], [{}], [True], [{"id": 1}], SOURCES * 2):
            with self.assertRaises(CalculationError):
                validate_rows("expense", [row()], sources)

    def test_200_row_limit_and_large_exact_total(self):
        rows = [row(f"r-{index}", "999999999999999999.999999") for index in range(200)]
        summary = summarize_rows("expense", validate_rows("expense", rows, SOURCES))
        self.assertEqual(summary["groups"][0]["total"], "199999999999999999999.999800")
        self.assertEqual(summary["row_count"], 200)
        with self.assertRaises(CalculationError):
            validate_rows("expense", rows + [row("extra")], SOURCES)
        for rows in (None, {}, "", [None], [1]):
            with self.assertRaises(CalculationError):
                validate_rows("expense", rows, SOURCES)

    def test_empty_rows_and_explicit_zero_are_distinct(self):
        self.assertEqual(summarize_rows("expense", validate_rows("expense", [], [])),
                         {"groups": [], "row_count": 0, "scope": SCOPE})
        self.assertEqual(summarize_rows("expense", [row(value="0.000000")])["groups"][0]["total"], "0.000000")


if __name__ == "__main__":
    unittest.main()
