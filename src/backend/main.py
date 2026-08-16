import os
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
import os
import json
import cv2
import numpy as np
from deepface import DeepFace
import base64
import uuid
from dotenv import load_dotenv
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

from datetime import datetime, date, timedelta
from functools import wraps

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash

# ===================================================================
# 📂 1. โฟลเดอร์ frontend (templates / static)
# ===================================================================
template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../frontend/templates'))
static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../frontend/static'))

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
app.secret_key = os.environ.get("SECRET_KEY", "vcarcare-dev-secret-change-me")
CORS(app, supports_credentials=True)

# ===================================================================
# ⚙️ 2. การเชื่อมต่อฐานข้อมูล PostgreSQL
#    ตั้งค่าผ่าน Environment Variables ได้ (ถ้าไม่ตั้ง จะใช้ค่า default ด้านล่าง)
# ===================================================================
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    # Support both the documented DB_PASSWORD and the existing DB_PASS name.
    "database": os.environ.get("DB_NAME", "v_carcare"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD") or os.environ.get("DB_PASS", "postgres"),
    "port": os.environ.get("DB_PORT", "5432"),
}


def get_db_connection():
    """เปิดการเชื่อมต่อกับฐานข้อมูล"""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def create_face_embedding(image_path):
    """
    สร้าง Face Embedding จากรูปภาพ

    ลองไล่ detector backend หลายตัวตามลำดับความแม่นยำ เพราะ 'opencv'
    (Haar Cascade) ตัวเดียวค่อนข้างไวต่อแสง/มุมหน้า/ระยะ ทำให้รูปที่
    หน้าคนอยู่จริงๆ ถูกปฏิเสธว่า "ไม่พบใบหน้า" บ่อยเกินไป
    """
    detector_backends = ["retinaface", "mtcnn", "opencv"]

    for backend in detector_backends:
        try:
            embedding = DeepFace.represent(
                img_path=image_path,
                model_name="Facenet512",
                detector_backend=backend,
                enforce_detection=True
            )
            return embedding[0]["embedding"]
        except Exception as e:
            print(f"[create_face_embedding] backend '{backend}' failed: {e}")
            continue

    # ลองทุก backend แล้วยังไม่เจอใบหน้าจริงๆ
    return None


FACE_MATCH_DISTANCE_THRESHOLD = 0.30  # ยิ่งน้อยยิ่งเข้มงวด (cosine distance ของ Facenet512)


THAI_SERVICE_NAMES = {
    'wash': 'ล้างภายนอก',
    'washVacuum': 'ล้างภายนอกและดูดฝุ่น',
    'fullFlush': 'ล้าง ดูดฝุ่น และฉีดล้างช่วงล่าง',
    'engineWash': 'ล้าง ดูดฝุ่น และล้างห้องเครื่อง',
    'fullEngine': 'ล้างครบชุด พร้อมล้างช่วงล่างและห้องเครื่อง',
    'ozone': 'อบโอโซนกำจัดกลิ่น',
    'wax': 'เคลือบแว็กซ์',
}


def ensure_thai_service_names(cur):
    """อัปเดตชื่อบริการมาตรฐานเดิมให้เป็นภาษาไทย โดยไม่แก้รหัสบริการ"""
    for code, name in THAI_SERVICE_NAMES.items():
        cur.execute("UPDATE services SET name = %s WHERE code = %s AND name <> %s;", (name, code, name))


def _load_embedding(raw):
    if raw is None:
        return None

    # PostgreSQL BYTEA
    if isinstance(raw, (bytes, bytearray, memoryview)):
        raw = bytes(raw).decode("utf-8")

    if isinstance(raw, str):
        return json.loads(raw)

    return raw


def _cosine_distance(a, b):
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 1.0
    return 1 - (np.dot(a, b) / denom)


def find_matching_app_user(captured_embedding):
    """
    เทียบ embedding ที่ถ่ายมากับทุกโปรไฟล์ใบหน้าที่บันทึกไว้
    (ทั้งพนักงานและผู้จัดการ เพราะทุกคนมีแถวใน app_users)
    คืนค่า app_user_id ที่ใกล้เคียงที่สุด ถ้าไม่มีใครผ่าน threshold คืน None
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT app_user_id, embedding FROM face_profiles WHERE app_user_id IS NOT NULL;")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    best_user_id = None
    best_distance = None

    for row in rows:
        stored_embedding = _load_embedding(row['embedding'])
        print(type(stored_embedding))
        print(stored_embedding)
        distance = _cosine_distance(captured_embedding, stored_embedding)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_user_id = row['app_user_id']

    if best_distance is not None and best_distance <= FACE_MATCH_DISTANCE_THRESHOLD:
        return best_user_id
    return None


def _iso_week_bounds(iso_year, iso_week):
    """คืนค่า (วันจันทร์, วันอาทิตย์) ของสัปดาห์ ISO ที่กำหนด"""
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = date.fromisocalendar(iso_year, iso_week, 7)
    return monday, sunday


def _period_to_range(period, start_str, end_str):
    """
    แปลงค่า period ('day' | 'week' | 'month' | 'custom') พร้อม start/end
    (รูปแบบ YYYY-MM-DD) ให้เป็นช่วงวันที่ (start_date, end_date)

    หมายเหตุ: ฟังก์ชันนี้หายไปจากไฟล์ต้นฉบับแต่ถูกเรียกใช้ใน
    /api/finance/summary จึงเพิ่มกลับเข้ามาเพื่อให้โค้ดรันได้
    """
    today = date.today()

    if period == 'day':
        return today, today

    if period == 'week':
        start = today - timedelta(days=today.weekday())
        return start, today

    if period == 'month':
        start = today.replace(day=1)
        return start, today

    if period == 'custom':
        try:
            start = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else today
        except ValueError:
            start = today
        try:
            end = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today
        except ValueError:
            end = today
        return start, end

    # ค่า default หากไม่ตรงกับรูปแบบใดเลย
    return today, today


# ===================================================================
# 🔐 3. ระบบยืนยันตัวตน (Session-based Auth)
# ===================================================================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "กรุณาเข้าสู่ระบบก่อน"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "กรุณาเข้าสู่ระบบก่อน"}), 401
            return redirect(url_for("login"))
        if session.get("role") != "manager":
            if request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "เฉพาะผู้จัดการเท่านั้น"}), 403
            flash("หน้านี้สำหรับผู้จัดการเท่านั้น", "error")
            return redirect(url_for("pos"))
        return f(*args, **kwargs)
    return wrapper


# ===================================================================
# 🌐 4. ROUTES สำหรับแสดงผลหน้าเว็บ
# ===================================================================
@app.route('/')
@login_required
def index():
    return render_template('index.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role_type = request.form.get('role_type')
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            if role_type == 'manager':
                username = (request.form.get('username') or '').strip()
                password = request.form.get('password') or ''
                cur.execute(
                    "SELECT * FROM app_users WHERE username = %s AND role = 'manager' AND is_active = true;",
                    (username,)
                )
                user = cur.fetchone()
                if user and check_password_hash(user['password_hash'], password):
                    session['user_id'] = user['id']
                    session['role'] = 'manager'
                    session['staff_id'] = None
                    session['display_name'] = username
                    return redirect(url_for('index'))
                flash("ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง", "error")
                return redirect(url_for('login'))

            elif role_type == 'staff':
                staff_id = request.form.get('staff_id')
                pin_code = request.form.get('pin_code') or ''
                cur.execute(
                    "SELECT * FROM staff WHERE id = %s AND is_active = true;",
                    (staff_id,)
                )
                staff = cur.fetchone()
                if not staff or not staff.get('pin_hash'):
                    flash("ไม่พบพนักงานนี้ หรือยังไม่ได้ตั้งรหัส PIN กรุณาติดต่อผู้จัดการ", "error")
                    return redirect(url_for('login'))

                if check_password_hash(staff['pin_hash'], pin_code):
                    session['user_id'] = f"staff-{staff['id']}"
                    session['role'] = 'staff'
                    session['staff_id'] = staff['id']
                    session['display_name'] = staff['full_name']

                    # เช็คอินอัตโนมัติเมื่อเข้าสู่ระบบ (ถ้ายังไม่ได้เช็คอินวันนี้)
                    cur.execute(
                        "SELECT id FROM staff_attendance WHERE staff_id = %s AND work_date = CURRENT_DATE;",
                        (staff['id'],)
                    )
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO staff_attendance (staff_id, work_date, check_in_at, method) VALUES (%s, CURRENT_DATE, NOW(), 'login');",
                            (staff['id'],)
                        )
                        conn.commit()

                    return redirect(url_for('pos'))
                flash("รหัส PIN ไม่ถูกต้อง", "error")
                return redirect(url_for('login'))

            flash("กรุณาเลือกประเภทผู้ใช้งาน", "error")
            return redirect(url_for('login'))
        finally:
            cur.close()
            conn.close()

    # GET: ดึงรายชื่อพนักงานที่ยัง active มาแสดงใน dropdown
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, full_name, position FROM staff WHERE is_active = true ORDER BY full_name;")
        staff_list = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return render_template('login.html', staff_list=staff_list)


@app.route('/logout')
def logout():
    # เช็คเอาต์ให้พนักงานอัตโนมัติเมื่อออกจากระบบ
    staff_id = session.get('staff_id')
    if staff_id:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE staff_attendance SET check_out_at = NOW()
                   WHERE staff_id = %s AND work_date = CURRENT_DATE AND check_out_at IS NULL;""",
                (staff_id,)
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/face-login', methods=['POST'])
def face_login():
    """
    ล็อกอินด้วยใบหน้า (ใช้แทนกรณีลืมรหัสผ่าน/PIN)
    ใช้ได้ทั้งพนักงานและผู้จัดการ เพราะเทียบจาก app_users.id
    """
    data = request.json or {}
    face_image_b64 = data.get('face_image')

    if not face_image_b64:
        return jsonify({"status": "error", "message": "ไม่พบรูปภาพ"}), 400

    if ',' in face_image_b64:
        face_image_b64 = face_image_b64.split(',')[1]

    tmp_dir = os.path.join(app.static_folder, 'faces', 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"login_{uuid.uuid4().hex}.jpg")

    try:
        with open(tmp_path, 'wb') as fh:
            fh.write(base64.b64decode(face_image_b64))

        embedding = create_face_embedding(tmp_path)
        if embedding is None:
            return jsonify({"status": "error", "message": "ไม่พบใบหน้าในภาพ กรุณาลองใหม่"}), 400

        matched_user_id = find_matching_app_user(embedding)
        if matched_user_id is None:
            return jsonify({"status": "error", "message": "ไม่พบใบหน้านี้ในระบบ กรุณาติดต่อผู้จัดการ"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM app_users WHERE id = %s AND is_active = true;", (matched_user_id,))
            user = cur.fetchone()
            if not user:
                return jsonify({"status": "error", "message": "บัญชีนี้ถูกปิดใช้งาน"}), 403

            if user['role'] == 'manager':
                session['user_id'] = user['id']
                session['role'] = 'manager'
                session['staff_id'] = None
                session['display_name'] = user['username']
                redirect_url = url_for('index')

            else:  # staff
                cur.execute("SELECT full_name FROM staff WHERE id = %s AND is_active = true;", (user['staff_id'],))
                staff = cur.fetchone()
                if not staff:
                    return jsonify({"status": "error", "message": "ไม่พบข้อมูลพนักงาน หรือถูกปิดใช้งาน"}), 403

                session['user_id'] = f"staff-{user['staff_id']}"
                session['role'] = 'staff'
                session['staff_id'] = user['staff_id']
                session['display_name'] = staff['full_name']

                # เช็คอินอัตโนมัติเหมือนตอนล็อกอินด้วย PIN (ถ้ายังไม่ได้เช็คอินวันนี้)
                cur.execute(
                    "SELECT id FROM staff_attendance WHERE staff_id = %s AND work_date = CURRENT_DATE;",
                    (user['staff_id'],)
                )
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO staff_attendance (staff_id, work_date, check_in_at, method) VALUES (%s, CURRENT_DATE, NOW(), 'face');",
                        (user['staff_id'],)
                    )
                    conn.commit()

                redirect_url = url_for('pos')

            return jsonify({
                "status": "success",
                "role": session['role'],
                "display_name": session['display_name'],
                "redirect": redirect_url
            }), 200
        finally:
            cur.close()
            conn.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.route('/pos')
@login_required
def pos():
    return render_template('pos.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/register')
@login_required
def register():
    return render_template('register.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/history')
@login_required
def history():
    """ประวัติรถที่รับแล้ว — พนักงานและผู้จัดการเข้าดูได้"""
    return render_template('history.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/service-management')
@manager_required
def service_management():
    return render_template('service_management.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/track')
def track():
    # หน้าลูกค้าติดตามสถานะ ไม่ต้องล็อกอิน (เข้าผ่านลิงก์/QR ได้เลย)
    return render_template('track.html')


@app.route('/face-checkin')
def face_checkin():
    """หน้าสำหรับสแกนหน้าเช็คอิน"""
    return render_template('face_checkin.html')


@app.route('/staff')
@manager_required
def staff_page():
    return render_template('staff.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/finance')
@manager_required
def finance():
    return render_template('finance.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/staff-advances')
@manager_required
def staff_advances_page():
    return render_template('staff_advances.html', session_role=session.get("role"), session_name=session.get("display_name"))


# ===================================================================
# 🔑 5. ROUTE ตั้งค่าเริ่มต้นระบบ (ไม่ต้องพิมพ์โค้ดใน pgAdmin)
# ===================================================================
@app.route('/setup-admin')
def setup_admin():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM app_users WHERE username = 'admin';")
        existing_user = cur.fetchone()
        if existing_user:
            return jsonify({
                "status": "info",
                "message": "มีบัญชี admin อยู่ในระบบเรียบร้อยแล้ว",
                "account": {"username": "admin", "password": "admin123", "role": "manager"}
            }), 200

        hashed_password = generate_password_hash('admin123')
        cur.execute(
            "INSERT INTO app_users (username, password_hash, role) VALUES (%s, %s, 'manager') RETURNING id, username, role;",
            ('admin', hashed_password)
        )
        conn.commit()
        return jsonify({
            "status": "success",
            "message": "สร้างบัญชีผู้จัดการสำเร็จ",
            "account": {"username": "admin", "password": "admin123", "role": "manager"}
        }), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/setup-staff-pins')
def setup_staff_pins():
    """ตั้งรหัส PIN เริ่มต้น (1234) ให้พนักงานทุกคนที่ยังไม่มี pin_hash ในระบบ"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, full_name FROM staff WHERE pin_hash IS NULL;")
        staff_without_pin = cur.fetchall()
        default_hash = generate_password_hash('1234')
        for s in staff_without_pin:
            cur.execute("UPDATE staff SET pin_hash = %s WHERE id = %s;", (default_hash, s['id']))
        conn.commit()
        return jsonify({
            "status": "success",
            "message": f"ตั้งรหัส PIN เริ่มต้น (1234) ให้พนักงาน {len(staff_without_pin)} คนเรียบร้อย",
            "staff_updated": [s['full_name'] for s in staff_without_pin],
            "default_pin": "1234"
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ===================================================================
# 🔌 6. API: คิวงาน / Kanban (index.html)
# ===================================================================
@app.route('/api/orders', methods=['GET'])
@login_required
def get_orders():
    status_filter = request.args.get('status')
    history_date = request.args.get('date')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        base_query = """
            SELECT o.id AS order_id, o.queue_no, o.status, o.total_amount, o.payment_method,
                   o.created_at, o.started_at, o.completed_at, o.updated_at AS picked_up_at,
                   v.license_plate, v.province, v.category AS vehicle_category, v.size_code,
                   c.phone,
                   COALESCE(
                     (SELECT string_agg(soi.service_name, ' + ' ORDER BY soi.id)
                      FROM service_order_items soi WHERE soi.order_id = o.id),
                     ''
                   ) AS services_summary
            FROM service_orders o
            JOIN vehicles v ON o.vehicle_id = v.id
            JOIN customers c ON o.customer_id = c.id
        """
        if status_filter and history_date:
            cur.execute(base_query + " WHERE o.status = %s AND DATE(o.updated_at) = %s ORDER BY o.updated_at DESC;", (status_filter, history_date))
        elif status_filter:
            cur.execute(base_query + " WHERE o.status = %s ORDER BY o.created_at ASC;", (status_filter,))
        else:
            cur.execute(base_query + " WHERE o.status != 'cancelled' ORDER BY o.created_at ASC;")
        orders = cur.fetchall()
        return jsonify(orders), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
@login_required
def update_order_status(order_id):
    new_status = (request.json or {}).get('status')
    valid_statuses = ('pending', 'in_progress', 'drying', 'ready', 'completed', 'picked_up', 'cancelled')
    if new_status not in valid_statuses:
        return jsonify({"status": "error", "message": "สถานะไม่ถูกต้อง"}), 400
    if new_status == 'picked_up':
        return jsonify({"status": "error", "message": "กรุณาชำระเงินผ่านปุ่มชำระเงินก่อนรับรถ"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        extra_set = ""
        if new_status == 'in_progress':
            extra_set = ", started_at = COALESCE(started_at, NOW())"
        elif new_status == 'completed':
            extra_set = ", completed_at = NOW()"

        cur.execute(
            f"UPDATE service_orders SET status = %s, updated_at = NOW() {extra_set} WHERE id = %s RETURNING *;",
            (new_status, order_id)
        )
        updated_order = cur.fetchone()
        if not updated_order:
            conn.rollback()
            return jsonify({"status": "error", "message": "ไม่พบคิวนี้"}), 404
        conn.commit()
        return jsonify({"status": "success", "order": updated_order}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/orders/<int:order_id>/payment', methods=['POST'])
@login_required
def pay_and_pick_up_order(order_id):
    payment_method = (request.json or {}).get('payment_method', 'cash')
    if payment_method not in ('cash', 'transfer', 'card', 'other'):
        return jsonify({"status": "error", "message": "ช่องทางชำระเงินไม่ถูกต้อง"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT o.queue_no, o.status, o.total_amount, v.license_plate
               FROM service_orders o JOIN vehicles v ON v.id = o.vehicle_id
               WHERE o.id = %s FOR UPDATE;""",
            (order_id,)
        )
        order = cur.fetchone()
        if not order:
            conn.rollback()
            return jsonify({"status": "error", "message": "ไม่พบคิวนี้"}), 404
        if order['status'] != 'completed':
            conn.rollback()
            return jsonify({"status": "error", "message": "ชำระเงินได้เฉพาะรถที่ล้างเสร็จแล้ว"}), 400

        cur.execute("SELECT 1 FROM payments WHERE order_id = %s AND status = 'paid';", (order_id,))
        if cur.fetchone():
            conn.rollback()
            return jsonify({"status": "error", "message": "คิวนี้ชำระเงินแล้ว"}), 409

        cur.execute(
            "INSERT INTO payments (order_id, method, amount, status) VALUES (%s, %s, %s, 'paid');",
            (order_id, payment_method, order['total_amount'])
        )
        cur.execute(
            """INSERT INTO finance_transactions (order_id, transaction_type, category, description, amount)
               VALUES (%s, 'income', 'service', %s, %s);""",
            (order_id, f"รายรับจากคิว {order['queue_no']} (ทะเบียน {order['license_plate']})", order['total_amount'])
        )
        cur.execute(
            "UPDATE service_orders SET status = 'picked_up', payment_method = %s, updated_at = NOW() WHERE id = %s;",
            (payment_method, order_id)
        )
        conn.commit()
        return jsonify({"status": "success", "message": "ชำระเงินสำเร็จและเปลี่ยนสถานะเป็นรับรถแล้ว"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/track/<string:queue_no>', methods=['GET'])
def track_order(queue_no):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT o.id AS order_id, o.queue_no, o.status, o.total_amount, o.created_at,
                   v.license_plate, v.province, v.category AS vehicle_category, v.size_code,
                   COALESCE(
                     (SELECT json_agg(json_build_object('service_name', soi.service_name, 'price', soi.price) ORDER BY soi.id)
                      FROM service_order_items soi WHERE soi.order_id = o.id),
                     '[]'::json
                   ) AS items
            FROM service_orders o
            JOIN vehicles v ON o.vehicle_id = v.id
            WHERE o.queue_no = %s;
            """,
            (queue_no,)
        )
        order = cur.fetchone()
        if order:
            return jsonify({"status": "success", "data": order}), 200
        return jsonify({"status": "error", "message": "ไม่พบหมายเลขคิวนี้ กรุณาตรวจสอบอีกครั้ง"}), 404
    finally:
        cur.close()
        conn.close()


# ===================================================================
# 🔌 7. API: POS - ราคาบริการ / เปิดบิล (pos.html)
# ===================================================================
@app.route('/api/services', methods=['GET'])
@login_required
def get_services_with_prices():
    category = request.args.get('category', 'car')      # car | bike
    size_code = request.args.get('size', 'M')            # S | M | L | XL

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        ensure_thai_service_names(cur)
        cur.execute(
            """
            SELECT s.id AS service_id, s.code, s.name, s.estimated_minutes, sp.price
            FROM services s
            JOIN service_prices sp ON s.id = sp.service_id
            WHERE sp.vehicle_category = %s AND sp.size_code = %s AND s.is_active = true
            ORDER BY sp.price ASC;
            """,
            (category, size_code)
        )
        services = cur.fetchall()
        conn.commit()
        return jsonify(services), 200
    finally:
        cur.close()
        conn.close()


def _ensure_promotions_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promotions (
            id BIGSERIAL PRIMARY KEY, name VARCHAR(160) NOT NULL,
            description TEXT, discount_type VARCHAR(10) NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
            discount_value NUMERIC(10,2) NOT NULL CHECK (discount_value >= 0),
            starts_at DATE, ends_at DATE, is_active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


@app.route('/api/manage/services', methods=['GET', 'POST'])
@manager_required
def manage_services():
    conn = get_db_connection(); cur = conn.cursor()
    try:
        ensure_thai_service_names(cur)
        if request.method == 'GET':
            cur.execute("""SELECT s.id, s.code, s.name, s.category, s.estimated_minutes, s.is_active,
                                  sp.id AS price_id, sp.vehicle_category, sp.size_code, sp.price
                           FROM services s LEFT JOIN service_prices sp ON sp.service_id = s.id
                           ORDER BY s.id, sp.vehicle_category, sp.size_code;""")
            services = cur.fetchall()
            conn.commit()
            return jsonify(services)
        data = request.json or {}
        code = (data.get('code') or '').strip().lower().replace(' ', '_')
        name = (data.get('name') or '').strip()
        category = data.get('category', 'all')
        minutes = data.get('estimated_minutes', 30)
        prices = data.get('prices', [])
        if not code or not name or category not in ('car', 'bike', 'all') or not prices:
            return jsonify({'message': 'กรุณากรอกข้อมูลบริการและราคาให้ครบ'}), 400
        cur.execute("INSERT INTO services (code, name, category, estimated_minutes) VALUES (%s, %s, %s, %s) RETURNING id;", (code, name, category, minutes))
        service_id = cur.fetchone()['id']
        for price in prices:
            if price.get('vehicle_category') not in ('car', 'bike') or not price.get('size_code'):
                raise ValueError('ข้อมูลราคามีรูปแบบไม่ถูกต้อง')
            cur.execute("INSERT INTO service_prices (service_id, vehicle_category, size_code, price) VALUES (%s, %s, %s, %s);", (service_id, price['vehicle_category'], price['size_code'], price.get('price', 0)))
        conn.commit()
        return jsonify({'status': 'success', 'id': service_id}), 201
    except (ValueError, psycopg2.Error) as e:
        conn.rollback(); return jsonify({'message': str(e)}), 400
    finally:
        cur.close(); conn.close()


@app.route('/api/manage/services/<int:service_id>', methods=['PUT', 'DELETE'])
@manager_required
def manage_service(service_id):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if request.method == 'DELETE':
            cur.execute("UPDATE services SET is_active = false WHERE id = %s RETURNING id;", (service_id,))
        else:
            data = request.json or {}
            cur.execute("UPDATE services SET name = %s, estimated_minutes = %s, is_active = %s WHERE id = %s RETURNING id;", ((data.get('name') or '').strip(), data.get('estimated_minutes', 30), bool(data.get('is_active', True)), service_id))
        if not cur.fetchone(): return jsonify({'message': 'ไม่พบบริการ'}), 404
        conn.commit(); return jsonify({'status': 'success'})
    finally:
        cur.close(); conn.close()


@app.route('/api/manage/service-prices/<int:price_id>', methods=['PUT'])
@manager_required
def update_service_price(price_id):
    price = (request.json or {}).get('price')
    try: price = float(price)
    except (TypeError, ValueError): return jsonify({'message': 'ราคาไม่ถูกต้อง'}), 400
    if price < 0: return jsonify({'message': 'ราคาต้องไม่น้อยกว่า 0'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE service_prices SET price = %s WHERE id = %s RETURNING id;", (price, price_id))
        if not cur.fetchone(): return jsonify({'message': 'ไม่พบราคา'}), 404
        conn.commit(); return jsonify({'status': 'success'})
    finally:
        cur.close(); conn.close()


@app.route('/api/manage/promotions', methods=['GET', 'POST'])
@manager_required
def manage_promotions():
    conn = get_db_connection(); cur = conn.cursor()
    try:
        _ensure_promotions_table(cur)
        if request.method == 'GET':
            cur.execute("SELECT * FROM promotions ORDER BY is_active DESC, created_at DESC;")
            conn.commit(); return jsonify(cur.fetchall())
        data = request.json or {}
        if not (data.get('name') or '').strip() or data.get('discount_type') not in ('percent', 'fixed'):
            return jsonify({'message': 'กรุณากรอกชื่อและรูปแบบส่วนลด'}), 400
        cur.execute("INSERT INTO promotions (name, description, discount_type, discount_value, starts_at, ends_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id;", ((data['name']).strip(), data.get('description'), data['discount_type'], data.get('discount_value', 0), data.get('starts_at') or None, data.get('ends_at') or None))
        promotion_id = cur.fetchone()['id']; conn.commit(); return jsonify({'status': 'success', 'id': promotion_id}), 201
    finally:
        cur.close(); conn.close()


@app.route('/api/manage/promotions/<int:promotion_id>', methods=['DELETE'])
@manager_required
def delete_promotion(promotion_id):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        _ensure_promotions_table(cur)
        cur.execute("DELETE FROM promotions WHERE id = %s RETURNING id;", (promotion_id))
        if not cur.fetchone(): return jsonify({'message': 'ไม่พบโปรโมชัน'}), 404
        conn.commit(); return jsonify({'status': 'success'})
    finally:
        cur.close(); conn.close()


@app.route('/api/vehicles/lookup', methods=['GET'])
@login_required
def lookup_vehicle():
    license_plate = (request.args.get('license_plate') or '').strip()
    if not license_plate:
        return jsonify({"status": "error", "message": "กรุณากรอกทะเบียนรถ"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT v.id AS vehicle_id, v.license_plate, v.province, v.category, v.size_code,
                      c.phone, c.line_id
               FROM vehicles v JOIN customers c ON c.id = v.customer_id
               WHERE UPPER(REPLACE(v.license_plate, ' ', '')) = UPPER(REPLACE(%s, ' ', ''))
               ORDER BY v.id DESC LIMIT 1;""",
            (license_plate,)
        )
        vehicle = cur.fetchone()
        if not vehicle:
            return jsonify({"status": "error", "message": "ไม่พบทะเบียนนี้ กรุณาลงทะเบียนรถก่อน"}), 404
        return jsonify({"status": "success", "data": vehicle}), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/registrations', methods=['POST'])
@login_required
def register_vehicle():
    data = request.json or {}
    license_plate = (data.get('license_plate') or '').strip()
    province = (data.get('province') or '').strip() or None
    phone = (data.get('phone') or '').strip()
    line_id = (data.get('line_id') or '').strip() or None
    category = data.get('category')
    size_code = data.get('size')
    if not all((license_plate, phone, category, size_code)) or category not in ('car', 'bike'):
        return jsonify({"status": "error", "message": "กรุณากรอกข้อมูลลูกค้าและรถให้ครบถ้วน"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM customers WHERE phone = %s;", (phone,))
        customer = cur.fetchone()
        if customer:
            customer_id = customer['id']
            cur.execute("UPDATE customers SET line_id = COALESCE(%s, line_id), updated_at = NOW() WHERE id = %s;", (line_id, customer_id))
        else:
            cur.execute("INSERT INTO customers (phone, line_id) VALUES (%s, %s) RETURNING id;", (phone, line_id))
            customer_id = cur.fetchone()['id']

        cur.execute("SELECT id FROM vehicles WHERE license_plate = %s AND province IS NOT DISTINCT FROM %s;", (license_plate, province))
        vehicle = cur.fetchone()
        if vehicle:
            cur.execute("UPDATE vehicles SET customer_id = %s, category = %s, size_code = %s WHERE id = %s;", (customer_id, category, size_code, vehicle['id']))
            vehicle_id = vehicle['id']
        else:
            cur.execute("INSERT INTO vehicles (customer_id, license_plate, province, category, size_code) VALUES (%s, %s, %s, %s, %s) RETURNING id;", (customer_id, license_plate, province, category, size_code))
            vehicle_id = cur.fetchone()['id']
        conn.commit()
        return jsonify({"status": "success", "vehicle_id": vehicle_id, "message": "ลงทะเบียนรถเรียบร้อยแล้ว"}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    data = request.json or {}
    vehicle_id = data.get('vehicle_id')
    selected_services = data.get('services', [])

    if not vehicle_id:
        return jsonify({"status": "error", "message": "กรุณาค้นหาและเลือกรถที่ลงทะเบียนแล้ว"}), 400
    if not selected_services:
        return jsonify({"status": "error", "message": "กรุณาเลือกบริการอย่างน้อย 1 รายการ"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT customer_id, license_plate, category, size_code FROM vehicles WHERE id = %s;", (vehicle_id,))
        vehicle = cur.fetchone()
        if not vehicle:
            conn.rollback()
            return jsonify({"status": "error", "message": "ไม่พบรถที่ลงทะเบียนไว้"}), 404
        license_plate = vehicle['license_plate']
        customer_id = vehicle['customer_id']
        category = vehicle['category']
        size_code = vehicle['size_code']

        # 3. สร้างรหัสคิวประจำวัน
        queue_prefix = datetime.now().strftime("Q%Y%m%d-")
        cur.execute("SELECT COUNT(*) + 1 AS next_q FROM service_orders WHERE queue_no LIKE %s;", (f"{queue_prefix}%",))
        next_q = cur.fetchone()['next_q']
        queue_no = f"{queue_prefix}{next_q:04d}"

        # 4. ตรวจสอบราคาบริการจริงจากฐานข้อมูล (ป้องกันการปลอมราคาจากฝั่ง client)
        service_ids = [item['service_id'] for item in selected_services]
        cur.execute(
            """SELECT s.id AS service_id, s.code, s.name, sp.price
               FROM services s JOIN service_prices sp ON s.id = sp.service_id
               WHERE s.id = ANY(%s) AND sp.vehicle_category = %s AND sp.size_code = %s;""",
            (service_ids, category, size_code)
        )
        verified_services = cur.fetchall()
        if len(verified_services) != len(set(service_ids)):
            conn.rollback()
            return jsonify({"status": "error", "message": "ข้อมูลบริการหรือราคาไม่ถูกต้อง กรุณาลองใหม่"}), 400

        total_amount = sum(item['price'] for item in verified_services)

        # 5. สร้างออเดอร์ (created_by ต้องอ้างอิง app_users.id เท่านั้น)
        if session.get('role') == 'manager':
            created_by_ref = session.get('user_id')
        else:
            created_by_ref = None
            if session.get('staff_id'):
                cur.execute("SELECT id FROM app_users WHERE staff_id = %s;", (session.get('staff_id'),))
                app_user_row = cur.fetchone()
                created_by_ref = app_user_row['id'] if app_user_row else None

        cur.execute(
            """INSERT INTO service_orders (queue_no, customer_id, vehicle_id, status, total_amount, created_by)
               VALUES (%s, %s, %s, 'pending', %s, %s) RETURNING id;""",
            (queue_no, customer_id, vehicle_id, total_amount, created_by_ref)
        )
        order_id = cur.fetchone()['id']

        # 6. รายการบริการ
        for item in verified_services:
            cur.execute(
                """INSERT INTO service_order_items (order_id, service_id, service_code, service_name, price)
                   VALUES (%s, %s, %s, %s, %s);""",
                (order_id, item['service_id'], item['code'], item['name'], item['price'])
            )

        conn.commit()
        return jsonify({
            "status": "success",
            "queue_no": queue_no,
            "order_id": order_id,
            "total_amount": float(total_amount)
        }), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ===================================================================
# 🔌 8. API: พนักงาน / เวลาเข้างาน (staff.html)
# ===================================================================
@app.route('/api/staff', methods=['GET'])
@login_required
def get_staff():
    show_all = request.args.get('all') == 'true'
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT s.id, s.employee_code, s.full_name, s."position", s.daily_wage, s.is_active,
                   sa.check_in_at, sa.check_out_at
            FROM staff s
            LEFT JOIN staff_attendance sa ON s.id = sa.staff_id AND sa.work_date = CURRENT_DATE
        """
        if not show_all:
            query += " WHERE s.is_active = true"
        query += " ORDER BY s.full_name;"
        cur.execute(query)
        staff_list = cur.fetchall()
        return jsonify(staff_list), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/<int:staff_id>', methods=['PUT'])
@manager_required
def update_staff(staff_id):
    data = request.json or {}
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if 'daily_wage' in data:
            cur.execute("UPDATE staff SET daily_wage = %s, updated_at = NOW() WHERE id = %s;", (data['daily_wage'], staff_id))
        if 'is_active' in data:
            cur.execute("UPDATE staff SET is_active = %s, updated_at = NOW() WHERE id = %s;", (data['is_active'], staff_id))
        if 'position' in data:
            cur.execute('UPDATE staff SET "position" = %s, updated_at = NOW() WHERE id = %s;', (data['position'], staff_id))
        conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/<int:staff_id>', methods=['DELETE'])
@manager_required
def delete_staff(staff_id):
    # ลบแบบ soft-delete เพื่อรักษาประวัติการทำงาน/บัญชีเก่าไว้
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE staff SET is_active = false, updated_at = NOW() WHERE id = %s;", (staff_id,))
        conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/attendance', methods=['POST'])
@login_required
def staff_attendance():
    data = request.json or {}
    staff_id = data.get('staff_id')
    action = data.get('action')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if action == 'check_in':
            cur.execute(
                """INSERT INTO staff_attendance (staff_id, work_date, check_in_at, method)
                   VALUES (%s, CURRENT_DATE, NOW(), 'manual')
                   ON CONFLICT (staff_id, work_date) DO UPDATE SET check_in_at = NOW()
                   RETURNING *;""",
                (staff_id,)
            )
        elif action == 'check_out':
            cur.execute(
                """UPDATE staff_attendance SET check_out_at = NOW()
                   WHERE staff_id = %s AND work_date = CURRENT_DATE RETURNING *;""",
                (staff_id,)
            )
        else:
            return jsonify({"status": "error", "message": "action ต้องเป็น check_in หรือ check_out"}), 400

        record = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "record": record}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ===================================================================
# 🔌 9. API: บัญชีการเงิน (finance.html)
# ===================================================================
# ===================================================================
# API: Staff withdrawals / advances (staff_advances.html)
#
# ระบบเบิกเงินพนักงานแบบมีขั้นตอนอนุมัติ (ตาราง staff_withdrawals):
#   1) พนักงาน/ผู้จัดการ "ขอเบิก"      -> status = pending
#   2) ผู้จัดการ "อนุมัติ" หรือ "ปฏิเสธ" -> status = approved / rejected
#   3) ผู้จัดการ "จ่ายเงินจริง"         -> status = paid
#      (ตอนนี้เท่านั้นที่จะไปโผล่เป็นรายจ่ายใน finance_transactions
#       เพื่อไม่ให้ยอดเบิกที่ยังไม่อนุมัติ/ยังไม่จ่ายจริงปนกับบัญชีจริง)
#   ผู้จัดการยกเลิกคำขอที่ pending/approved (ยังไม่จ่าย) ได้ -> cancelled
# ===================================================================

STAFF_WITHDRAWAL_MAX_PER_REQUEST = 3000
STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK = 2


@app.route('/api/staff-withdrawals', methods=['GET'])
@manager_required
def get_staff_withdrawals():
    staff_id = request.args.get('staff_id', type=int)
    status_filter = request.args.get('status')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT sw.*, s.employee_code, s.full_name,
                   approver.username AS approved_by_username
            FROM staff_withdrawals sw
            JOIN staff s ON s.id = sw.staff_id
            LEFT JOIN app_users approver ON approver.id = sw.approved_by
            WHERE 1 = 1
        """
        params = []
        if staff_id is not None:
            query += " AND sw.staff_id = %s"
            params.append(staff_id)
        if status_filter:
            query += " AND sw.status = %s"
            params.append(status_filter)
        query += " ORDER BY sw.created_at DESC;"
        cur.execute(query, params)
        return jsonify(cur.fetchall()), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff-withdrawals', methods=['POST'])
@manager_required
def create_staff_withdrawal():
    """สร้างคำขอเบิกเงินใหม่ (สถานะ pending รอผู้จัดการอนุมัติ)"""
    data = request.json or {}

    staff_id = data.get('staff_id')
    reason = (data.get('reason') or '').strip()

    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        amount = 0

    if not str(staff_id).isdigit() or amount <= 0:
        return jsonify({"status": "error", "message": "กรุณาระบุพนักงานและจำนวนเงิน"}), 400

    if amount > STAFF_WITHDRAWAL_MAX_PER_REQUEST:
        return jsonify({
            "status": "error",
            "message": f"ขอเบิกได้ไม่เกิน {STAFF_WITHDRAWAL_MAX_PER_REQUEST:,.0f} บาทต่อครั้ง"
        }), 400

    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    week_start, week_end = _iso_week_bounds(iso_year, iso_week)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. เช็คข้อมูลพนักงาน
        cur.execute("SELECT id, full_name, daily_wage FROM staff WHERE id = %s;", (int(staff_id),))
        staff_member = cur.fetchone()
        if not staff_member:
            return jsonify({"status": "error", "message": "ไม่พบพนักงาน"}), 404

        # 2. รายได้สะสมในสัปดาห์นี้ (จากจำนวนวันที่เช็คอิน)
        cur.execute(
            """SELECT COUNT(*) AS work_days FROM staff_attendance
               WHERE staff_id = %s AND work_date BETWEEN %s AND %s;""",
            (int(staff_id), week_start, week_end)
        )
        work_days = cur.fetchone()['work_days']
        earned_income = work_days * float(staff_member['daily_wage'])

        # 3. คำขอที่ยังมีผลอยู่ในสัปดาห์นี้ (ไม่นับที่ถูกปฏิเสธ/ยกเลิกไปแล้ว)
        cur.execute(
            """SELECT COUNT(*) AS req_count, COALESCE(SUM(request_amount), 0) AS req_total
               FROM staff_withdrawals
               WHERE staff_id = %s AND withdraw_week = %s AND withdraw_year = %s
               AND status NOT IN ('rejected', 'cancelled');""",
            (int(staff_id), iso_week, iso_year)
        )
        existing = cur.fetchone()

        if existing['req_count'] >= STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK:
            return jsonify({
                "status": "error",
                "message": f"พนักงานขอเบิกครบ {STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK} ครั้งในสัปดาห์นี้แล้ว"
            }), 400

        remaining_income = earned_income - float(existing['req_total'])
        if amount > remaining_income:
            return jsonify({
                "status": "error",
                "message": f"รายได้สะสมคงเหลือของสัปดาห์นี้ {remaining_income:.2f} บาท ไม่สามารถขอเบิกเกินได้"
            }), 400

        # 4. บันทึกคำขอ (รอผู้จัดการอนุมัติ)
        description = reason or f"คำขอเบิกเงินพนักงาน {staff_member['full_name']}"

        cur.execute(
            """INSERT INTO staff_withdrawals
                   (staff_id, request_amount, reason, withdraw_week, withdraw_year, status)
               VALUES (%s, %s, %s, %s, %s, 'pending')
               RETURNING *;""",
            (int(staff_id), amount, description, iso_week, iso_year)
        )
        new_request = cur.fetchone()
        conn.commit()

        return jsonify({
            "status": "success",
            "withdrawal": new_request,
            "work_days": work_days,
            "earned_income": earned_income,
            "remaining_after_this_request": remaining_income - amount
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.route('/api/staff-withdrawals/<int:withdrawal_id>/status', methods=['PUT'])
@manager_required
def update_staff_withdrawal_status(withdrawal_id):
    """ผู้จัดการอนุมัติ / ปฏิเสธ / จ่ายเงินจริง / ยกเลิก คำขอเบิกเงิน"""
    data = request.json or {}
    new_status = data.get('status')
    valid_statuses = ('approved', 'rejected', 'paid', 'cancelled')

    if new_status not in valid_statuses:
        return jsonify({"status": "error", "message": "สถานะไม่ถูกต้อง"}), 400

    note = (data.get('note') or '').strip() or None
    manager_app_user_id = session.get('user_id')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM staff_withdrawals WHERE id = %s;", (withdrawal_id,))
        withdrawal = cur.fetchone()
        if not withdrawal:
            return jsonify({"status": "error", "message": "ไม่พบคำขอเบิกเงินนี้"}), 404

        if new_status == 'approved':
            if withdrawal['status'] != 'pending':
                return jsonify({"status": "error", "message": "อนุมัติได้เฉพาะคำขอที่ยังรออนุมัติเท่านั้น"}), 400

            approved_amount_raw = data.get('approved_amount')
            try:
                approved_amount = float(approved_amount_raw) if approved_amount_raw is not None else float(withdrawal['request_amount'])
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "จำนวนเงินที่อนุมัติไม่ถูกต้อง"}), 400

            if approved_amount <= 0 or approved_amount > float(withdrawal['request_amount']):
                return jsonify({"status": "error", "message": "จำนวนเงินที่อนุมัติต้องมากกว่า 0 และไม่เกินยอดที่ขอเบิก"}), 400

            cur.execute(
                """UPDATE staff_withdrawals
                   SET status = 'approved', approved_amount = %s, approved_by = %s,
                       approved_at = NOW(), note = COALESCE(%s, note), updated_at = NOW()
                   WHERE id = %s RETURNING *;""",
                (approved_amount, manager_app_user_id, note, withdrawal_id)
            )

        elif new_status == 'rejected':
            if withdrawal['status'] != 'pending':
                return jsonify({"status": "error", "message": "ปฏิเสธได้เฉพาะคำขอที่ยังรออนุมัติเท่านั้น"}), 400

            cur.execute(
                """UPDATE staff_withdrawals
                   SET status = 'rejected', approved_by = %s, approved_at = NOW(),
                       note = COALESCE(%s, note), updated_at = NOW()
                   WHERE id = %s RETURNING *;""",
                (manager_app_user_id, note, withdrawal_id)
            )

        elif new_status == 'cancelled':
            if withdrawal['status'] not in ('pending', 'approved'):
                return jsonify({"status": "error", "message": "ยกเลิกได้เฉพาะคำขอที่ยังไม่จ่ายเงินจริง"}), 400

            cur.execute(
                """UPDATE staff_withdrawals
                   SET status = 'cancelled', note = COALESCE(%s, note), updated_at = NOW()
                   WHERE id = %s RETURNING *;""",
                (note, withdrawal_id)
            )

        elif new_status == 'paid':
            if withdrawal['status'] != 'approved':
                return jsonify({"status": "error", "message": "จ่ายเงินได้เฉพาะคำขอที่อนุมัติแล้วเท่านั้น"}), 400

            pay_amount = float(withdrawal['approved_amount'])

            cur.execute(
                """UPDATE staff_withdrawals
                   SET status = 'paid', paid_at = NOW(), note = COALESCE(%s, note), updated_at = NOW()
                   WHERE id = %s RETURNING *;""",
                (note, withdrawal_id)
            )

            # บันทึกเป็นรายจ่ายจริงในบัญชีก็ต่อเมื่อ "จ่ายเงินแล้ว" เท่านั้น
            cur.execute(
                """INSERT INTO finance_transactions
                       (staff_id, transaction_type, category, description, amount)
                   VALUES (%s, 'expense', 'staff_advance', %s, %s);""",
                (
                    withdrawal['staff_id'],
                    f"เบิกเงินพนักงาน (คำขอ #{withdrawal_id}) {withdrawal['reason'] or ''}".strip(),
                    pay_amount
                )
            )

        updated = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "withdrawal": updated}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.route('/api/finance/summary', methods=['GET'])
@manager_required
def get_finance_summary():
    period = request.args.get('period', 'day')
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    start_date, end_date = _period_to_range(period, start_str, end_str)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS total_income,
                COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
                COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0) AS net_profit
            FROM finance_transactions
            WHERE occurred_at::date BETWEEN %s AND %s;
            """,
            (start_date, end_date)
        )
        summary = cur.fetchone()

        cur.execute(
            """SELECT id, transaction_type, category, description, amount, occurred_at
               FROM finance_transactions
               WHERE occurred_at::date BETWEEN %s AND %s
               ORDER BY occurred_at DESC LIMIT 200;""",
            (start_date, end_date)
        )
        transactions = cur.fetchall()

        return jsonify({
            "period": period,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "summary": summary,
            "transactions": transactions
        }), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/finance/transactions', methods=['POST'])
@manager_required
def add_transaction():
    data = request.json or {}
    trans_type = data.get('transaction_type', 'expense')
    category = data.get('category', 'general')
    amount = data.get('amount')
    description = data.get('description', '')

    if trans_type not in ('income', 'expense') or amount is None:
        return jsonify({"status": "error", "message": "ข้อมูลไม่ถูกต้อง"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO finance_transactions (transaction_type, category, description, amount)
               VALUES (%s, %s, %s, %s) RETURNING *;""",
            (trans_type, category, description, amount)
        )
        new_trans = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "transaction": new_trans}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ===================================================================
# 🔌 API: เพิ่มพนักงานใหม่ + บันทึกรูปภาพและสกัด Face Embedding
# ===================================================================
@app.route('/api/manager/face-enroll', methods=['POST'])
@manager_required
def manager_face_enroll():
    """ให้ผู้จัดการที่ล็อกอินอยู่ลงทะเบียน/เปลี่ยนใบหน้าของตัวเอง (สูงสุด 5 รูป)"""
    data = request.json or {}
    face_images = data.get('face_images', [])

    if not face_images:
        return jsonify({"status": "error", "message": "กรุณาถ่ายรูปใบหน้าอย่างน้อย 1 รูป"}), 400

    app_user_id = session.get('user_id')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # ลงทะเบียนใหม่ = ล้างโปรไฟล์ใบหน้าเดิมของผู้จัดการคนนี้ก่อน
        cur.execute("DELETE FROM face_profiles WHERE app_user_id = %s;", (app_user_id,))

        faces_dir = os.path.join(app.static_folder, 'faces')
        os.makedirs(faces_dir, exist_ok=True)

        saved_images = 0
        for index, face_image_b64 in enumerate(face_images[:5]):
            if ',' in face_image_b64:
                face_image_b64 = face_image_b64.split(',')[1]

            filename = f"manager_{app_user_id}_{index}_{uuid.uuid4().hex[:6]}.jpg"
            filepath = os.path.join(faces_dir, filename)

            with open(filepath, 'wb') as fh:
                fh.write(base64.b64decode(face_image_b64))

            embedding = create_face_embedding(filepath)
            if embedding is None:
                conn.rollback()
                return jsonify({
                    "status": "error",
                    "message": f"ไม่พบใบหน้าในรูปที่ {index + 1}"
                }), 400

            cur.execute(
                """INSERT INTO face_profiles (app_user_id, image_path, embedding, model_name)
                   VALUES (%s, %s, %s, 'Facenet512');""",
                (app_user_id, f"faces/{filename}", psycopg2.Binary(json.dumps(embedding).encode('utf-8')))
            )
            saved_images += 1

        conn.commit()
        return jsonify({
            "status": "success",
            "message": f"บันทึกใบหน้า {saved_images} รูปเรียบร้อย"
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.route('/api/staff', methods=['POST'])
@manager_required
def add_staff():
    data = request.json or {}

    full_name = (data.get('full_name') or '').strip()
    position = (data.get('position') or 'Staff').strip()
    daily_wage = data.get('daily_wage', 0)
    pin_code = data.get('pin_code') or '1234'
    face_images = data.get('face_images', [])

    if not full_name:
        return jsonify({
            "status": "error",
            "message": "กรุณากรอกชื่อพนักงาน"
        }), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # =====================================================
        # 1. สร้างรหัสพนักงาน
        # =====================================================
        # Do not use COUNT(*) here: staff may have been disabled/deleted while
        # their app_users account remains, which can reuse an existing username
        # (for example S02) and make INSERT ... RETURNING return None.
        cur.execute(
            """SELECT COALESCE(MAX(CAST(SUBSTRING(username FROM 2) AS INTEGER)), 0) AS max_no
               FROM app_users
               WHERE username ~ '^S[0-9]+$';"""
        )
        next_no = cur.fetchone()['max_no'] + 1
        employee_code = f"S{next_no:02d}"

        pin_hash = generate_password_hash(str(pin_code))

        # =====================================================
        # 2. เพิ่มข้อมูลพนักงาน
        # =====================================================
        cur.execute(
            """
            INSERT INTO staff
                (employee_code, full_name, "position", daily_wage, pin_hash)
            VALUES
                (%s, %s, %s, %s, %s)
            RETURNING id, employee_code, full_name, "position", daily_wage;
            """,
            (employee_code, full_name, position, daily_wage, pin_hash)
        )
        new_staff = cur.fetchone()

        # =====================================================
        # 3. สร้าง User Login ให้พนักงาน
        # =====================================================
        cur.execute(
            """
            INSERT INTO app_users
                (username, password_hash, role, staff_id)
            VALUES
                (%s, %s, 'staff', %s)
            ON CONFLICT(username) DO NOTHING
            RETURNING id;
            """,
            (employee_code, pin_hash, new_staff['id'])
        )
        app_user_row = cur.fetchone()
        if app_user_row:
            new_app_user_id = app_user_row['id']
        else:
            # เผื่อกรณี ON CONFLICT ชนจนไม่ได้ id กลับมา ให้ query แยกอีกที
            cur.execute("SELECT id FROM app_users WHERE staff_id = %s;", (new_staff['id'],))
            existing_app_user = cur.fetchone()
            if not existing_app_user:
                raise RuntimeError("ไม่สามารถสร้างบัญชีล็อกอินสำหรับพนักงานได้")
            new_app_user_id = existing_app_user['id']

        # =====================================================
        # 4. หากมีการส่งรูปใบหน้ามา ให้บันทึกทั้งหมด (สูงสุด 5 รูป)
        #    และสร้าง Face Embedding ของแต่ละรูป
        # =====================================================
        saved_images = 0

        if face_images:
            faces_dir = os.path.join(app.static_folder, 'faces')
            os.makedirs(faces_dir, exist_ok=True)

            for index, face_image_b64 in enumerate(face_images[:5]):
                # ตัด prefix data:image/jpeg;base64,
                if ',' in face_image_b64:
                    face_image_b64 = face_image_b64.split(',')[1]

                filename = f"staff_{new_staff['id']}_{index}_{uuid.uuid4().hex[:6]}.jpg"
                filepath = os.path.join(faces_dir, filename)

                # decode และบันทึกรูป
                image_data = base64.b64decode(face_image_b64)
                with open(filepath, "wb") as fh:
                    fh.write(image_data)

                # สร้าง Face Embedding
                embedding = create_face_embedding(filepath)

                if embedding is None:
                    conn.rollback()
                    return jsonify({
                        "status": "error",
                        "message": f"ไม่พบใบหน้าในรูปที่ {index + 1}"
                    }), 400

                # บันทึก face profile
                cur.execute(
                    """
                    INSERT INTO face_profiles
                        (staff_id, app_user_id, image_path, embedding, model_name)
                    VALUES
                        (%s, %s, %s, %s, 'Facenet512');
                    """,
                    (new_staff["id"], new_app_user_id, f"faces/{filename}", psycopg2.Binary(json.dumps(embedding).encode('utf-8')))
                )

                saved_images += 1

        conn.commit()

        new_staff['pin_code'] = str(pin_code)

        message = f"บันทึกรูปใบหน้า {saved_images} รูปเรียบร้อย" if saved_images else "เพิ่มพนักงานสำเร็จ"

        return jsonify({
            "status": "success",
            "message": message,
            "staff": new_staff
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    # Run HTTP locally by default.  The previous hard-coded certificate files
    # were not part of the project, so Flask failed before the app could start.
    app.run(
        host=os.environ.get('HOST', '0.0.0.0'),
        port=int(os.environ.get('PORT', '5000')),
        debug=os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes'},
    )
