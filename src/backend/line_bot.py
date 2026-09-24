"""การตั้งค่า LINE Messaging API จาก environment ของโครงการ"""

import os
import json
import logging
from dotenv import load_dotenv
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
backend_env_path = os.path.join(project_root, 'src', 'backend', '.env')
load_dotenv(backend_env_path, override=False)

LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_LOGIN_CHANNEL_ID = os.environ.get('LINE_LOGIN_CHANNEL_ID')
LINE_LIFF_ID = os.environ.get('LINE_LIFF_ID')
LINE_LIFF_URL = os.environ.get('LINE_LIFF_URL')

logger = logging.getLogger(__name__)


def is_line_configured():
    """คืนค่าว่าระบบมีข้อมูลสำหรับเชื่อมต่อ LINE ครบหรือไม่"""
    return bool(LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN)


def verify_liff_access_token(access_token):
    """ตรวจ access token กับ LINE แล้วคืน profile ที่ LINE ยืนยันเท่านั้น"""
    if not access_token or not LINE_LOGIN_CHANNEL_ID:
        return None
    try:
        verify_request = Request(
            'https://api.line.me/oauth2/v2.1/verify',
            headers={'Authorization': f'Bearer {access_token}'}, method='GET'
        )
        with urlopen(verify_request, timeout=10) as response:
            verified = json.load(response)
        if str(verified.get('client_id')) != str(LINE_LOGIN_CHANNEL_ID) or int(verified.get('expires_in', 0)) <= 0:
            return None
        profile_request = Request(
            'https://api.line.me/v2/profile',
            headers={'Authorization': f'Bearer {access_token}'}, method='GET'
        )
        with urlopen(profile_request, timeout=10) as response:
            profile = json.load(response)
        return profile if profile.get('userId') else None
    except (HTTPError, URLError, ValueError, OSError):
        logger.warning('LINE identity verification failed')
        return None


def verify_liff_id_token(id_token):
    """ตรวจสอบ LINE ID token แล้วคืนข้อมูลผู้ใช้ที่ LINE ยืนยัน"""
    if not id_token or not LINE_LOGIN_CHANNEL_ID:
        return None
    try:
        payload = urlencode({
            'id_token': id_token,
            'client_id': LINE_LOGIN_CHANNEL_ID,
        }).encode('utf-8')
        verify_request = Request(
            'https://api.line.me/oauth2/v2.1/verify', data=payload, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        with urlopen(verify_request, timeout=10) as response:
            verified = json.load(response)
        return verified if verified.get('sub') else None
    except (HTTPError, URLError, ValueError, OSError):
        logger.warning('LINE ID token verification failed')
        return None


def send_line_notification(line_user_id, message):
    """ส่งข้อความแบบ push; ข้อผิดพลาดของ LINE ต้องไม่ทำให้ธุรกรรมหลักล้มเหลว"""
    if not line_user_id or not LINE_CHANNEL_ACCESS_TOKEN:
        logger.info('LINE notification skipped: account is not linked or LINE is not configured')
        return False
    payload = json.dumps({'to': line_user_id, 'messages': [{'type': 'text', 'text': message[:5000]}]}).encode('utf-8')
    try:
        push_request = Request(
            'https://api.line.me/v2/bot/message/push', data=payload, method='POST',
            headers={'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}', 'Content-Type': 'application/json'},
        )
        with urlopen(push_request, timeout=10):
            pass
        logger.info('LINE notification sent')
        return True
    except (HTTPError, URLError, OSError):
        logger.warning('LINE notification failed')
        return False
