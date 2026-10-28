"""จุดรวมการเชื่อมต่อ PostgreSQL และเครื่องมือฐานข้อมูล"""

import socket

import psycopg2
from psycopg2.extras import RealDictCursor

from .config import DB_CONFIG


class DatabaseClient:
    """Infrastructure adapter for PostgreSQL connections and local networking."""

    def __init__(self, config=None):
        self._config = config or DB_CONFIG

    def connect(self):
        return psycopg2.connect(**self._config, cursor_factory=RealDictCursor)

    @staticmethod
    def local_network_ip():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(('8.8.8.8', 80))
                return sock.getsockname()[0]
        except OSError:
            return '127.0.0.1'


database_client = DatabaseClient()


def get_db_connection():
    return database_client.connect()


def get_local_network_ip():
    return database_client.local_network_ip()
