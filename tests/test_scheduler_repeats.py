import random
import unittest
from datetime import datetime, timedelta

import scheduler
from tests.test_scheduler import make_job

# 2026-10-12 is a Monday
MONDAY_9 = datetime(2026, 10, 12, 9, 0)


class RepeatSpecTest(unittest.TestCase):
    def test_known_repeats_are_valid(self):
        for repeat in ("none", "daily", "weekly", "weekdays", "monthly", "days:mon", "days:mon,wed,fri", "days:SAT, sun"):
            self.assertTrue(scheduler.is_valid_repeat(repeat), repeat)

    def test_others_are_not(self):
        for repeat in ("yearly", "days:", "days:funday", "days:mon,xyz", "", None, 5):
            self.assertFalse(scheduler.is_valid_repeat(repeat), repeat)

    def test_days_from_a_set_of_weekdays(self):
        self.assertEqual(scheduler.days_repeat({4, 0, 2}), "days:mon,wed,fri")
        with self.assertRaises(ValueError):
            scheduler.days_repeat(set())
        with self.assertRaises(ValueError):
            scheduler.days_repeat({7})

    def test_labels(self):
        self.assertEqual(scheduler.describe_repeat("weekdays"), "Thứ 2 đến Thứ 6")
        self.assertEqual(scheduler.describe_repeat("days:fri,mon"), "Thứ 2, Thứ 6")
        self.assertEqual(scheduler.describe_repeat("monthly"), "Hằng tháng")

    def test_a_job_can_be_made_with_the_new_repeats(self):
        job = scheduler.new_job("x", "", "", MONDAY_9, "days:tue,thu", account="shop", allow_duplicate=True, jitter_minutes=7)
        self.assertEqual((job["account"], job["allow_duplicate"], job["jitter_minutes"]), ("shop", True, 7))
        self.assertNotIn("account", scheduler.new_job("x", "", "", MONDAY_9))

    def test_a_bad_repeat_or_jitter_is_refused(self):
        with self.assertRaises(ValueError):
            scheduler.new_job("x", "", "", MONDAY_9, "days:funday")
        with self.assertRaises(ValueError):
            scheduler.new_job("x", "", "", MONDAY_9, "daily", jitter_minutes=-1)


class NextOccurrenceTest(unittest.TestCase):
    def test_weekdays_skip_the_weekend(self):
        friday = datetime(2026, 10, 16, 9, 0)
        self.assertEqual(
            scheduler.next_occurrence(friday, "weekdays", datetime(2026, 10, 16, 9, 2)), datetime(2026, 10, 19, 9, 0)
        )

    def test_weekdays_next_day(self):
        self.assertEqual(
            scheduler.next_occurrence(MONDAY_9, "weekdays", datetime(2026, 10, 12, 9, 2)), datetime(2026, 10, 13, 9, 0)
        )

    def test_chosen_days(self):
        repeat = "days:mon,wed,fri"
        self.assertEqual(scheduler.next_occurrence(MONDAY_9, repeat, datetime(2026, 10, 12, 9, 2)), datetime(2026, 10, 14, 9, 0))
        self.assertEqual(
            scheduler.next_occurrence(datetime(2026, 10, 14, 9, 0), repeat, datetime(2026, 10, 14, 9, 2)),
            datetime(2026, 10, 16, 9, 0),
        )
        self.assertEqual(
            scheduler.next_occurrence(datetime(2026, 10, 16, 9, 0), repeat, datetime(2026, 10, 16, 9, 2)),
            datetime(2026, 10, 19, 9, 0),
        )

    def test_a_long_gap_lands_on_an_allowed_day_at_the_same_time(self):
        result = scheduler.next_occurrence(MONDAY_9, "days:tue", datetime(2027, 3, 3, 12, 0))
        self.assertEqual((result.weekday(), result.hour, result.minute), (1, 9, 0))
        self.assertGreater(result, datetime(2027, 3, 3, 12, 0))
        self.assertLess(result - datetime(2027, 3, 3, 12, 0), timedelta(days=8))

    def test_a_future_start_on_a_wrong_day_moves_to_the_first_allowed_day(self):
        self.assertEqual(
            scheduler.next_occurrence(MONDAY_9, "days:wed", datetime(2026, 10, 1, 0, 0)), datetime(2026, 10, 14, 9, 0)
        )

    def test_a_future_start_on_an_allowed_day_stays(self):
        self.assertEqual(scheduler.next_occurrence(MONDAY_9, "days:mon", datetime(2026, 10, 1, 0, 0)), MONDAY_9)

    def test_monthly(self):
        self.assertEqual(
            scheduler.next_occurrence(MONDAY_9, "monthly", datetime(2026, 10, 12, 9, 2)), datetime(2026, 11, 12, 9, 0)
        )

    def test_monthly_across_the_new_year(self):
        self.assertEqual(
            scheduler.next_occurrence(datetime(2026, 12, 5, 9, 0), "monthly", datetime(2026, 12, 5, 9, 1)),
            datetime(2027, 1, 5, 9, 0),
        )

    def test_monthly_on_the_31st_uses_the_last_day_of_short_months_and_goes_back(self):
        jan = datetime(2026, 1, 31, 9, 0)
        feb = scheduler.next_occurrence(jan, "monthly", datetime(2026, 1, 31, 9, 1), anchor_day=31)
        self.assertEqual(feb, datetime(2026, 2, 28, 9, 0))
        mar = scheduler.next_occurrence(feb, "monthly", datetime(2026, 2, 28, 9, 1), anchor_day=31)
        self.assertEqual(mar, datetime(2026, 3, 31, 9, 0))

    def test_existing_repeats_behave_as_before(self):
        self.assertIsNone(scheduler.next_occurrence(MONDAY_9, "none", MONDAY_9))
        self.assertEqual(scheduler.next_occurrence(MONDAY_9, "daily", MONDAY_9), datetime(2026, 10, 13, 9, 0))
        self.assertEqual(scheduler.next_occurrence(MONDAY_9, "weekly", MONDAY_9), datetime(2026, 10, 19, 9, 0))


class AdvanceTest(unittest.TestCase):
    def test_a_monthly_job_remembers_its_day(self):
        job = make_job(run_at=datetime(2026, 1, 31, 9, 0), repeat="monthly", anchor_day=31)
        run_dates = []
        for _ in range(3):
            now = scheduler.parse_run_at(job["run_at"]) + timedelta(minutes=2)
            scheduler.after_run(job, True, "ok", now)
            run_dates.append(job["run_at"])
        self.assertEqual(run_dates, ["2026-02-28 09:00", "2026-03-31 09:00", "2026-04-30 09:00"])

    def test_a_job_run_early_by_hand_keeps_its_slot(self):
        job = make_job(run_at=datetime(2026, 10, 20, 9, 0), repeat="weekdays")
        scheduler.after_run(job, True, "ok", datetime(2026, 10, 12, 9, 2))
        self.assertEqual((job["status"], job["run_at"]), ("pending", "2026-10-20 09:00"))

    def test_jitter_moves_the_run_inside_the_window(self):
        job = make_job(run_at=MONDAY_9, repeat="daily", jitter_minutes=30)
        seen = set()
        for seed in range(60):
            trial = dict(job)
            scheduler.after_run(trial, True, "ok", datetime(2026, 10, 12, 9, 2), rng=random.Random(seed))
            moved = scheduler.parse_run_at(trial["run_at"])
            self.assertLessEqual(abs(moved - datetime(2026, 10, 13, 9, 0)), timedelta(minutes=30))
            self.assertEqual(trial["base_run_at"], "2026-10-13 09:00")
            seen.add(moved)
        self.assertGreater(len(seen), 10)

    def test_jitter_does_not_add_up(self):
        job = make_job(run_at=MONDAY_9, repeat="daily", jitter_minutes=45)
        rng = random.Random(3)
        for day in range(1, 40):
            now = scheduler.parse_run_at(job["run_at"]) + timedelta(minutes=1)
            scheduler.after_run(job, True, "ok", now, rng=rng)
            base = scheduler.parse_run_at(job["base_run_at"])
            self.assertEqual(base, MONDAY_9 + timedelta(days=day))
            self.assertLessEqual(abs(scheduler.parse_run_at(job["run_at"]) - base), timedelta(minutes=45))

    def test_an_early_jittered_run_does_not_run_the_same_slot_twice(self):
        job = make_job(run_at=datetime(2026, 10, 12, 8, 50), repeat="daily", jitter_minutes=30, base_run_at="2026-10-12 09:00")
        scheduler.after_run(job, True, "ok", datetime(2026, 10, 12, 8, 51), rng=random.Random(1))
        self.assertEqual(job["base_run_at"], "2026-10-13 09:00")
        self.assertGreater(scheduler.parse_run_at(job["run_at"]), datetime(2026, 10, 12, 23, 0))

    def test_without_jitter_no_base_time_is_kept(self):
        job = make_job(run_at=MONDAY_9, repeat="daily", base_run_at="2026-10-12 09:00")
        scheduler.after_run(job, True, "ok", datetime(2026, 10, 12, 9, 2))
        self.assertNotIn("base_run_at", job)

    def test_missing_a_run_moves_a_flexible_repeat_on(self):
        job = make_job(run_at=MONDAY_9, repeat="days:wed,fri")
        scheduler.skip_overdue([job], datetime(2026, 10, 12, 12, 0))
        self.assertEqual((job["status"], job["run_at"]), ("pending", "2026-10-14 09:00"))
        self.assertIn("Lỡ lần chạy 2026-10-12 09:00", job["last_message"])


class RetryTest(unittest.TestCase):
    now = datetime(2026, 10, 12, 9, 2)

    def test_a_failed_job_waits_and_is_pending_again(self):
        job = make_job(run_at=MONDAY_9)
        self.assertTrue(scheduler.schedule_retry(job, self.now, 2, 5, "Facebook chậm"))
        self.assertEqual((job["status"], job["attempts"]), ("pending", 1))
        self.assertEqual(scheduler.ready_at(job), datetime(2026, 10, 12, 9, 7))
        self.assertIn("thử lại lúc 09:07", job["last_message"])
        self.assertIn("Facebook chậm", job["last_message"])

    def test_it_is_not_due_until_the_wait_is_over(self):
        job = make_job(run_at=MONDAY_9)
        scheduler.schedule_retry(job, self.now, 2, 5)
        self.assertEqual(scheduler.due_jobs([job], datetime(2026, 10, 12, 9, 6)), [])
        self.assertEqual(scheduler.due_jobs([job], datetime(2026, 10, 12, 9, 7)), [job])

    def test_tries_run_out(self):
        job = make_job(run_at=MONDAY_9)
        results = [scheduler.schedule_retry(job, self.now, 2, 5) for _ in range(3)]
        self.assertEqual(results, [True, True, False])
        self.assertEqual(job["attempts"], 2)

    def test_no_retries_when_none_are_allowed(self):
        self.assertFalse(scheduler.schedule_retry(make_job(), self.now, 0, 5))

    def test_a_finished_run_clears_the_count(self):
        job = make_job(run_at=MONDAY_9, attempts=2)
        scheduler.after_run(job, True, "ok", self.now)
        self.assertNotIn("attempts", job)

    def test_a_retry_wait_is_not_taken_for_a_missed_run(self):
        job = make_job(run_at=MONDAY_9)
        scheduler.schedule_retry(job, datetime(2026, 10, 12, 9, 12), 2, 5)
        self.assertFalse(scheduler.skip_overdue([job], datetime(2026, 10, 12, 9, 18)))

    def test_new_fields_are_repaired_when_loading(self):
        raw = dict(make_job(), attempts="x", jitter_minutes=-4, anchor_day=True, account=5, allow_duplicate="yes", base_run_at="hôm qua")
        (job,), _ = scheduler.valid_jobs([raw])
        for key in ("attempts", "jitter_minutes", "anchor_day", "account", "allow_duplicate", "base_run_at"):
            self.assertNotIn(key, job)

    def test_good_new_fields_survive_loading(self):
        raw = dict(make_job(repeat="days:mon"), attempts=1, jitter_minutes=5, account="shop", allow_duplicate=True,
                   base_run_at="2026-10-12 09:00")
        (job,), _ = scheduler.valid_jobs([raw])
        self.assertEqual((job["attempts"], job["jitter_minutes"], job["account"], job["allow_duplicate"]), (1, 5, "shop", True))
        self.assertEqual((job["repeat"], job["base_run_at"]), ("days:mon", "2026-10-12 09:00"))


if __name__ == "__main__":
    unittest.main()
