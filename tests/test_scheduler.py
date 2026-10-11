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

    def test_job_held_back_by_its_batch_is_not_missed(self):
        job = make_job(run_at=self.now - timedelta(minutes=20))
        job["not_before"] = (self.now - timedelta(minutes=1)).isoformat()
        self.assertFalse(scheduler.recover_at_startup([job], self.now))
        self.assertEqual(job["status"], "pending")


class SkipOverdueTest(unittest.TestCase):
    now = datetime(2026, 10, 12, 12, 0)

    def test_late_job_is_missed_with_the_reason(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0))
        self.assertTrue(scheduler.skip_overdue([job], self.now, reason="máy ngủ"))
        self.assertEqual(job["status"], "missed")
        self.assertIn("máy ngủ", job["last_message"])

    def test_running_job_is_left_alone(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0), status="running")
        self.assertFalse(scheduler.skip_overdue([job], self.now))
        self.assertEqual(job["status"], "running")

    def test_missed_job_drops_its_hold(self):
        job = make_job(run_at=datetime(2026, 10, 12, 9, 0), repeat="daily")
        job["not_before"] = datetime(2026, 10, 12, 9, 30).isoformat()
        scheduler.skip_overdue([job], self.now)
        self.assertEqual(job["run_at"], "2026-10-13 09:00")
        self.assertNotIn("not_before", job)


class BatchTest(unittest.TestCase):
    start = datetime(2026, 10, 12, 9, 0)

    def batch(self, gap=5, repeat="none"):
        jobs = [
            make_job(run_at=moment, repeat=repeat) for moment in scheduler.stagger(self.start, 3, gap)
        ]
        scheduler.link_batch(jobs, gap)
        return jobs

    def test_jobs_of_one_post_share_a_batch(self):
        first, second, third = self.batch()
        self.assertEqual(first["batch"], second["batch"])
        self.assertEqual(second["batch"], third["batch"])
        self.assertNotEqual(first["batch"], self.batch()[0]["batch"])

    def test_gap_counts_from_when_the_previous_post_finished(self):
        first, second, third = jobs = self.batch(gap=5)
        # The first post was a slow video upload: planned 09:00, finished 09:07:30
        finished = datetime(2026, 10, 12, 9, 7, 30)
        scheduler.after_run(first, True, "ok", finished)
        scheduler.hold_back_batch(jobs, first, finished)
        self.assertEqual(scheduler.ready_at(second), datetime(2026, 10, 12, 9, 12, 30))
        self.assertEqual(scheduler.due_jobs(jobs, datetime(2026, 10, 12, 9, 12)), [])
        self.assertEqual(scheduler.due_jobs(jobs, datetime(2026, 10, 12, 9, 13))[0], second)
        self.assertIn("09:12:30", second["last_message"])

    def test_jobs_already_far_enough_away_keep_their_planned_time(self):
        first, second, third = jobs = self.batch(gap=5)
        scheduler.hold_back_batch(jobs, first, datetime(2026, 10, 12, 9, 0, 40))
        self.assertEqual(scheduler.ready_at(second), datetime(2026, 10, 12, 9, 5, 40))
        self.assertEqual(scheduler.ready_at(third), datetime(2026, 10, 12, 9, 10))
        self.assertNotIn("not_before", third)
        # The planned time shown in the queue is not rewritten
        self.assertEqual(second["run_at"], "2026-10-12 09:05")

    def test_jobs_outside_the_batch_are_not_held(self):
        first, second, third = jobs = self.batch(gap=5)
        other = make_job(run_at=self.start)
        scheduler.hold_back_batch(jobs + [other], first, datetime(2026, 10, 12, 9, 30))
        self.assertEqual(scheduler.ready_at(other), self.start)

    def test_single_post_holds_nothing(self):
        alone = make_job(run_at=self.start)
        other = make_job(run_at=self.start)
        scheduler.hold_back_batch([alone, other], alone, datetime(2026, 10, 12, 9, 30))
        self.assertNotIn("not_before", other)

    def test_running_the_held_job_clears_the_hold(self):
        first, second, third = jobs = self.batch(gap=5)
        scheduler.hold_back_batch(jobs, first, datetime(2026, 10, 12, 9, 20))
        scheduler.after_run(second, True, "ok", datetime(2026, 10, 12, 9, 26))
        self.assertNotIn("not_before", second)

    def test_repeating_batch_keeps_its_planned_time_for_the_next_day(self):
        first, second, third = jobs = self.batch(gap=5, repeat="daily")
        scheduler.hold_back_batch(jobs, first, datetime(2026, 10, 12, 9, 20))
        scheduler.after_run(second, True, "ok", datetime(2026, 10, 12, 9, 26))
        self.assertEqual(second["run_at"], "2026-10-13 09:05")


class ValidJobsTest(unittest.TestCase):
    def test_good_jobs_pass_through(self):
        jobs = [make_job(), make_job(repeat="daily", status="done")]
        self.assertEqual(scheduler.valid_jobs(jobs), (jobs, 0))

    def test_entries_without_a_readable_time_are_dropped(self):
        good = make_job()
        broken = [
            "không phải bài",
            None,
            {"content": "thiếu giờ"},
            dict(make_job(), run_at="12/10/2026 9h"),
            dict(make_job(), run_at=None),
        ]
        self.assertEqual(scheduler.valid_jobs(broken + [good]), ([good], len(broken)))

    def test_missing_fields_get_defaults(self):
        (job,), dropped = scheduler.valid_jobs([{"run_at": "2026-10-12 09:00"}])
        self.assertEqual(dropped, 0)
        self.assertEqual(
            {key: job[key] for key in ("content", "page_url", "image_path", "repeat", "status")},
            {"content": "", "page_url": "", "image_path": "", "repeat": "none", "status": "pending"},
        )
        self.assertTrue(job["id"])
        self.assertEqual(scheduler.due_jobs([job], datetime(2026, 10, 12, 9, 0)), [job])

    def test_unknown_repeat_and_status_are_reset(self):
        (job,), _ = scheduler.valid_jobs([dict(make_job(), repeat="monthly", status="???")])
        self.assertEqual((job["repeat"], job["status"]), ("none", "pending"))

    def test_unreadable_hold_is_removed(self):
        (job,), _ = scheduler.valid_jobs([dict(make_job(), not_before="lúc nào đó", gap_minutes="5")])
        self.assertNotIn("not_before", job)
        self.assertEqual(job["gap_minutes"], 0)

    def test_the_loaded_list_is_not_modified(self):
        loaded = [{"run_at": "2026-10-12 09:00"}]
        scheduler.valid_jobs(loaded)
        self.assertEqual(loaded, [{"run_at": "2026-10-12 09:00"}])


if __name__ == "__main__":
    unittest.main()
