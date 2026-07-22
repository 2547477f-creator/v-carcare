import os
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash

# โหลดตัวแปรสภาพแวดล้อมจากไฟล์ .env (ที่อยู่ระดับ Root)
load_dotenv()

# --------------------------------------------------
# ตั้งค่า Path โฟลเดอร์ templates และ static ให้ถูกต้อง
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, '..', 'frontend')

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, 'templates'),
    static_folder=os.path.join(FRONTEND_DIR, 'static')
)

# ตั้งค่า Secret Key สำหรับจัดการ Session
app.secret_key = os.getenv("SECRET_KEY", "default_secret_key_vcarcare")

# --------------------------------------------------
# ฟังก์ชันเชื่อมต่อฐานข้อมูล PostgreSQL
# --------------------------------------------------
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            database=os.getenv("DB_NAME", "vcarcare_db"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASS", "1234"),
            port=os.getenv("DB_PORT", "5432"),
            cursor_factory=RealDictCursor
        )
        return conn
    except Exception as e:
        print("\n" + "="*50)
        print(f"❌ DATABASE CONNECTION ERROR:")
        print(e)
        print("="*50 + "\n")
        return None

# --------------------------------------------------
# Decorator ตรวจสอบการเข้าสู่ระบบ
# --------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('กรุณาเข้าสู่ระบบก่อนใช้งาน', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --------------------------------------------------
# Routes: ระบบล็อกอิน & ออกจากระบบ
# --------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'logged_in' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        conn = get_db_connection()
        if conn is None:
            flash('ไม่สามารถเชื่อมต่อฐานข้อมูลได้ กรุณาตรวจสอบการตั้งค่า PostgreSQL (เช็ก Error ใน Terminal)', 'danger')
            return render_template('login.html')

        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE username = %s;", (username,))
            user = cur.fetchone()
            cur.close()
            conn.close()

            # ตรวจสอบชื่อผู้ใช้และรหัสผ่าน (Hash)
            if user and check_password_hash(user['password_hash'], password):
                session['logged_in'] = True
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['fullname'] = user.get('fullname', user['username'])
                session['role'] = user.get('role', 'staff')

                flash(f"ยินดีต้อนรับคุณ {session['fullname']}", 'success')
                return redirect(url_for('index'))
            else:
                flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง!', 'danger')
        except Exception as e:
            flash(f'เกิดข้อผิดพลาดในการตรวจสอบข้อมูล: {e}', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('ออกจากระบบเรียบร้อยแล้ว', 'info')
    return redirect(url_for('login'))

# --------------------------------------------------
# Routes: หน้าหลักการทำงานระบบ
# --------------------------------------------------

# 1. หน้าภาพรวมระบบ (Dashboard / Kanban Board)
@app.route('/')
@login_required
def index():
    return render_template('index.html')


# 2. หน้ารับรถหน้าร้าน (POS System)
@app.route('/pos')
@login_required
def pos():
    return render_template('pos.html')


# 3. หน้าจัดการพนักงาน (Staff Management)
@app.route('/staff')
@login_required
def staff():
    return render_template('staff.html')


# 4. หน้าสรุปบัญชีรายรับ (Finance Summary)
@app.route('/finance')
@login_required
def finance():
    return render_template('finance.html')


# 5. หน้าติดตามสถานะรถ (Customer Tracking)
@app.route('/track')
def track():
    return render_template('track.html')

# --------------------------------------------------
# API Endpoints
# --------------------------------------------------
@app.route('/api/queue', methods=['POST'])
@login_required
def add_queue():
    data = request.get_json()
    return jsonify({"status": "success", "message": "บันทึกคิวเรียบร้อยแล้ว", "data": data})

# --------------------------------------------------
# สั่งรัน Server
# --------------------------------------------------
if __name__ == '__main__':
    print("🚀 เริ่มต้นเซิร์ฟเวอร์ที่ http://127.0.0.1:5000")
    app.run(debug=True, host='127.0.0.1', port=5000)