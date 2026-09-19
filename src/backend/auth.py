"""ตัวตกแต่งสำหรับยืนยันตัวตนและสิทธิ์ตามบทบาท"""

from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'กรุณาเข้าสู่ระบบก่อน'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'กรุณาเข้าสู่ระบบก่อน'}), 401
            return redirect(url_for('login'))
        if session.get('role') != 'manager':
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'เฉพาะผู้จัดการเท่านั้น'}), 403
            flash('หน้านี้สำหรับผู้จัดการเท่านั้น', 'error')
            return redirect(url_for('pos'))
        return f(*args, **kwargs)
    return wrapper
