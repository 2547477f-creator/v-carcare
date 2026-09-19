"""กฎเวลาเข้างานและสถานะการลงเวลา"""

import time as clock
from datetime import datetime

from .config import WORK_START_TIME
from .database import get_db_connection


_attendance_start_cache = {'loaded_at': 0, 'time': WORK_START_TIME}


def configured_attendance_start_time():
    if clock.monotonic() - _attendance_start_cache['loaded_at'] < 60:
        return _attendance_start_cache['time']
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'auto_shop_open_time';")
        row = cur.fetchone()
        if row and row['setting_value']:
            _attendance_start_cache['time'] = datetime.strptime(row['setting_value'], '%H:%M').time()
        cur.close()
        conn.close()
    except Exception:
        pass
    _attendance_start_cache['loaded_at'] = clock.monotonic()
    return _attendance_start_cache['time']


def calculate_attendance_status(check_in_at):
    if not check_in_at:
        return 'absent', 0
    if getattr(check_in_at, 'tzinfo', None):
        check_in_at = check_in_at.replace(tzinfo=None)
    start_time = configured_attendance_start_time()
    if check_in_at.time() <= start_time:
        return 'on_time', 0
    late_minutes = int((check_in_at - datetime.combine(check_in_at.date(), start_time)).total_seconds() // 60)
    return 'late', max(late_minutes, 1)
