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


def is_public_request():
    path = request.path.rstrip('/') or '/'
    return (
        path in {item.rstrip('/') or '/' for item in PUBLIC_PATHS}
        or path.startswith('/static/')
        or path.startswith('/api/track/')
        or path.startswith('/face-checkin/')
        or path.startswith('/api/face-login/')
    )


def require_authenticated_routes():
    """Flask before-request handler used as a safe default-deny access policy."""
    if is_public_request() or session.get('user_id'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'กรุณาเข้าสู่ระบบก่อน'}), 401
    return redirect(url_for('login'))
