"""Focused tests for Technician Details -> Jobs sorting and searching."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.ui.technician_finance_view import (
    JOB_COLUMN_WIDTHS,
    JOB_COLUMNS,
    TechnicianFinanceView,
    search_technician_jobs,
    technician_job_sort_value,
    technician_job_visible_values,
)
from app.ui.technician_manager import (TECHNICIAN_DETAILS_DEFAULT_SIZE,
                                       TECHNICIAN_DETAILS_MIN_SIZE)
from app.ui.treeview_utils import ordered_tree_items


def job(job_id, **changes):
    row = {
        "job_id": job_id,
        "external_job_id": f"JOB-{job_id}",
        "project_name_source": f"Project {job_id}",
        "job_address": "1 Main Street",
        "scheduled_start_at": "2026-08-07T10:00:00",
        "completed_at": None,
        "job_status": "Scheduled",
        "finance_status": "Calculated—not generated",
        "earned_cents": 12500,
        "base_pay_cents": 10000,
        "travel_pay_cents": 2500,
        "paid_cents": 0,
        "approved_due_cents": 12500,
    }
    row.update(changes)
    return row


def sorted_jobs(rows, column, descending=False):
    return ordered_tree_items(
        rows, lambda row: technician_job_sort_value(row, column), descending
    )


class TechnicianJobsSortingTests(unittest.TestCase):
    def test_text_sort_ascends_descends_naturally_and_ignores_case(self):
        rows = [job(1, external_job_id="job-10"), job(2, external_job_id="JOB-2")]
        self.assertEqual([row["job_id"] for row in sorted_jobs(rows, "job")], [2, 1])
        self.assertEqual([row["job_id"] for row in sorted_jobs(rows, "job", True)], [1, 2])

    def test_dates_sort_chronologically(self):
        rows = [job(1, scheduled_start_at="2026-12-01T09:00:00"),
                job(2, scheduled_start_at="2025-01-31T09:00:00")]
        self.assertEqual([row["job_id"] for row in sorted_jobs(rows, "date")], [2, 1])

    def test_full_and_blank_dates_use_shared_display_convention(self):
        full = technician_job_visible_values(job(1, completed_at="2025-09-27T16:30:00"))
        blank = technician_job_visible_values(
            job(2, completed_at=None, scheduled_start_at=None)
        )
        self.assertEqual(full["date"], "09/27/2025")
        self.assertEqual(blank["date"], "")

    def test_date_presentation_does_not_mutate_stored_value(self):
        row = job(1, scheduled_start_at="2025-09-27T16:30:00")
        before = dict(row)
        technician_job_visible_values(row)
        self.assertEqual(row, before)

    def test_invalid_legacy_date_remains_distinguishable(self):
        row = job(1, scheduled_start_at="legacy-unknown")
        self.assertEqual(technician_job_visible_values(row)["date"], "legacy-unknown")

    def test_currency_uses_typed_cents_including_commas_and_negatives(self):
        rows = [job(1, earned_cents=104609), job(2, earned_cents=-500),
                job(3, earned_cents=12500)]
        self.assertEqual([row["job_id"] for row in sorted_jobs(rows, "earned")], [2, 3, 1])
        self.assertEqual(technician_job_visible_values(rows[0])["earned"], "$1,046.09")

    def test_blank_and_dash_values_remain_last_in_both_directions(self):
        rows = [job(1, paid_cents=None), job(2, paid_cents=100), job(3, paid_cents=200)]
        self.assertEqual([row["job_id"] for row in sorted_jobs(rows, "paid")], [2, 3, 1])
        self.assertEqual([row["job_id"] for row in sorted_jobs(rows, "paid", True)], [3, 2, 1])

    def test_sorting_a_searched_result_uses_only_that_result(self):
        rows = [job(1, project_name_source="Keep Z"), job(2, project_name_source="Ignore"),
                job(3, project_name_source="Keep A")]
        found = search_technician_jobs(rows, "keep")
        self.assertEqual([row["job_id"] for row in sorted_jobs(found, "project")], [3, 1])

    def test_sorting_a_changed_filter_result_uses_only_that_result(self):
        all_rows = [job(1, job_status="Completed"), job(2, job_status="Scheduled")]
        completed = [row for row in all_rows if row["job_status"] == "Completed"]
        self.assertEqual([row["job_id"] for row in sorted_jobs(completed, "job")], [1])


class TechnicianJobsSearchTests(unittest.TestCase):
    def setUp(self):
        self.row = job(1046, project_name_source="Apollo Atrium", job_status="Completed",
                       finance_status="Approved", earned_cents=104609,
                       base_pay_cents=100000, travel_pay_cents=4609,
                       paid_cents=50000, approved_due_cents=54609)

    def test_full_and_partial_job_id(self):
        self.assertEqual(search_technician_jobs([self.row], "JOB-1046"), [self.row])
        self.assertEqual(search_technician_jobs([self.row], "104"), [self.row])

    def test_project_search_is_partial_and_case_insensitive(self):
        self.assertEqual(search_technician_jobs([self.row], "aTrIu"), [self.row])

    def test_status_searches_job_and_earnings_status(self):
        self.assertEqual(search_technician_jobs([self.row], "completed"), [self.row])
        self.assertEqual(search_technician_jobs([self.row], "approved"), [self.row])

    def test_formatted_and_plain_currency_search(self):
        self.assertEqual(search_technician_jobs([self.row], "$1,046.09"), [self.row])
        self.assertEqual(search_technician_jobs([self.row], "1046.09"), [self.row])

    def test_every_visible_column_is_searchable(self):
        visible = technician_job_visible_values(self.row)
        self.assertEqual(set(visible), set(JOB_COLUMNS))
        for column, value in visible.items():
            with self.subTest(column=column, value=value):
                self.assertEqual(search_technician_jobs([self.row], str(value)), [self.row])

    def test_no_match_and_empty_query_clear_behavior(self):
        rows = [self.row]
        self.assertEqual(search_technician_jobs(rows, "does-not-exist"), [])
        self.assertEqual(search_technician_jobs(rows, ""), rows)
        self.assertEqual(search_technician_jobs(rows, "   "), rows)

    def test_search_is_limited_to_rows_supplied_by_selected_filter_and_technician(self):
        completed_for_tech = [self.row]
        upcoming_for_tech = [job(2, project_name_source="Other project")]
        another_technicians_job = job(3, project_name_source="Apollo Secret")
        self.assertEqual(search_technician_jobs(completed_for_tech, "Apollo"), [self.row])
        self.assertEqual(search_technician_jobs(upcoming_for_tech, "Apollo"), [])
        self.assertNotIn(another_technicians_job,
                         search_technician_jobs(completed_for_tech, "Apollo"))

    def test_search_and_sort_do_not_mutate_rows_or_financial_values(self):
        rows = [self.row]
        before = dict(self.row)
        sorted_jobs(search_technician_jobs(rows, "Apollo"), "earned")
        self.assertEqual(self.row, before)
        self.assertEqual(self.row["earned_cents"], 104609)
        self.assertEqual(self.row["finance_status"], "Approved")


class TechnicianJobsInteractionTests(unittest.TestCase):
    class Tree:
        def __init__(self, region="cell", row="job-42"):
            self.region, self.row = region, row
            self.selected = None

        def identify_region(self, _x, _y): return self.region
        def identify_row(self, _y): return self.row
        def selection_set(self, iid): self.selected = iid
        def focus(self, _iid): pass

    def view(self, tree=None):
        view = TechnicianFinanceView.__new__(TechnicianFinanceView)
        view.jobs_tree = tree or self.Tree()
        view.job_rows = {"job-42": job(42)}
        view.auth = object()
        view.job_opener = Mock()
        view.refresh = Mock()
        return view

    def test_double_click_passes_internal_job_id_to_shared_opener(self):
        view = self.view()
        view._open_double_clicked_job(SimpleNamespace(x=4, y=8))
        view.job_opener.assert_called_once_with(view, view.auth, 42, wait=True)
        view.refresh.assert_called_once_with()
        self.assertEqual(view.jobs_tree.selected, "job-42")

    def test_double_click_empty_space_or_missing_row_is_safe(self):
        for tree in (self.Tree(region="nothing"), self.Tree(row="")):
            view = self.view(tree)
            view._open_double_clicked_job(SimpleNamespace(x=4, y=8))
            view.job_opener.assert_not_called()
            view.refresh.assert_not_called()

    def test_layout_allocates_project_the_largest_width(self):
        self.assertEqual(TECHNICIAN_DETAILS_DEFAULT_SIZE, (1400, 800))
        self.assertEqual(TECHNICIAN_DETAILS_MIN_SIZE, (1050, 650))
        self.assertGreater(JOB_COLUMN_WIDTHS["project"][0],
                           max(width for column, (width, _minimum) in
                               JOB_COLUMN_WIDTHS.items() if column != "project"))


if __name__ == "__main__":
    unittest.main()
