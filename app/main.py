"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import calendar
import re 
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
def ensure_indexes():
    """Create every index the app needs. Safe to run on each startup (same spec = no-op)."""
    db.employees.create_index("emp_code", unique=True, name="emp_code_unique")
    db.employees.create_index([("department", 1), ("emp_code", 1)], name="dept_emp_code")
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True, name="emp_date_unique")
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)], name="date_desc_emp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_indexes()
    except PyMongoError as exc:
        print("WARNING: could not create indexes:", exc)
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


def to_ms(dt):
    """BSON datetime -> epoch milliseconds (int). None stays None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH) // timedelta(milliseconds=1)


def from_ms(ms):
    """Epoch milliseconds -> tz-aware UTC datetime."""
    return EPOCH + timedelta(milliseconds=ms)


def history_out(h: dict) -> dict:
    """One stored history entry -> API shape. punch_in/punch_out from/to are BSON dates -> epoch ms."""
    changes = {}
    for field, ch in h.get("changes", {}).items():
        if field in ("punch_in", "punch_out"):
            changes[field] = {"from": to_ms(ch.get("from")), "to": to_ms(ch.get("to"))}
        else:
            changes[field] = {"from": ch.get("from"), "to": ch.get("to")}
    return {"at": to_ms(h["at"]), "by": h["by"], "reason": h["reason"], "changes": changes}


def record_out(doc: dict) -> dict:
    """Stored attendance document -> API shape: epoch ms, legacy gaps filled, no _id."""
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_ms(doc.get("punch_in")),
        "punch_out": to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": [history_out(h) for h in doc.get("history") or []],
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, date_str: str) -> int:
    """R2: late only if strictly more than 10:00 after shift_start (IST); floor minutes since shift_start."""
    h, m = map(int, shift_start.split(":"))
    punch_in = punch_in.replace(microsecond=0)
    start = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    late_seconds = (punch_in - start).total_seconds()
    if late_seconds > 600:
        return int(late_seconds // 60)
    return 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: seconds / 3600, rounded half-up to 2 decimals. Instants are truncated to whole seconds first (R1)."""
    seconds = int((punch_out.replace(microsecond=0) - punch_in.replace(microsecond=0)).total_seconds())
    return float((Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    """R3: whole minutes (floor) from shift_end to punch_out, counted only if >= 30. Overnight shifts end the next day."""
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=eh, minute=em, tzinfo=IST)
    if (eh, em) <= (sh, sm):
        end += timedelta(days=1)
    overtime = int((punch_out.replace(microsecond=0) - end).total_seconds() // 60)
    return overtime if overtime >= 30 else 0

def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    """R1: IST calendar date of punch_in; an overnight-shift punch-in earlier than shift_end belongs to the previous day."""
    ist = punch_in.replace(microsecond=0).astimezone(IST)
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    day = ist.date()
    if (eh, em) <= (sh, sm) and (ist.hour, ist.minute) < (eh, em):
        day -= timedelta(days=1)
    return day.isoformat()

Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]


def parse_date(value: str, name: str) -> str:
    """Strict YYYY-MM-DD calendar date, else 422. Returns the same string."""
    try:
        if len(value) != 10:
            raise ValueError
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, f"{name} must be a valid YYYY-MM-DD date")
    return value


def attendance_query(emp_code, date_from, date_to, status) -> dict:
    """Build the MongoDB filter for GET /attendance (also reused by the explain endpoint)."""
    q = {}
    if emp_code is not None:
        q["emp_code"] = emp_code
    if date_from is not None:
        parse_date(date_from, "date_from")
    if date_to is not None:
        parse_date(date_to, "date_to")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    if date_from is not None or date_to is not None:
        q["date"] = {}
        if date_from is not None:
            q["date"]["$gte"] = date_from
        if date_to is not None:
            q["date"]["$lte"] = date_to
    if status is not None:
        q["status"] = status
    return q

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP[0-9]{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=HHMM)
    shift_end: str = Field("18:30", pattern=HHMM)
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def _valid_joined_on(cls, v):
        try:
            parse_date(v, "joined_on")
        except HTTPException:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return v

    @model_validator(mode="after")
    def _shift_times_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


EpochMs = Annotated[StrictInt, Field(ge=100000000000, le=4102444800000)]


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None

class RegularizeIn(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[EpochMs] = None
    punch_out: Optional[EpochMs] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


PRESENCE = ("PRESENT", "WFH", "ON_DUTY")
TRACKED = ("status", "punch_in", "punch_out", "late_minutes", "work_hours", "overtime_minutes", "half_day")


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(503, "MongoDB is not reachable")
    return {"status": "ok"}


def employee_out(doc: dict) -> dict:
    """Stored employee document -> API shape: created_at in epoch ms, no _id."""
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_ms(doc["created_at"]),
    }


def employee_out(doc: dict) -> dict:
    """Stored employee document -> API shape: created_at in epoch ms, no _id."""
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_ms(doc["created_at"]),
    }


def employee_out(doc: dict) -> dict:
    """Stored employee document -> API shape: created_at in epoch ms, no _id."""
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_ms(doc["created_at"]),
    }


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = from_ms(to_ms(datetime.now(timezone.utc)))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return employee_out(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department is not None:
        q["department"] = department
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    return {"items": [employee_out(d) for d in cursor], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = from_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc)
    ts = ts.replace(microsecond=0)  # R1: truncate to whole seconds, store the truncated value
    d = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)  # unique index (emp_code, date) decides the race
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return record_out(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = attendance_query(emp_code, date_from, date_to, status)
    total = db.attendance_logs.count_documents(q)
    cursor = (
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {"items": [record_out(d) for d in cursor], "total": total, "page": page, "page_size": page_size}

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = from_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc)
    ts = ts.replace(microsecond=0)
    # most recent record opened at or before punched_at (works across midnight for overnight shifts)
    rec = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}}, sort=[("punch_in", -1)]
    )
    if rec is None:
        raise HTTPException(404, "no punch-in found")
    if rec["punch_out"] is not None:
        raise HTTPException(409, "already punched out")
    if ts <= rec["punch_in"] or ts - rec["punch_in"] > timedelta(hours=24):
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours of it")
    work_hours = compute_work_hours(rec["punch_in"], ts)
    # one atomic update: only succeeds while punch_out is still null, so simultaneous requests yield one 200
    updated = db.attendance_logs.find_one_and_update(
        {"_id": rec["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": compute_overtime(ts, emp["shift_start"], emp["shift_end"], rec["date"]),
            "half_day": work_hours < 4.50,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return record_out(updated)

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    parse_date(date, "date")
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if rec is None:
        raise HTTPException(404, "no attendance record for that date")

    # stored state (legacy documents may lack some fields)
    old = {
        "status": rec["status"],
        "punch_in": rec.get("punch_in"),
        "punch_out": rec.get("punch_out"),
        "late_minutes": rec.get("late_minutes", 0),
        "work_hours": rec.get("work_hours"),
        "overtime_minutes": rec.get("overtime_minutes", 0),
        "half_day": rec.get("half_day", False),
    }
    # omitted fields are unchanged; supplied instants are truncated to whole seconds (R1)
    status = body.status or old["status"]
    p_in = from_ms(body.punch_in).replace(microsecond=0) if body.punch_in is not None else old["punch_in"]
    p_out = from_ms(body.punch_out).replace(microsecond=0) if body.punch_out is not None else old["punch_out"]

    if status in PRESENCE:
        if p_in is None:
            raise HTTPException(422, "a presence status requires a punch_in")
        if attendance_date(p_in, emp["shift_start"], emp["shift_end"]) != date:
            raise HTTPException(422, "punch_in must fall on the record's attendance date")
        if p_out is not None and (p_out <= p_in or p_out - p_in > timedelta(hours=24)):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours of it")
    else:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT and LEAVE records cannot have punch times")
        p_in = p_out = None

    # recompute derived fields from the final times (R2-R5)
    late = compute_late_minutes(p_in, emp["shift_start"], date) if p_in is not None else 0
    if p_out is not None:
        work_hours = compute_work_hours(p_in, p_out)
        overtime = compute_overtime(p_out, emp["shift_start"], emp["shift_end"], date)
        half_day = work_hours < 4.50
    else:
        work_hours, overtime, half_day = None, 0, False
    new = {
        "status": status, "punch_in": p_in, "punch_out": p_out, "late_minutes": late,
        "work_hours": work_hours, "overtime_minutes": overtime, "half_day": half_day,
    }

    changes = {f: {"from": old[f], "to": new[f]} for f in TRACKED if old[f] != new[f]}
    if not any(f in changes for f in ("status", "punch_in", "punch_out")):
        raise HTTPException(422, "request changes nothing")

    # optimistic concurrency: write only if nobody changed the record since we read it
    history = rec.get("history")
    guard = {
        "_id": rec["_id"],
        "status": old["status"],
        "punch_in": old["punch_in"],
        "punch_out": old["punch_out"],
        "history": {"$size": len(history)} if history is not None else {"$exists": False},
    }
    entry = {
        "at": from_ms(to_ms(datetime.now(timezone.utc))),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    updated = db.attendance_logs.find_one_and_update(
        guard, {"$set": new, "$push": {"history": entry}}, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        raise HTTPException(409, "record was modified concurrently; retry")
    return record_out(updated)



def month_bounds(month: str):
    """Validate YYYY-MM -> (first_day, last_day) as 'YYYY-MM-DD' strings. 422 on bad input."""
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) or month.startswith("0000"):
        raise HTTPException(422, "month must be a valid YYYY-MM")
    y, m = int(month[:4]), int(month[5:])
    return f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def count_working_days(first: str, last: str, joined_on: str) -> int:
    """R7: Mon-Fri days from max(first, joined_on) to last, inclusive."""
    start = datetime.strptime(max(first, joined_on), "%Y-%m-%d").date()
    end = datetime.strptime(last, "%Y-%m-%d").date()
    n = 0
    while start <= end:
        if start.weekday() < 5:
            n += 1
        start += timedelta(days=1)
    return n


def round_half_up(value, places: int = 2) -> float:
    """R8: half-up rounding done in Decimal (never float rounding)."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return float(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def weekend_dates(first: str, last: str) -> list:
    """Every Saturday/Sunday between first and last (inclusive) as 'YYYY-MM-DD' strings."""
    d = datetime.strptime(first, "%Y-%m-%d").date()
    end = datetime.strptime(last, "%Y-%m-%d").date()
    out = []
    while d <= end:
        if d.weekday() >= 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def employee_monthly_pipeline(emp_code: str, first: str, last: str) -> list:
    """One employee, one month. Present counts only Mon-Fri; half day = 0.5 (R5-R7)."""
    on_weekday = {"$eq": [{"$in": ["$date", weekend_dates(first, last)]}, False]}
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first, "$lte": last}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", list(PRESENCE)]}, on_weekday]},
                {"$cond": ["$half_day", 0.5, 1]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"},
        }},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    first, last = month_bounds(month)
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    stats = next(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, first, last)), None) or {}
    working_days = count_working_days(first, last, emp["joined_on"])
    present = stats.get("present_days", 0)
    pct = round_half_up(Decimal(str(present)) / Decimal(working_days) * 100) if working_days > 0 else None
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": float(present),
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": pct,
    }



def department_summary_pipeline(first: str, last: str, department: Optional[str] = None) -> list:
    """Starts from employees (so people with zero logs still count, R9), joins that month's logs,
    groups per employee, then per department."""
    emp_match = {"joined_on": {"$lte": last}}
    if department is not None:
        emp_match["department"] = department
    is_present = {"$in": ["$logs.status", list(PRESENCE)]}
    on_weekday = {"$eq": [{"$in": ["$logs.date", weekend_dates(first, last)]}, False]}
    has_hours = {"$and": [is_present, {"$gt": ["$logs.work_hours", None]}]}

    def one_if(cond):
        return {"$cond": [cond, 1, 0]}

    return [
        {"$match": emp_match},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "pipeline": [{"$match": {"date": {"$gte": first, "$lte": last}}}],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": {"department": "$department", "emp_code": "$emp_code"},
            "present_days": {"$sum": {"$cond": [{"$and": [is_present, on_weekday]}, {"$cond": ["$logs.half_day", 0.5, 1]}, 0]}},
            "work_hundredths": {"$sum": {"$cond": [has_hours, {"$multiply": ["$logs.work_hours", 100]}, 0]}},
            "work_records": {"$sum": one_if(has_hours)},
            "late_count": {"$sum": one_if({"$gt": ["$logs.late_minutes", 0]})},
            "total_late_minutes": {"$sum": "$logs.late_minutes"},
            "leave_count": {"$sum": one_if({"$eq": ["$logs.status", "LEAVE"]})},
            "on_duty_count": {"$sum": one_if({"$eq": ["$logs.status", "ON_DUTY"]})},
        }},
        {"$group": {
            "_id": "$_id.department",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": "$present_days"},
            "work_hundredths": {"$sum": "$work_hundredths"},
            "work_records": {"$sum": "$work_records"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
        }},
        {"$sort": {"_id": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    first, last = month_bounds(month)
    items = []
    for r in db.employees.aggregate(department_summary_pipeline(first, last, department)):
        n = int(r["work_records"])
        # work hours are summed as whole hundredths, so the average is exact and rounded half-up (R8)
        avg = round_half_up(Decimal(int(round(r["work_hundredths"]))) / Decimal(n) / 100) if n else None
        items.append({
            "department": r["_id"],
            "headcount": r["headcount"],
            "present_days": float(r["present_days"]),
            "avg_work_hours": avg,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"],
        })
    return {"month": month, "items": items}