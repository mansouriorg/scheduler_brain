"""Tests for the timetable solver (app/solver.py) and its data shapes (app/models.py).

Run them from the scheduler-brain folder:

    venv/bin/python -m unittest discover -s tests -t . -v

They use Python's built-in unittest, so nothing extra has to be installed.
"""

import contextlib
import io
import unittest

from app.models import ScheduledClass, ScheduleRequest
from app.solver import generate_schedule

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]
FIRST_START = 8 * 60  # 08:00
LAST_END = 18 * 60 + 30  # 18:30


def solve(request_data):
    """Runs the solver on plain data, as brain_server sends it, without its log lines."""
    request = ScheduleRequest.model_validate(request_data)
    with contextlib.redirect_stdout(io.StringIO()):
        return generate_schedule(request)


def minutes(clock):
    """'08:30' -> 510 (minutes after midnight)."""
    hours, mins = clock.split(":")
    return int(hours) * 60 + int(mins)


def a_room(room_id, room_type, capacity, equipment):
    return {
        "id": room_id,
        "name": room_id,
        "capacity": capacity,
        "roomType": room_type,
        "campusId": "campus-1",
        "buildingId": "building-1",
        "equipment": equipment,
    }


def a_professor(prof_id, max_hours=20.0, days_off=()):
    return {
        "id": prof_id,
        "name": f"Dr. {prof_id}",
        "maxHoursPerWeek": max_hours,
        "timePreference": "NO_PREFERENCE",
        "isStrictTimeRequirement": False,
        "unavailableDays": list(days_off),
    }


def a_class(
    class_id,
    course,
    part,
    length,
    room_type,
    prof_id,
    equipment=(),
    headcount=2,
    cohort="cohort-1",
    students=("student-1", "student-2"),
):
    return {
        "id": class_id,
        "courseCode": course,
        "durationMinutes": length,
        "requiresRoomType": room_type,
        "requiredEquipment": list(equipment),
        "componentType": part,
        "professorId": prof_id,
        "cohortId": cohort,
        "cohortHeadcount": headcount,
        "studentIds": list(students),
    }


def a_fixed(class_id, day, start, end, room="amphi-a", prof_id=None, cohort="cohort-9", students=()):
    """A class that is already in the timetable, as brain_server sends it."""
    return {
        "id": class_id,
        "roomId": room,
        "dayOfWeek": day,
        "startTime": start,
        "endTime": end,
        "durationMinutes": minutes(end) - minutes(start),
        "professorId": prof_id,
        "cohortId": cohort,
        "studentIds": list(students),
    }


def a_week_with_one_free_slot(**owner):
    """Classes already in the timetable that keep one room, professor, cohort or
    student busy all week, except on Friday from 14:00 to 15:30."""
    full_days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "SATURDAY"]
    week = [a_fixed(f"fixed-{day.lower()}", day, "08:00", "18:30", **owner) for day in full_days]
    week.append(a_fixed("fixed-friday-morning", "FRIDAY", "08:00", "14:00", **owner))
    week.append(a_fixed("fixed-friday-evening", "FRIDAY", "15:30", "18:30", **owner))
    return week


def a_week_off_but(day, start, end):
    """The times off of a teacher who can only teach on one day, from start to end."""
    times = [{"dayOfWeek": d, "startTime": "08:00", "endTime": "18:30"} for d in DAYS if d != day]
    if start != "08:00":
        times.append({"dayOfWeek": day, "startTime": "08:00", "endTime": start})
    if end != "18:30":
        times.append({"dayOfWeek": day, "startTime": end, "endTime": "18:30"})
    return times


# A small week that has solutions: two rooms, two professors (prof-1 can only
# teach on Wednesdays), cohort-1 with two courses, and two more cohorts whose
# classes share nothing but the lecture hall.
SMALL_WEEK = {
    "rooms": [
        a_room("amphi-a", "AMPHI", 120, ["PROJECTOR"]),
        a_room("lab-304", "COMPUTER_LAB", 30, ["COMPUTERS", "PROJECTOR"]),
    ],
    "professors": [
        a_professor("prof-1", days_off=["MONDAY", "TUESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]),
        a_professor("prof-2"),
    ],
    "classes": [
        a_class("dev-lecture", "DEV-101", "LECTURE", 90, "AMPHI", "prof-1"),
        a_class("dev-td", "DEV-101", "TD", 90, "COMPUTER_LAB", "prof-2"),
        a_class("dev-tp", "DEV-101", "TP", 180, "COMPUTER_LAB", "prof-2", ["COMPUTERS"]),
        a_class("db-lecture", "DB-201", "LECTURE", 120, "AMPHI", "prof-1"),
        a_class("net-lecture", "NET-301", "LECTURE", 60, "AMPHI", None, cohort="cohort-2", students=()),
        a_class("ops-lecture", "OPS-401", "LECTURE", 60, "AMPHI", None, cohort="cohort-3", students=()),
    ],
}


class DataShapeTests(unittest.TestCase):
    def test_accepts_the_request_that_brain_server_sends(self):
        # The same example as in brain_server/src/scheduler/scheduler.service.spec.ts.
        payload = {
            "rooms": [
                {
                    "id": "room-1",
                    "name": "Lab 304",
                    "capacity": 30,
                    "roomType": "COMPUTER_LAB",
                    "campusId": "campus-1",
                    "buildingId": "building-1",
                    "equipment": ["COMPUTERS", "PROJECTOR"],
                }
            ],
            "professors": [
                {
                    "id": "prof-1",
                    "name": "Dr. Amal Ben Salah",
                    "maxHoursPerWeek": 12,
                    "timePreference": "MORNING",
                    "isStrictTimeRequirement": False,
                    "unavailableTimes": [
                        {"dayOfWeek": "FRIDAY", "startTime": "08:00", "endTime": "18:00"}
                    ],
                }
            ],
            "classes": [
                {
                    "id": "class-1",
                    "courseCode": "DEV-101",
                    "durationMinutes": 90,
                    "requiresRoomType": "COMPUTER_LAB",
                    "requiredEquipment": ["COMPUTERS"],
                    "componentType": "TD",
                    "professorId": "prof-1",
                    "cohortId": "cohort-1",
                    "cohortHeadcount": 2,
                    "studentIds": ["student-1", "student-2"],
                },
                {
                    "id": "class-2",
                    "courseCode": "DEV-101",
                    "durationMinutes": 90,
                    "requiresRoomType": "COMPUTER_LAB",
                    "requiredEquipment": ["COMPUTERS"],
                    "componentType": "TP",
                    "professorId": None,
                    "cohortId": "cohort-1",
                    "cohortHeadcount": 2,
                    "studentIds": ["student-1", "student-2"],
                },
            ],
        }

        request = ScheduleRequest.model_validate(payload)

        self.assertEqual(request.rooms[0].campusId, "campus-1")
        self.assertEqual(request.professors[0].unavailableTimes[0].dayOfWeek, "FRIDAY")
        self.assertEqual(request.professors[0].unavailableDays, [])
        self.assertEqual(len(request.classes), 2)
        self.assertIsNone(request.classes[1].professorId)

    def test_accepts_the_classes_already_in_the_timetable(self):
        payload = {
            "rooms": [],
            "professors": [],
            "classes": [],
            "fixedClasses": [
                {
                    "id": "class-9",
                    "roomId": "room-1",
                    "dayOfWeek": "MONDAY",
                    "startTime": "08:30",
                    "endTime": "10:00",
                    "durationMinutes": 90,
                    "professorId": "prof-1",
                    "cohortId": "cohort-1",
                    "studentIds": ["student-1"],
                }
            ],
        }

        request = ScheduleRequest.model_validate(payload)

        self.assertEqual(request.fixedClasses[0].roomId, "room-1")
        self.assertEqual(request.fixedClasses[0].studentIds, ["student-1"])
        # Without the field (an empty timetable), there are none.
        self.assertEqual(ScheduleRequest.model_validate({**payload, "fixedClasses": []}).fixedClasses, [])
        del payload["fixedClasses"]
        self.assertEqual(ScheduleRequest.model_validate(payload).fixedClasses, [])

    def test_answers_with_the_fields_brain_server_saves(self):
        self.assertEqual(
            set(ScheduledClass.model_fields),
            {"classId", "roomId", "dayOfWeek", "startTime", "endTime"},
        )


class SolvableWeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = solve(SMALL_WEEK)
        cls.rooms = {r["id"]: r for r in SMALL_WEEK["rooms"]}
        cls.classes = {c["id"]: c for c in SMALL_WEEK["classes"]}
        cls.placed = cls.result.scheduledClasses

    def test_places_every_class_exactly_once(self):
        self.assertTrue(self.result.success, self.result.message)
        self.assertEqual(
            sorted(p.classId for p in self.placed), sorted(self.classes)
        )

    def test_uses_a_suitable_room_and_the_right_length(self):
        for p in self.placed:
            wanted = self.classes[p.classId]
            room = self.rooms[p.roomId]
            with self.subTest(p.classId):
                self.assertEqual(room["roomType"], wanted["requiresRoomType"])
                self.assertGreaterEqual(room["capacity"], wanted["cohortHeadcount"])
                self.assertTrue(set(wanted["requiredEquipment"]) <= set(room["equipment"]))
                self.assertIn(p.dayOfWeek, DAYS)
                self.assertEqual(
                    minutes(p.endTime) - minutes(p.startTime), wanted["durationMinutes"]
                )
                self.assertGreaterEqual(minutes(p.startTime), FIRST_START)
                self.assertLessEqual(minutes(p.endTime), LAST_END)

    def test_keeps_the_days_off_of_professors(self):
        for p in self.placed:
            if self.classes[p.classId]["professorId"] == "prof-1":
                self.assertEqual(p.dayOfWeek, "WEDNESDAY", p.classId)

    def test_never_double_books_a_room_a_professor_or_a_student(self):
        for i, first in enumerate(self.placed):
            for second in self.placed[i + 1 :]:
                overlap = (
                    first.dayOfWeek == second.dayOfWeek
                    and minutes(first.startTime) < minutes(second.endTime)
                    and minutes(second.startTime) < minutes(first.endTime)
                )
                if not overlap:
                    continue
                a, b = self.classes[first.classId], self.classes[second.classId]
                with self.subTest(first=first.classId, second=second.classId):
                    self.assertNotEqual(first.roomId, second.roomId)
                    if a["professorId"] is not None:
                        self.assertNotEqual(a["professorId"], b["professorId"])
                    self.assertFalse(set(a["studentIds"]) & set(b["studentIds"]))

    def test_puts_the_parts_of_one_course_on_different_days(self):
        days = [p.dayOfWeek for p in self.placed if self.classes[p.classId]["courseCode"] == "DEV-101"]
        self.assertEqual(len(days), 3)
        self.assertEqual(len(set(days)), 3)


class CohortTests(unittest.TestCase):
    def full_day_classes(self, count):
        # Full-day classes of different courses for one cohort, without enrolled
        # students: only the cohort rule keeps them on different days.
        return {
            "rooms": [a_room("amphi-a", "AMPHI", 120, []), a_room("amphi-b", "AMPHI", 120, [])],
            "professors": [],
            "classes": [
                a_class(f"c{n}", f"COURSE-{n}", "LECTURE", 630, "AMPHI", None, students=())
                for n in range(count)
            ],
        }

    def test_never_puts_two_classes_of_one_cohort_at_the_same_time(self):
        result = solve(self.full_day_classes(6))

        self.assertTrue(result.success, result.message)
        self.assertEqual(len({p.dayOfWeek for p in result.scheduledClasses}), 6)

    def test_finds_no_timetable_when_the_cohort_would_need_two_places_at_once(self):
        result = solve(self.full_day_classes(7))

        self.assertFalse(result.success)
        self.assertTrue(result.message.startswith("Scheduling Failed (Infeasible)"), result.message)


class TimesOffTests(unittest.TestCase):
    """The classes of a teacher stay out of the times when that teacher cannot teach."""

    def place(self, times_off, fixed=()):
        professor = a_professor("prof-1", max_hours=80.0)
        professor["unavailableTimes"] = times_off
        data = {
            "rooms": [a_room("amphi-a", "AMPHI", 120, [])],
            "professors": [professor],
            "classes": [a_class("new", "DEV-101", "LECTURE", 90, "AMPHI", "prof-1")],
            "fixedClasses": list(fixed),
        }
        return solve(data)

    def placed(self, result):
        self.assertTrue(result.success, result.message)
        return [(p.dayOfWeek, p.startTime, p.endTime) for p in result.scheduledClasses]

    def test_places_a_class_only_when_its_teacher_can_teach(self):
        result = self.place(a_week_off_but("TUESDAY", "14:00", "15:30"))
        self.assertEqual(self.placed(result), [("TUESDAY", "14:00", "15:30")])

    def test_a_class_may_end_when_a_time_off_starts(self):
        # Free on Thursday from 08:00 to 09:30 only: the 90-minute class just fits.
        result = self.place(a_week_off_but("THURSDAY", "08:00", "09:30"))
        self.assertEqual(self.placed(result), [("THURSDAY", "08:00", "09:30")])

    def test_says_so_when_the_teacher_can_never_teach_then(self):
        # Free for one hour only: too short for the 90-minute class.
        result = self.place(a_week_off_but("TUESDAY", "14:00", "15:00"))

        self.assertFalse(result.success)
        self.assertEqual(
            result.message,
            "No time is left for DEV-101 LECTURE: Dr. prof-1 cannot teach at any time it could take place. "
            "Change the times off of Dr. prof-1 on the Teachers page, or choose another teacher for it.",
        )

    def test_also_names_the_classes_already_in_the_timetable(self):
        fixed = [a_fixed("fixed-tuesday", "TUESDAY", "14:00", "15:30", room="amphi-a")]

        result = self.place(a_week_off_but("TUESDAY", "14:00", "15:30"), fixed)

        self.assertFalse(result.success)
        self.assertIn("(the other times are taken by the classes already in the timetable)", result.message)


class FixedClassTests(unittest.TestCase):
    """Classes already in the timetable keep their place; new classes go around them."""

    def place(self, fixed, prof_id=None, students=()):
        data = {
            "rooms": [a_room("amphi-a", "AMPHI", 120, []), a_room("lab-304", "COMPUTER_LAB", 30, [])],
            # prof-1 may teach the whole week, so only the times can stop them.
            "professors": [a_professor("prof-1", max_hours=80.0)],
            "classes": [a_class("new", "DEV-101", "LECTURE", 90, "AMPHI", prof_id, students=students)],
            "fixedClasses": fixed,
        }
        return solve(data)

    def assert_in_the_free_slot(self, result):
        self.assertTrue(result.success, result.message)
        placed = [(p.classId, p.roomId, p.dayOfWeek, p.startTime, p.endTime) for p in result.scheduledClasses]
        self.assertEqual(placed, [("new", "amphi-a", "FRIDAY", "14:00", "15:30")])

    def test_keeps_new_classes_out_of_a_room_in_use(self):
        self.assert_in_the_free_slot(self.place(a_week_with_one_free_slot(room="amphi-a")))

    def test_keeps_a_professor_to_one_class_at_a_time(self):
        fixed = a_week_with_one_free_slot(room="lab-304", prof_id="prof-1")
        self.assert_in_the_free_slot(self.place(fixed, prof_id="prof-1"))

    def test_keeps_a_cohort_to_one_class_at_a_time(self):
        self.assert_in_the_free_slot(self.place(a_week_with_one_free_slot(room="lab-304", cohort="cohort-1")))

    def test_keeps_a_student_to_one_class_at_a_time(self):
        fixed = a_week_with_one_free_slot(room="lab-304", students=["student-1"])
        self.assert_in_the_free_slot(self.place(fixed, students=("student-1", "student-2")))

    def test_says_so_when_no_room_and_time_is_left(self):
        fixed = [a_fixed(f"fixed-{day.lower()}", day, "08:00", "18:30") for day in DAYS]

        result = self.place(fixed)

        self.assertFalse(result.success)
        self.assertEqual(
            result.message,
            "No room and time is left for DEV-101 LECTURE: at every time it could take place, the classes "
            "already in the timetable use every room it fits in, or its teacher, cohort or students. "
            "Clear the timetable and generate it again to place all classes together.",
        )

    def test_counts_the_hours_of_classes_already_in_the_timetable(self):
        data = {
            "rooms": [a_room("amphi-a", "AMPHI", 120, [])],
            "professors": [a_professor("prof-1", max_hours=11.0)],
            "classes": [a_class("new", "DEV-101", "LECTURE", 90, "AMPHI", "prof-1")],
            "fixedClasses": [a_fixed("fixed", "MONDAY", "08:00", "18:30", room="lab-304", prof_id="prof-1")],
        }

        result = solve(data)

        self.assertFalse(result.success)
        self.assertEqual(
            result.message,
            "Impossible Setup: Professor Dr. prof-1 is assigned 12.0 hours, but their max is 11.0.",
        )


class ImpossibleSetupTests(unittest.TestCase):
    def test_explains_when_a_professor_has_too_many_hours(self):
        data = {
            "rooms": [a_room("amphi-a", "AMPHI", 120, [])],
            "professors": [a_professor("prof-1", max_hours=2.0)],
            "classes": [
                a_class("c1", "DEV-101", "LECTURE", 90, "AMPHI", "prof-1"),
                a_class("c2", "DB-201", "LECTURE", 90, "AMPHI", "prof-1"),
            ],
        }

        result = solve(data)

        self.assertFalse(result.success)
        self.assertEqual(result.scheduledClasses, [])
        self.assertEqual(
            result.message,
            "Impossible Setup: Professor Dr. prof-1 is assigned 3.0 hours, but their max is 2.0.",
        )

    def test_explains_when_no_room_is_big_enough(self):
        data = {
            "rooms": [a_room("amphi-a", "AMPHI", 120, [])],
            "professors": [a_professor("prof-1")],
            "classes": [a_class("c1", "DEV-101", "LECTURE", 90, "AMPHI", "prof-1", headcount=500)],
        }

        result = solve(data)

        self.assertFalse(result.success)
        self.assertTrue(
            result.message.startswith("Impossible: No valid room/time combo found for class DEV-101."),
            result.message,
        )

    def test_reports_infeasible_when_the_week_is_too_short(self):
        # Seven full-day sessions of one course for one cohort, but only six days.
        data = {
            "rooms": [a_room("amphi-a", "AMPHI", 120, [])],
            "professors": [],
            "classes": [
                a_class(f"c{n}", "DEV-101", "LECTURE", 630, "AMPHI", None) for n in range(7)
            ],
        }

        result = solve(data)

        self.assertFalse(result.success)
        self.assertEqual(result.scheduledClasses, [])
        self.assertTrue(result.message.startswith("Scheduling Failed (Infeasible)"), result.message)


if __name__ == "__main__":
    unittest.main()
