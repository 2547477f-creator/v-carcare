"""เชื่อมรถกับบัญชี LINE ด้วยลิงก์ LIFF แบบใช้ครั้งเดียว"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import jsonify, render_template, request

from .auth import login_required, manager_required
from .rate_limit import rate_limiter
from .line_bot import (
    LINE_CHANNEL_SECRET, LINE_LIFF_ID, LINE_LIFF_URL,
    send_line_reply, verify_liff_access_token, verify_liff_id_token,
)

logger = logging.getLogger(__name__)


def register_line_link_routes(app, get_db_connection):
    def get_link(cur, token):
        cur.execute('''SELECT l.id, l.vehicle_id, l.customer_id, l.line_user_id, l.expires_at, l.linked_at,
                              l.revoked_at, v.license_plate, v.province
                       FROM vehicle_line_links l JOIN vehicles v ON v.id=l.vehicle_id
                       WHERE l.link_token=%s FOR UPDATE;''', (token,))
        return cur.fetchone()

    @app.route('/api/liff/register', methods=['POST'])
    @rate_limiter.limit(5, 60)
    def register_liff_vehicle():
        data = request.json or {}
        profile = verify_liff_id_token(data.get('id_token'))
        if not profile:
            return jsonify({'status': 'error', 'message': 'ไม่สามารถยืนยันตัวตน LINE ได้ กรุณาเปิดหน้านี้ผ่าน LINE'}), 401

        phone = (data.get('phone') or '').strip()
        license_plate = (data.get('license_plate') or '').strip()
        province = (data.get('province') or '').strip()
        category = (data.get('category') or '').strip()
        size_code = (data.get('size') or '').strip()
        if not all((phone, license_plate, province, category, size_code)) or category not in ('car', 'bike'):
            return jsonify({'status': 'error', 'message': 'กรุณากรอกข้อมูลลูกค้าและรถให้ครบถ้วน'}), 400

        line_user_id = profile['sub']
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('''SELECT id FROM vehicles
                           WHERE license_plate=%s AND province=%s FOR UPDATE;''',
                        (license_plate, province))
            if cur.fetchone():
                conn.rollback()
                return jsonify({'status': 'error', 'message': 'ทะเบียนรถและจังหวัดนี้ลงทะเบียนไว้แล้ว'}), 409

            cur.execute('SELECT id FROM customers WHERE phone=%s FOR UPDATE;', (phone,))
            customer = cur.fetchone()
            if customer:
                customer_id = customer['id']
            else:
                cur.execute('''INSERT INTO customers (phone, line_id)
                               VALUES (%s, %s) RETURNING id;''', (phone, line_user_id))
                customer_id = cur.fetchone()['id']

            cur.execute('''INSERT INTO vehicles
                           (customer_id, license_plate, province, category, size_code)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id;''',
                        (customer_id, license_plate, province, category, size_code))
            vehicle_id = cur.fetchone()['id']

            cur.execute('''SELECT id, line_user_id FROM vehicle_line_links
                           WHERE vehicle_id=%s AND linked_at IS NOT NULL AND revoked_at IS NULL
                           FOR UPDATE;''', (vehicle_id,))
            active_link = cur.fetchone()
            if active_link and active_link['line_user_id'] != line_user_id:
                conn.rollback()
                return jsonify({'status': 'error', 'message': 'ทะเบียนรถนี้ถูกเชื่อมต่อกับบัญชี LINE อื่นแล้ว'}), 409
            if not active_link:
                cur.execute('''INSERT INTO vehicle_line_links
                               (vehicle_id, customer_id, line_user_id, link_token, linked_at, expires_at)
                               VALUES (%s, %s, %s, %s, NOW(), NOW());''',
                            (vehicle_id, customer_id, line_user_id, secrets.token_urlsafe(32)))
            conn.commit()
            return jsonify({
                'status': 'success',
                'vehicle_id': vehicle_id,
                'vehicle': {'license_plate': license_plate, 'province': province},
                'line_linked': True,
                'message': 'ลงทะเบียนรถสำเร็จ',
            }), 201
        except Exception:
            conn.rollback()
            logger.exception('LIFF registration failed')
            return jsonify({'status': 'error', 'message': 'ไม่สามารถลงทะเบียนรถได้ กรุณาลองใหม่อีกครั้ง'}), 500
        finally:
            cur.close()
            conn.close()

    @app.route('/api/line/link/start', methods=['POST'])
    @login_required
    def start_line_link():
        try:
            vehicle_id = int((request.json or {}).get('vehicle_id'))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'vehicle_id ไม่ถูกต้อง'}), 400
        base_url = LINE_LIFF_URL or (f'https://liff.line.me/{LINE_LIFF_ID}' if LINE_LIFF_ID else None)
        if not base_url:
            return jsonify({'status': 'error', 'message': 'ยังไม่ได้ตั้งค่า LINE_LIFF_URL หรือ LINE_LIFF_ID'}), 503
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('SELECT id, customer_id, license_plate, province FROM vehicles WHERE id=%s;', (vehicle_id,))
            vehicle = cur.fetchone()
            if not vehicle:
                return jsonify({'status': 'error', 'message': 'ไม่พบรถ'}), 404
            cur.execute('''UPDATE vehicle_line_links SET revoked_at=NOW()
                           WHERE vehicle_id=%s AND linked_at IS NULL AND revoked_at IS NULL;''', (vehicle_id,))
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            cur.execute('''INSERT INTO vehicle_line_links (vehicle_id, customer_id, link_token, expires_at)
                           VALUES (%s, %s, %s, %s);''', (vehicle_id, vehicle['customer_id'], token, expires_at))
            conn.commit()
            separator = '&' if '?' in base_url else '?'
            return jsonify({'status': 'success', 'expires_at': expires_at.isoformat(),
                            'url': f'{base_url}{separator}token={token}', 'vehicle': vehicle}), 201
        except Exception:
            conn.rollback()
            logger.exception('LINE link start failed')
            return jsonify({'status': 'error', 'message': 'ไม่สามารถสร้าง QR ได้'}), 500
        finally:
            cur.close()
            conn.close()

    @app.route('/api/line/link/<string:token>', methods=['GET'])
    def line_link_details(token):
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            link = get_link(cur, token)
            if not link or link['revoked_at'] or link['linked_at'] or link['expires_at'] <= datetime.now(timezone.utc):
                return jsonify({'status': 'error', 'message': 'ลิงก์ไม่ถูกต้องหรือหมดอายุ'}), 404
            return jsonify({'status': 'success', 'vehicle': {'license_plate': link['license_plate'], 'province': link['province']}})
        finally:
            conn.rollback()
            cur.close()
            conn.close()

    @app.route('/api/line/link/confirm', methods=['POST'])
    @rate_limiter.limit(10, 60)
    def confirm_line_link():
        data = request.json or {}
        token = data.get('token')
        profile = verify_liff_access_token(data.get('access_token'))
        if not profile:
            return jsonify({'status': 'error', 'message': 'ไม่สามารถยืนยันบัญชี LINE ได้'}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            link = get_link(cur, token)
            if not link or link['revoked_at'] or link['linked_at'] or link['expires_at'] <= datetime.now(timezone.utc):
                return jsonify({'status': 'error', 'message': 'ลิงก์ไม่ถูกต้องหรือหมดอายุ'}), 400
            cur.execute('''SELECT line_user_id FROM vehicle_line_links
                           WHERE vehicle_id=%s AND linked_at IS NOT NULL AND revoked_at IS NULL
                           FOR UPDATE;''', (link['vehicle_id'],))
            if cur.fetchone():
                return jsonify({'status': 'error', 'message': 'รถคันนี้เชื่อมต่อกับบัญชี LINE อื่นอยู่แล้ว'}), 409
            cur.execute('''UPDATE vehicle_line_links SET line_user_id=%s, linked_at=NOW(), expires_at=NOW()
                           WHERE id=%s;''', (profile['userId'], link['id']))
            conn.commit()
            logger.info('LINE link success for vehicle %s', link['vehicle_id'])
            return jsonify({'status': 'success', 'display_name': profile.get('displayName')})
        except Exception:
            conn.rollback()
            logger.exception('LINE link confirm failed')
            return jsonify({'status': 'error', 'message': 'ไม่สามารถเชื่อม LINE ได้'}), 500
        finally:
            cur.close()
            conn.close()

    @app.route('/api/line/link/unlink', methods=['POST'])
    @manager_required
    def unlink_line():
        try:
            vehicle_id = int((request.json or {}).get('vehicle_id'))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'vehicle_id ไม่ถูกต้อง'}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('''UPDATE vehicle_line_links SET revoked_at=NOW(), expires_at=NOW()
                           WHERE vehicle_id=%s AND linked_at IS NOT NULL AND revoked_at IS NULL;''', (vehicle_id,))
            conn.commit()
            return jsonify({'status': 'success'})
        finally:
            cur.close()
            conn.close()

    @app.route('/api/vehicles/<int:vehicle_id>/line-status')
    @manager_required
    def vehicle_line_status(vehicle_id):
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('''SELECT EXISTS(SELECT 1 FROM vehicle_line_links
                           WHERE vehicle_id=%s AND linked_at IS NOT NULL AND revoked_at IS NULL) AS linked;''', (vehicle_id,))
            return jsonify(cur.fetchone())
        finally:
            cur.close()
            conn.close()

    @app.route('/line/link')
    def line_link_page():
        return render_template('line_link.html', liff_id=LINE_LIFF_ID)

    def link_line_user_to_vehicle(cur, line_user_id, vehicle):
        cur.execute('''SELECT line_user_id FROM vehicle_line_links
                       WHERE vehicle_id=%s AND linked_at IS NOT NULL AND revoked_at IS NULL
                       FOR UPDATE;''', (vehicle['vehicle_id'],))
        active_link = cur.fetchone()
        if active_link and active_link['line_user_id'] != line_user_id:
            return 'ทะเบียนรถนี้เชื่อมต่อกับบัญชี LINE อื่นแล้ว'
        if not active_link:
            cur.execute('''INSERT INTO vehicle_line_links
                           (vehicle_id, customer_id, line_user_id, link_token, linked_at, expires_at)
                           VALUES (%s, %s, %s, %s, NOW(), NOW());''',
                        (vehicle['vehicle_id'], vehicle['customer_id'], line_user_id,
                         secrets.token_urlsafe(32)))
        return None

    def handle_line_registration_message(event):
        source = event.get('source') or {}
        message = event.get('message') or {}
        line_user_id = source.get('userId')
        reply_token = event.get('replyToken')
        if not line_user_id or not reply_token:
            return

        if event.get('type') == 'follow':
            conn = get_db_connection()
            cur = conn.cursor()
            try:
                cur.execute('''INSERT INTO line_registration_sessions (line_user_id, step)
                               VALUES (%s, 'waiting_plate')
                               ON CONFLICT (line_user_id) DO UPDATE
                               SET step='waiting_plate', license_plate=NULL, phone=NULL, province=NULL,
                                   updated_at=NOW();''', (line_user_id,))
                conn.commit()
                send_line_reply(reply_token, 'ยินดีต้อนรับ V CARCARE\nกรุณาพิมพ์ทะเบียนรถเพื่อเริ่มเชื่อมต่อ LINE')
            except Exception:
                conn.rollback()
                logger.exception('LINE registration session start failed')
            finally:
                cur.close()
                conn.close()
            return

        if event.get('type') != 'message' or message.get('type') != 'text':
            return
        text = (message.get('text') or '').strip()
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('''SELECT step, license_plate, phone FROM line_registration_sessions
                           WHERE line_user_id=%s FOR UPDATE;''', (line_user_id,))
            registration = cur.fetchone()
            if not registration:
                if text != 'ลงทะเบียนรถ':
                    send_line_reply(reply_token, 'กรุณาพิมพ์คำว่า ลงทะเบียนรถ เพื่อเริ่มต้น')
                    return
                cur.execute('''INSERT INTO line_registration_sessions (line_user_id, step)
                               VALUES (%s, 'waiting_plate');''', (line_user_id,))
                conn.commit()
                send_line_reply(reply_token, 'กรุณาพิมพ์ทะเบียนรถ เช่น กข 1234')
                return

            if registration['step'] == 'waiting_plate':
                if not text:
                    send_line_reply(reply_token, 'กรุณาพิมพ์ทะเบียนรถ')
                    return
                cur.execute('''UPDATE line_registration_sessions
                               SET license_plate=%s, step='waiting_phone', updated_at=NOW()
                               WHERE line_user_id=%s;''', (text, line_user_id))
                conn.commit()
                send_line_reply(reply_token, 'กรุณาพิมพ์เบอร์โทรศัพท์ที่ใช้ลงทะเบียนรถ')
                return

            if registration['step'] == 'waiting_phone':
                cur.execute('''SELECT v.id AS vehicle_id, v.customer_id, v.license_plate, v.province
                               FROM vehicles v JOIN customers c ON c.id=v.customer_id
                               WHERE UPPER(REPLACE(v.license_plate, ' ', '')) = UPPER(REPLACE(%s, ' ', ''))
                                 AND c.phone=%s
                               ORDER BY v.id DESC LIMIT 2;''', (registration['license_plate'], text))
                vehicles = cur.fetchall()
                if not vehicles:
                    cur.execute('DELETE FROM line_registration_sessions WHERE line_user_id=%s;', (line_user_id,))
                    conn.commit()
                    send_line_reply(reply_token, 'ไม่พบข้อมูลทะเบียนรถและเบอร์โทรศัพท์ที่ตรงกัน กรุณาติดต่อพนักงาน')
                    return
                if len(vehicles) > 1:
                    cur.execute('''UPDATE line_registration_sessions
                                   SET phone=%s, step='waiting_province', updated_at=NOW()
                                   WHERE line_user_id=%s;''', (text, line_user_id))
                    conn.commit()
                    send_line_reply(reply_token, 'พบทะเบียนซ้ำหลายจังหวัด กรุณาพิมพ์จังหวัด')
                    return
                error_message = link_line_user_to_vehicle(cur, line_user_id, vehicles[0])
                cur.execute('DELETE FROM line_registration_sessions WHERE line_user_id=%s;', (line_user_id,))
                conn.commit()
                send_line_reply(reply_token, error_message or 'เชื่อมต่อ LINE กับทะเบียนรถสำเร็จแล้ว')
                return

            if registration['step'] == 'waiting_province':
                cur.execute('''SELECT v.id AS vehicle_id, v.customer_id, v.license_plate, v.province
                               FROM vehicles v JOIN customers c ON c.id=v.customer_id
                               WHERE UPPER(REPLACE(v.license_plate, ' ', '')) = UPPER(REPLACE(%s, ' ', ''))
                                 AND c.phone=%s AND UPPER(COALESCE(v.province, ''))=UPPER(%s)
                               FOR UPDATE;''', (registration['license_plate'], registration['phone'], text))
                vehicle = cur.fetchone()
                if not vehicle:
                    cur.execute('DELETE FROM line_registration_sessions WHERE line_user_id=%s;', (line_user_id,))
                    conn.commit()
                    send_line_reply(reply_token, 'ไม่พบข้อมูลทะเบียนรถ เบอร์โทรศัพท์ และจังหวัดที่ตรงกัน กรุณาติดต่อพนักงาน')
                    return
                error_message = link_line_user_to_vehicle(cur, line_user_id, vehicle)
                cur.execute('DELETE FROM line_registration_sessions WHERE line_user_id=%s;', (line_user_id,))
                conn.commit()
                send_line_reply(reply_token, error_message or 'เชื่อมต่อ LINE กับทะเบียนรถสำเร็จแล้ว')
        except Exception:
            conn.rollback()
            logger.exception('LINE registration message failed')
            send_line_reply(reply_token, 'ระบบขัดข้อง กรุณาลองใหม่อีกครั้ง')
        finally:
            cur.close()
            conn.close()

    @app.route('/callback', methods=['POST'])
    def line_callback():
        body = request.get_data()
        signature = request.headers.get('X-Line-Signature', '')
        if not LINE_CHANNEL_SECRET:
            logger.error('LINE webhook rejected because LINE_CHANNEL_SECRET is not configured')
            return 'LINE webhook is not configured', 503
        expected = base64.b64encode(hmac.new(
            LINE_CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256
        ).digest()).decode('utf-8')
        if not hmac.compare_digest(signature, expected):
            return 'Invalid signature', 400
        try:
            events = json.loads(body.decode('utf-8')).get('events', [])
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 'Invalid payload', 400
        for event in events:
            handle_line_registration_message(event)
        return 'OK', 200
