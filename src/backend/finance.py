"""API การเงินและเงินกองกลาง"""

from datetime import date, datetime, timedelta
import psycopg2
from flask import jsonify, request, session
from .auth import manager_required


def register_finance_routes(app, dependencies):
    """ลงทะเบียน API โดยรับ dependency จากตัวประกอบระบบเพื่อเลี่ยง circular import"""
    globals().update(dependencies)

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
    
    

