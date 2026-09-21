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
from .line_bot import is_line_configured, send_line_notification
from .line_link import register_line_link_routes

# ===================================================================
# ⚙️ 0. ตั้งค่า Environment Variable & Path
# ===================================================================
# ===================================================================
# 📂 1. โฟลเดอร์ frontend (templates / static) & Flask App Setup
# ===================================================================
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("SECRET_KEY")
CORS(app, supports_credentials=True)


@app.get('/health')
def health_check():
    """Lightweight readiness endpoint; it must not require a database query."""
    return jsonify({'status': 'ok'}), 200


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
from .orders import register_order_routes

register_order_routes(app, {
    'get_db_connection': get_db_connection,
    '_ensure_central_fund_tables': _ensure_central_fund_tables,
    '_ensure_finance_payment_method_column': _ensure_finance_payment_method_column,
    '_ensure_promotions_table': _ensure_promotions_table,
    '_record_central_fund_movement': _record_central_fund_movement,
    'ensure_thai_service_names': ensure_thai_service_names,
    'send_line_notification': send_line_notification,
})

register_line_link_routes(app, get_db_connection)

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


from .staff import register_attendance_bonus_routes

register_attendance_bonus_routes(app, get_db_connection, _record_central_fund_movement)

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


from .finance import register_finance_routes

register_finance_routes(app, {
    'get_db_connection': get_db_connection,
    '_ensure_finance_payment_method_column': _ensure_finance_payment_method_column,
    '_period_to_range': _period_to_range,
    '_record_central_fund_movement': _record_central_fund_movement,
    '_ensure_central_fund_tables': _ensure_central_fund_tables,
    '_get_auto_open_settings': _get_auto_open_settings,
    '_get_cash_float_amount': _get_cash_float_amount,
    '_ensure_daily_cash_float': _ensure_daily_cash_float,
    '_ensure_security_settings': _ensure_security_settings,
    '_central_fund_has_active_cash_float': _central_fund_has_active_cash_float,
    '_run_scheduled_shop_open': _run_scheduled_shop_open,
})

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


__all__ = ['app']
