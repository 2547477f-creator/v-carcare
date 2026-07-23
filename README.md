# V CarCare — ระบบจัดการร้านคาร์แคร์ (ฉบับแก้ไขให้ใช้งานได้จริง)

## สิ่งที่แก้ไปทั้งหมด

ไฟล์เดิมที่อัปโหลดมามีปัญหาหลักๆ คือ **หน้าเว็บกับฐานข้อมูลไม่ตรงกัน และหน้าเว็บส่วนใหญ่เป็นข้อมูลปลอม (mock) ที่ไม่ได้เชื่อมกับเซิร์ฟเวอร์เลย** เช่น:

- `main.py` เขียน SQL อ้างชื่อคอลัมน์ที่ไม่มีจริงในฐานข้อมูล (`queue_number` ที่จริงคือ `queue_no`, `total_price` ที่จริงคือ `total_amount`, `finance_transactions.type` ที่จริงคือ `transaction_type`, `vehicles.type/size` ที่จริงคือ `category/size_code`, `service_prices.vehicle_type` ที่จริงคือ `vehicle_category` ฯลฯ) — รันแล้วจะ error ทันที
- ไม่มีระบบ session/login guard เลย ใครก็เปิด `/staff`, `/finance`, `/pos` ได้โดยไม่ต้องล็อกอิน
- หน้า PIN พนักงานไม่เคยเช็คกับฐานข้อมูลจริง (พนักงานทุกคนไม่มี `pin_hash` ตั้งไว้ด้วยซ้ำ)
- `index.html` (แดชบอร์ด), `staff.html`, `finance.html`, `track.html` ทั้งหมด hardcode ข้อมูลปลอมไว้ใน JavaScript ปุ่มต่างๆ แค่ย้าย DOM ไปมา ไม่ได้บันทึกอะไรลงฐานข้อมูลจริง
- `pos.html` คำนวณราคาจากตารางราคาที่ hardcode ไว้ในหน้าเว็บ (ไม่ตรงกับราคาจริงในฐานข้อมูล) และกดบันทึกแล้วแค่ `alert()` ไม่ได้สร้างคิวจริง

**สิ่งที่ทำใหม่ทั้งหมด:**

1. เขียน `backend/main.py` ใหม่ทั้งหมดให้ตรงกับ schema จริงใน `database/dww.sql`
2. เพิ่มระบบ login แบบ session จริง (ผู้จัดการ = username/password, พนักงาน = เลือกชื่อ + PIN) พร้อม guard ป้องกันหน้าที่ต้องล็อกอินก่อน
3. หน้าเว็บทุกหน้าเปลี่ยนจาก mock data → เรียก API จริงด้วย `fetch()` ทั้งหมด (โหลดข้อมูล, บันทึก, แก้ไข, ลบ)
4. หน้าแดชบอร์ด (`/`) และหน้าติดตามคิว (`/track`) จะโพลข้อมูลอัตโนมัติ (auto-refresh) ทุก 6-8 วินาที เพื่อให้เห็นสถานะล่าสุดแบบเรียลไทม์
5. POS ดึงราคาจริงจากตาราง `service_prices` ตาม ประเภทรถ+ขนาดรถ ที่เลือก และตรวจสอบราคาซ้ำฝั่งเซิร์ฟเวอร์ก่อนบันทึก (กันลูกค้า/พนักงานแก้ราคาจาก DevTools)
6. เมื่อปิดบิลสำเร็จ ระบบจะสร้างข้อมูลครบทุกตาราง (customers → vehicles → service_orders → service_order_items → payments → finance_transactions) โดยอัตโนมัติในทรานแซกชันเดียว
7. หน้าบัญชีการเงินคำนวณยอดจริงจาก `finance_transactions` ตามช่วงเวลาที่เลือก (วัน/เดือน/ปี/กำหนดเอง) และเพิ่มปุ่มบันทึกรายจ่ายได้จริง

---

## โครงสร้างโปรเจกต์

```
vcarcare/
├── backend/
│   ├── main.py            <- Flask server (แก้ใหม่ทั้งหมด)
│   └── requirements.txt
├── frontend/
│   ├── templates/         <- ไฟล์ .html ทั้งหมด (Jinja2, เชื่อม API จริง)
│   └── static/css/style.css
├── database/
│   └── dww.sql             <- schema + ข้อมูลตัวอย่างเดิมของคุณ (ใช้ตัวนี้ import เข้า PostgreSQL)
└── README.md
```

## วิธีติดตั้งและรัน

### 1) เตรียมฐานข้อมูล PostgreSQL

```bash
createdb v_carcare
psql -U postgres -d v_carcare -f database/dww.sql
```

(ถ้าใช้ pgAdmin ก็เปิดไฟล์ `database/dww.sql` แล้วรันได้เลย)

### 2) ติดตั้งไลบรารี Python

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3) ตั้งค่าการเชื่อมต่อฐานข้อมูล

ตั้งค่าผ่าน environment variables (แนะนำ ไม่ต้อง hardcode รหัสผ่านในโค้ด):

```bash
export DB_HOST=localhost
export DB_NAME=v_carcare
export DB_USER=postgres
export DB_PASSWORD=รหัสผ่านจริงของคุณ
export DB_PORT=5432
export SECRET_KEY=สุ่มข้อความยาวๆ-สำหรับ-session
```

(Windows PowerShell ใช้ `$env:DB_PASSWORD="..."` แทน)

ถ้าไม่ตั้งค่าอะไรเลย ระบบจะพยายามต่อ `localhost` / db `v_carcare` / user `postgres` / password `postgres` เป็นค่าเริ่มต้น

### 4) รันเซิร์ฟเวอร์

```bash
python main.py
```

เปิดเบราว์เซอร์ไปที่ `http://localhost:5000`

### 5) ตั้งค่าบัญชีเริ่มต้น (ทำครั้งเดียว)

- เปิด `http://localhost:5000/setup-admin` เพื่อสร้างบัญชีผู้จัดการ `admin` / `admin123`
  (ถ้า import `dww.sql` ไปแล้ว บัญชี admin มีอยู่แล้วในข้อมูลตัวอย่าง — เปิด route นี้ก็ได้ ระบบจะแจ้งว่ามีอยู่แล้ว)
- เปิด `http://localhost:5000/setup-staff-pins` เพื่อตั้งรหัส PIN เริ่มต้น `1234` ให้พนักงานทุกคนที่ยังไม่เคยตั้ง PIN
  (ข้อมูลตัวอย่างเดิมของคุณไม่มีการตั้ง `pin_hash` ไว้เลย จึงต้องรันขั้นตอนนี้ก่อน ไม่งั้นพนักงานจะล็อกอินไม่ได้)

หลังจากนั้นเข้าสู่ระบบได้ที่หน้า `/login`:
- **ผู้จัดการ:** username `admin` / password `admin123`
- **พนักงาน:** เลือกชื่อจาก dropdown แล้วกด PIN `1234`

> อย่าลืมเปลี่ยนรหัสผ่านและ PIN จริงในระบบ production, และเปลี่ยน `SECRET_KEY` เป็นค่าที่สุ่มปลอดภัย

---

## สรุป API ทั้งหมดที่พร้อมใช้งาน

| Method | Path | คำอธิบาย | สิทธิ์ |
|---|---|---|---|
| GET/POST | `/login` | หน้า/ทำการล็อกอิน | ทุกคน |
| GET | `/logout` | ออกจากระบบ (เช็คเอาต์พนักงานอัตโนมัติ) | ต้องล็อกอิน |
| GET | `/api/orders?status=` | ดึงรายการคิวงาน | ต้องล็อกอิน |
| POST | `/api/orders` | เปิดบิล/สร้างคิวใหม่ (POS) | ต้องล็อกอิน |
| PUT | `/api/orders/<id>/status` | เปลี่ยนสถานะคิว | ต้องล็อกอิน |
| GET | `/api/track/<queue_no>` | ลูกค้าติดตามสถานะ (ไม่ต้องล็อกอิน) | สาธารณะ |
| GET | `/api/services?category=&size=` | ราคาบริการตามประเภท/ขนาดรถ | ต้องล็อกอิน |
| GET | `/api/staff?all=true` | รายชื่อพนักงาน + สถานะเข้างานวันนี้ | ต้องล็อกอิน |
| POST | `/api/staff` | เพิ่มพนักงานใหม่ (สร้างบัญชีล็อกอินให้อัตโนมัติ) | ผู้จัดการ |
| PUT/DELETE | `/api/staff/<id>` | แก้ไข/ปิดใช้งานพนักงาน | ผู้จัดการ |
| POST | `/api/staff/attendance` | เช็คอิน/เช็คเอาต์ | ต้องล็อกอิน |
| GET | `/api/finance/summary?period=` | สรุปบัญชีตามช่วงเวลา | ผู้จัดการ |
| POST | `/api/finance/transactions` | เพิ่มรายรับ/รายจ่ายเอง | ผู้จัดการ |

ทุกหน้าเว็บ (`index.html`, `pos.html`, `staff.html`, `finance.html`, `track.html`) เรียกใช้ endpoint พวกนี้ผ่าน `fetch()` จริงแล้ว ข้อมูลจะไหลจาก POS → ฐานข้อมูล → แดชบอร์ด/บัญชี/หน้าติดตามคิว โดยอัตโนมัติ
