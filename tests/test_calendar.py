import tkinter as tk
import unittest
from datetime import date, datetime, time, timedelta

import calendar_tab
import schedule_view
import scheduler

NOW = datetime(2026, 10, 14, 10, 0)  # a Wednesday


def make_job(run_at, status="pending", repeat="none", **extra):
    job = scheduler.new_job("Nội dung", "", "", run_at, repeat)
    job["status"] = status
    job.update(extra)
    return job


class DatesTest(unittest.TestCase):
    def test_weeks_start_on_monday(self):
        self.assertEqual(schedule_view.week_start(date(2026, 10, 14)), date(2026, 10, 12))
        self.assertEqual(schedule_view.week_start(date(2026, 10, 18)), date(2026, 10, 12))
        self.assertEqual(schedule_view.week_days(date(2026, 10, 14))[0], date(2026, 10, 12))
        self.assertEqual(schedule_view.week_days(date(2026, 10, 14))[-1], date(2026, 10, 18))

    def test_a_month_is_shown_in_whole_weeks(self):
        weeks = schedule_view.month_weeks(2026, 10)
        self.assertTrue(all(len(week) == 7 for week in weeks))
        self.assertEqual(weeks[0][0], date(2026, 9, 28))
        self.assertEqual(weeks[-1][-1], date(2026, 11, 1))

    def test_months_roll_over_years(self):
        self.assertEqual(schedule_view.shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(schedule_view.shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(schedule_view.shift_month(2026, 5, 0), (2026, 5))

    def test_times_snap_to_quarter_hours(self):
        self.assertEqual(schedule_view.snap(datetime(2026, 10, 14, 9, 7)), datetime(2026, 10, 14, 9, 0))
        self.assertEqual(schedule_view.snap(datetime(2026, 10, 14, 9, 8)), datetime(2026, 10, 14, 9, 15))
        self.assertEqual(schedule_view.snap(datetime(2026, 10, 14, 23, 59)), datetime(2026, 10, 14, 23, 45))

    def test_minutes_stay_inside_the_day(self):
        self.assertEqual(schedule_view.minutes_to_time(-30), time(0, 0))
        self.assertEqual(schedule_view.minutes_to_time(24 * 60 + 100), time(23, 45))
        self.assertEqual(schedule_view.minutes_to_time(9 * 60 + 20), time(9, 15))

    def test_jobs_are_grouped_by_the_day_and_time_they_run(self):
        late = make_job(datetime(2026, 10, 14, 15, 0))
        early = make_job(datetime(2026, 10, 14, 8, 0))
        held = make_job(datetime(2026, 10, 14, 9, 0), not_before="2026-10-15T09:30:00")
        grouped = schedule_view.jobs_by_day([late, early, held])
        self.assertEqual(grouped[date(2026, 10, 14)], [early, late])
        self.assertEqual(grouped[date(2026, 10, 15)], [held])

    def test_titles(self):
        self.assertEqual(schedule_view.title_for("month", date(2026, 10, 14)), "Tháng 10/2026")
        self.assertEqual(schedule_view.title_for("week", date(2026, 10, 14)), "Tuần 12/10 – 18/10/2026")


class ProjectionTest(unittest.TestCase):
    def test_a_daily_post_is_shown_on_the_following_days(self):
        job = make_job(datetime(2026, 10, 14, 9, 0), repeat="daily")
        runs = schedule_view.projected_runs(job, datetime(2026, 10, 12), datetime(2026, 10, 19))
        self.assertEqual(runs, [datetime(2026, 10, 15, 9, 0) + timedelta(days=n) for n in range(4)])

    def test_the_post_itself_and_earlier_days_are_not_repeated(self):
        job = make_job(datetime(2026, 10, 14, 9, 0), repeat="weekdays")
        runs = schedule_view.projected_runs(job, datetime(2026, 10, 12), datetime(2026, 10, 19))
        self.assertEqual([run.date() for run in runs], [date(2026, 10, 15), date(2026, 10, 16)])

    def test_a_one_off_or_finished_post_has_no_repeats(self):
        window = (datetime(2026, 10, 12), datetime(2026, 10, 19))
        self.assertEqual(schedule_view.projected_runs(make_job(datetime(2026, 10, 14, 9, 0)), *window), [])
        self.assertEqual(schedule_view.projected_runs(make_job(datetime(2026, 10, 14, 9, 0), "done", "daily"), *window), [])

    def test_a_long_window_is_capped(self):
        job = make_job(datetime(2026, 10, 14, 9, 0), repeat="daily")
        runs = schedule_view.projected_runs(job, datetime(2026, 10, 1), datetime(2030, 1, 1))
        self.assertEqual(len(runs), schedule_view.MAX_PROJECTED)

    def test_a_window_far_ahead_starts_in_the_window(self):
        job = make_job(datetime(2026, 10, 14, 9, 0), repeat="daily")
        runs = schedule_view.projected_runs(job, datetime(2026, 12, 1), datetime(2026, 12, 3))
        self.assertEqual(runs, [datetime(2026, 12, 1, 9, 0), datetime(2026, 12, 2, 9, 0)])


class DropTargetTest(unittest.TestCase):
    def test_week_view_uses_the_dropped_time(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        target = schedule_view.drop_target(job, datetime(2026, 10, 16, 14, 7), NOW, "week")
        self.assertEqual(target, datetime(2026, 10, 16, 14, 0))

    def test_month_view_changes_only_the_day(self):
        job = make_job(datetime(2026, 10, 15, 9, 30))
        target = schedule_view.drop_target(job, datetime(2026, 10, 22), NOW, "month")
        self.assertEqual(target, datetime(2026, 10, 22, 9, 30))

    def test_the_past_is_refused(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        with self.assertRaises(ValueError):
            schedule_view.drop_target(job, datetime(2026, 10, 14, 9, 0), NOW, "week")

    def test_only_waiting_posts_move(self):
        job = make_job(datetime(2026, 10, 15, 9, 0), status="done")
        with self.assertRaises(ValueError):
            schedule_view.drop_target(job, datetime(2026, 10, 20, 9, 0), NOW, "week")


class TabTestCase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Không có màn hình để chạy Tkinter: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.jobs = []
        self.moves = []
        self.opened = []
        self.errors = []
        self.tab = calendar_tab.CalendarTab(
            self.root,
            get_jobs=lambda: self.jobs,
            move_job=lambda job_id, moment: self.moves.append((job_id, moment)),
            open_job=self.opened.append,
            label_for=lambda job: job["content"],
            now=lambda: NOW,
            on_error=self.errors.append,
        )
        self.tab.pack(fill="both", expand=True)
        self.tab.refresh()

    def chip_ids(self):
        return [chip[4] for chip in self.tab.chips]

    def centre(self, job_id):
        for x0, y0, x1, y1, chip_id, movable in self.tab.chips:
            if chip_id == job_id:
                return (x0 + x1) / 2, (y0 + y1) / 2
        self.fail("chip not drawn")


class WeekViewTest(TabTestCase):
    def test_posts_of_the_week_are_drawn(self):
        inside = make_job(datetime(2026, 10, 15, 9, 0))
        outside = make_job(datetime(2026, 10, 28, 9, 0))
        self.jobs = [inside, outside]
        self.tab.refresh()
        self.assertEqual(self.chip_ids(), [inside["id"]])

    def test_the_title_names_the_week(self):
        self.assertEqual(self.tab.title_var.get(), "Tuần 12/10 – 18/10/2026")

    def test_stepping_moves_a_week(self):
        self.tab.step(1)
        self.assertEqual(self.tab.title_var.get(), "Tuần 19/10 – 25/10/2026")
        self.tab.go_today()
        self.assertEqual(self.tab.title_var.get(), "Tuần 12/10 – 18/10/2026")

    def test_a_position_maps_back_to_a_day_and_time(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        self.jobs = [job]
        self.tab.refresh()
        x, y = self.centre(job["id"])
        moment = self.tab.moment_at(x, y)
        self.assertEqual(moment.date(), date(2026, 10, 15))
        self.assertIn(moment.hour, (9, 10))

    def test_outside_the_grid_is_nothing(self):
        self.assertIsNone(self.tab.moment_at(2, 2))

    def test_dropping_on_another_day_moves_the_post(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        self.jobs = [job]
        self.tab.refresh()
        geometry = self.tab.geometry
        x = geometry["gutter"] + geometry["col"] * 5.5  # Saturday
        y = geometry["header"] + geometry["row"] * 14  # 14:00
        self.tab.drop(job["id"], x, y)
        self.assertEqual(self.moves, [(job["id"], datetime(2026, 10, 17, 14, 0))])
        self.assertEqual(self.errors, [])

    def test_dropping_in_the_past_is_reported_and_nothing_moves(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        self.jobs = [job]
        self.tab.refresh()
        geometry = self.tab.geometry
        self.tab.drop(job["id"], geometry["gutter"] + geometry["col"] * 0.5, geometry["header"] + geometry["row"] * 8)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.errors, ["Thời điểm mới phải ở tương lai."])

    def test_finished_posts_cannot_be_dragged(self):
        job = make_job(datetime(2026, 10, 15, 9, 0), status="done")
        self.jobs = [job]
        self.tab.refresh()
        geometry = self.tab.geometry
        self.assertFalse(self.tab.chips[0][5])
        self.tab.drop(job["id"], geometry["gutter"] + geometry["col"] * 5.5, geometry["header"] + geometry["row"] * 10)
        self.assertEqual(self.moves, [])
        self.assertEqual(len(self.errors), 1)

    def test_dropping_outside_the_grid_does_nothing(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        self.jobs = [job]
        self.tab.refresh()
        self.tab.drop(job["id"], 1, 1)
        self.assertEqual((self.moves, self.errors), ([], []))

    def test_the_same_slot_is_not_a_move(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        self.jobs = [job]
        self.tab.refresh()
        geometry = self.tab.geometry
        self.tab.drop(job["id"], geometry["gutter"] + geometry["col"] * 3.5, geometry["header"] + geometry["row"] * 9 + 1)
        self.assertEqual(self.moves, [])

    def test_repeats_are_drawn_as_faint_copies_that_cannot_be_dragged(self):
        job = make_job(datetime(2026, 10, 14, 9, 0), repeat="daily")
        self.jobs = [job]
        self.tab.refresh()
        self.assertEqual(self.chip_ids(), [job["id"]])
        items = self.tab.canvas.find_all()
        dashed = [item for item in items if self.tab.canvas.type(item) == "rectangle" and self.tab.canvas.itemcget(item, "dash")]
        self.assertGreaterEqual(len(dashed), 4)

    def test_double_click_opens_the_post(self):
        job = make_job(datetime(2026, 10, 15, 9, 0))
        self.jobs = [job]
        self.tab.refresh()
        x, y = self.centre(job["id"])

        class Event:
            pass

        event = Event()
        event.x, event.y = int(x), int(y - self.tab.canvas.canvasy(0))
        self.tab._on_double(event)
        self.assertEqual(self.opened, [job["id"]])

    def test_two_posts_in_the_same_hour_share_the_cell(self):
        first = make_job(datetime(2026, 10, 15, 9, 0))
        second = make_job(datetime(2026, 10, 15, 9, 30))
        self.jobs = [first, second]
        self.tab.refresh()
        a, b = self.tab.chips
        self.assertLess(a[2], b[2])
        self.assertLessEqual(a[2], b[0] + 1)


class MonthViewTest(TabTestCase):
    def setUp(self):
        super().setUp()
        self.tab.set_mode("month")

    def test_the_title_names_the_month(self):
        self.assertEqual(self.tab.title_var.get(), "Tháng 10/2026")

    def test_stepping_moves_a_month(self):
        self.tab.step(1)
        self.assertEqual(self.tab.title_var.get(), "Tháng 11/2026")
        self.tab.step(-2)
        self.assertEqual(self.tab.title_var.get(), "Tháng 9/2026")

    def test_posts_of_the_month_are_drawn(self):
        jobs = [make_job(datetime(2026, 10, day, 9, 0)) for day in (2, 15, 30)]
        self.jobs = jobs
        self.tab.refresh()
        self.assertEqual(sorted(self.chip_ids()), sorted(job["id"] for job in jobs))

    def test_dropping_on_another_day_keeps_the_time(self):
        job = make_job(datetime(2026, 10, 15, 9, 30))
        self.jobs = [job]
        self.tab.refresh()
        geometry = self.tab.geometry
        weeks = geometry["weeks"]
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if day == date(2026, 10, 22):
                    x, y = (c + 0.5) * geometry["col"], geometry["header"] + (r + 0.5) * geometry["row"]
        self.tab.drop(job["id"], x, y)
        self.assertEqual(self.moves, [(job["id"], datetime(2026, 10, 22, 9, 30))])

    def test_busy_days_show_how_many_more(self):
        self.jobs = [make_job(datetime(2026, 10, 15, 8 + n, 0), content=f"Bài {n}") for n in range(12)]
        self.tab.refresh()
        texts = [self.tab.canvas.itemcget(item, "text") for item in self.tab.canvas.find_all()
                 if self.tab.canvas.type(item) == "text"]
        self.assertTrue(any("bài nữa" in text for text in texts))

    def test_switching_back_to_the_week(self):
        self.tab.set_mode("week")
        self.assertTrue(self.tab.title_var.get().startswith("Tuần"))


if __name__ == "__main__":
    unittest.main()
