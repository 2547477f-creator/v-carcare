import os
from flask import Flask, render_template, request, jsonify

# 1. จัดการ Path ให้ Flask หาโฟลเดอร์ frontend เจอถูกต้อง
backend_dir = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.join(backend_dir, '..', 'frontend')

app = Flask(
    __name__,
    template_folder=frontend_dir,  # ให้ Flask อ่านไฟล์ HTML จากโฟลเดอร์ frontend
    static_folder=frontend_dir     # ให้ Flask อ่านไฟล์ Static (CSS, JS, Images) จาก frontend
)

# ---------------------------------------------------------
# Page Routes (ส่วนการแสดงผลหน้าเว็บ HTML)
# ---------------------------------------------------------

@app.route('/')
def index():
    """หน้าหลัก / หน้าแรก"""
    return render_template('index.html')

@app.route('/pos')
def pos():
    """ระบบ POS บันทึกการขาย/บริการ"""
    return render_template('pos.html')

@app.route('/staff')
def staff():
    """ระบบลงเวลาพนักงาน (สแกนใบหน้า/Manual)"""
    return render_template('staff.html')

@app.route('/finance')
def finance():
    """แดชบอร์ดสรุปรายรับ-รายจ่าย"""
    return render_template('finance.html')

@app.route('/track')
def track():
    """ติดตามสถานะการล้างรถสำหรับลูกค้า"""
    return render_template('track.html')


# ---------------------------------------------------------
# API Endpoints (ส่วนจัดการข้อมูล backend)
# ---------------------------------------------------------

@app.route('/api/customers', methods=['POST'])
def add_customer():
    """API บันทึกข้อมูลลูกค้า (เบอร์โทร, LINE ID)"""
    data = request.json
    phone = data.get('phone')
    line_id = data.get('line_id')
    
    # TODO: เพิ่มโค้ดบันทึกลง Database (เช่น MongoDB / SQL)
    
    return jsonify({
        "status": "success",
        "message": "บันทึกข้อมูลลูกค้าเรียบร้อยแล้ว",
        "data": {"phone": phone, "line_id": line_id}
    }), 201


@app.route('/api/staff/check-in', methods=['POST'])
def staff_check_in():
    """API ลงเวลาพนักงาน (สแกนหน้า หรือ Manual)"""
    data = request.json
    employee_id = data.get('employee_id')
    method = data.get('method') # 'face_scan' หรือ 'manual'
    
    # TODO: เพิ่มโค้ดประมวลผลการเช็คอิน และส่งแจ้งเตือนผ่าน LINE Notify
    
    return jsonify({
        "status": "success",
        "message": f"ลงเวลาสำเร็จ ({method})",
        "employee_id": employee_id
    }), 200


# ---------------------------------------------------------
# Main Execution
# ---------------------------------------------------------
if __name__ == '__main__':
    # รันแอปพลิเคชัน Flask
    app.run(host='0.0.0.0', port=5000, debug=True)