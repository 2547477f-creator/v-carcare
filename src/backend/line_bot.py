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


class LineMessagingService:
    """Encapsulates LINE authentication, replies, and push notifications."""

    def __init__(
        self,
        channel_secret=LINE_CHANNEL_SECRET,
        channel_access_token=LINE_CHANNEL_ACCESS_TOKEN,
        login_channel_id=LINE_LOGIN_CHANNEL_ID,
        *,
        opener=urlopen,
    ):
        self.channel_secret = channel_secret
        self.channel_access_token = channel_access_token
        self.login_channel_id = login_channel_id
        self._opener = opener

    def is_configured(self):
        return bool(self.channel_secret and self.channel_access_token)

    def verify_access_token(self, access_token):
        if not access_token or not self.login_channel_id:
            return None
        try:
            verified = self._load_json(Request(
                'https://api.line.me/oauth2/v2.1/verify',
                headers={'Authorization': f'Bearer {access_token}'}, method='GET',
            ))
            if (str(verified.get('client_id')) != str(self.login_channel_id)
                    or int(verified.get('expires_in', 0)) <= 0):
                return None
            profile = self._load_json(Request(
                'https://api.line.me/v2/profile',
                headers={'Authorization': f'Bearer {access_token}'}, method='GET',
            ))
            return profile if profile.get('userId') else None
        except (HTTPError, URLError, ValueError, OSError):
            logger.warning('LINE identity verification failed')
            return None

    def verify_id_token(self, id_token):
        if not id_token or not self.login_channel_id:
            return None
        try:
            payload = urlencode({
                'id_token': id_token,
                'client_id': self.login_channel_id,
            }).encode('utf-8')
            verified = self._load_json(Request(
                'https://api.line.me/oauth2/v2.1/verify', data=payload, method='POST',
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            ))
            return verified if verified.get('sub') else None
        except (HTTPError, URLError, ValueError, OSError):
            logger.warning('LINE ID token verification failed')
            return None

    def reply(self, reply_token, message):
        if not reply_token or not self.channel_access_token:
            logger.info('LINE reply skipped: LINE is not configured')
            return False
        return self._send('https://api.line.me/v2/bot/message/reply', {
            'replyToken': reply_token,
            'messages': [{'type': 'text', 'text': message[:5000]}],
        }, 'LINE reply failed')

    def notify(self, line_user_id, message):
        if not line_user_id or not self.channel_access_token:
            logger.info('LINE notification skipped: account is not linked or LINE is not configured')
            return False
        sent = self._send('https://api.line.me/v2/bot/message/push', {
            'to': line_user_id,
            'messages': [{'type': 'text', 'text': message[:5000]}],
        }, 'LINE notification failed')
        if sent:
            logger.info('LINE notification sent')
        return sent

    def _load_json(self, request):
        with self._opener(request, timeout=10) as response:
            return json.load(response)

    def _send(self, url, body, failure_log):
        payload = json.dumps(body).encode('utf-8')
        try:
            request = Request(
                url, data=payload, method='POST',
                headers={
                    'Authorization': f'Bearer {self.channel_access_token}',
                    'Content-Type': 'application/json',
                },
            )
            with self._opener(request, timeout=10):
                pass
            return True
        except (HTTPError, URLError, OSError):
            logger.warning(failure_log)
            return False


line_messaging_service = LineMessagingService()


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


def send_line_reply(reply_token, message):
    """ตอบข้อความจาก webhook ด้วย reply token"""
    if not reply_token or not LINE_CHANNEL_ACCESS_TOKEN:
        logger.info('LINE reply skipped: LINE is not configured')
        return False
    payload = json.dumps({
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': message[:5000]}],
    }).encode('utf-8')
    try:
        reply_request = Request(
            'https://api.line.me/v2/bot/message/reply', data=payload, method='POST',
            headers={'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}', 'Content-Type': 'application/json'},
        )
        with urlopen(reply_request, timeout=10):
            pass
        return True
    except (HTTPError, URLError, OSError):
        logger.warning('LINE reply failed')
        return False


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
