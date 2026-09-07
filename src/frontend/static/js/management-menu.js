document.addEventListener('DOMContentLoaded', async () => {
    const nav = document.querySelector('.sidebar .nav');
    if (!nav) return;
    const isServiceManagement = window.location.pathname === '/service-management';
    let serviceMobileNav = null;
    if (isServiceManagement && window.innerWidth < 768) {
        serviceMobileNav = document.createElement('nav');
        serviceMobileNav.className = 'fb-nav-tabs d-flex d-md-none';
        serviceMobileNav.innerHTML = `
            <a href="/" class="fb-tab-item"><i class="bi bi-speedometer2"></i><span>ภาพรวม</span></a>
            <a href="/pos" class="fb-tab-item"><i class="bi bi-cart-check"></i><span>รับรถ</span></a>
            <a href="/register" class="fb-tab-item"><i class="bi bi-person-vcard"></i><span>ลงทะเบียน</span></a>
            <a href="/history" class="fb-tab-item"><i class="bi bi-clock-history"></i><span>ประวัติ</span></a>
            <a href="/staff" class="fb-tab-item"><i class="bi bi-people-fill"></i><span>พนักงาน</span></a>
            <a href="/staff-advances" class="fb-tab-item"><i class="bi bi-cash-stack"></i><span>เบิกเงิน</span></a>
            <a href="/finance" class="fb-tab-item"><i class="bi bi-wallet2"></i><span>บัญชี</span></a>
            <a href="/service-management" class="fb-tab-item active"><i class="bi bi-tags"></i><span>บริการ</span></a>
            <a href="/logout" class="fb-tab-item logout-link text-danger"><i class="bi bi-box-arrow-right"></i><span>ออก</span></a>`;
        document.body.appendChild(serviceMobileNav);
    }
    const logout = nav.querySelector('.logout-link');
    let faceScanAvailable = false;
    let canEnrollManagerFace = false;
    let currentRole = null;
    try {
        const response = await fetch('/api/face-scan-availability');
        const availability = response.ok ? await response.json() : {};
        faceScanAvailable = availability.available === true;
        canEnrollManagerFace = availability.can_enroll_manager_face === true;
        currentRole = availability.current_role;
    } catch (_) {}
    if (faceScanAvailable && logout && !nav.querySelector('.face-scan-link')) {
        const faceLink = document.createElement('a');
        faceLink.href = `/face-checkin?role=${encodeURIComponent(currentRole || 'staff')}`;
        faceLink.className = 'nav-link face-scan-link text-warning';
        faceLink.innerHTML = '<i class="bi bi-camera-fill me-2"></i> สแกนหน้าเช็กอิน';
        nav.insertBefore(faceLink, logout);
    }
    if (canEnrollManagerFace && logout && !nav.querySelector('.manager-face-enroll-link')) {
        const enrollLink = document.createElement('a');
        enrollLink.href = '/face-checkin?mode=enroll';
        enrollLink.className = 'nav-link manager-face-enroll-link text-info';
        enrollLink.innerHTML = '<i class="bi bi-person-bounding-box me-2"></i> ลงทะเบียนใบหน้าผู้จัดการ';
        nav.insertBefore(enrollLink, logout);
    }
    if (canEnrollManagerFace && logout && !nav.querySelector('.manager-change-link')) {
        const changeLink = document.createElement('a');
        changeLink.href = '/face-checkin?mode=verify-manager-change';
        changeLink.className = 'nav-link manager-change-link text-warning';
        changeLink.innerHTML = '<i class="bi bi-person-gear me-2"></i> เปลี่ยนผู้จัดการ';
        nav.insertBefore(changeLink, logout);
    }
    if (canEnrollManagerFace && logout && !nav.querySelector('.change-ip-link')) {
        const ipLink = document.createElement('a');
        ipLink.href = '/face-checkin?mode=change-ip';
        ipLink.className = 'nav-link change-ip-link text-info';
        ipLink.innerHTML = '<i class="bi bi-router-fill me-2"></i> ตั้งค่า IP เครื่องหลัก';
        nav.insertBefore(ipLink, logout);
    }
    // เมนูมือถือเป็นแถบล่างคนละชุดกับ Sidebar จึงต้องเพิ่มปุ่มแยกต่างหาก
    if (canEnrollManagerFace) {
        document.querySelectorAll('.fb-nav-tabs').forEach(mobileNav => {
            if (mobileNav.querySelector('.change-ip-link')) return;
            const mobileLogout = mobileNav.querySelector('.logout-link');
            if (!mobileLogout) return;
            const ipLink = document.createElement('a');
            ipLink.href = '/face-checkin?mode=change-ip';
            ipLink.className = 'fb-tab-item change-ip-link text-info';
            ipLink.innerHTML = '<i class="bi bi-router-fill"></i><span>ตั้งค่า IP</span>';
            mobileNav.insertBefore(ipLink, mobileLogout);
        });
    }
    // ปุ่มสแกนหน้าบนมือถือจะแสดงได้เฉพาะอุปกรณ์ที่เป็นเครื่องหลักเท่านั้น
    if (faceScanAvailable) {
        document.querySelectorAll('.fb-nav-tabs').forEach(mobileNav => {
            if (mobileNav.querySelector('.face-scan-link')) return;
            const mobileLogout = mobileNav.querySelector('.logout-link');
            if (!mobileLogout) return;
            const faceLink = document.createElement('a');
            faceLink.href = `/face-checkin?role=${encodeURIComponent(currentRole || 'staff')}`;
            faceLink.className = 'fb-tab-item face-scan-link text-warning';
            faceLink.innerHTML = '<i class="bi bi-camera-fill"></i><span>สแกนหน้า</span>';
            mobileNav.insertBefore(faceLink, mobileLogout);
        });
    }
    if (faceScanAvailable && serviceMobileNav && !serviceMobileNav.querySelector('.face-scan-link')) {
        const faceLink = document.createElement('a');
        faceLink.href = `/face-checkin?role=${encodeURIComponent(currentRole || 'staff')}`;
        faceLink.className = 'fb-tab-item face-scan-link text-warning';
        faceLink.innerHTML = '<i class="bi bi-camera-fill"></i><span>สแกนหน้า</span>';
        serviceMobileNav.insertBefore(faceLink, serviceMobileNav.querySelector('.logout-link'));
    }
    if (nav.querySelector('.management-menu')) return;
    const staff = nav.querySelector('a[href="/staff"]');
    const advances = nav.querySelector('a[href="/staff-advances"]');
    const services = nav.querySelector('a[href="/service-management"]');
    if (!staff || !advances || !services) return;
    if (getComputedStyle(staff).display === 'none') return;
    const activePath = window.location.pathname;
    const menu = document.createElement('details');
    menu.className = 'management-menu';
    menu.open = ['/staff', '/staff-advances', '/expense-management', '/income-management', '/service-management'].includes(activePath);
    menu.innerHTML = `<summary><i class="bi bi-gear-fill me-2"></i>การจัดการ <i class="bi bi-chevron-down menu-chevron"></i></summary><div class="management-submenu"><a href="/staff" class="${activePath === '/staff' ? 'active' : ''}"><i class="bi bi-people-fill me-2"></i>จัดการพนักงาน</a><a href="/staff-advances" class="${activePath === '/staff-advances' ? 'active' : ''}"><i class="bi bi-cash-stack me-2"></i>จัดการเบิก</a><a href="/income-management" class="${activePath === '/income-management' ? 'active' : ''}"><i class="bi bi-graph-up-arrow me-2"></i>จัดการรายรับ</a><a href="/expense-management" class="${activePath === '/expense-management' ? 'active' : ''}"><i class="bi bi-receipt-cutoff me-2"></i>จัดการรายจ่าย</a><a href="/service-management" class="${activePath === '/service-management' ? 'active' : ''}"><i class="bi bi-tags me-2"></i>จัดการบริการและโปรโมชัน</a></div>`;
    staff.remove(); advances.remove(); services.remove();
    nav.insertBefore(menu, nav.querySelector('a[href="/finance"]') || nav.lastElementChild);

    const serviceForm = document.getElementById('serviceForm');
    if (!serviceForm) return;
    const category = serviceForm.elements.category;
    const priceInput = serviceForm.elements.price;
    const sizeField = document.createElement('select');
    sizeField.name = 'size_code';
    sizeField.className = 'form-select form-select-custom';
    const sizeColumn = document.createElement('div');
    sizeColumn.className = 'col-md-2';
    sizeColumn.appendChild(sizeField);
    priceInput.closest('.col-md-3').before(sizeColumn);

    const setSizes = () => {
        const sizes = category.value === 'car' ? ['S', 'M', 'L', 'XL'] : ['S', 'M', 'L'];
        sizeField.innerHTML = sizes.map(size => `<option value="${size}">ขนาด ${size}</option>`).join('');
    };
    category.addEventListener('change', setSizes);
    setSizes();

    serviceForm.onsubmit = async event => {
        event.preventDefault();
        const form = new FormData(serviceForm);
        const selectedCategory = form.get('category');
        const selectedSize = form.get('size_code');
        const categories = selectedCategory === 'all' ? ['car', 'bike'] : [selectedCategory];
        const prices = categories.map(vehicle_category => ({ vehicle_category, size_code: selectedSize, price: Number(form.get('price')) }));
        const response = await fetch('/api/manage/services', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: form.get('name'), code: form.get('code'), category: selectedCategory, estimated_minutes: form.get('estimated_minutes'), prices }) });
        if (!response.ok) window.notify ? window.notify((await response.json()).message, 'danger') : alert('เพิ่มบริการไม่สำเร็จ');
        else { location.reload(); }
    };
});
