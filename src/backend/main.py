import base64
import binascii
from datetime import date, datetime, time, timedelta
from functools import wraps
import json
import os
import time as clock
import threading
import uuid

import cv2
from insightface.app import FaceAnalysis
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_cors import CORS
import numpy as np
import psycopg2
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import login_required, manager_required
from .config import (
    CENTRAL_FUND_OPENING_FLOAT, FACE_MATCH_DISTANCE_THRESHOLD, FACE_MODEL_NAME,
    ROOT_DIR, STAFF_WITHDRAWAL_MAX_PER_REQUEST, STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK,
    STATIC_DIR, TEMPLATE_DIR, THAI_SERVICE_NAMES, WORK_START_TIME,
)
from .database import (
    get_db_connection as _get_db_connection,
    get_local_network_ip as _get_local_network_ip,
)

# ===================================================================
# ⚙️ 0. ตั้งค่า Environment Variable & Path
# ===================================================================
# ===================================================================
# 📂 1. โฟลเดอร์ frontend (templates / static) & Flask App Setup
# ===================================================================
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("SECRET_KEY")
CORS(app, supports_credentials=True)


@app.after_request
def disable_browser_cache_for_face_updates(response):
    """Ensure scan terminals receive the current role-locking JavaScript."""
    if response.mimetype == 'text/html' or request.path.endswith('.js'):
        response.headers['Cache-Control'] = 'no-store, max-age=0, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# ===================================================================
# ⚙️ 2. การเชื่อมต่อฐานข้อมูล PostgreSQL & Config
# ===================================================================
FACE_MATCH_DISTANCE_THRESHOLD = 0.30  # ยิ่งน้อยยิ่งเข้มงวด (cosine distance ของ Facenet512)
FACE_MATCH_DISTANCE_THRESHOLD = 0.42
_face_app = None
_auto_shop_open_thread = None
_auto_shop_open_thread_lock = threading.Lock()
_attendance_start_cache = {"loaded_at": 0, "time": WORK_START_TIME}

THAI_SERVICE_NAMES = {
    'wash': 'ล้างภายนอก',
    'washVacuum': 'ล้างภายนอกและดูดฝุ่น',
    'fullFlush': 'ล้าง ดูดฝุ่น และฉีดล้างช่วงล่าง',
    'engineWash': 'ล้าง ดูดฝุ่น และล้างห้องเครื่อง',
    'fullEngine': 'ล้างครบชุด พร้อมล้างช่วงล่างและห้องเครื่อง',
    'ozone': 'อบโอโซนกำจัดกลิ่น',
    'wax': 'เคลือบแว็กซ์',
}


def get_db_connection():
    """เปิดการเชื่อมต่อกับฐานข้อมูล"""
    return _get_db_connection()


def get_local_network_ip():
    return _get_local_network_ip()


def configured_attendance_start_time():
    """Use the configured shop-opening time as the attendance cut-off."""
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
        # Attendance remains usable when settings have not been initialized.
        pass
    _attendance_start_cache['loaded_at'] = clock.monotonic()
    return _attendance_start_cache['time']


# ===================================================================
# 🧠 Helper Functions & Face Recognition Utilities
# ===================================================================
def calculate_attendance_status(check_in_at):
    """
    คำนวณสถานะการเข้างานจากวันที่และเวลาของ check_in_at

    <= 08:00 = on_time
    > 08:00 = late
    """

    if not check_in_at:
        return 'absent', 0

    # ถ้ามี timezone ให้ถอดออกก่อน
    if getattr(check_in_at, 'tzinfo', None):
        check_in_at = check_in_at.replace(tzinfo=None)

    work_date = check_in_at.date()
    check_in_time = check_in_at.time()

    start_dt = datetime.combine(
        work_date,
        configured_attendance_start_time()
    )

    # มาตรงเวลา
    if check_in_time <= configured_attendance_start_time():
        return 'on_time', 0

    late_minutes = int(
        (check_in_at - start_dt).total_seconds() // 60
    )

    return 'late', max(late_minutes, 1)


from .attendance import calculate_attendance_status, configured_attendance_start_time


def create_face_embedding(image_path):
    """สร้าง Face Embedding จากรูปภาพ"""
    global _face_app
    if _face_app is None:
        _face_app = FaceAnalysis(
            name='buffalo_sc', root=os.path.join(ROOT_DIR, '.models'),
            providers=['CPUExecutionProvider'],
        )
        _face_app.prepare(ctx_id=-1, det_size=(320, 320))
    image = cv2.imread(image_path)
    if image is None:
        return None
    faces = _face_app.get(image)
    if not faces:
        return None
    face = max(faces, key=lambda item: (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]))
    return face.normed_embedding.tolist()


def warm_up_face_model():
    """Load the recognition model before the first user presses Scan."""
    try:
        create_face_embedding(os.path.join(app.static_folder, 'faces', 'warmup.jpg'))
        print("[face] InsightFace model ready")
    except Exception as exc:
        # Do not prevent the web server from starting; represent() will retry.
        print(f"[face] model warm-up failed: {exc}")


def ensure_thai_service_names(cur):
    """อัปเดตชื่อบริการมาตรฐานเดิมให้เป็นภาษาไทย โดยไม่แก้รหัสบริการ"""
    for code, name in THAI_SERVICE_NAMES.items():
        cur.execute("UPDATE services SET name = %s WHERE code = %s AND name <> %s;", (name, code, name))


def _load_embedding(raw):
    if raw is None:
        return None

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


FACE_PROFILE_CACHE_SECONDS = 60
_face_profile_cache = {"loaded_at": 0, "rows": []}


def find_matching_app_user(captured_embedding, role=None):
    """เทียบ embedding ที่ถ่ายมากับทุกโปรไฟล์ใบหน้าที่บันทึกไว้"""
    if clock.monotonic() - _face_profile_cache["loaded_at"] >= FACE_PROFILE_CACHE_SECONDS:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT fp.app_user_id, fp.embedding, au.role
                FROM face_profiles fp
                JOIN app_users au ON au.id = fp.app_user_id
                WHERE fp.app_user_id IS NOT NULL
                  AND au.is_active = true
                  AND fp.model_name = %s;
            """, (FACE_MODEL_NAME,))
            _face_profile_cache["rows"] = cur.fetchall()
            _face_profile_cache["loaded_at"] = clock.monotonic()
        finally:
            cur.close()
            conn.close()

    rows = _face_profile_cache["rows"]

    best_user_id = None
    best_distance = None

    for row in rows:
        if role and row["role"] != role:
            continue
        stored_embedding = _load_embedding(row['embedding'])
        distance = _cosine_distance(captured_embedding, stored_embedding)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_user_id = row['app_user_id']

    if best_distance is not None and best_distance <= FACE_MATCH_DISTANCE_THRESHOLD:
        return best_user_id
    return None


from .face_recognition import create_face_embedding, warm_up_face_model


def _iso_week_bounds(iso_year, iso_week):
    """คืนค่า (วันจันทร์, วันอาทิตย์) ของสัปดาห์ ISO ที่กำหนด"""
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = date.fromisocalendar(iso_year, iso_week, 7)
    return monday, sunday


def _period_to_range(period, start_str, end_str):
    """แปลงค่า period ให้เป็นช่วงวันที่ (start_date, end_date)"""
    today = date.today()

    if period == 'day':
        return today, today

    if period == 'week':
        start = today - timedelta(days=today.weekday())
        return start, today

    if period == 'month':
        start = today.replace(day=1)
        return start, today

    if period == 'year':
        start = today.replace(month=1, day=1)
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

    return today, today


def _ensure_promotions_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promotions (
            id BIGSERIAL PRIMARY KEY, 
            name VARCHAR(160) NOT NULL,
            description TEXT, 
            discount_type VARCHAR(10) NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
            discount_value NUMERIC(10,2) NOT NULL CHECK (discount_value >= 0),
            starts_at DATE, 
            ends_at DATE, 
            is_active BOOLEAN NOT NULL DEFAULT true, 
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def _ensure_central_fund_tables(cur):
    """Create the cash-pool tables for both fresh and already deployed databases."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS central_fund (
            id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            balance NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
            cash_float_balance NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (cash_float_balance >= 0),
            opening_balance NUMERIC(12,2),
            shop_opened_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        INSERT INTO central_fund (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

        CREATE TABLE IF NOT EXISTS central_fund_transactions (
            id BIGSERIAL PRIMARY KEY,
            movement_type VARCHAR(30) NOT NULL CHECK (movement_type IN
                ('income', 'expense', 'opening_float', 'closing_float', 'adjustment', 'fund_received')),
            amount NUMERIC(12,2) NOT NULL CHECK (amount <> 0),
            balance_before NUMERIC(12,2),
            balance_after NUMERIC(12,2) NOT NULL CHECK (balance_after >= 0),
            description TEXT NOT NULL,
            finance_transaction_id BIGINT REFERENCES finance_transactions(id) ON DELETE SET NULL,
            created_by BIGINT REFERENCES app_users(id) ON DELETE SET NULL,
            opening_date DATE,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE central_fund_transactions
            DROP CONSTRAINT IF EXISTS central_fund_transactions_movement_type_check;
        ALTER TABLE central_fund_transactions
            ADD CONSTRAINT central_fund_transactions_movement_type_check
            CHECK (movement_type IN
                ('income', 'expense', 'opening_float', 'closing_float', 'adjustment', 'fund_received'));
        ALTER TABLE central_fund
            ADD COLUMN IF NOT EXISTS shop_opened_at TIMESTAMPTZ;
        ALTER TABLE central_fund
            ADD COLUMN IF NOT EXISTS opening_balance NUMERIC(12,2);
        ALTER TABLE central_fund_transactions
            ADD COLUMN IF NOT EXISTS balance_before NUMERIC(12,2);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_central_fund_transaction_finance
            ON central_fund_transactions(finance_transaction_id)
            WHERE finance_transaction_id IS NOT NULL;
        DROP INDEX IF EXISTS uq_central_fund_opening_per_day;
        CREATE INDEX IF NOT EXISTS idx_central_fund_transactions_occurred_at
            ON central_fund_transactions(occurred_at DESC);
    """)


def _record_central_fund_movement(cur, amount, movement_type, description,
                                  finance_transaction_id=None, created_by=None):
    """Apply one signed movement and keep the balance and ledger in sync."""
    _ensure_central_fund_tables(cur)
    cur.execute("SELECT balance FROM central_fund WHERE id = 1 FOR UPDATE;")
    fund = cur.fetchone()
    new_balance = float(fund['balance']) + float(amount)
    if new_balance < -0.00001:
        raise ValueError('ยอดเงินกองกลางไม่เพียงพอ')
    new_balance = max(new_balance, 0)
    cur.execute(
        "UPDATE central_fund SET balance = %s, updated_at = NOW() WHERE id = 1;",
        (new_balance,)
    )
    cur.execute(
        """INSERT INTO central_fund_transactions
               (movement_type, amount, balance_before, balance_after, description, finance_transaction_id, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *;""",
        (movement_type, amount, float(fund['balance']), new_balance, description, finance_transaction_id, created_by)
    )
    return cur.fetchone()


def _ensure_finance_payment_method_column(cur):
    """Store the receipt channel with each finance entry for history filtering."""
    cur.execute("""
        ALTER TABLE finance_transactions
        ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20);
        CREATE INDEX IF NOT EXISTS idx_finance_transactions_payment_method
        ON finance_transactions(payment_method);
    """)


def _client_ip():
    """Return the direct client IP; do not trust forwarded headers unless a proxy is configured."""
    return request.remote_addr or ''


def _is_primary_face_terminal(primary_ip):
    """Treat localhost and this computer's LAN address as one scan terminal."""
    client_ip = _client_ip()
    if not primary_ip or primary_ip == client_ip:
        return True
    loopback_ips = {'127.0.0.1', '::1'}
    return client_ip in loopback_ips and primary_ip in loopback_ips | {get_local_network_ip()}


def _ensure_security_settings(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            setting_key VARCHAR(80) PRIMARY KEY,
            setting_value TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def _primary_face_scan_ip(cur):
    _ensure_security_settings(cur)
    cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'primary_face_scan_ip';")
    row = cur.fetchone()
    return row['setting_value'] if row else None


def _face_image_to_user_id(face_image_b64, prefix):
    if not face_image_b64:
        raise ValueError('ไม่พบรูปภาพใบหน้า')
    raw_image = face_image_b64.split(',', 1)[-1]
    tmp_dir = os.path.join(app.static_folder, 'faces', 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"{prefix}_{uuid.uuid4().hex}.jpg")
    try:
        with open(tmp_path, 'wb') as fh:
            fh.write(base64.b64decode(raw_image))
        embedding = create_face_embedding(tmp_path)
        if embedding is None:
            raise ValueError('ไม่พบใบหน้าในภาพ')
        matched_user_id = find_matching_app_user(embedding)
        if matched_user_id is None:
            raise ValueError('ไม่พบใบหน้านี้ในระบบ')
        return matched_user_id
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _ensure_daily_cash_float(cur, force=False):
    """Move the fixed change float once per calendar day, when funds are available."""
    _ensure_central_fund_tables(cur)
    cash_float_amount = _get_cash_float_amount(cur)
    if not force:
        cur.execute("""SELECT 1 FROM central_fund_transactions
           WHERE movement_type = 'opening_float' AND opening_date = CURRENT_DATE;""")
        if cur.fetchone():
            return False
    cur.execute("SELECT balance, cash_float_balance FROM central_fund WHERE id = 1 FOR UPDATE;")
    fund = cur.fetchone()
    # Never issue another float while the shop is still open.  ``force`` only
    # permits another opening after a same-day close; it must not bypass this
    # active-float safeguard.
    if float(fund['cash_float_balance'] or 0) > 0 or float(fund['balance']) < cash_float_amount:
        return False
    cur.execute(
        """UPDATE central_fund
           SET balance = balance - %s, cash_float_balance = cash_float_balance + %s,
               opening_balance = balance,
               shop_opened_at = CASE WHEN %s THEN NOW() ELSE shop_opened_at END, updated_at = NOW()
           WHERE id = 1;""",
        (cash_float_amount, cash_float_amount, force)
    )
    cur.execute(
        """INSERT INTO central_fund_transactions
               (movement_type, amount, balance_after, description, opening_date)
           VALUES ('opening_float', %s, %s, %s, CURRENT_DATE);""",
        (-cash_float_amount, float(fund['balance']) - cash_float_amount,
         'นำเงินออกเป็นเงินทอนประจำวัน')
    )
    return True


def _get_auto_open_settings(cur):
    _ensure_security_settings(cur)
    cur.execute("SELECT setting_key, setting_value FROM system_settings WHERE setting_key IN ('auto_shop_open_enabled', 'auto_shop_open_time');")
    values = {row['setting_key']: row['setting_value'] for row in cur.fetchall()}
    return {
        'enabled': values.get('auto_shop_open_enabled', 'false').lower() == 'true',
        'time': values.get('auto_shop_open_time', '08:00'),
    }


def _get_cash_float_amount(cur):
    """Return the manager-configured float, retaining 3,000 as the default."""
    _ensure_security_settings(cur)
    cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'cash_float_amount';")
    row = cur.fetchone()
    try:
        amount = float(row['setting_value']) if row else CENTRAL_FUND_OPENING_FLOAT
        return amount if amount > 0 else CENTRAL_FUND_OPENING_FLOAT
    except (TypeError, ValueError):
        return CENTRAL_FUND_OPENING_FLOAT


def _get_withdrawal_max_requests(cur):
    _ensure_security_settings(cur)
    cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'staff_withdrawal_max_requests_per_week';")
    row = cur.fetchone()
    try:
        value = int(row['setting_value']) if row else STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK
        return value if value > 0 else STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK
    except (TypeError, ValueError):
        return STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK


def _central_fund_has_active_cash_float(cur):
    cur.execute("SELECT cash_float_balance FROM central_fund WHERE id = 1;")
    fund = cur.fetchone()
    return bool(fund and float(fund['cash_float_balance'] or 0) > 0)


def _run_scheduled_shop_open():
    """Open once per day after the configured time while this server is running."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        settings = _get_auto_open_settings(cur)
        if not settings['enabled'] or datetime.now().strftime('%H:%M') < settings['time']:
            return False
        today = date.today().isoformat()
        cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'auto_shop_open_last_date';")
        last_run = cur.fetchone()
        if last_run and last_run['setting_value'] == today:
            return False
        opened = _ensure_daily_cash_float(cur, force=True)
        # A shop already opened manually at the scheduled moment is also a
        # successful scheduled state.  Mark it so closing later today does not
        # cause the scheduler to open a second float.
        if opened or _central_fund_has_active_cash_float(cur):
            cur.execute("""INSERT INTO system_settings (setting_key, setting_value, updated_at)
                           VALUES ('auto_shop_open_last_date', %s, NOW())
                           ON CONFLICT (setting_key) DO UPDATE
                           SET setting_value = EXCLUDED.setting_value, updated_at = NOW();""", (today,))
        conn.commit()
        return opened
    except Exception as exc:
        conn.rollback()
        app.logger.warning('Scheduled shop open failed: %s', exc)
        return False
    finally:
        cur.close()
        conn.close()


def _auto_shop_open_loop():
    while True:
        _run_scheduled_shop_open()
        clock.sleep(5)


def start_auto_shop_open_scheduler():
    """Start one in-process scheduler regardless of how Flask is launched."""
    global _auto_shop_open_thread
    with _auto_shop_open_thread_lock:
        if _auto_shop_open_thread and _auto_shop_open_thread.is_alive():
            return
        _auto_shop_open_thread = threading.Thread(
            target=_auto_shop_open_loop, name='auto-shop-open', daemon=True
        )
        _auto_shop_open_thread.start()


@app.before_request
def ensure_auto_shop_open_scheduler():
    # This also covers deployments started with ``flask run`` or a WSGI server,
    # which do not execute the module's ``__main__`` block below.
    start_auto_shop_open_scheduler()


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
from .auth import login_required, manager_required


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

                    cur.execute(
                        """
                        SELECT *
                        FROM staff_attendance
                        WHERE staff_id = %s
                          AND work_date = CURRENT_DATE;
                        """,
                        (staff['id'],)
                    )

                    existing_attendance = cur.fetchone()

                    if not existing_attendance:
                        check_in_time = datetime.now()

                        attendance_status, late_minutes = calculate_attendance_status(
                            check_in_time
                        )

                        cur.execute(
                            """
                            INSERT INTO staff_attendance
                                (
                                    staff_id,
                                    work_date,
                                    check_in_at,
                                    method,
                                    status,
                                    late_minutes
                                )
                            VALUES
                                (%s, CURRENT_DATE, %s, 'login', %s, %s);
                            """,
                            (
                                staff['id'],
                                check_in_time,
                                attendance_status,
                                late_minutes
                            )
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
    session.clear()
    return redirect(url_for('login'))


@app.route('/pos')
@login_required
def pos():
    return render_template('pos.html', session_role=session.get('role'), session_name=session.get('display_name'))


@app.route('/register')
@login_required
def register():
    return render_template(
        'register.html', session_role=session.get('role'), session_name=session.get('display_name'),
        edit_vehicle_id=request.args.get('vehicle_id', type=int)
    )


@app.route('/history')
@login_required
def history():
    return render_template('history.html', session_role=session.get('role'), session_name=session.get('display_name'))


@app.route('/track')
def track():
    return render_template('track.html')


@app.route('/face-checkin')
@app.route('/face-checkin/')
@app.route('/face-checkin/<string:required_role>')
@app.route('/face-checkin/<string:required_role>/')
def face_checkin(required_role=None):
    mode = request.args.get('mode', 'login')
    if required_role and session.get('role') and required_role != session['role']:
        return "ไม่อนุญาตให้สแกนข้ามสิทธิ์", 403
    # The scan screen's selected role must win over an existing browser
    # session, so one enrolled face can be used independently for Staff or
    # Manager without being routed to the previous session's role.
    scan_role = required_role or request.args.get('role') or session.get('role')
    if scan_role not in ('manager', 'staff'):
        scan_role = None
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        primary_ip = _primary_face_scan_ip(cur)
    finally:
        cur.close()
        conn.close()
    if not _is_primary_face_terminal(primary_ip) and mode != 'change-ip':
        return "ไม่อนุญาตให้ใช้งานสแกนหน้าจากเครื่องนี้", 403
    return render_template(
        'face_checkin.html',
        face_mode=mode,
        scan_role=scan_role,
        scan_allowed=_is_primary_face_terminal(primary_ip),
    )


@app.route('/api/face-scan-availability')
def face_scan_availability():
    """Expose only whether the current device is the configured scan terminal."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        primary_ip = _primary_face_scan_ip(cur)
        current_role = session.get('role')
        return jsonify({
            "available": bool(primary_ip and _is_primary_face_terminal(primary_ip)),
            "current_role": current_role,
            "can_enroll_manager_face": current_role == 'manager',
        })
    finally:
        cur.close()
        conn.close()


@app.route('/staff')
@manager_required
def staff_page():
    return render_template('staff.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/finance')
@manager_required
def finance():
    return render_template('finance.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/expense-management')
@manager_required
def expense_management():
    return render_template(
        'transaction_management.html',
        transaction_type='expense',
        page_title='จัดการรายจ่าย',
        session_role=session.get("role"),
        session_name=session.get("display_name"),
    )


@app.route('/income-management')
@manager_required
def income_management():
    return render_template(
        'transaction_management.html',
        transaction_type='income',
        page_title='จัดการรายรับ',
        session_role=session.get("role"),
        session_name=session.get("display_name"),
    )


@app.route('/service-management')
@manager_required
def service_management():
    return render_template(
        'service_management.html',
        session_role=session.get("role"),
        session_name=session.get("display_name"),
    )


@app.route('/staff-advances')
@manager_required
def staff_advances_page():
    return render_template('staff_advances.html', session_role=session.get("role"), session_name=session.get("display_name"))


# ===================================================================
# 🔑 5. ROUTE ตั้งค่าเริ่มต้นระบบ & Authentication APIs
# ===================================================================
@app.route('/api/password-reset/face/verify', methods=['POST'])
def verify_password_reset_face():
    data = request.json or {}
    role = data.get('role')
    if role not in ('manager', 'staff'):
        return jsonify({"status": "error", "message": "กรุณาเลือกสิทธิ์ที่จะเปลี่ยนรหัสผ่าน"}), 400
    try:
        matched_id = _face_image_to_user_id(data.get('face_image'), 'reset')
        conn = get_db_connection()
        cur = conn.cursor()
        if role == 'manager':
            cur.execute("SELECT id, username AS display_name, username FROM app_users WHERE id = %s AND role = 'manager' AND is_active = true;", (matched_id,))
            target = cur.fetchone()
        else:
            cur.execute("""SELECT au.id, au.staff_id, s.full_name AS display_name, au.username
                           FROM app_users au JOIN staff s ON s.id = au.staff_id
                           WHERE au.id = %s AND au.role = 'staff' AND au.is_active = true;""", (matched_id,))
            target = cur.fetchone()
        if not target:
            return jsonify({"status": "error", "message": "ใบหน้าไม่อยู่ในสิทธิ์ที่เลือก"}), 403
        session['password_reset_user_id'] = target['id']
        session['password_reset_role'] = role
        return jsonify({"status": "success", "display_name": target['display_name'] or target['username']}), 200
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        app.logger.exception('Password-reset face verification failed')
        return jsonify({"status": "error", "message": "ไม่สามารถตรวจสอบใบหน้าได้ กรุณาลองใหม่"}), 500
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()


@app.route('/api/password-reset/face', methods=['POST'])
def reset_password_with_face():
    data = request.json or {}
    password = data.get('new_password') or ''
    user_id = session.get('password_reset_user_id')
    role = session.get('password_reset_role')
    if not user_id or role not in ('manager', 'staff'):
        return jsonify({"status": "error", "message": "กรุณาสแกนใบหน้าเพื่อยืนยันตัวตนก่อน"}), 403
    if len(password) < 4:
        return jsonify({"status": "error", "message": "รหัสใหม่ต้องมีอย่างน้อย 4 ตัว"}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if role == 'manager':
            cur.execute("UPDATE app_users SET password_hash = %s WHERE id = %s AND role = 'manager' AND is_active = true;", (generate_password_hash(password), user_id))
        else:
            cur.execute("""UPDATE staff SET pin_hash = %s WHERE id = (
                             SELECT staff_id FROM app_users WHERE id = %s AND role = 'staff' AND is_active = true
                         );""", (generate_password_hash(password), user_id))
        if cur.rowcount != 1:
            conn.rollback()
            return jsonify({"status": "error", "message": "ไม่พบบัญชีที่ยืนยันไว้"}), 404
        conn.commit()
        session.pop('password_reset_user_id', None)
        session.pop('password_reset_role', None)
        return jsonify({"status": "success", "message": "เปลี่ยนรหัสผ่านเรียบร้อยแล้ว", "redirect": "/login"})
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        cur.close(); conn.close()


@app.route('/api/security/change-primary-face-ip', methods=['POST'])
def change_primary_face_ip():
    try:
        matched_user_id = _face_image_to_user_id((request.json or {}).get('face_image'), 'ip_change')
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT role FROM app_users WHERE id = %s AND is_active = true;", (matched_user_id,))
        user = cur.fetchone()
        if not user or user['role'] != 'manager':
            return jsonify({"status": "error", "message": "ต้องยืนยันด้วยใบหน้าผู้จัดการเท่านั้น"}), 403
        _ensure_security_settings(cur)
        cur.execute("""INSERT INTO system_settings (setting_key, setting_value, updated_at)
                       VALUES ('primary_face_scan_ip', %s, NOW())
                       ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW();""", (_client_ip(),))
        conn.commit()
        return jsonify({"status": "success", "ip": _client_ip()}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/face-login', methods=['POST'])
@app.route('/api/face-login/', methods=['POST'])
@app.route('/api/face-login/<string:required_role>', methods=['POST'])
@app.route('/api/face-login/<string:required_role>/', methods=['POST'])
def face_login(required_role=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        primary_ip = _primary_face_scan_ip(cur)
    finally:
        cur.close()
        conn.close()
    if not _is_primary_face_terminal(primary_ip):
        return jsonify({"status": "error", "message": "สแกนหน้าได้เฉพาะเครื่องหลักที่กำหนดไว้"}), 403
    data = request.json or {}
    face_image_b64 = data.get('face_image')
    expected_role = required_role or data.get('expected_role') or session.get('role') or 'staff'
    if required_role and data.get('expected_role') not in (None, required_role):
        return jsonify({"status": "error", "message": "หน้าสแกนและสิทธิ์ที่ส่งมาไม่ตรงกัน"}), 403
    if expected_role not in ('manager', 'staff'):
        return jsonify({"status": "error", "message": "กรุณาเลือกหน้าสแกน Manager หรือ Staff"}), 400

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

        matched_user_id = find_matching_app_user(embedding, expected_role)
        if matched_user_id is None:
            return jsonify({"status": "error", "message": "ไม่พบใบหน้านี้ในระบบ กรุณาติดต่อผู้จัดการ"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM app_users WHERE id = %s AND is_active = true;", (matched_user_id,))
            user = cur.fetchone()
            if not user:
                return jsonify({"status": "error", "message": "บัญชีนี้ถูกปิดใช้งาน"}), 403

            if user['role'] != expected_role:
                return jsonify({"status": "error", "message": "ใบหน้านี้ไม่ใช่สิทธิ์ของหน้าสแกนที่เลือก"}), 403

            if user['role'] == 'manager':
                session['user_id'] = user['id']
                session['role'] = 'manager'
                session['staff_id'] = None
                session['display_name'] = user['username']
                redirect_url = url_for('index')

            else:
                cur.execute("SELECT full_name FROM staff WHERE id = %s AND is_active = true;", (user['staff_id'],))
                staff = cur.fetchone()
                if not staff:
                    return jsonify({"status": "error", "message": "ไม่พบข้อมูลพนักงาน หรือถูกปิดใช้งาน"}), 403

                session['user_id'] = f"staff-{user['staff_id']}"
                session['role'] = 'staff'
                session['staff_id'] = user['staff_id']
                session['display_name'] = staff['full_name']

                cur.execute(
                    """
                    SELECT *
                    FROM staff_attendance
                    WHERE staff_id = %s
                      AND work_date = CURRENT_DATE;
                    """,
                    (user['staff_id'],)
                )

                attendance = cur.fetchone()

                # ถ้าวันนี้ยังไม่มีรายการ -> สร้างเช็กอิน
                if not attendance:
                    check_in_time = datetime.now()
                    earliest_scan = datetime.combine(
                        check_in_time.date(), configured_attendance_start_time()
                    ) - timedelta(hours=1)
                    if check_in_time < earliest_scan:
                        return jsonify({
                            "status": "error",
                            "message": f"สามารถสแกนเข้างานได้ตั้งแต่ {earliest_scan.strftime('%H:%M')} น."
                        }), 400

                    status, late_minutes = calculate_attendance_status(
                        check_in_time
                    )

                    cur.execute(
                        """
                        INSERT INTO staff_attendance
                            (
                                staff_id,
                                work_date,
                                check_in_at,
                                method,
                                status,
                                late_minutes
                            )
                        VALUES
                            (
                                %s,
                                CURRENT_DATE,
                                %s,
                                'face',
                                %s,
                                %s
                            )
                        RETURNING *;
                        """,
                        (
                            user['staff_id'],
                            check_in_time,
                            status,
                            late_minutes
                        )
                    )

                    attendance = cur.fetchone()
                    conn.commit()

                else:
                    # มีรายการวันนี้แล้ว
                    # ห้ามสร้างแถวใหม่และห้ามเปลี่ยนเวลาเช็กอิน
                    pass

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


@app.route('/api/staff/face-checkout', methods=['POST'])
@login_required
def staff_face_checkout():
    if session.get('role') != 'staff':
        return jsonify({"status": "error", "message": "เฉพาะพนักงานเท่านั้นที่ต้องสแกนหน้าเพื่อเช็กเอาต์"}), 403

    data = request.json or {}
    face_image_b64 = data.get('face_image')

    if not face_image_b64:
        return jsonify({"status": "error", "message": "ไม่พบรูปภาพ"}), 400

    if ',' in face_image_b64:
        face_image_b64 = face_image_b64.split(',', 1)[1]

    tmp_dir = os.path.join(app.static_folder, 'faces', 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"checkout_{uuid.uuid4().hex}.jpg")

    try:
        try:
            image_data = base64.b64decode(face_image_b64)
        except Exception:
            return jsonify({"status": "error", "message": "รูปภาพไม่ถูกต้อง"}), 400

        with open(tmp_path, 'wb') as fh:
            fh.write(image_data)

        embedding = create_face_embedding(tmp_path)
        if embedding is None:
            return jsonify({"status": "error", "message": "ไม่พบใบหน้าในภาพ กรุณาสแกนใหม่"}), 400

        matched_user_id = find_matching_app_user(embedding)
        if matched_user_id is None:
            return jsonify({"status": "error", "message": "ไม่พบใบหน้านี้ในระบบ"}), 401

        cur_session_staff_id = session.get('staff_id')
        if not cur_session_staff_id:
            return jsonify({"status": "error", "message": "ไม่พบข้อมูลพนักงานใน Session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT id, role, staff_id FROM app_users WHERE id = %s AND is_active = true;",
                (matched_user_id,)
            )
            user = cur.fetchone()

            if not user:
                return jsonify({"status": "error", "message": "ไม่พบบัญชีผู้ใช้งาน"}), 401

            if user['role'] != 'staff':
                return jsonify({"status": "error", "message": "ใบหน้านี้ไม่ใช่พนักงาน"}), 403

            if user['staff_id'] != cur_session_staff_id:
                return jsonify({"status": "error", "message": "ใบหน้าไม่ตรงกับพนักงานที่กำลังเข้าสู่ระบบ"}), 403

            cur.execute(
                """UPDATE staff_attendance
                   SET check_out_at = NOW()
                   WHERE staff_id = %s AND work_date = CURRENT_DATE AND check_out_at IS NULL
                   RETURNING id, staff_id, work_date, check_in_at, check_out_at, method;""",
                (cur_session_staff_id,)
            )
            attendance = cur.fetchone()

            if not attendance:
                return jsonify({"status": "error", "message": "ไม่พบรายการเช็กอินวันนี้ หรือเช็กเอาต์ไปแล้ว"}), 400

            conn.commit()
            session.clear()

            return jsonify({
                "status": "success",
                "message": "เช็กเอาต์สำเร็จ",
                "redirect": url_for('login'),
                "attendance": attendance
            }), 200

        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    except Exception as e:
        print("[staff_face_checkout] error:", e)
        return jsonify({"status": "error", "message": "เกิดข้อผิดพลาดในการเช็กเอาต์"}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


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
                   COALESCE(o.damage_note, 'ไม่มีอะไรเสียหาย') AS damage_note,
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
        # ``history`` is a display filter used by the vehicle-history page,
        # not a value stored in service_orders.status. Include completed,
        # picked-up, and cancelled orders; cancellation only changes status,
        # so the original record remains visible and auditable.
        if status_filter == 'history' and history_date:
            cur.execute(
                base_query + " WHERE o.status IN ('completed', 'picked_up', 'cancelled') AND DATE(o.updated_at) = %s ORDER BY o.updated_at DESC;",
                (history_date,)
            )
        elif status_filter == 'history':
            cur.execute(
                base_query + " WHERE o.status IN ('completed', 'picked_up', 'cancelled') ORDER BY o.updated_at DESC;"
            )
        elif status_filter and history_date:
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
        _ensure_finance_payment_method_column(cur)
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
            """INSERT INTO finance_transactions (order_id, transaction_type, category, description, amount, payment_method)
               VALUES (%s, 'income', 'service', %s, %s, NULL) RETURNING *;""",
            (order_id, f"รายรับจากคิว {order['queue_no']} (ทะเบียน {order['license_plate']})", order['total_amount'])
        )
        finance_transaction = cur.fetchone()
        _ensure_finance_payment_method_column(cur)
        cur.execute(
            "UPDATE finance_transactions SET payment_method = %s WHERE id = %s;",
            (payment_method, finance_transaction['id'])
        )
        if payment_method == 'cash':
            # Cash received belongs in the till used for change, not the central fund yet.
            _ensure_central_fund_tables(cur)
            cur.execute(
                """UPDATE central_fund
                   SET cash_float_balance = cash_float_balance + %s, updated_at = NOW()
                   WHERE id = 1;""",
                (order['total_amount'],)
            )
        else:
            # QR/transfer and other non-cash payments are available in the fund immediately.
            _record_central_fund_movement(
                cur, float(order['total_amount']), 'income', finance_transaction['description'],
                finance_transaction_id=finance_transaction['id'], created_by=session.get('user_id')
            )
        damage_note = (request.json or {}).get('damage_note')
        damage_note = damage_note.strip() if isinstance(damage_note, str) else None
        cur.execute(
            """UPDATE service_orders
               SET status = 'picked_up', payment_method = %s, damage_note = %s, updated_at = NOW()
               WHERE id = %s;""",
            (payment_method, damage_note or None, order_id)
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
    category = request.args.get('category', 'car')
    size_code = request.args.get('size', 'M')

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


@app.route('/api/manage/services', methods=['GET', 'POST'])
@manager_required
def manage_services():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        ensure_thai_service_names(cur)
        if request.method == 'GET':
            cur.execute("""
                SELECT s.id, s.code, s.name, s.category, s.estimated_minutes, s.is_active,
                       sp.id AS price_id, sp.vehicle_category, sp.size_code, sp.price
                FROM services s 
                LEFT JOIN service_prices sp ON sp.service_id = s.id
                ORDER BY s.id, sp.vehicle_category, sp.size_code;
            """)
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

        cur.execute(
            "INSERT INTO services (code, name, category, estimated_minutes) VALUES (%s, %s, %s, %s) RETURNING id;",
            (code, name, category, minutes)
        )
        service_id = cur.fetchone()['id']

        for price in prices:
            if price.get('vehicle_category') not in ('car', 'bike') or not price.get('size_code'):
                raise ValueError('ข้อมูลราคามีรูปแบบไม่ถูกต้อง')
            cur.execute(
                "INSERT INTO service_prices (service_id, vehicle_category, size_code, price) VALUES (%s, %s, %s, %s);",
                (service_id, price['vehicle_category'], price['size_code'], price.get('price', 0))
            )
        conn.commit()
        return jsonify({'status': 'success', 'id': service_id}), 201
    except (ValueError, psycopg2.Error) as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cur.close()
        conn.close()


@app.route('/api/manage/services/<int:service_id>', methods=['PUT', 'DELETE'])
@manager_required
def manage_service(service_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if request.method == 'DELETE':
            cur.execute("UPDATE services SET is_active = false WHERE id = %s RETURNING id;", (service_id,))
        else:
            data = request.json or {}
            cur.execute(
                "UPDATE services SET name = %s, estimated_minutes = %s, is_active = %s WHERE id = %s RETURNING id;",
                ((data.get('name') or '').strip(), data.get('estimated_minutes', 30), bool(data.get('is_active', True)), service_id)
            )
        if not cur.fetchone():
            return jsonify({'message': 'ไม่พบบริการ'}), 404
        conn.commit()
        return jsonify({'status': 'success'})
    finally:
        cur.close()
        conn.close()


@app.route('/api/manage/service-prices/<int:price_id>', methods=['PUT'])
@manager_required
def update_service_price(price_id):
    price = (request.json or {}).get('price')
    try:
        price = float(price)
    except (TypeError, ValueError):
        return jsonify({'message': 'ราคาไม่ถูกต้อง'}), 400

    if price < 0:
        return jsonify({'message': 'ราคาต้องไม่น้อยกว่า 0'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE service_prices SET price = %s WHERE id = %s RETURNING id;", (price, price_id))
        if not cur.fetchone():
            return jsonify({'message': 'ไม่พบราคา'}), 404
        conn.commit()
        return jsonify({'status': 'success'})
    finally:
        cur.close()
        conn.close()


@app.route('/api/manage/promotions', methods=['GET', 'POST'])
@manager_required
def manage_promotions():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_promotions_table(cur)
        if request.method == 'GET':
            cur.execute("SELECT * FROM promotions ORDER BY is_active DESC, created_at DESC;")
            conn.commit()
            return jsonify(cur.fetchall())

        data = request.json or {}
        if not (data.get('name') or '').strip() or data.get('discount_type') not in ('percent', 'fixed'):
            return jsonify({'message': 'กรุณากรอกชื่อและรูปแบบส่วนลด'}), 400

        cur.execute(
            """INSERT INTO promotions (name, description, discount_type, discount_value, starts_at, ends_at) 
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;""",
            (data['name'].strip(), data.get('description'), data['discount_type'], data.get('discount_value', 0), data.get('starts_at') or None, data.get('ends_at') or None)
        )
        promotion_id = cur.fetchone()['id']
        conn.commit()
        return jsonify({'status': 'success', 'id': promotion_id}), 201
    finally:
        cur.close()
        conn.close()


@app.route('/api/manage/promotions/<int:promotion_id>', methods=['DELETE'])
@manager_required
def delete_promotion(promotion_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_promotions_table(cur)
        cur.execute("DELETE FROM promotions WHERE id = %s RETURNING id;", (promotion_id,))
        if not cur.fetchone():
            return jsonify({'message': 'ไม่พบโปรโมชัน'}), 404
        conn.commit()
        return jsonify({'status': 'success'})
    finally:
        cur.close()
        conn.close()


@app.route('/api/vehicles/lookup', methods=['GET'])
@login_required
def lookup_vehicle():
    license_plate = (request.args.get('license_plate') or '').strip()
    province = (request.args.get('province') or '').strip()
    if not license_plate:
        return jsonify({"status": "error", "message": "กรุณากรอกทะเบียนรถ"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        province_filter_sql = ''
        params = [license_plate]
        if province:
            province_filter_sql = " AND UPPER(COALESCE(v.province, '')) = UPPER(%s)"
            params.append(province)

        cur.execute(
            """SELECT v.id AS vehicle_id, v.license_plate, v.province, v.category, v.size_code,
                      c.phone, c.line_id
               FROM vehicles v JOIN customers c ON c.id = v.customer_id
               WHERE UPPER(REPLACE(v.license_plate, ' ', '')) = UPPER(REPLACE(%s, ' ', ''))
            """ + province_filter_sql + """
               ORDER BY v.id DESC LIMIT 2;""",
            tuple(params)
        )
        vehicles = cur.fetchall()
        if not vehicles:
            return jsonify({"status": "error", "message": "ไม่พบทะเบียนนี้ กรุณาลงทะเบียนรถก่อน"}), 404
        if not province and len(vehicles) > 1:
            return jsonify({
                "status": "error",
                "message": "พบทะเบียนนี้มากกว่า 1 จังหวัด กรุณาระบุจังหวัดก่อนค้นหา"
            }), 409
        return jsonify({"status": "success", "data": vehicles[0]}), 200
    finally:
        cur.close()
        conn.close()


def _ensure_vehicle_edit_history(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_edit_history (
            id BIGSERIAL PRIMARY KEY,
            vehicle_id BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            customer_id BIGINT REFERENCES customers(id) ON DELETE SET NULL,
            before_data JSONB NOT NULL,
            after_data JSONB NOT NULL,
            edited_by BIGINT REFERENCES app_users(id) ON DELETE SET NULL,
            edited_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_edit_history_vehicle
            ON vehicle_edit_history(vehicle_id, edited_at DESC);
    """)


@app.route('/api/vehicles/<int:vehicle_id>', methods=['GET'])
@login_required
def get_vehicle(vehicle_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT v.id AS vehicle_id, v.customer_id, v.license_plate, v.province,
                             v.category, v.size_code, c.phone, c.line_id
                      FROM vehicles v JOIN customers c ON c.id = v.customer_id
                      WHERE v.id = %s;""", (vehicle_id,))
        vehicle = cur.fetchone()
        if not vehicle:
            return jsonify({"status": "error", "message": "ไม่พบข้อมูลรถ"}), 404
        return jsonify({"status": "success", "data": vehicle}), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/vehicles/<int:vehicle_id>', methods=['PUT'])
@login_required
def update_vehicle(vehicle_id):
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
        _ensure_vehicle_edit_history(cur)
        cur.execute("""SELECT v.id AS vehicle_id, v.customer_id, v.license_plate, v.province,
                             v.category, v.size_code, c.phone, c.line_id
                      FROM vehicles v JOIN customers c ON c.id = v.customer_id
                      WHERE v.id = %s FOR UPDATE;""", (vehicle_id,))
        before = cur.fetchone()
        if not before:
            conn.rollback()
            return jsonify({"status": "error", "message": "ไม่พบข้อมูลรถ"}), 404

        cur.execute("""SELECT id FROM vehicles
                       WHERE license_plate = %s AND province IS NOT DISTINCT FROM %s AND id <> %s;""",
                    (license_plate, province, vehicle_id))
        if cur.fetchone():
            conn.rollback()
            return jsonify({"status": "error", "message": "ทะเบียนและจังหวัดนี้ถูกลงทะเบียนไว้แล้ว"}), 409

        cur.execute("SELECT id FROM customers WHERE phone = %s FOR UPDATE;", (phone,))
        matched_customer = cur.fetchone()
        if matched_customer:
            customer_id = matched_customer['id']
            cur.execute("UPDATE customers SET line_id = %s, updated_at = NOW() WHERE id = %s;", (line_id, customer_id))
        else:
            cur.execute("INSERT INTO customers (phone, line_id) VALUES (%s, %s) RETURNING id;", (phone, line_id))
            customer_id = cur.fetchone()['id']

        cur.execute("""UPDATE vehicles SET customer_id = %s, license_plate = %s, province = %s,
                                            category = %s, size_code = %s
                       WHERE id = %s RETURNING id AS vehicle_id, customer_id, license_plate, province, category, size_code;""",
                    (customer_id, license_plate, province, category, size_code, vehicle_id))
        after = cur.fetchone()
        after['phone'] = phone
        after['line_id'] = line_id
        cur.execute("""INSERT INTO vehicle_edit_history
                       (vehicle_id, customer_id, before_data, after_data, edited_by)
                       VALUES (%s, %s, %s::jsonb, %s::jsonb, %s);""",
                    (vehicle_id, customer_id, json.dumps(dict(before), default=str),
                     json.dumps(dict(after), default=str), session.get('user_id')))
        conn.commit()
        return jsonify({"status": "success", "data": after, "message": "บันทึกการแก้ไขข้อมูลรถเรียบร้อยแล้ว"}), 200
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500
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

        customer_id = vehicle['customer_id']
        category = vehicle['category']
        size_code = vehicle['size_code']

        queue_prefix = datetime.now().strftime("Q%Y%m%d-")
        cur.execute("SELECT COUNT(*) + 1 AS next_q FROM service_orders WHERE queue_no LIKE %s;", (f"{queue_prefix}%",))
        next_q = cur.fetchone()['next_q']
        queue_no = f"{queue_prefix}{next_q:04d}"

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

    # ==========================================================
    # รับวันที่จากหน้า staff.html
    # ==========================================================
    selected_date_str = (
        request.args.get('date')
        or date.today().strftime('%Y-%m-%d')
    )

    try:
        selected_date = datetime.strptime(
            selected_date_str,
            '%Y-%m-%d'
        ).date()
    except ValueError:
        return jsonify({
            "status": "error",
            "message": "รูปแบบวันที่ไม่ถูกต้อง ต้องเป็น YYYY-MM-DD"
        }), 400

    show_all = request.args.get('all') == 'true'

    conn = get_db_connection()
    cur = conn.cursor()

    try:

        # ==========================================================
        # ดึงพนักงานทั้งหมด + attendance ของวันที่เลือก
        # ==========================================================
        query = """
            SELECT
                s.id,
                s.employee_code,
                s.full_name,
                s."position",
                s.daily_wage,
                s.is_active,

                sa.id AS attendance_id,
                sa.work_date,
                sa.check_in_at,
                sa.check_out_at,
                sa.method,
                sa.status,
                sa.late_minutes

            FROM staff s

            LEFT JOIN staff_attendance sa
                ON s.id = sa.staff_id
               AND sa.work_date = %s
        """

        params = [selected_date]

        if not show_all:
            query += """
                WHERE s.is_active = true
            """

        query += """
            ORDER BY s.full_name ASC;
        """

        print(
            f"[get_staff] selected_date = {selected_date}"
        )

        cur.execute(
            query,
            params
        )

        rows = cur.fetchall()

        print(
            f"[get_staff] rows = {len(rows)}"
        )

        staff_list = []

        # ==========================================================
        # แปลงข้อมูลให้ JSON ได้
        # ==========================================================
        for row in rows:

            staff = dict(row)

            # ------------------------------------------------------
            # Decimal -> float
            # ------------------------------------------------------
            if staff.get('daily_wage') is not None:
                staff['daily_wage'] = float(
                    staff['daily_wage']
                )

            # ------------------------------------------------------
            # date -> string
            # ------------------------------------------------------
            if staff.get('work_date') is not None:
                staff['work_date'] = (
                    staff['work_date'].isoformat()
                )

            # ------------------------------------------------------
            # datetime -> string
            # ------------------------------------------------------
            if staff.get('check_in_at') is not None:
                staff['check_in_at'] = (
                    staff['check_in_at'].isoformat()
                )

            if staff.get('check_out_at') is not None:
                staff['check_out_at'] = (
                    staff['check_out_at'].isoformat()
                )

            # ------------------------------------------------------
            # ถ้ามีเวลาเข้างาน แต่ status ไม่มี
            # ให้คำนวณใหม่
            # ------------------------------------------------------
            original_check_in = row.get('check_in_at')

            if original_check_in:

                if not row.get('status'):
                    attendance_status, late_minutes = (
                        calculate_attendance_status(
                            original_check_in
                        )
                    )

                    staff['status'] = attendance_status
                    staff['late_minutes'] = late_minutes

            else:
                staff['status'] = 'absent'
                staff['late_minutes'] = 0

            # ------------------------------------------------------
            # กัน None
            # ------------------------------------------------------
            if staff.get('late_minutes') is None:
                staff['late_minutes'] = 0

            staff_list.append(staff)

        print(
            f"[get_staff] return {len(staff_list)} staff"
        )

        return jsonify(staff_list), 200

    except Exception as e:

        print("======================================")
        print("[get_staff] ERROR")
        print("TYPE:", type(e).__name__)
        print("MESSAGE:", str(e))
        print("======================================")

        return jsonify({
            "status": "error",
            "message": "ไม่สามารถโหลดข้อมูลพนักงานได้",
            "error": str(e)
        }), 500

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
        return jsonify({"status": "error", "message": "กรุณากรอกชื่อพนักงาน"}), 400
    if not face_images:
        return jsonify({"status": "error", "message": "กรุณาถ่ายรูปใบหน้าอย่างน้อย 1 รูป"}), 400

    # Validate the face before creating any staff/account rows.  One clear
    # InsightFace embedding is sufficient and avoids making enrollment wait for
    # five identical CPU inferences.
    prepared_faces = []
    tmp_dir = os.path.join(app.static_folder, 'faces', 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        face_image_b64 = face_images[0].split(',', 1)[-1]
        tmp_path = os.path.join(tmp_dir, f"staff_enroll_{uuid.uuid4().hex}.jpg")
        with open(tmp_path, 'wb') as fh:
            fh.write(base64.b64decode(face_image_b64))
        embedding = create_face_embedding(tmp_path)
        if embedding is None:
            raise ValueError("ไม่พบใบหน้าในรูป กรุณาถ่ายใหม่ให้เห็นใบหน้าชัดเจน")
        prepared_faces.append((tmp_path, embedding))
    except (ValueError, TypeError, binascii.Error) as exc:
        for tmp_path, _ in prepared_faces:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return jsonify({"status": "error", "message": str(exc) or "รูปใบหน้าไม่ถูกต้อง"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """SELECT COALESCE(MAX(CAST(SUBSTRING(username FROM 2) AS INTEGER)), 0) AS max_no
               FROM app_users
               WHERE username ~ '^S[0-9]+$';"""
        )
        next_no = cur.fetchone()['max_no'] + 1
        employee_code = f"S{next_no:02d}"

        pin_hash = generate_password_hash(str(pin_code))

        cur.execute(
            """INSERT INTO staff (employee_code, full_name, "position", daily_wage, pin_hash)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id, employee_code, full_name, "position", daily_wage;""",
            (employee_code, full_name, position, daily_wage, pin_hash)
        )
        new_staff = cur.fetchone()

        cur.execute(
            """INSERT INTO app_users (username, password_hash, role, staff_id)
               VALUES (%s, %s, 'staff', %s)
               ON CONFLICT(username) DO NOTHING
               RETURNING id;""",
            (employee_code, pin_hash, new_staff['id'])
        )
        app_user_row = cur.fetchone()
        if app_user_row:
            new_app_user_id = app_user_row['id']
        else:
            cur.execute("SELECT id FROM app_users WHERE staff_id = %s;", (new_staff['id'],))
            existing_app_user = cur.fetchone()
            if not existing_app_user:
                raise RuntimeError("ไม่สามารถสร้างบัญชีล็อกอินสำหรับพนักงานได้")
            new_app_user_id = existing_app_user['id']

        faces_dir = os.path.join(app.static_folder, 'faces')
        saved_images = 0
        for index, (tmp_path, embedding) in enumerate(prepared_faces):
            filename = f"staff_{new_staff['id']}_{index}_{uuid.uuid4().hex[:6]}.jpg"
            filepath = os.path.join(faces_dir, filename)
            os.replace(tmp_path, filepath)
            cur.execute(
                """INSERT INTO face_profiles (staff_id, app_user_id, image_path, embedding, model_name)
                   VALUES (%s, %s, %s, %s, %s);""",
                (new_staff["id"], new_app_user_id, f"faces/{filename}", psycopg2.Binary(json.dumps(embedding).encode('utf-8')), FACE_MODEL_NAME)
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
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()
        for tmp_path, _ in prepared_faces:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


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
@manager_required
def staff_attendance():
    data = request.json or {}

    staff_id = data.get('staff_id')
    action = data.get('action')

    if not staff_id:
        return jsonify({
            "status": "error",
            "message": "ไม่พบรหัสพนักงาน"
        }), 400

    if action not in ('check_in', 'check_out'):
        return jsonify({
            "status": "error",
            "message": "action ต้องเป็น check_in หรือ check_out"
        }), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # =====================================================
        # เช็กอิน
        # =====================================================
        if action == 'check_in':
            cur.execute(
                """
                SELECT *
                FROM staff_attendance
                WHERE staff_id = %s
                  AND work_date = CURRENT_DATE;
                """,
                (staff_id,)
            )

            attendance = cur.fetchone()

            if attendance:
                if attendance['check_in_at'] is not None:
                    return jsonify({
                        "status": "error",
                        "message": "พนักงานคนนี้เช็กอินวันนี้แล้ว",
                        "record": attendance
                    }), 409

                check_in_time = datetime.now()

                attendance_status, late_minutes = calculate_attendance_status(
                    check_in_time
                )

                cur.execute(
                    """
                    UPDATE staff_attendance
                    SET check_in_at = %s,
                        method = 'manual',
                        status = %s,
                        late_minutes = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (
                        check_in_time,
                        attendance_status,
                        late_minutes,
                        attendance['id']
                    )
                )

            else:
                check_in_time = datetime.now()

                attendance_status, late_minutes = calculate_attendance_status(
                    check_in_time
                )

                cur.execute(
                    """
                    INSERT INTO staff_attendance
                        (
                            staff_id,
                            work_date,
                            check_in_at,
                            method,
                            status,
                            late_minutes
                        )
                    VALUES
                        (
                            %s,
                            CURRENT_DATE,
                            %s,
                            'manual',
                            %s,
                            %s
                        )
                    RETURNING *;
                    """,
                    (
                        staff_id,
                        check_in_time,
                        attendance_status,
                        late_minutes
                    )
                )

            attendance = cur.fetchone()
            conn.commit()

            return jsonify({
                "status": "success",
                "message": "เช็กอินสำเร็จ",
                "record": attendance
            }), 200

        # =====================================================
        # เช็กเอาต์
        # =====================================================
        elif action == 'check_out':
            cur.execute(
                """
                SELECT *
                FROM staff_attendance
                WHERE staff_id = %s
                  AND work_date = CURRENT_DATE;
                """,
                (staff_id,)
            )

            attendance = cur.fetchone()

            if not attendance:
                return jsonify({
                    "status": "error",
                    "message": "ยังไม่มีรายการเช็กอินวันนี้"
                }), 400

            if attendance['check_in_at'] is None:
                return jsonify({
                    "status": "error",
                    "message": "พนักงานยังไม่ได้เช็กอิน"
                }), 400

            if attendance['check_out_at'] is not None:
                return jsonify({
                    "status": "error",
                    "message": "พนักงานคนนี้เช็กเอ้าวันนี้แล้ว",
                    "record": attendance
                }), 409

            cur.execute(
                """
                UPDATE staff_attendance
                SET check_out_at = NOW()
                WHERE id = %s
                RETURNING *;
                """,
                (attendance['id'],)
            )

            attendance = cur.fetchone()
            conn.commit()

            return jsonify({
                "status": "success",
                "message": "เช็กเอ้าสำเร็จ",
                "record": attendance
            }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/attendance/leave', methods=['POST'])
@manager_required
def mark_staff_leave():
    data = request.json or {}
    staff_id = data.get('staff_id')
    try:
        work_date = datetime.strptime((data.get('work_date') or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'status': 'error', 'message': 'กรุณาระบุวันที่ให้ถูกต้อง'}), 400
    if not staff_id or work_date > date.today():
        return jsonify({'status': 'error', 'message': 'ไม่สามารถบันทึกการลาในวันอนาคตได้'}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT id FROM staff WHERE id = %s;', (staff_id,))
        if not cur.fetchone():
            return jsonify({'status': 'error', 'message': 'ไม่พบพนักงาน'}), 404
        cur.execute('SELECT id FROM staff_attendance WHERE staff_id = %s AND work_date = %s;', (staff_id, work_date))
        record = cur.fetchone()
        if record:
            cur.execute("""UPDATE staff_attendance
                           SET check_in_at = NULL, check_out_at = NULL, method = 'manual_edit',
                               status = 'leave', late_minutes = 0
                           WHERE id = %s RETURNING *;""", (record['id'],))
        else:
            cur.execute("""INSERT INTO staff_attendance
                           (staff_id, work_date, method, status, late_minutes)
                           VALUES (%s, %s, 'manual_edit', 'leave', 0) RETURNING *;""", (staff_id, work_date))
        attendance = cur.fetchone()
        conn.commit()
        return jsonify({'status': 'success', 'message': 'บันทึกสถานะลาเรียบร้อยแล้ว', 'record': attendance}), 200
    except Exception as exc:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/attendance/backdate', methods=['POST'])
@manager_required
def backdate_staff_attendance():

    data = request.json or {}

    staff_id = data.get('staff_id')
    work_date_str = (data.get('work_date') or '').strip()
    check_in_time_str = (data.get('check_in_time') or '').strip()
    check_out_time_str = (data.get('check_out_time') or '').strip()

    if not staff_id:
        return jsonify({
            "status": "error",
            "message": "ไม่พบรหัสพนักงาน"
        }), 400

    if not work_date_str:
        return jsonify({
            "status": "error",
            "message": "กรุณาระบุวันที่"
        }), 400

    if not check_in_time_str:
        return jsonify({
            "status": "error",
            "message": "กรุณาระบุเวลาเข้างาน"
        }), 400

    try:
        work_date = datetime.strptime(
            work_date_str,
            '%Y-%m-%d'
        ).date()

    except ValueError:
        return jsonify({
            "status": "error",
            "message": "รูปแบบวันที่ไม่ถูกต้อง"
        }), 400

    try:
        check_in_time_only = datetime.strptime(
            check_in_time_str,
            '%H:%M'
        ).time()

    except ValueError:
        return jsonify({
            "status": "error",
            "message": "รูปแบบเวลาไม่ถูกต้อง ต้องเป็น HH:MM"
        }), 400

    if work_date > date.today():
        return jsonify({
            "status": "error",
            "message": "ไม่สามารถบันทึกเวลาในอนาคตได้"
        }), 400

    check_in_datetime = datetime.combine(
        work_date,
        check_in_time_only
    )
    check_out_datetime = None
    if check_out_time_str:
        try:
            check_out_datetime = datetime.combine(
                work_date, datetime.strptime(check_out_time_str, '%H:%M').time()
            )
        except ValueError:
            return jsonify({"status": "error", "message": "รูปแบบเวลาออกงานไม่ถูกต้อง"}), 400
        if check_out_datetime < check_in_datetime:
            return jsonify({"status": "error", "message": "เวลาออกงานต้องไม่ก่อนเวลาเข้างาน"}), 400
    attendance_status, late_minutes = calculate_attendance_status(
        check_in_datetime
    )
    conn = get_db_connection()
    cur = conn.cursor()

    try:

        cur.execute(
            """
            SELECT id, full_name, is_active
            FROM staff
            WHERE id = %s;
            """,
            (staff_id,)
        )

        staff = cur.fetchone()

        if not staff:
            return jsonify({
                "status": "error",
                "message": "ไม่พบพนักงาน"
            }), 404

        attendance_status, late_minutes = (
            calculate_attendance_status(
                check_in_datetime
            )
        )

        cur.execute(
            """
            SELECT *
            FROM staff_attendance
            WHERE staff_id = %s
              AND work_date = %s;
            """,
            (
                staff_id,
                work_date
            )
        )

        attendance = cur.fetchone()

        if attendance:

            if attendance['check_in_at'] is not None:
                return jsonify({
                    "status": "error",
                    "message": "พนักงานคนนี้มีเวลาเข้างานในวันที่เลือกแล้ว",
                    "record": attendance
                }), 409

            cur.execute(
                """
                UPDATE staff_attendance
                SET
                    check_in_at = %s,
                    check_out_at = %s,
                    method = 'manual_backdate',
                    status = %s,
                    late_minutes = %s
                WHERE id = %s
                RETURNING *;
                """,
                (
                    check_in_datetime,
                    check_out_datetime,
                    attendance_status,
                    late_minutes,
                    attendance['id']
                )
            )

        else:

            cur.execute(
                """
                INSERT INTO staff_attendance
                    (
                        staff_id,
                        work_date,
                        check_in_at,
                        check_out_at,
                        method,
                        status,
                        late_minutes
                    )
                VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        'manual_backdate',
                        %s,
                        %s
                    )
                RETURNING *;
                """,
                (
                    staff_id,
                    work_date,
                    check_in_datetime,
                    check_out_datetime,
                    attendance_status,
                    late_minutes
                )
            )

        attendance = cur.fetchone()

        conn.commit()

        if attendance_status == 'on_time':
            message = "บันทึกเข้างานย้อนหลังสำเร็จ — มาตรงเวลา"
        else:
            message = (
                f"บันทึกเข้างานย้อนหลังสำเร็จ — "
                f"มาสาย {late_minutes} นาที"
            )

        return jsonify({
            "status": "success",
            "message": message,
            "record": attendance
        }), 200

    except Exception as e:

        conn.rollback()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/attendance/edit', methods=['POST'])
@manager_required
def edit_staff_attendance():
    data = request.json or {}
    staff_id = data.get('staff_id')
    work_date_str = (data.get('work_date') or '').strip()
    check_in_time_str = (data.get('check_in_time') or '').strip()
    check_out_time_str = (data.get('check_out_time') or '').strip()

    if not staff_id:
        return jsonify({"status": "error", "message": "ไม่พบรหัสพนักงาน"}), 400
    if not work_date_str:
        return jsonify({"status": "error", "message": "กรุณาระบุวันที่"}), 400

    try:
        work_date = datetime.strptime(work_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"status": "error", "message": "รูปแบบวันที่ไม่ถูกต้อง"}), 400

    if work_date > date.today():
        return jsonify({"status": "error", "message": "ไม่สามารถแก้ไขเวลาของวันในอนาคตได้"}), 400

    check_in_datetime = None
    if check_in_time_str:
        try:
            check_in_time_only = datetime.strptime(check_in_time_str, '%H:%M').time()
            check_in_datetime = datetime.combine(work_date, check_in_time_only)
        except ValueError:
            return jsonify({"status": "error", "message": "รูปแบบเวลาเข้างานไม่ถูกต้อง"}), 400

    check_out_datetime = None
    if check_out_time_str:
        try:
            check_out_time_only = datetime.strptime(check_out_time_str, '%H:%M').time()
            check_out_datetime = datetime.combine(work_date, check_out_time_only)
        except ValueError:
            return jsonify({"status": "error", "message": "รูปแบบเวลาออกงานไม่ถูกต้อง"}), 400

    if not check_in_datetime and not check_out_datetime:
        return jsonify({"status": "error", "message": "กรุณาระบุเวลาเข้างานหรือเวลาออกงานอย่างน้อยหนึ่งรายการ"}), 400

    if check_out_datetime and not check_in_datetime:
        return jsonify({"status": "error", "message": "กรุณาระบุเวลาเข้างานก่อนบันทึกเวลาออกงาน"}), 400

    if check_in_datetime and check_out_datetime and check_out_datetime < check_in_datetime:
        return jsonify({"status": "error", "message": "เวลาออกงานต้องไม่ก่อนเวลาเข้างาน"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT id FROM staff WHERE id = %s;", (staff_id,))
        if not cur.fetchone():
            return jsonify({"status": "error", "message": "ไม่พบพนักงาน"}), 404

        attendance_status = 'on_time'
        late_minutes = 0
        if check_in_datetime:
            attendance_status, late_minutes = calculate_attendance_status(check_in_datetime)

        cur.execute(
            """
            SELECT id FROM staff_attendance
            WHERE staff_id = %s AND work_date = %s;
            """,
            (staff_id, work_date)
        )
        attendance = cur.fetchone()

        if attendance:
            cur.execute(
                """
                UPDATE staff_attendance
                SET check_in_at = %s,
                    check_out_at = %s,
                    method = 'manual_edit',
                    status = %s,
                    late_minutes = %s
                WHERE id = %s
                RETURNING *;
                """,
                (check_in_datetime, check_out_datetime, attendance_status, late_minutes, attendance['id'])
            )
        else:
            cur.execute(
                """
                INSERT INTO staff_attendance
                    (staff_id, work_date, check_in_at, check_out_at, method, status, late_minutes)
                VALUES (%s, %s, %s, %s, 'manual_edit', %s, %s)
                RETURNING *;
                """,
                (staff_id, work_date, check_in_datetime, check_out_datetime, attendance_status, late_minutes)
            )

        attendance_record = cur.fetchone()
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "บันทึกเวลาเข้า-ออกงานสำเร็จ",
            "record": attendance_record
        }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ===================================================================
# 🔌 9. API: การเงิน & เบิกเงินพนักงาน (staff_advances.html / finance.html)
# ===================================================================
@app.route('/api/staff/<int:staff_id>/attendance-summary', methods=['GET'])
@manager_required
def staff_attendance_summary(staff_id):
    month = request.args.get('month') or date.today().strftime('%Y-%m')
    try:
        month_start = datetime.strptime(month, '%Y-%m').date().replace(day=1)
    except ValueError:
        return jsonify({'message': 'รูปแบบเดือนต้องเป็น YYYY-MM'}), 400

    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT id, full_name, daily_wage FROM staff WHERE id = %s;', (staff_id,))
        staff_member = cur.fetchone()
        if not staff_member:
            return jsonify({'message': 'ไม่พบพนักงาน'}), 404
        cur.execute('''
            SELECT work_date, check_in_at, check_out_at, status, late_minutes
            FROM staff_attendance
            WHERE staff_id = %s AND work_date >= %s AND work_date < %s
            ORDER BY work_date;
        ''', (staff_id, month_start, month_end))
        records = [dict(row) for row in cur.fetchall()]
        for record in records:
            record['work_date'] = record['work_date'].isoformat()
            for key in ('check_in_at', 'check_out_at'):
                if record[key]:
                    record[key] = record[key].isoformat()
        worked_days = sum(1 for record in records if record['check_in_at'])
        late_days = sum(1 for record in records if record['check_in_at'] and record['status'] == 'late')
        return jsonify({
            'staff': dict(staff_member), 'month': month, 'records': records,
            'worked_days': worked_days, 'late_days': late_days,
            'earned': worked_days * float(staff_member['daily_wage'] or 0),
        }), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff/attendance/history', methods=['GET'])
@manager_required
def staff_attendance_history():
    history_date = request.args.get('date')

    if not history_date:
        history_date = str(date.today())

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT
                s.id,
                s.employee_code,
                s.full_name,
                s."position",
                s.daily_wage,

                sa.id AS attendance_id,
                sa.work_date,
                sa.check_in_at,
                sa.check_out_at,
                sa.method,
                sa.status,
                sa.late_minutes

            FROM staff s

            LEFT JOIN staff_attendance sa
                ON s.id = sa.staff_id
               AND sa.work_date = %s

            WHERE s.is_active = true

            ORDER BY s.full_name;
            """,
            (history_date,)
        )

        records = cur.fetchall()
        return jsonify(records), 200

    finally:
        cur.close()
        conn.close()


@app.route('/api/staff-withdrawals/settings', methods=['GET', 'PUT'])
@manager_required
def staff_withdrawal_settings():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if request.method == 'PUT':
            try:
                max_requests = int((request.json or {}).get('max_requests_per_week'))
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': 'กรุณาระบุจำนวนครั้งเป็นตัวเลข'}), 400
            if not 1 <= max_requests <= 20:
                return jsonify({'status': 'error', 'message': 'จำนวนครั้งต้องอยู่ระหว่าง 1 ถึง 20'}), 400
            _ensure_security_settings(cur)
            cur.execute("""INSERT INTO system_settings (setting_key, setting_value, updated_at)
                           VALUES ('staff_withdrawal_max_requests_per_week', %s, NOW())
                           ON CONFLICT (setting_key) DO UPDATE
                           SET setting_value = EXCLUDED.setting_value, updated_at = NOW();""", (str(max_requests),))
            conn.commit()
        return jsonify({'status': 'success', 'max_requests_per_week': _get_withdrawal_max_requests(cur)})
    except Exception as exc:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        cur.close()
        conn.close()


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


@app.route('/api/staff-withdrawals/eligibility', methods=['GET'])
@manager_required
def staff_withdrawal_eligibility():
    staff_id = request.args.get('staff_id', type=int)
    if not staff_id:
        return jsonify({"status": "error", "message": "กรุณาระบุพนักงาน"}), 400

    iso_year, iso_week, _ = date.today().isocalendar()
    week_start, week_end = _iso_week_bounds(iso_year, iso_week)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        max_requests = _get_withdrawal_max_requests(cur)
        cur.execute("SELECT daily_wage FROM staff WHERE id = %s AND is_active = true;", (staff_id,))
        staff_member = cur.fetchone()
        if not staff_member:
            return jsonify({"status": "error", "message": "ไม่พบพนักงาน"}), 404

        cur.execute(
            """SELECT COUNT(*) AS work_days FROM staff_attendance
               WHERE staff_id = %s AND work_date BETWEEN %s AND %s;""",
            (staff_id, week_start, week_end),
        )
        work_days = cur.fetchone()['work_days']
        earned_income = work_days * float(staff_member['daily_wage'])

        cur.execute(
            """SELECT COUNT(*) AS req_count, COALESCE(SUM(request_amount), 0) AS req_total
               FROM staff_withdrawals
               WHERE staff_id = %s AND withdraw_week = %s AND withdraw_year = %s
                 AND status NOT IN ('rejected', 'cancelled');""",
            (staff_id, iso_week, iso_year),
        )
        requested = cur.fetchone()
        already_requested = float(requested['req_total'])
        return jsonify({
            "work_days": work_days,
            "earned_income": earned_income,
            "already_requested_this_week": already_requested,
            "remaining_income": max(earned_income - already_requested, 0),
            "remaining_requests": max(max_requests - requested['req_count'], 0),
            "max_requests_per_week": max_requests,
            "max_per_request": STAFF_WITHDRAWAL_MAX_PER_REQUEST,
        }), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/staff-withdrawals', methods=['POST'])
@manager_required
def create_staff_withdrawal():
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
        max_requests = _get_withdrawal_max_requests(cur)
        cur.execute("SELECT id, full_name, daily_wage FROM staff WHERE id = %s;", (int(staff_id),))
        staff_member = cur.fetchone()
        if not staff_member:
            return jsonify({"status": "error", "message": "ไม่พบพนักงาน"}), 404

        cur.execute(
            """SELECT COUNT(*) AS work_days FROM staff_attendance
               WHERE staff_id = %s AND work_date BETWEEN %s AND %s;""",
            (int(staff_id), week_start, week_end)
        )
        work_days = cur.fetchone()['work_days']
        earned_income = work_days * float(staff_member['daily_wage'])

        cur.execute(
            """SELECT COUNT(*) AS req_count, COALESCE(SUM(request_amount), 0) AS req_total
               FROM staff_withdrawals
               WHERE staff_id = %s AND withdraw_week = %s AND withdraw_year = %s
               AND status NOT IN ('rejected', 'cancelled');""",
            (int(staff_id), iso_week, iso_year)
        )
        existing = cur.fetchone()

        if existing['req_count'] >= max_requests:
            return jsonify({
                "status": "error",
                "message": f"พนักงานขอเบิกครบ {max_requests} ครั้งในสัปดาห์นี้แล้ว"
            }), 400

        remaining_income = earned_income - float(existing['req_total'])
        if amount > remaining_income:
            return jsonify({
                "status": "error",
                "message": f"รายได้สะสมคงเหลือของสัปดาห์นี้ {remaining_income:.2f} บาท ไม่สามารถขอเบิกเกินได้"
            }), 400

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
    data = request.json or {}
    new_status = data.get('status')
    valid_statuses = ('paid', 'rejected')

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

        if new_status == 'rejected':
            if withdrawal['status'] != 'pending':
                return jsonify({"status": "error", "message": "ปฏิเสธได้เฉพาะรายการที่รอจ่ายเงิน"}), 400
            cur.execute(
                """UPDATE staff_withdrawals
                   SET status = 'rejected', note = COALESCE(%s, note), updated_at = NOW()
                   WHERE id = %s RETURNING *;""",
                (note, withdrawal_id)
            )
            updated = cur.fetchone()

        elif new_status == 'paid':
            if withdrawal['status'] not in ('pending', 'approved'):
                return jsonify({"status": "error", "message": "รายการนี้จ่ายเงินแล้วหรือไม่สามารถจ่ายได้"}), 400

            pay_amount = float(withdrawal['request_amount'])

            cur.execute(
                """UPDATE staff_withdrawals
                   SET status = 'paid', approved_amount = request_amount, paid_at = NOW(),
                       note = COALESCE(%s, note), updated_at = NOW()
                   WHERE id = %s RETURNING *;""",
                (note, withdrawal_id)
            )
            updated = cur.fetchone()

            cur.execute(
                """INSERT INTO finance_transactions
                       (staff_id, transaction_type, category, description, amount)
                   VALUES (%s, 'expense', 'staff_advance', %s, %s) RETURNING *;""",
                (
                    withdrawal['staff_id'],
                    f"เบิกเงินพนักงาน (คำขอ #{withdrawal_id}) {withdrawal['reason'] or ''}".strip(),
                    pay_amount
                )
            )
            finance_transaction = cur.fetchone()
            _record_central_fund_movement(
                cur, -pay_amount, 'expense', finance_transaction['description'],
                finance_transaction_id=finance_transaction['id'], created_by=manager_app_user_id
            )

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
    transaction_period = request.args.get('transaction_period', period)
    transaction_start_str = request.args.get('transaction_start', start_str)
    transaction_end_str = request.args.get('transaction_end', end_str)
    transaction_type = request.args.get('transaction_type', 'all')
    if transaction_type not in ('all', 'income', 'expense', 'central_fund'):
        transaction_type = 'all'
    payment_method = request.args.get('payment_method', 'all')
    if payment_method not in ('all', 'cash', 'transfer'):
        payment_method = 'all'
    try:
        page = max(int(request.args.get('page', 1)), 1)
        page_size = min(max(int(request.args.get('page_size', 25)), 1), 100)
    except (TypeError, ValueError):
        page, page_size = 1, 25
    start_date, end_date = _period_to_range(period, start_str, end_str)
    transaction_start_date, transaction_end_date = _period_to_range(
        transaction_period, transaction_start_str, transaction_end_str
    )

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_finance_payment_method_column(cur)
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS total_income,
                COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
                COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END), 0) AS net_profit
            FROM finance_transactions
            WHERE occurred_at::date BETWEEN %s AND %s
              AND category <> 'central_fund_deposit';
            """,
            (start_date, end_date)
        )
        summary = cur.fetchone()

        if transaction_type == 'central_fund':
            type_filter_sql = " AND f.category = 'central_fund_deposit'"
            type_filter_params = ()
        else:
            # Keep fund deposits out of normal income/expense history.
            type_filter_sql = " AND f.category <> 'central_fund_deposit'"
            type_filter_params = ()
            if transaction_type != 'all':
                type_filter_sql += ' AND f.transaction_type = %s'
                type_filter_params = (transaction_type,)
        payment_filter_sql = '' if payment_method == 'all' else " AND COALESCE(f.payment_method, p.method) = %s"
        payment_filter_params = () if payment_method == 'all' else (payment_method,)

        cur.execute(
            """SELECT COUNT(*) AS total
               FROM finance_transactions f
               LEFT JOIN payments p ON p.order_id = f.order_id AND p.status = 'paid'
               WHERE f.occurred_at::date BETWEEN %s AND %s""" + type_filter_sql + payment_filter_sql + ';',
            (transaction_start_date, transaction_end_date) + type_filter_params + payment_filter_params
        )
        total_transactions = cur.fetchone()['total']
        total_pages = max((total_transactions + page_size - 1) // page_size, 1)
        page = min(page, total_pages)

        cur.execute(
            """SELECT f.id, f.transaction_type, f.category, f.description, f.amount, f.occurred_at,
                      COALESCE(f.payment_method, p.method) AS payment_method
               FROM finance_transactions f
               LEFT JOIN payments p ON p.order_id = f.order_id AND p.status = 'paid'
               WHERE f.occurred_at::date BETWEEN %s AND %s""" + type_filter_sql + payment_filter_sql + """
               ORDER BY f.occurred_at DESC
               LIMIT %s OFFSET %s;""",
            (transaction_start_date, transaction_end_date) + type_filter_params + payment_filter_params + (page_size, (page - 1) * page_size)
        )
        transactions = cur.fetchall()

        return jsonify({
            "period": period,
            "transaction_type": transaction_type,
            "payment_method": payment_method,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "transaction_start_date": str(transaction_start_date),
            "transaction_end_date": str(transaction_end_date),
            "summary": summary,
            "transactions": transactions,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total_transactions,
                "total_pages": total_pages,
            }
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
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return jsonify({"status": "error", "message": "จำนวนเงินต้องมากกว่า 0"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO finance_transactions (transaction_type, category, description, amount)
               VALUES (%s, %s, %s, %s) RETURNING *;""",
            (trans_type, category, description, amount)
        )
        new_trans = cur.fetchone()
        _record_central_fund_movement(
            cur, amount if trans_type == 'income' else -amount,
            trans_type, description or category,
            finance_transaction_id=new_trans['id'], created_by=session.get('user_id')
        )
        conn.commit()
        return jsonify({"status": "success", "transaction": new_trans}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund', methods=['GET'])
@manager_required
def get_central_fund():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_central_fund_tables(cur)
        cur.execute("SELECT balance, cash_float_balance, opening_balance, updated_at FROM central_fund WHERE id = 1;")
        fund = cur.fetchone()
        cur.execute(
            """SELECT COALESCE(SUM(CASE WHEN p.method = 'transfer' THEN p.amount ELSE 0 END), 0) AS transfer_received,
                      COALESCE(SUM(CASE WHEN p.method = 'cash' THEN p.amount ELSE 0 END), 0) AS cash_received
               FROM payments p WHERE p.status = 'paid' AND p.paid_at::date = CURRENT_DATE;"""
        )
        daily_receipts = cur.fetchone()
        # This is a display-only daily summary.  In particular, do not use this
        # aggregate to create another central-fund movement when the shop closes:
        # transfer payments already enter the fund when paid, while cash enters it
        # once through the cash-float closing flow.
        cur.execute(
            """SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS total_income,
                      COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense
               FROM finance_transactions
               WHERE occurred_at::date = CURRENT_DATE
                 AND category <> 'central_fund_deposit';"""
        )
        daily_summary = cur.fetchone()
        cur.execute(
            """SELECT EXISTS(SELECT 1 FROM central_fund_transactions
               WHERE movement_type = 'opening_float' AND opening_date = CURRENT_DATE) AS is_open;"""
        )
        is_open = float(fund['cash_float_balance'] or 0) > 0
        auto_open = _get_auto_open_settings(cur)
        cash_float_amount = _get_cash_float_amount(cur)
        cur.execute(
            """SELECT DISTINCT ON (occurred_at::date) occurred_at::date AS day, balance_after
               FROM central_fund_transactions
               WHERE occurred_at >= CURRENT_DATE - INTERVAL '29 days'
               ORDER BY occurred_at::date, occurred_at DESC;"""
        )
        history = cur.fetchall()
        cur.execute(
            """SELECT id, movement_type, amount, balance_after, description, occurred_at
               FROM central_fund_transactions ORDER BY occurred_at DESC LIMIT 8;"""
        )
        movements = cur.fetchall()
        conn.commit()
        return jsonify({"fund": fund, "history": history, "movements": movements,
                        "daily_receipts": daily_receipts, "is_open": is_open,
                        "daily_summary": daily_summary, "auto_open": auto_open,
                        "cash_float_amount": cash_float_amount}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund/auto-open', methods=['PUT'])
@manager_required
def update_auto_shop_open():
    data = request.json or {}
    enabled = bool(data.get('enabled'))
    open_time = str(data.get('time') or '08:00')
    try:
        datetime.strptime(open_time, '%H:%M')
    except ValueError:
        return jsonify({"status": "error", "message": "เวลาต้องเป็นรูปแบบ HH:MM"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_security_settings(cur)
        for key, value in (('auto_shop_open_enabled', str(enabled).lower()), ('auto_shop_open_time', open_time)):
            cur.execute("""INSERT INTO system_settings (setting_key, setting_value, updated_at)
                           VALUES (%s, %s, NOW())
                           ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW();""", (key, value))
        # A newly saved time is a new schedule.  Do not let a run from an
        # earlier configuration prevent the manager from applying it today.
        cur.execute("""INSERT INTO system_settings (setting_key, setting_value, updated_at)
                       VALUES ('auto_shop_open_last_date', '', NOW())
                       ON CONFLICT (setting_key) DO UPDATE
                       SET setting_value = EXCLUDED.setting_value, updated_at = NOW();""")
        conn.commit()
        # Apply a newly saved schedule immediately.  This makes a time that is
        # already due open the shop now instead of waiting for the next poll.
        opened_now = _run_scheduled_shop_open() if enabled else False
        return jsonify({
            "status": "success",
            "auto_open": {"enabled": enabled, "time": open_time},
            "opened_now": opened_now,
            "is_open": _central_fund_has_active_cash_float(cur),
        })
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund/cash-float-amount', methods=['PUT'])
@manager_required
def update_cash_float_amount():
    try:
        amount = float((request.json or {}).get('amount'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "กรุณาระบุจำนวนเงินทอนที่ถูกต้อง"}), 400
    if not 0 < amount <= 1_000_000:
        return jsonify({"status": "error", "message": "จำนวนเงินทอนต้องมากกว่า 0 และไม่เกิน 1,000,000 บาท"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_security_settings(cur)
        cur.execute("""INSERT INTO system_settings (setting_key, setting_value, updated_at)
                       VALUES ('cash_float_amount', %s, NOW())
                       ON CONFLICT (setting_key) DO UPDATE
                       SET setting_value = EXCLUDED.setting_value, updated_at = NOW();""", (str(amount),))
        conn.commit()
        return jsonify({"status": "success", "cash_float_amount": amount,
                        "message": "บันทึกยอดเงินทอนแล้ว มีผลกับการเปิดร้านครั้งถัดไป"})
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund/history', methods=['GET'])
@manager_required
def get_central_fund_history():
    period = request.args.get('period', 'day')
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    activity = request.args.get('activity', 'all')
    if activity not in ('all', 'adjustment', 'fund_received'):
        activity = 'all'
    start_date, end_date = _period_to_range(period, start_str, end_str)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_central_fund_tables(cur)
        type_sql = '' if activity == 'all' else ' AND movement_type = %s'
        type_params = () if activity == 'all' else (activity,)
        cur.execute(
            """SELECT id, movement_type, amount, balance_before, balance_after, description, occurred_at
               FROM central_fund_transactions
               WHERE movement_type IN ('adjustment', 'fund_received')
                 AND occurred_at::date BETWEEN %s AND %s""" + type_sql + " ORDER BY occurred_at DESC;",
            (start_date, end_date) + type_params
        )
        return jsonify({"transactions": cur.fetchall(), "start_date": str(start_date), "end_date": str(end_date)}), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund/open', methods=['POST'])
@manager_required
def open_cash_float():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_central_fund_tables(cur)
        # A manual re-open is valid after the float has been closed, even on the
        # same day.  Only block an active shop so two floats cannot overlap.
        cur.execute("SELECT cash_float_balance FROM central_fund WHERE id = 1 FOR UPDATE;")
        if float(cur.fetchone()['cash_float_balance'] or 0) > 0:
            return jsonify({"status": "error", "message": "ร้านยังเปิดอยู่ กรุณาปิดร้านหรือฝากเงินทอนก่อน"}), 400
        opened = _ensure_daily_cash_float(cur, force=True)
        if not opened:
            return jsonify({"status": "error", "message": "ยอดกองกลางไม่เพียงพอสำหรับเงินทอน 3,000 บาท"}), 400
        cur.execute("SELECT balance, cash_float_balance, shop_opened_at, updated_at FROM central_fund WHERE id = 1;")
        fund = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "opened": opened, "fund": fund}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund', methods=['PUT'])
@manager_required
def adjust_central_fund():
    data = request.json or {}
    try:
        new_balance = float(data.get('balance'))
    except (TypeError, ValueError):
        new_balance = -1
    if new_balance < 0:
        return jsonify({"status": "error", "message": "ยอดเงินกองกลางต้องเป็น 0 หรือมากกว่า"}), 400

    note = (data.get('note') or 'ปรับยอดเงินกองกลางโดยผู้จัดการ').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_central_fund_tables(cur)
        cur.execute("SELECT balance FROM central_fund WHERE id = 1 FOR UPDATE;")
        current = float(cur.fetchone()['balance'])
        difference = new_balance - current
        if abs(difference) > 0.00001:
            _record_central_fund_movement(
                cur, difference, 'adjustment',
                f"{note} | ยอดเดิม {current:,.2f} บาท → ยอดใหม่ {new_balance:,.2f} บาท",
                created_by=session.get('user_id')
            )
        cur.execute("SELECT balance, cash_float_balance, updated_at FROM central_fund WHERE id = 1;")
        fund = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "fund": fund}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund/deposits', methods=['POST'])
@manager_required
def receive_central_fund():
    data = request.json or {}
    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return jsonify({"status": "error", "message": "จำนวนเงินต้องมากกว่า 0"}), 400

    description = (data.get('description') or 'ได้รับเงินเพิ่มเข้ากองกลาง').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_finance_payment_method_column(cur)
        cur.execute(
            """INSERT INTO finance_transactions (transaction_type, category, description, amount)
               VALUES ('income', 'central_fund_deposit', %s, %s) RETURNING *;""",
            (description, amount)
        )
        finance_transaction = cur.fetchone()
        movement = _record_central_fund_movement(
            cur, amount, 'fund_received', description,
            finance_transaction_id=finance_transaction['id'], created_by=session.get('user_id')
        )
        cur.execute("SELECT balance, cash_float_balance, updated_at FROM central_fund WHERE id = 1;")
        fund = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "fund": fund, "movement": movement}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/central-fund/close-cash-float', methods=['POST'])
@manager_required
def close_cash_float():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _ensure_central_fund_tables(cur)
        cur.execute("SELECT balance, cash_float_balance FROM central_fund WHERE id = 1 FOR UPDATE;")
        fund = cur.fetchone()
        cash_float = float(fund['cash_float_balance'])
        if cash_float <= 0:
            return jsonify({"status": "error", "message": "ไม่มีเงินทอนคงค้างให้ปิดร้าน"}), 400
        new_balance = float(fund['balance']) + cash_float
        cur.execute(
            """UPDATE central_fund
               SET balance = %s, cash_float_balance = 0, updated_at = NOW() WHERE id = 1;""",
            (new_balance,)
        )
        cur.execute(
            """INSERT INTO central_fund_transactions (movement_type, amount, balance_after, description)
               VALUES ('closing_float', %s, %s, 'นำเงินทอนกลับเข้ากองกลางเมื่อปิดร้าน');""",
            (cash_float, new_balance)
        )
        conn.commit()
        return jsonify({"status": "success", "fund": {"balance": new_balance, "cash_float_balance": 0}}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/manager/face-enroll', methods=['POST'])
@manager_required
def manager_face_enroll():
    data = request.json or {}
    face_images = data.get('face_images', [])

    if not face_images:
        return jsonify({"status": "error", "message": "กรุณาถ่ายรูปใบหน้าอย่างน้อย 1 รูป"}), 400

    app_user_id = session.get('user_id')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM face_profiles WHERE app_user_id = %s;", (app_user_id,))

        faces_dir = os.path.join(app.static_folder, 'faces')
        os.makedirs(faces_dir, exist_ok=True)

        saved_images = 0
        # The browser sends one enrollment image.  Keep the server-side limit at
        # one as well so a crafted request cannot make camera enrollment slow.
        for index, face_image_b64 in enumerate(face_images[:1]):
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
                   VALUES (%s, %s, %s, %s);""",
                (app_user_id, f"faces/{filename}", psycopg2.Binary(json.dumps(embedding).encode('utf-8')), FACE_MODEL_NAME)
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


# ===================================================================
# 🚀 10. Main Execution Block
# ===================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    local_ip = get_local_network_ip()
    warm_up_face_model(app.static_folder)
    start_auto_shop_open_scheduler()
    # Browsers only expose cameras to secure contexts.  Use an adhoc HTTPS
    # certificate by default so LAN clients can scan via the machine IP.
    use_https = os.environ.get('FLASK_HTTPS', '1').lower() not in {'0', 'false', 'no'}
    cert_path = os.path.join(ROOT_DIR, '.certs', 'vcarcare-cert.pem')
    key_path = os.path.join(ROOT_DIR, '.certs', 'vcarcare-key.pem')
    ssl_context = (cert_path, key_path) if use_https and os.path.exists(cert_path) and os.path.exists(key_path) else ('adhoc' if use_https else None)
    scheme = 'https' if use_https else 'http'
    print('\nV CarCare is ready:')
    print(f'- Local computer: {scheme}://127.0.0.1:{port}')
    print(f'- Mobile / same Wi-Fi: {scheme}://{local_ip}:{port}')
    print('  If the browser shows a certificate warning, choose Advanced > Continue.\n')
    app.run(
        host='0.0.0.0',
        port=port,
        ssl_context=ssl_context,
        debug=os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes'},
        use_reloader=False,
    )
