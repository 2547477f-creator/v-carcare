"""กฎเวลาเข้างานและสถานะการลงเวลา"""

import time as clock
from datetime import datetime

from .config import WORK_START_TIME
from .database import get_db_connection


_attendance_start_cache = {'loaded_at': 0, 'time': WORK_START_TIME}


class AttendanceService:
    """Attendance timing rules and cached shop-opening configuration."""

    def __init__(self, connection_factory=get_db_connection):
        self._connection_factory = connection_factory
        self._cache = {'loaded_at': 0, 'time': WORK_START_TIME}

    def configured_start_time(self):
        if clock.monotonic() - self._cache['loaded_at'] < 60:
            return self._cache['time']
        try:
            conn = self._connection_factory()
            cur = conn.cursor()
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'auto_shop_open_time';")
            row = cur.fetchone()
            if row and row['setting_value']:
                self._cache['time'] = datetime.strptime(row['setting_value'], '%H:%M').time()
            cur.close()
            conn.close()
        except Exception:
            pass
        self._cache['loaded_at'] = clock.monotonic()
        return self._cache['time']

    def calculate_status(self, check_in_at):
        if not check_in_at:
            return 'absent', 0
        if getattr(check_in_at, 'tzinfo', None):
            check_in_at = check_in_at.replace(tzinfo=None)
        start_time = self.configured_start_time()
        if check_in_at.time() <= start_time:
            return 'on_time', 0
        late_minutes = int((check_in_at - datetime.combine(check_in_at.date(), start_time)).total_seconds() // 60)
        return 'late', max(late_minutes, 1)


attendance_service = AttendanceService()


def configured_attendance_start_time():
    return attendance_service.configured_start_time()


def _legacy_configured_attendance_start_time():
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
    return attendance_service.calculate_status(check_in_at)


def _legacy_calculate_attendance_status(check_in_at):
    if not check_in_at:
        return 'absent', 0
    if getattr(check_in_at, 'tzinfo', None):
        check_in_at = check_in_at.replace(tzinfo=None)
    start_time = configured_attendance_start_time()
    if check_in_at.time() <= start_time:
        return 'on_time', 0
    late_minutes = int((check_in_at - datetime.combine(check_in_at.date(), start_time)).total_seconds() // 60)
    return 'late', max(late_minutes, 1)
