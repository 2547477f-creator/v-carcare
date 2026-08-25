document.addEventListener('DOMContentLoaded', () => {
    const nav = document.querySelector('.sidebar .nav');
    if (!nav || nav.querySelector('.management-menu')) return;
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
});
