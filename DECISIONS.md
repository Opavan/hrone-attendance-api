# DECISIONS.md

## 1. Indexes

I created a unique index on `employees.emp_code` to prevent duplicate employee codes. A compound index on `department` and `emp_code` supports department-filtered employee listings. A unique compound index on `attendance_logs.emp_code` and `date` guarantees one attendance record per employee per day. An index on `date` descending and `emp_code` ascending supports attendance listing. I would avoid adding indexes without a query or uniqueness requirement because they increase write costs.

## 2. Punch-in race

If two identical punch-in requests arrive together, both may initially find that no record exists. The unique compound index allows only one insert to succeed. The other insert raises a duplicate-key error, which the API converts into HTTP 409 Conflict.

## 3. Ties

The leaderboard uses competition ranking. Employees tied on late minutes receive the same rank. Because the limit is applied to rank rather than simply the number of returned documents, ties at the cutoff can produce more results than the requested limit.

## 4. Headcount

The department summary starts from employees, not attendance logs. It includes employees who joined on or before the reporting period ends, then looks up their attendance records. Employees with no logs are therefore still counted.

## 5. Scaling 100x

I would benchmark with a production-sized dataset, inspect execution plans, and monitor slow queries. I would then optimize indexes and aggregation pipelines based on measured bottlenecks rather than adding indexes blindly.
