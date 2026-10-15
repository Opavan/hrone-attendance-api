# Employee Attendance & Analytics API

A REST API built with **FastAPI, Python, and MongoDB** for employee management, attendance tracking, attendance regularization, and workforce analytics.

## Features

- Create and list employees with pagination and department filtering.
- Record employee punch-in and punch-out times.
- Calculate late minutes, work hours, overtime, and half-day status.
- Regularize attendance records with an audit history.
- Generate monthly employee analytics and department summaries.
- Provide a late-arrival leaderboard with ranking and tie handling.
- Generate daily department attendance trends with a seven-day moving average.
- Inspect MongoDB query execution plans through administrative endpoints.

## Technology Stack

- Python
- FastAPI
- MongoDB 6.0 or newer
- PyMongo
- Pydantic

## Prerequisites

- Python 3.10 or newer, compatible with the installed dependencies
- MongoDB 6.0 or newer
- Git

MongoDB Atlas is also supported.

## Local Setup

### 1. Create and activate a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Create a local `.env` file using `.env.example` as a reference, if provided.

Configure the following variables:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=employee_attendance
```

Use your own MongoDB connection URI and database name where appropriate. Never commit `.env` or credentials to the repository.

### 4. Start MongoDB

If MongoDB is installed locally, ensure the MongoDB service is running.

Alternatively, run MongoDB with Docker:

```bash
docker run -p 27017:27017 mongo:7
```

### 5. Load the sample data

```bash
python sample_seed.py
```

Run this command when you want to load the provided sample documents into the configured database.

### 6. Start the API

```bash
uvicorn app.main:app --port 8000
```

For local development with automatic reloading:

```bash
uvicorn app.main:app --port 8000 --reload
```

## API Endpoints

The API runs locally at `http://127.0.0.1:8000`.

| Method | Endpoint                                    | Purpose                                      |
| ------ | ------------------------------------------- | -------------------------------------------- |
| GET    | `/health`                                   | Check API and MongoDB health                 |
| POST   | `/employees`                                | Create an employee                           |
| GET    | `/employees`                                | List employees                               |
| POST   | `/attendance/punch-in`                      | Record punch-in                              |
| GET    | `/attendance`                               | List attendance records                      |
| POST   | `/attendance/punch-out`                     | Record punch-out                             |
| PATCH  | `/attendance/{emp_code}/{date}`             | Regularize attendance                        |
| GET    | `/attendance/{emp_code}/{date}`             | Retrieve an employee's attendance for a date |
| GET    | `/analytics/employees/{emp_code}/monthly`   | Retrieve monthly employee analytics          |
| GET    | `/analytics/departments/summary`            | Retrieve department summaries                |
| GET    | `/analytics/leaderboard/late`               | Retrieve the late-arrival leaderboard        |
| GET    | `/analytics/departments/{department}/trend` | Retrieve department attendance trends        |
| GET    | `/admin/explain/{endpoint}`                 | Inspect query execution statistics           |

Refer to `openapi.yaml` for request schemas, query parameters, response formats, and business rules.

## API Documentation and Health Check

After starting the server, open:

- Interactive API documentation: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- Health check: `http://127.0.0.1:8000/health`

The health endpoint returns a successful response when MongoDB is reachable.

## Business Rules

The API implements the attendance rules specified in `openapi.yaml`, including:

- IST-based attendance dates and overnight shifts.
- Late-arrival grace periods and overtime thresholds.
- Work-hour rounding and half-day calculation.
- Attendance status validation and regularization history.
- Weekday-based attendance analytics and employee headcount rules.
- Pagination, duplicate protection, and leaderboard ranking.

## Project Structure

```text
.
├── app/
│   └── main.py
├── sample_data/
├── sample_seed.py
├── requirements.txt
├── openapi.yaml
├── DATA_MODEL.md
├── REVIEW.md
├── DECISIONS.md
└── README.md
```

## Submission

Submit a public Git repository containing the required application code, dependencies, documentation, and supporting assignment files.

Do not commit `.env`, credentials, secrets, virtual-environment files, or database dumps.
