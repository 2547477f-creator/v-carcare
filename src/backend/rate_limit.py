"""จำกัดจำนวนคำขอของ API สาธารณะตามหมายเลขไอพี"""

import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import jsonify, request


class RateLimiter:
    """เก็บช่วงเวลาคำขอในหน่วยความจำสำหรับเว็บเซิร์ฟเวอร์หนึ่งตัว"""

    def __init__(self):
        self.requests = defaultdict(deque)
        self.lock = Lock()

    def limit(self, maximum, window_seconds):
        def decorator(function):
            @wraps(function)
            def wrapper(*args, **kwargs):
                client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
                client_ip = client_ip.split(',')[0].strip()
                key = (function.__name__, client_ip)
                now = time.monotonic()
                with self.lock:
                    timestamps = self.requests[key]
                    while timestamps and timestamps[0] <= now - window_seconds:
                        timestamps.popleft()
                    if len(timestamps) >= maximum:
                        return jsonify({
                            'status': 'error',
                            'message': 'ส่งคำขอบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่',
                        }), 429
                    timestamps.append(now)
                return function(*args, **kwargs)
            return wrapper
        return decorator


rate_limiter = RateLimiter()
