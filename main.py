import http.server
import socketserver
import socket

# 1. ตั้งค่าพอร์ตที่ต้องการใช้งาน
PORT = 8000

# 2. ฟังก์ชันช่วยหาเลข IP เครื่องคอมพิวเตอร์ของคุณอัตโนมัติ
def get_local_ip():
    try:
        # สร้าง socket ชั่วคราวเพื่อเช็ก IP วงเน็ตที่ใช้งานอยู่จริง
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()

# 3. ตั้งค่าให้เซิร์ฟเวอร์ยอมรับการเชื่อมต่อจากอุปกรณ์ภายนอก (เช่น มือถือ)
Handler = http.server.SimpleHTTPRequestHandler

class MyServer(socketserver.TCPServer):
    allow_reuse_address = True  # ช่วยให้เปิด-ปิดรันใหม่ได้ทันที พอร์ตไม่ค้าง

print("=" * 60)
print("       🚗 V-CARCARE LOCAL SERVER IS RUNNING 🚗")
print("=" * 60)
print(f"  สำหรับเปิดบนคอมพิวเตอร์:")
print(f"    -> http://localhost:{PORT}/frontend/pos.html")
print("-" * 60)
print(f" สำหรับเปิดบนมือถือ (ต้องต่อ Wi-Fi/Hotspot เดียวกัน):")
print(f"    -> http://{LOCAL_IP}:{PORT}/frontend/pos.html")
print("=" * 60)
print(" กดปุ่ม Ctrl + C บนคีย์บอร์ดเพื่อปิดระบบเซิร์ฟเวอร์")
print("=" * 60)

# เริ่มทำงานเซิร์ฟเวอร์
with MyServer(("0.0.0.0", PORT), Handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 ปิดระบบเซิร์ฟเวอร์เรียบร้อยแล้วครับทีม!")