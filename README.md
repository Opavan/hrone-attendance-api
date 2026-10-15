# Employee Attendance & Analytics API

A FastAPI + MongoDB service for punch-in/out, manual attendance corrections with an audit trail, and analytics built on MongoDB aggregation pipelines. All code is in `app/main.py`.

## Run it

Requires Python 3.11+ and MongoDB 6.0+ (local, or a free MongoDB Atlas cluster).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a local `.env` file (never commit it) with your own values:

```
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Real environment variables take priority over `.env`. Then start the API:

```bash
uvicorn app.main:app --port 8000
```

All indexes are created automatically at startup (idempotent). `GET /health` returns 200 only when MongoDB answers a ping, otherwise 503.

## What is implemented

Every endpoint in `openapi.yaml`: `/health`, `POST` and `GET /employees`, `POST /attendance/punch-in`, `POST /attendance/punch-out`, `GET /attendance`, `PATCH /attendance/{emp_code}/{date}`, the four `/analytics/...` endpoints (employee monthly, department summary, late leaderboard, department trend) and `GET /admin/explain/{endpoint}`.

**Nothing is knowingly missing.**

## How I tested

- The expected analytics values were computed with a separate plain-Python script from the sample data (plus extra records I created), then compared with the API's output on MongoDB Atlas.
- Through the API with `curl`: punch-in lateness (09:40:01 gives 10 minutes), punch-out, a half-up rounding tie in a correction (9.175 hours gives 9.18), history appending, duplicate punch-in and punch-out returning 409, mid-month joiners, half days, leaderboard ties and `limit`, a trend over a range with no records, and the 404 and 422 cases.
- The helper functions (late, work hours, overtime, attendance date) were checked with direct Python calls. Overnight-shift handling was verified that way, not through the HTTP endpoints.
- `/admin/explain/...` shows `IXSCAN` and no `COLLSCAN` for all five endpoints.

## Limits of my testing

- I did not load-test with about 100,000 records, so the index plans are checked but not timed.
- Race protection (duplicate punch-in, punch-out, concurrent PATCH) rests on the unique index, an atomic conditional update and an optimistic guard. I did not run true parallel requests against it.

## Assumptions

- `moving_avg_7d` averages the already-rounded `attendance_rate` values.
- A date range of up to 92 days inclusive is accepted for the trend.
- Sending `punch_in` or `punch_out` for a record that stays `ABSENT` or `LEAVE` returns 422.

See `REVIEW.md` for the defects found in the starter code and `DECISIONS.md` for design decisions.
