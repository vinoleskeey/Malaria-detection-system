# TODO - Role-Based Access Control Implementation

## Phase 1: Database & Authentication Updates
- [x] 1.1 Update users table - add role column (patient/staff/admin)
- [x] 1.2 Update patients table - add user_id foreign key
- [x] 1.3 Modify registration to include role selection
- [x] 1.4 Update login to store user role in session

## Phase 2: Access Control Decorators
- [x] 2.1 Create staff_required decorator
- [x] 2.2 Create patient_required decorator
- [x] 2.3 Apply decorators to routes

## Phase 3: Route Modifications
- [x] 3.1 Dashboard - show different content based on role
- [x] 3.2 Patients - staff can view all, patients see only theirs
- [x] 3.3 Predictions - staff can run, patients can only view results
- [x] 3.4 Staff routes - staff only access

## Phase 4: Profile Page
- [x] 4.1 Create /profile route (GET/POST)
- [x] 4.2 Create profile.html template
- [x] 4.3 Add profile link to navbar

## Phase 5: Template Updates
- [x] 5.1 Update base.html navbar for role-based menu items
- [x] 5.2 Update register.html with role selection

## Phase 6: Deployment Configuration
- [x] 6.1 Create railway.json
- [x] 6.2 Verify Procfile

## Dependencies:
- No new Python packages needed
