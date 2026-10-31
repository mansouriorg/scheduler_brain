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

- `ScheduleRequest`: `rooms`, `professors`, `classes`.
- `ScheduleResponse`: `success`, `scheduledClasses` (class, room, day, start, end), `message`.

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
- days off of professors are kept, and no room, professor or student is
  double-booked;
- the parts of one course for one cohort fall on different days;
- impossible setups (too many hours, no room big enough, a week that is too
  short) get a clear message instead of a timetable.

## Settings and secrets

The code does not read any environment variable at the moment.
`.env` is listed in `.gitignore`: keep secrets there and never commit it.

## Updating dependencies

`requirements.txt` lists only the packages the code imports, with exact versions.
After you upgrade one inside `venv/`, write its new version there
(`pip show PACKAGE` prints it).
