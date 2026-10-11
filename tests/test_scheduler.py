import unittest
from datetime import datetime, timedelta

import scheduler


def make_job(run_at=datetime(2026, 10, 12, 9, 0), repeat="none", **overrides):
    job = scheduler.new_job(
        content="Xin chào", page_url="", image_path="", run_at=run_at, repeat=repeat
    )
    job.update(overrides)
    return job


class ParseRunAtTest(unittest.TestCase):
    def test_parses_date_and_time(self):
        self.assertEqual(
            scheduler.parse_run_at("2026-10-12 09:30"), datetime(2026, 10, 12, 9, 30)
        )

    def test_ignores_surrounding_spaces(self):
        self.assertEqual(
            scheduler.parse_run_at("  2026-10-12 09:30 "), datetime(2026, 10, 12, 9, 30)
        )

    def test_rejects_wrong_format(self):
        with self.assertRaises(ValueError):
            scheduler.parse_run_at("12/10/2026 9h30")

    def test_rejects_impossible_date(self):
        with self.assertRaises(ValueError):
            scheduler.parse_run_at("2026-02-30 09:30")

    def test_format_round_trips(self):
        moment = datetime(2026, 1, 5, 7, 5)
        self.assertEqual(scheduler.parse_run_at(scheduler.format_run_at(moment)), moment)


class NewJobTest(unittest.TestCase):
    def test_starts_pending_with_text_run_at(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0))
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["run_at"], "2026-10-12 09:00")

    def test_ids_are_unique(self):
        self.assertNotEqual(make_job()["id"], make_job()["id"])

    def test_rejects_unknown_repeat(self):
        with self.assertRaises(ValueError):
            make_job(repeat="monthly")


class StaggerTest(unittest.TestCase):
    start = datetime(2026, 10, 12, 9, 0)

    def test_spaces_run_times_by_the_gap(self):
        self.assertEqual(
            scheduler.stagger(self.start, 3, 5),
            [self.start, datetime(2026, 10, 12, 9, 5), datetime(2026, 10, 12, 9, 10)],
        )

    def test_zero_gap_gives_the_same_time_for_all(self):
        self.assertEqual(scheduler.stagger(self.start, 3, 0), [self.start] * 3)

    def test_single_target_runs_at_the_start_time(self):
        self.assertEqual(scheduler.stagger(self.start, 1, 30), [self.start])

    def test_gap_can_cross_midnight(self):
        late = datetime(2026, 10, 12, 23, 50)
        self.assertEqual(scheduler.stagger(late, 2, 20)[1], datetime(2026, 10, 13, 0, 10))

    def test_rejects_a_negative_gap(self):
        with self.assertRaises(ValueError):
            scheduler.stagger(self.start, 2, -1)


class DueJobsTest(unittest.TestCase):
    def test_returns_pending_jobs_whose_time_has_come(self):
        now = datetime(2026, 10, 12, 9, 0)
        due = make_job(run_at=now)
        future = make_job(run_at=now + timedelta(minutes=1))
        self.assertEqual(scheduler.due_jobs([due, future], now), [due])

    def test_orders_by_run_time(self):
        now = datetime(2026, 10, 12, 12, 0)
        later = make_job(run_at=datetime(2026, 10, 12, 11, 0))
        earlier = make_job(run_at=datetime(2026, 10, 12, 10, 0))
        self.assertEqual(scheduler.due_jobs([later, earlier], now), [earlier, later])

    def test_skips_jobs_that_are_not_pending(self):
        now = datetime(2026, 10, 12, 12, 0)
        jobs = [make_job(status=s) for s in ("done", "failed", "missed", "running")]
        self.assertEqual(scheduler.due_jobs(jobs, now), [])


class NextOccurrenceTest(unittest.TestCase):
    def test_one_off_job_has_no_next_occurrence(self):
        now = datetime(2026, 10, 12, 9, 0)
        self.assertIsNone(scheduler.next_occurrence(now, "none", now))

    def test_daily_moves_to_the_next_day(self):
        run_at = datetime(2026, 10, 12, 9, 0)
        now = datetime(2026, 10, 12, 9, 1)
        self.assertEqual(
            scheduler.next_occurrence(run_at, "daily", now), datetime(2026, 10, 13, 9, 0)
        )

    def test_daily_skips_days_already_in_the_past(self):
        run_at = datetime(2026, 10, 12, 9, 0)
        now = datetime(2026, 10, 15, 10, 0)
        self.assertEqual(
            scheduler.next_occurrence(run_at, "daily", now), datetime(2026, 10, 16, 9, 0)
        )

    def test_weekly_keeps_the_weekday(self):
        run_at = datetime(2026, 10, 12, 9, 0)
        now = datetime(2026, 10, 20, 8, 0)
        result = scheduler.next_occurrence(run_at, "weekly", now)
        self.assertEqual(result, datetime(2026, 10, 26, 9, 0))
        self.assertEqual(result.weekday(), run_at.weekday())

    def test_next_occurrence_is_strictly_after_now(self):
        moment = datetime(2026, 10, 12, 9, 0)
        self.assertEqual(
            scheduler.next_occurrence(moment, "daily", moment), datetime(2026, 10, 13, 9, 0)
        )


class AfterRunTest(unittest.TestCase):
    def test_successful_one_off_job_is_done(self):
        job = make_job()
        now = datetime(2026, 10, 12, 9, 2)
        scheduler.after_run(job, True, "ok", now)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["last_run"], "2026-10-12 09:02")

    def test_failed_one_off_job_keeps_the_error(self):
        job = make_job()
        scheduler.after_run(job, False, "Mã thoát 1", datetime(2026, 10, 12, 9, 2))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["last_message"], "Mã thoát 1")

    def test_repeating_job_is_rescheduled(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0), repeat="daily")
        scheduler.after_run(job, True, "ok", datetime(2026, 10, 12, 9, 2))
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["run_at"], "2026-10-13 09:00")

    def test_failed_repeating_job_still_gets_its_next_run(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0), repeat="weekly")
        scheduler.after_run(job, False, "Mã thoát 1", datetime(2026, 10, 12, 9, 2))
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["run_at"], "2026-10-19 09:00")
        self.assertEqual(job["last_message"], "Mã thoát 1")


class RecoverAtStartupTest(unittest.TestCase):
    now = datetime(2026, 10, 12, 12, 0)

    def test_overdue_one_off_job_is_marked_missed(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0))
        changed = scheduler.recover_at_startup([job], self.now)
        self.assertTrue(changed)
        self.assertEqual(job["status"], "missed")

    def test_job_within_the_grace_period_still_runs(self):
        job = make_job(run_at=self.now - timedelta(minutes=3))
        changed = scheduler.recover_at_startup([job], self.now)
        self.assertFalse(changed)
        self.assertEqual(job["status"], "pending")

    def test_future_job_is_untouched(self):
        job = make_job(run_at=self.now + timedelta(hours=1))
        self.assertFalse(scheduler.recover_at_startup([job], self.now))
        self.assertEqual(job["status"], "pending")

    def test_overdue_repeating_job_jumps_to_its_next_run(self):
        job = make_job(run_at=datetime(2026, 10, 10, 9, 0), repeat="daily")
        scheduler.recover_at_startup([job], self.now)
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["run_at"], "2026-10-13 09:00")
        self.assertIn("2026-10-10 09:00", job["last_message"])

    def test_job_left_running_by_a_crash_is_failed(self):
        job = make_job(status="running")
        scheduler.recover_at_startup([job], self.now)
        self.assertEqual(job["status"], "failed")


if __name__ == "__main__":
    unittest.main()
