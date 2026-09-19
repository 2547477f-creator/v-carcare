"""ฟังก์ชันและ API ที่เกี่ยวกับการจัดการพนักงาน"""

from datetime import date, datetime, timedelta

import psycopg2
from flask import jsonify, request, session

from .auth import manager_required


def register_attendance_bonus_routes(app, get_db_connection, record_fund_movement):
    """ลงทะเบียน API เบี้ยขยันโดยไม่ให้โมดูลพนักงาน import main กลับ"""

    def ensure_attendance_bonus_tables(cur):
        cur.execute('''
            CREATE TABLE IF NOT EXISTS attendance_bonus_payouts (
                id BIGSERIAL PRIMARY KEY,
                staff_id BIGINT NOT NULL REFERENCES staff(id),
                bonus_month DATE NOT NULL,
                amount NUMERIC(10,2) NOT NULL CHECK (amount > 0),
                finance_transaction_id BIGINT UNIQUE REFERENCES finance_transactions(id),
                approved_by BIGINT REFERENCES app_users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (staff_id, bonus_month)
            );
            CREATE INDEX IF NOT EXISTS idx_attendance_bonus_payouts_month
                ON attendance_bonus_payouts(bonus_month DESC);
            INSERT INTO system_settings (setting_key, setting_value, updated_at)
            VALUES ('attendance_bonus_amount', '500', NOW())
            ON CONFLICT (setting_key) DO NOTHING;
        ''')

    def parse_month(month_value):
        try:
            month_start = datetime.strptime(month_value, '%Y-%m').date().replace(day=1)
        except (TypeError, ValueError):
            return None
        return month_start, (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    @app.route('/api/staff/attendance-bonus', methods=['GET', 'PUT'])
    @manager_required
    def attendance_bonus():
        month = request.args.get('month') or (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        month_range = parse_month(month)
        if not month_range:
            return jsonify({'status': 'error', 'message': 'รูปแบบเดือนต้องเป็น YYYY-MM'}), 400
        month_start, month_end = month_range
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            ensure_attendance_bonus_tables(cur)
            if request.method == 'PUT':
                try:
                    amount = float((request.json or {}).get('amount'))
                except (TypeError, ValueError):
                    return jsonify({'status': 'error', 'message': 'กรุณาระบุจำนวนเงินเบี้ยขยัน'}), 400
                if not 0 < amount <= 100000:
                    return jsonify({'status': 'error', 'message': 'จำนวนเงินต้องมากกว่า 0 และไม่เกิน 100,000 บาท'}), 400
                cur.execute('''INSERT INTO system_settings (setting_key, setting_value, updated_at)
                               VALUES ('attendance_bonus_amount', %s, NOW())
                               ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW();''', (str(amount),))
                conn.commit()
                return jsonify({'status': 'success', 'amount': amount}), 200
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'attendance_bonus_amount';")
            setting = cur.fetchone()
            amount = float(setting['setting_value']) if setting else 500.0
            cur.execute('''
                SELECT s.id, s.employee_code, s.full_name,
                       COUNT(sa.id) FILTER (WHERE sa.check_in_at IS NOT NULL) AS attended_days,
                       COUNT(sa.id) FILTER (WHERE sa.status IN ('late', 'leave', 'absent') OR sa.check_in_at IS NULL) AS disqualifying_days,
                       p.id AS payout_id, p.amount AS paid_amount, p.created_at AS paid_at
                FROM staff s
                LEFT JOIN staff_attendance sa ON sa.staff_id = s.id AND sa.work_date >= %s AND sa.work_date < %s
                LEFT JOIN attendance_bonus_payouts p ON p.staff_id = s.id AND p.bonus_month = %s
                WHERE s.is_active = true AND s.created_at::date < %s
                GROUP BY s.id, s.employee_code, s.full_name, p.id, p.amount, p.created_at
                ORDER BY s.full_name;
            ''', (month_start, month_end, month_start, month_end))
            staff_rows = []
            for row in cur.fetchall():
                item = dict(row)
                item['eligible'] = item['attended_days'] > 0 and item['disqualifying_days'] == 0
                staff_rows.append(item)
            return jsonify({'month': month, 'amount': amount, 'staff': staff_rows}), 200
        finally:
            cur.close()
            conn.close()

    @app.route('/api/staff/attendance-bonus/<int:staff_id>/pay', methods=['POST'])
    @manager_required
    def pay_attendance_bonus(staff_id):
        month = (request.json or {}).get('month')
        month_range = parse_month(month)
        if not month_range:
            return jsonify({'status': 'error', 'message': 'รูปแบบเดือนต้องเป็น YYYY-MM'}), 400
        month_start, month_end = month_range
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            ensure_attendance_bonus_tables(cur)
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'attendance_bonus_amount';")
            amount = float((cur.fetchone() or {'setting_value': '500'})['setting_value'])
            cur.execute('''SELECT s.full_name, COUNT(sa.id) FILTER (WHERE sa.check_in_at IS NOT NULL) AS attended_days,
                                  COUNT(sa.id) FILTER (WHERE sa.status IN ('late', 'leave', 'absent') OR sa.check_in_at IS NULL) AS bad_days
                           FROM staff s LEFT JOIN staff_attendance sa ON sa.staff_id=s.id AND sa.work_date >= %s AND sa.work_date < %s
                           WHERE s.id=%s AND s.is_active=true GROUP BY s.id, s.full_name;''', (month_start, month_end, staff_id))
            staff_member = cur.fetchone()
            if not staff_member:
                return jsonify({'status': 'error', 'message': 'ไม่พบพนักงาน'}), 404
            if staff_member['attended_days'] == 0 or staff_member['bad_days'] > 0:
                return jsonify({'status': 'error', 'message': 'พนักงานคนนี้ไม่ผ่านเงื่อนไขเบี้ยขยันของเดือนที่เลือก'}), 400
            description = f"เบี้ยขยัน {staff_member['full_name']} ประจำเดือน {month}"
            cur.execute('''INSERT INTO finance_transactions (staff_id, transaction_type, category, description, amount)
                           VALUES (%s, 'expense', 'attendance_bonus', %s, %s) RETURNING *;''', (staff_id, description, amount))
            transaction = cur.fetchone()
            cur.execute('''INSERT INTO attendance_bonus_payouts (staff_id, bonus_month, amount, finance_transaction_id, approved_by)
                           VALUES (%s, %s, %s, %s, %s);''', (staff_id, month_start, amount, transaction['id'], session.get('user_id')))
            record_fund_movement(cur, -amount, 'expense', description, transaction['id'], session.get('user_id'))
            conn.commit()
            return jsonify({'status': 'success', 'transaction': transaction}), 201
        except psycopg2.IntegrityError:
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'จ่ายเบี้ยขยันให้พนักงานคนนี้สำหรับเดือนนี้แล้ว'}), 409
        except Exception as exc:
            conn.rollback()
            return jsonify({'status': 'error', 'message': str(exc)}), 500
        finally:
            cur.close()
            conn.close()
