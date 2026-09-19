"""จุดเริ่มต้นสำหรับรัน V CarCare จากโฟลเดอร์โครงการ"""

import os
import threading
import webbrowser

from src.backend.config import ROOT_DIR
from src.backend.database import get_local_network_ip
from src.backend.face_recognition import warm_up_face_model
from src.backend.main import app, start_auto_shop_open_scheduler


def _ssl_context():
    use_https = os.environ.get('FLASK_HTTPS', '1').lower() not in {'0', 'false', 'no'}
    if not use_https:
        return None, 'http'
    cert_path = os.path.join(ROOT_DIR, '.certs', 'vcarcare-cert.pem')
    key_path = os.path.join(ROOT_DIR, '.certs', 'vcarcare-key.pem')
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return (cert_path, key_path), 'https'
    return 'adhoc', 'https'


def main():
    port = int(os.environ.get('PORT', '5000'))
    ssl_context, scheme = _ssl_context()
    url = f'{scheme}://127.0.0.1:{port}'
    print('V CarCare กำลังเริ่มระบบ...')
    print(f'หน้าเว็บเครื่องนี้: {url}')
    print(f'เครื่องอื่นใน Wi-Fi: {scheme}://{get_local_network_ip()}:{port}')
    warm_up_face_model(app.static_folder)
    start_auto_shop_open_scheduler()
    threading.Timer(1, webbrowser.open, args=(url,)).start()
    app.run(
        host='0.0.0.0', port=port, ssl_context=ssl_context,
        debug=os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes'},
        use_reloader=False,
    )


if __name__ == '__main__':
    main()
