"""API POS, คำสั่งซื้อ บริการ โปรโมชั่น และติดตามรถ"""

import json
import uuid
import logging
from datetime import datetime
import psycopg2
from flask import jsonify, request, session
from .auth import login_required, manager_required

logger = logging.getLogger(__name__)


def register_order_routes(app, dependencies):
    """ลงทะเบียน API โดยรับ dependency จากตัวประกอบระบบ"""
    globals().update(dependencies)

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
            if new_status in ('in_progress', 'ready', 'completed'):
                try:
                    cur.execute('''SELECT c.line_user_id, v.license_plate
                                   FROM service_orders o JOIN vehicles v ON v.id=o.vehicle_id
                                   JOIN customers c ON c.id=v.customer_id WHERE o.id=%s;''', (order_id,))
                    recipient = cur.fetchone()
                    if recipient and recipient.get('line_user_id'):
                        status_text = {'in_progress': 'กำลังดำเนินการ', 'ready': 'พร้อมรับรถ', 'completed': 'เสร็จเรียบร้อยแล้ว'}[new_status]
                        send_line_notification(recipient['line_user_id'], f'V CARCARE\nรถทะเบียน {recipient["license_plate"]}\n{status_text}')
                except Exception:
                    logger.warning('LINE notification skipped after order status update')
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
