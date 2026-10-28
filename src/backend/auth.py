"""ตัวตกแต่งสำหรับยืนยันตัวตนและสิทธิ์ตามบทบาท"""

from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for


class AuthorizationService:
    """Session-based authorization decisions used by Flask decorators."""

    @staticmethod
    def is_authenticated():
        return bool(session.get('user_id'))

    @classmethod
    def is_manager(cls):
        return cls.is_authenticated() and session.get('role') == 'manager'


authorization_service = AuthorizationService()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not authorization_service.is_authenticated():
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'กรุณาเข้าสู่ระบบก่อน'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not authorization_service.is_authenticated():
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'กรุณาเข้าสู่ระบบก่อน'}), 401
            return redirect(url_for('login'))
        if not authorization_service.is_manager():
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'เฉพาะผู้จัดการเท่านั้น'}), 403
            flash('หน้านี้สำหรับผู้จัดการเท่านั้น', 'error')
            return redirect(url_for('pos'))
        return f(*args, **kwargs)
    return wrapper
