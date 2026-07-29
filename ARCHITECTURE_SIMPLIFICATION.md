# Architecture Simplification Plan

## Overview
Remove multi-tenancy architecture and super admin concept. Simplify to single admin account with global settings.

## Changes Required

### 1. Database Schema Changes
- Remove `workspace_id` column from all tables
- Remove `workspaces` table entirely
- Remove `tenant_id` references
- Update all queries to not filter by workspace

### 2. User Model Changes
- Remove `workspace_id` from User class
- Simplify to single admin user
- Remove superadmin user creation

### 3. Settings Changes
- Remove `workspace_id` from settings table
- Make all settings global (single row per key)
- Update `get_setting()` and `set_setting()` to not use workspace_id

### 4. Routes to Remove/Modify
- **admin.py**: Remove tenant management routes (create, delete, list, detail)
- Keep only AI config routes (but simplify to global settings)
- **auth.py**: Keep simple login/logout
- **settings.py**: Simplify to global settings management

### 5. Templates to Remove/Modify
- **admin/tenants.html**: Remove (tenant listing)
- **admin/create_tenant.html**: Remove (tenant creation)
- **admin/tenant_detail.html**: Remove (tenant detail)
- **admin/dashboard.html**: Simplify to single-app dashboard
- **admin/ai_config.html**: Keep but simplify (global AI config)

### 6. Services to Modify
- **workspace_service.py**: Simplify to always return 1 or remove workspace logic
- **ownership.py**: Simplify ownership checks (no workspace filtering)

### 7. Background Tasks
- Update all Celery tasks to not pass workspace_id
- Update all background workers to not use workspace context

### 8. Tracking
- Update tracking tokens to not include workspace_id
- Update tracking_events table to remove workspace_id

### 9. AI Configuration
- Move from per-workspace to global AI settings
- Single AI config section in settings
- No per-tenant AI keys

## Implementation Steps

### Phase 1: Database Migration
1. Create migration script to remove workspace_id columns
2. Drop workspaces table
3. Update all existing data

### Phase 2: Core Code Changes
1. Update utils/db.py - remove workspace filtering
2. Update services/workspace_service.py - simplify
3. Update services/ownership.py - simplify
4. Update app.py - remove workspace logic

### Phase 3: Routes Cleanup
1. Remove tenant management routes from admin.py
2. Simplify settings routes
3. Update all route handlers

### Phase 4: Template Cleanup
1. Remove tenant management templates
2. Simplify admin dashboard
3. Update navigation

### Phase 5: Testing
1. Test login/logout
2. Test campaign creation/sending
3. Test settings save/load
4. Test AI configuration

## Files to Modify

### Core Files
- `utils/db.py` - Remove workspace_id from queries
- `utils/init_db.py` - Remove workspace creation
- `utils/pg_schema.py` - Remove workspace columns
- `services/workspace_service.py` - Simplify
- `services/ownership.py` - Simplify
- `app.py` - Remove workspace logic

### Routes
- `routes/admin.py` - Remove tenant management
- `routes/settings.py` - Simplify to global settings
- `routes/auth.py` - Keep simple

### Templates
- Remove: `templates/admin/tenants.html`
- Remove: `templates/admin/create_tenant.html`
- Remove: `templates/admin/tenant_detail.html`
- Modify: `templates/admin/dashboard.html`
- Modify: `templates/admin/ai_config.html`

### Services
- `services/workspace_service.py` - Simplify to single workspace
- `services/ownership.py` - Simplify ownership checks

## New Architecture

```
Single Admin Account
├── One Login (/login)
├── One Dashboard (/dashboard)
├── Global Settings (no workspace isolation)
│   ├── AI Configuration (Groq, Gemini keys)
│   ├── SMTP Configuration
│   ├── IMAP Configuration
│   └── Tracking Configuration
├── All Data in Single Database
└── No Tenant Isolation
```

## Benefits
- Simpler deployment
- Easier to maintain
- No tenant management overhead
- Single point of configuration
- Reduced complexity
