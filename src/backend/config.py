"""การตั้งค่าระบบที่อ่านจาก environment โดยไม่เก็บข้อมูลลับในซอร์สโค้ด"""

import os
from datetime import time

from dotenv import load_dotenv


os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

TEMPLATE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../frontend/templates'))
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../frontend/static'))

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'database': os.environ.get('DB_NAME', 'v_carcare'),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD'),
    'port': os.environ.get('DB_PORT', '5432'),
}

FACE_MATCH_DISTANCE_THRESHOLD = 0.42
FACE_MODEL_NAME = 'InsightFace-buffalo_sc'
WORK_START_TIME = time(8, 0)
STAFF_WITHDRAWAL_MAX_PER_REQUEST = 3000
STAFF_WITHDRAWAL_MAX_REQUESTS_PER_WEEK = 2
CENTRAL_FUND_OPENING_FLOAT = 3000

THAI_SERVICE_NAMES = {
    'wash': 'ล้างภายนอก',
    'washVacuum': 'ล้างภายนอกและดูดฝุ่น',
    'fullFlush': 'ล้าง ดูดฝุ่น และฉีดล้างช่วงล่าง',
    'engineWash': 'ล้าง ดูดฝุ่น และล้างห้องเครื่อง',
    'fullEngine': 'ล้างครบชุด พร้อมล้างช่วงล่างและห้องเครื่อง',
    'ozone': 'อบโอโซนกำจัดกลิ่น',
    'wax': 'เคลือบแว็กซ์',
}
