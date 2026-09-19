"""จุดรวมการเชื่อมต่อ PostgreSQL และเครื่องมือฐานข้อมูล"""

import socket

import psycopg2
from psycopg2.extras import RealDictCursor

from .config import DB_CONFIG


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def get_local_network_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
