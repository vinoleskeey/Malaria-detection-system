# Admin Portal - Malaria Detection System

## Task: Create a separate admin/hospital staff portal

## Plan Completed:

### 1. Admin App (app_admin.py) ✅
- [x] Create new Flask app for admin portal
- [x] Separate database (uses same database with role-based access)
- [x] Admin-specific authentication (register/login)
- [x] Routes for admin dashboard
- [x] Account switching routes

### 2. Admin Templates ✅
- [x] Admin login page (templates/admin/login.html)
- [x] Admin register page (templates/admin/register.html)
- [x] Admin dashboard with patient switching (templates/admin/dashboard.html)

### 3. Account Switching Features ✅
- [x] Switch to patient account route
- [x] Switch to staff account route
- [x] Switch back to admin route
- [x] UI dropdown in admin dashboard for switching

### 4. Features Implemented:
- Separate admin login at /admin/login
- Separate admin register at /admin/register
- Admin dashboard at /admin/dashboard
- Account switcher dropdown to view patient accounts
- Account switcher dropdown to view staff accounts
- "Back to Admin" link when viewing as patient/staff
- Link back to main patient portal

## How to Run:

1. Run the main app (patient portal): `python app.py`
   - Main portal at: http://localhost:5000

2. Run the admin app: `python app_admin.py`
   - Admin portal at: http://localhost:5001

## Default Admin Account:
- Email: victorshittu17@gmail.com
- Password: admin123
