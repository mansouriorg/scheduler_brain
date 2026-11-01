# scheduler-brain

The timetable solver of the university scheduler. It is a small FastAPI service:
brain_server sends it rooms, professors and classes, and it answers with a weekly
timetable computed by Google OR-Tools (the CP-SAT solver).

## Requirements

- Python 3.13 (tested with 3.13.7)

## Setup

Run these once, inside this folder:

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

## Run

    source venv/bin/activate
    uvicorn app.main:app --reload --port 8000

Use the port that brain_server calls: `SOLVER_URL` in the `.env` of brain_server
(default `http://127.0.0.1:8000`).

- Health check: open http://localhost:8000/ (it answers with a short JSON status).
- Interactive API docs: http://localhost:8000/docs

## API

| Method | Path     | Body             | Answer           |
|--------|----------|------------------|------------------|
| GET    | `/`      | none             | status message   |
| POST   | `/solve` | `ScheduleRequest` | `ScheduleResponse` |

- `ScheduleRequest`: `rooms`, `professors`, `classes` (the classes to place), and
  `fixedClasses`: the classes already in the timetable, with their room, day and
  time. They stay where they are; the new classes are placed around them. It may
  be left out (an empty timetable).
- Each professor may have `unavailableTimes`: the times each week when they cannot
  teach (`dayOfWeek`, `startTime`, `endTime`, "HH:mm"). Their classes stay out of
  those times; a class that may end at 10:00 can still take place before a time off
  that starts at 10:00. `unavailableDays` (whole days) still works too.
- Each professor has a `timePreference`: `MORNING` (08:00 to 12:00), `AFTERNOON`
  (13:00 to 17:00), `EVENING` (17:00 to 18:30) or `NO_PREFERENCE`. It is a wish, not a
  rule: after the first timetable, a second search of at most 30 seconds
  (`PREFERENCE_SEARCH_SECONDS` in `app/solver.py`) moves classes into the preferred
  part of the day where the rules allow it. Without a better timetable in time, the
  first one stays.
- `ScheduleResponse`: `success`, `scheduledClasses` (class, room, day, start, end),
  `message`, and `outsidePreference`: the ids of the classes placed (partly) outside
  the part of the day their teacher prefers.

The exact fields are defined in `app/models.py`.

## Files

| File                   | Role                                        |
|------------------------|---------------------------------------------|
| `app/main.py`          | HTTP endpoints                              |
| `app/models.py`        | request and response shapes (Pydantic)      |
| `app/solver.py`        | the OR-Tools model and the solve step       |
| `app/constraints.py`   | empty for now                               |
| `tests/test_solver.py` | tests for the solver and the data shapes    |

## Tests

    venv/bin/python -m unittest discover -s tests -t . -v

The tests use Python's built-in `unittest`, so nothing extra is installed. They
run the real OR-Tools solver on small made-up weeks and check that:

- the data brain_server sends is accepted as it is, and the answer has the
  fields brain_server saves;
- every class is placed exactly once, in a room of the right type, size and
  equipment, for its full length, between 08:00 and 18:30;
- days off and times off of professors are kept (a class with no time left
  because of them gets a clear message), and no room, professor or student is
  double-booked;
- a cohort follows one class at a time, also without enrolled students;
- the classes already in the timetable keep their place: new classes avoid their
  rooms, professors, cohorts and students, their hours count for the professors,
  and a class with no room and time left gets a clear message;
- the parts of one course for one cohort fall on different days;
- a class goes in the part of the day its teacher prefers when it can (times off
  still come first), and a class that cannot is named in `outsidePreference`;
- impossible setups (too many hours, no room big enough, a week that is too
  short) get a clear message instead of a timetable.

## Settings and secrets

The code does not read any environment variable at the moment.
`.env` is listed in `.gitignore`: keep secrets there and never commit it.

## Updating dependencies

`requirements.txt` lists only the packages the code imports, with exact versions.
After you upgrade one inside `venv/`, write its new version there
(`pip show PACKAGE` prints it).
