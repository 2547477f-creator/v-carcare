"""Central access gate for routes that must never be public."""

from flask import jsonify, redirect, request, session, url_for


# These routes support customer tracking, LINE registration, sign-in, or service health.
# Every route not listed here requires an authenticated browser session.
PUBLIC_PATHS = frozenset({
    '/health', '/login', '/logout', '/track', '/register', '/callback',
    '/face-checkin', '/face-checkin/', '/api/face-scan-availability',
    '/api/face-login', '/api/face-login/',
    '/api/password-reset/face/verify', '/api/password-reset/face',
    '/api/security/change-primary-face-ip',
    '/api/liff/register', '/api/track/orders',
})


class AccessControlPolicy:
    """Default-deny policy for public and authenticated Flask requests."""

    def __init__(self, public_paths=PUBLIC_PATHS):
        self._public_paths = frozenset(item.rstrip('/') or '/' for item in public_paths)

    def is_public(self, path):
        path = path.rstrip('/') or '/'
        return (
            path in self._public_paths or path.startswith('/static/')
            or path.startswith('/api/track/') or path.startswith('/face-checkin/')
            or path.startswith('/api/face-login/')
        )


access_control_policy = AccessControlPolicy()


def is_public_request():
    return access_control_policy.is_public(request.path)


def require_authenticated_routes():
    """Flask before-request handler used as a safe default-deny access policy."""
    if is_public_request() or session.get('user_id'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'กรุณาเข้าสู่ระบบก่อน'}), 401
    return redirect(url_for('login'))
