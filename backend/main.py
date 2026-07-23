import os
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
    "database": os.environ.get("DB_NAME", "v_carcare"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", "postgres"),
    "port": os.environ.get("DB_PORT", "5432"),
}


def get_db_connection():
    """เปิดการเชื่อมต่อกับฐานข้อมูล"""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


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


@app.route('/pos')
@login_required
def pos():
    return render_template('pos.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/track')
def track():
    # หน้าลูกค้าติดตามสถานะ ไม่ต้องล็อกอิน (เข้าผ่านลิงก์/QR ได้เลย)
    return render_template('track.html')


@app.route('/staff')
@manager_required
def staff_page():
    return render_template('staff.html', session_role=session.get("role"), session_name=session.get("display_name"))


@app.route('/finance')
@manager_required
def finance():
    return render_template('finance.html', session_role=session.get("role"), session_name=session.get("display_name"))


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
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        base_query = """
            SELECT o.id AS order_id, o.queue_no, o.status, o.total_amount, o.payment_method,
                   o.created_at, o.started_at, o.completed_at,
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
        if status_filter:
            cur.execute(base_query + " WHERE o.status = %s ORDER BY o.created_at ASC;", (status_filter,))
        else:
            cur.execute(base_query + " WHERE o.status != 'completed' AND o.status != 'cancelled' ORDER BY o.created_at ASC;")
        orders = cur.fetchall()
        return jsonify(orders), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
@login_required
def update_order_status(order_id):
    new_status = (request.json or {}).get('status')
    valid_statuses = ('pending', 'in_progress', 'drying', 'ready', 'completed', 'cancelled')
    if new_status not in valid_statuses:
        return jsonify({"status": "error", "message": "สถานะไม่ถูกต้อง"}), 400

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
        return jsonify(services), 200
    finally:
        cur.close()
        conn.close()


@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    data = request.json or {}
    license_plate = (data.get('license_plate') or '').strip()
    province = (data.get('province') or '').strip() or None
    phone = (data.get('phone') or '').strip()
    line_id = (data.get('line_id') or '').strip() or None
    category = data.get('category', 'car')
    size_code = data.get('size', 'M')
    selected_services = data.get('services', [])
    payment_method = data.get('payment_method', 'cash')

    if not license_plate or not phone:
        return jsonify({"status": "error", "message": "กรุณากรอกทะเบียนรถและเบอร์โทรศัพท์"}), 400
    if not selected_services:
        return jsonify({"status": "error", "message": "กรุณาเลือกบริการอย่างน้อย 1 รายการ"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. ลูกค้า (อ้างอิงจากเบอร์โทร ซึ่งเป็น unique)
        cur.execute("SELECT id FROM customers WHERE phone = %s;", (phone,))
        customer = cur.fetchone()
        if not customer:
            cur.execute(
                "INSERT INTO customers (phone, line_id) VALUES (%s, %s) RETURNING id;",
                (phone, line_id)
            )
            customer_id = cur.fetchone()['id']
        else:
            customer_id = customer['id']
            if line_id:
                cur.execute("UPDATE customers SET line_id = %s, updated_at = NOW() WHERE id = %s;", (line_id, customer_id))

        # 2. รถ (อ้างอิงจากทะเบียน+จังหวัด ซึ่งเป็น unique ร่วมกัน)
        cur.execute(
            "SELECT id FROM vehicles WHERE license_plate = %s AND province IS NOT DISTINCT FROM %s;",
            (license_plate, province)
        )
        vehicle = cur.fetchone()
        if not vehicle:
            cur.execute(
                "INSERT INTO vehicles (customer_id, license_plate, province, category, size_code) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                (customer_id, license_plate, province, category, size_code)
            )
            vehicle_id = cur.fetchone()['id']
        else:
            vehicle_id = vehicle['id']
            cur.execute("UPDATE vehicles SET category = %s, size_code = %s WHERE id = %s;", (category, size_code, vehicle_id))

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
            """INSERT INTO service_orders (queue_no, customer_id, vehicle_id, status, payment_method, total_amount, created_by)
               VALUES (%s, %s, %s, 'pending', %s, %s, %s) RETURNING id;""",
            (queue_no, customer_id, vehicle_id, payment_method, total_amount, created_by_ref)
        )
        order_id = cur.fetchone()['id']

        # 6. รายการบริการ
        for item in verified_services:
            cur.execute(
                """INSERT INTO service_order_items (order_id, service_id, service_code, service_name, price)
                   VALUES (%s, %s, %s, %s, %s);""",
                (order_id, item['service_id'], item['code'], item['name'], item['price'])
            )

        # 7. การชำระเงิน + บัญชีรายรับ
        cur.execute(
            "INSERT INTO payments (order_id, method, amount, status) VALUES (%s, %s, %s, 'paid');",
            (order_id, payment_method, total_amount)
        )
        cur.execute(
            """INSERT INTO finance_transactions (order_id, transaction_type, category, description, amount)
               VALUES (%s, 'income', 'service', %s, %s);""",
            (order_id, f"รายรับจากคิว {queue_no} (ทะเบียน {license_plate})", total_amount)
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


@app.route('/api/staff', methods=['POST'])
@manager_required
def add_staff():
    data = request.json or {}
    full_name = (data.get('full_name') or '').strip()
    position = (data.get('position') or 'Staff').strip()
    daily_wage = data.get('daily_wage', 0)
    pin_code = data.get('pin_code') or '1234'

    if not full_name:
        return jsonify({"status": "error", "message": "กรุณากรอกชื่อพนักงาน"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS c FROM staff;")
        next_no = cur.fetchone()['c'] + 1
        employee_code = f"S{next_no:02d}"
        pin_hash = generate_password_hash(str(pin_code))

        cur.execute(
            """INSERT INTO staff (employee_code, full_name, "position", daily_wage, pin_hash)
               VALUES (%s, %s, %s, %s, %s) RETURNING id, employee_code, full_name, "position", daily_wage;""",
            (employee_code, full_name, position, daily_wage, pin_hash)
        )
        new_staff = cur.fetchone()

        # สร้างบัญชีล็อกอิน (username = employee_code) ให้พนักงานใหม่โดยอัตโนมัติ
        cur.execute(
            "INSERT INTO app_users (username, password_hash, role, staff_id) VALUES (%s, %s, 'staff', %s) ON CONFLICT (username) DO NOTHING;",
            (employee_code, pin_hash, new_staff['id'])
        )

        conn.commit()
        new_staff['pin_code'] = str(pin_code)
        return jsonify({"status": "success", "staff": new_staff}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
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
def _period_to_range(period, start_str, end_str):
    today = date.today()
    if period == 'day':
        return today, today
    if period == 'month':
        return today.replace(day=1), today
    if period == 'year':
        return today.replace(month=1, day=1), today
    if period == 'custom' and start_str and end_str:
        return datetime.strptime(start_str, "%Y-%m-%d").date(), datetime.strptime(end_str, "%Y-%m-%d").date()
    return today, today


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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
