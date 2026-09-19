"""การเชื่อมรถกับบัญชี LINE ผ่าน token แบบใช้ครั้งเดียวและ LIFF"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import jsonify, render_template, request

from .auth import manager_required
from .line_bot import LINE_LIFF_ID, LINE_LIFF_URL, verify_liff_access_token

logger = logging.getLogger(__name__)


def register_line_link_routes(app, get_db_connection):
    def ensure_schema(cur):
        cur.execute('''
            ALTER TABLE customers ADD COLUMN IF NOT EXISTS line_user_id VARCHAR(80);
            ALTER TABLE customers ADD COLUMN IF NOT EXISTS name VARCHAR(160);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_line_user_id
                ON customers(line_user_id) WHERE line_user_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS vehicle_line_links (
                id BIGSERIAL PRIMARY KEY,
                vehicle_id BIGINT NOT NULL REFERENCES vehicles(id),
                customer_id BIGINT NOT NULL REFERENCES customers(id),
                line_user_id VARCHAR(80),
                link_token VARCHAR(128) NOT NULL UNIQUE,
                linked_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_vehicle_line_links_vehicle ON vehicle_line_links(vehicle_id);
        ''')

    def get_link(cur, token):
        cur.execute('''SELECT l.id, l.vehicle_id, l.customer_id, l.line_user_id, l.expires_at, l.linked_at,
                              v.license_plate, v.province
                       FROM vehicle_line_links l JOIN vehicles v ON v.id=l.vehicle_id
                       WHERE l.link_token=%s FOR UPDATE;''', (token,))
        return cur.fetchone()

    @app.route('/api/line/link/start', methods=['POST'])
    @manager_required
    def start_line_link():
        try:
            vehicle_id = int((request.json or {}).get('vehicle_id'))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'vehicle_id ไม่ถูกต้อง'}), 400
        base_url = LINE_LIFF_URL or (f'https://liff.line.me/{LINE_LIFF_ID}' if LINE_LIFF_ID else None)
        if not base_url:
            return jsonify({'status': 'error', 'message': 'ยังไม่ได้ตั้งค่า LINE_LIFF_URL หรือ LINE_LIFF_ID'}), 503
        conn = get_db_connection(); cur = conn.cursor()
        try:
            ensure_schema(cur)
            cur.execute('SELECT id, customer_id, license_plate, province FROM vehicles WHERE id=%s;', (vehicle_id,))
            vehicle = cur.fetchone()
            if not vehicle:
                return jsonify({'status': 'error', 'message': 'ไม่พบรถ'}), 404
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            cur.execute('''INSERT INTO vehicle_line_links (vehicle_id, customer_id, link_token, expires_at)
                           VALUES (%s, %s, %s, %s) RETURNING id;''', (vehicle_id, vehicle['customer_id'], token, expires_at))
            conn.commit()
            separator = '&' if '?' in base_url else '?'
            return jsonify({'status': 'success', 'token': token, 'expires_at': expires_at.isoformat(), 'url': f'{base_url}{separator}token={token}', 'vehicle': vehicle}), 201
        except Exception as exc:
            conn.rollback(); logger.exception('LINE link start failed')
            return jsonify({'status': 'error', 'message': 'ไม่สามารถสร้าง QR ได้'}), 500
        finally:
            cur.close(); conn.close()

    @app.route('/api/line/link/<string:token>', methods=['GET'])
    def line_link_details(token):
        conn = get_db_connection(); cur = conn.cursor()
        try:
            ensure_schema(cur); link = get_link(cur, token)
            if not link or link['linked_at'] or link['expires_at'] <= datetime.now(timezone.utc):
                return jsonify({'status': 'error', 'message': 'ลิงก์ไม่ถูกต้องหรือหมดอายุ'}), 404
            return jsonify({'status': 'success', 'vehicle': {'license_plate': link['license_plate'], 'province': link['province']}})
        finally:
            conn.rollback(); cur.close(); conn.close()

    @app.route('/api/line/link/confirm', methods=['POST'])
    def confirm_line_link():
        data = request.json or {}; token = data.get('token'); access_token = data.get('access_token')
        profile = verify_liff_access_token(access_token)
        if not profile:
            return jsonify({'status': 'error', 'message': 'ไม่สามารถยืนยันบัญชี LINE ได้'}), 401
        conn = get_db_connection(); cur = conn.cursor()
        try:
            ensure_schema(cur); link = get_link(cur, token)
            if not link or link['linked_at'] or link['expires_at'] <= datetime.now(timezone.utc):
                return jsonify({'status': 'error', 'message': 'ลิงก์ไม่ถูกต้องหรือหมดอายุ'}), 400
            cur.execute('UPDATE customers SET line_user_id=%s, updated_at=NOW() WHERE id=%s;', (profile['userId'], link['customer_id']))
            cur.execute('''UPDATE vehicle_line_links SET line_user_id=%s, linked_at=NOW(), expires_at=NOW()
                           WHERE id=%s;''', (profile['userId'], link['id']))
            conn.commit(); logger.info('LINE link success for vehicle %s', link['vehicle_id'])
            return jsonify({'status': 'success', 'display_name': profile.get('displayName')})
        except Exception:
            conn.rollback(); logger.exception('LINE link confirm failed')
            return jsonify({'status': 'error', 'message': 'ไม่สามารถเชื่อม LINE ได้'}), 500
        finally:
            cur.close(); conn.close()

    @app.route('/api/line/link/unlink', methods=['POST'])
    @manager_required
    def unlink_line():
        try: vehicle_id = int((request.json or {}).get('vehicle_id'))
        except (TypeError, ValueError): return jsonify({'status': 'error', 'message': 'vehicle_id ไม่ถูกต้อง'}), 400
        conn = get_db_connection(); cur = conn.cursor()
        try:
            ensure_schema(cur)
            cur.execute('''UPDATE vehicle_line_links SET line_user_id=NULL, linked_at=NULL, expires_at=NOW()
                           WHERE vehicle_id=%s AND linked_at IS NOT NULL;''', (vehicle_id,))
            conn.commit(); return jsonify({'status': 'success'})
        finally: cur.close(); conn.close()

    @app.route('/api/vehicles/<int:vehicle_id>/line-status')
    @manager_required
    def vehicle_line_status(vehicle_id):
        conn = get_db_connection(); cur = conn.cursor()
        try:
            ensure_schema(cur)
            cur.execute('''SELECT EXISTS(SELECT 1 FROM vehicle_line_links WHERE vehicle_id=%s AND linked_at IS NOT NULL) AS linked;''', (vehicle_id,))
            return jsonify(cur.fetchone())
        finally: conn.rollback(); cur.close(); conn.close()

    @app.route('/line/link')
    def line_link_page():
        return render_template('line_link.html', liff_id=LINE_LIFF_ID)
