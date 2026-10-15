# DECISIONS.md

1. **Indexes.** `employees.emp_code` is unique, so no two employees share a code. `employees(department, emp_code)` serves the filtered, sorted employee list. `attendance_logs(emp_code, date)` is unique and serves punch-in, the monthly summary and the trend. `attendance_logs(date desc, emp_code)` serves the attendance list and the leaderboard's date range. `attendance_logs(status, date desc, emp_code)` serves status filters, and `employees(joined_on)` and `(department, joined_on)` serve headcount. I rejected an index on `late_minutes`: queries filter by date first, so it would only slow writes.

2. **Punch-in race.** The code finds the employee, works out the IST date, then inserts without checking first. Both requests reach `insert_one`; the unique `(emp_code, date)` index lets one succeed, and the other gets a duplicate-key error, which becomes 409. The database decides the winner, not my code.

3. **Ties.** The leaderboard uses `$rank`, so tied employees share a rank (1, 2, 2, 4), then keeps `rank <= limit`. Both tied employees are returned, so there can be more rows than `limit`.

4. **Headcount.** The summary starts from `employees` (joined on or before month end), looks up that month's logs, and unwinds with `preserveNullAndEmptyArrays`. An employee with no logs survives and is counted.

5. **At 100x.** I would store a pre-computed daily summary per department, updated on each punch, so trend and summary stop scanning logs.
