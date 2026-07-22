import os
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash

# 1. ประกาศสร้างตัวแปร app และตั้งค่า Path
backend_dir = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.join(backend_dir, '..', 'frontend')

app = Flask(
    __name__,
    template_folder=os.path.join(frontend_dir, 'templates'),
    static_folder=os.path.join(frontend_dir, 'static')
)

app.secret_key = 'v_carcare_secret_key_2026'


# ---------------------------------------------------------
# Helper Decorators (ตัวคุมสิทธิ์การเข้าถึงแต่ละ Role)
# ---------------------------------------------------------
def login_required(f):
    """ตรวจสอบว่าผู้ใช้ล็อกอินหรือยัง"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_role' not in session:
            if request.path.startswith('/api/'):
                return jsonify({"status": "error", "message": "Login required"}), 401
            flash('กรุณาเข้าสู่ระบบก่อนใช้งาน', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(allowed_roles):
    """ตรวจสอบระดับสิทธิ์ (Manager / Staff)"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_role' not in session:
                return redirect(url_for('login'))
            if session.get('user_role') not in allowed_roles:
                flash('คุณไม่มีสิทธิ์เข้าถึงหน้านี้', 'error')
                return redirect(url_for('pos') if session.get('user_role') == 'staff' else url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ---------------------------------------------------------
# Page Routes (ส่วนการแสดงผลหน้าเว็บ HTML)
# ---------------------------------------------------------

@app.route('/')
@login_required
def index():
    """หน้าแรก / ภาพรวมระบบ (ถ้ายังไม่ล็อกอินจะเด้งไป /login)"""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """หน้าล็อคอินเข้าสู่ระบบ (แสดงฟอร์มเสมอเมื่อเข้า URL นี้)"""
    if request.method == 'POST':
        role_type = request.form.get('role_type')
        
        # ล็อคอินสำหรับผู้จัดการ
        if role_type == 'manager':
            username = request.form.get('username')
            password = request.form.get('password')
            
            if username == 'admin' and password == '1234':
                session['user_role'] = 'manager'
                session['user_name'] = 'ผู้จัดการ'
                return redirect(url_for('index'))
            else:
                flash('Username หรือ Password ไม่ถูกต้อง!', 'error')

        # ล็อคอินสำหรับพนักงาน
        elif role_type == 'staff':
            staff_id = request.form.get('staff_id')
            pin_code = request.form.get('pin_code')
            
            if pin_code == '1234':
                session['user_role'] = 'staff'
                session['user_name'] = 'พนักงาน'
                return redirect(url_for('pos'))
            else:
                flash('รหัส PIN ไม่ถูกต้อง!', 'error')

    return render_template('login.html')

@app.route('/logout')
def logout():
    """ออกจากระบบ"""
    session.clear()
    flash('ออกจากระบบเรียบร้อยแล้ว', 'info')
    return redirect(url_for('login'))

@app.route('/pos')
@login_required
@role_required(['manager', 'staff'])
def pos():
    """ระบบ POS บันทึกการขาย/บริการ"""
    return render_template('pos.html')

@app.route('/staff')
@login_required
@role_required(['manager', 'staff'])
def staff():
    """ระบบลงเวลาพนักงาน"""
    return render_template('staff.html')

@app.route('/finance')
@login_required
@role_required(['manager'])
def finance():
    """แดชบอร์ดสรุปรายรับ-รายจ่าย (เฉพาะ Manager)"""
    return render_template('finance.html')

@app.route('/track')
@login_required
def track():
    """ติดตามสถานะการล้างรถสำหรับลูกค้า"""
    return render_template('track.html')


# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------

@app.route('/api/customers', methods=['POST'])
@login_required
def add_customer():
    data = request.json or {}
    phone = data.get('phone')
    line_id = data.get('line_id')
    
    return jsonify({
        "status": "success",
        "message": "บันทึกข้อมูลลูกค้าเรียบร้อยแล้ว",
        "data": {"phone": phone, "line_id": line_id}
    }), 201


@app.route('/api/staff/check-in', methods=['POST'])
@login_required
def staff_check_in():
    data = request.json or {}
    employee_id = data.get('employee_id')
    method = data.get('method')
    
    return jsonify({
        "status": "success",
        "message": f"ลงเวลาสำเร็จ ({method})",
        "employee_id": employee_id
    }), 200


# ---------------------------------------------------------
# Main Execution
# ---------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
