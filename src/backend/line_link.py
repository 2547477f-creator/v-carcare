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
from .line_bot import LINE_CHANNEL_SECRET, LINE_LIFF_ID, LINE_LIFF_URL, verify_liff_access_token

logger = logging.getLogger(__name__)


def register_line_link_routes(app, get_db_connection):
    def get_link(cur, token):
        cur.execute('''SELECT l.id, l.vehicle_id, l.customer_id, l.line_user_id, l.expires_at, l.linked_at,
                              l.revoked_at, v.license_plate, v.province
                       FROM vehicle_line_links l JOIN vehicles v ON v.id=l.vehicle_id
                       WHERE l.link_token=%s FOR UPDATE;''', (token,))
        return cur.fetchone()

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
            if (event.get('source') or {}).get('userId'):
                logger.info('LINE webhook received from a user')
        return 'OK', 200
