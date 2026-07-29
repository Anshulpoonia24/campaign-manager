# Architecture Simplification - Code Changes

## Implementation Plan

### Phase 1: Core Infrastructure Changes
1. **utils/db.py** - Simplify workspace-related functions
2. **utils/init_db.py** - Remove workspace creation logic
3. **utils/pg_schema.py** - Remove workspace columns from schema
4. **services/workspace_service.py** - Simplify to single workspace
5. **services/ownership.py** - Simplify ownership checks

### Phase 2: Application Core Changes
6. **app.py** - Remove workspace_id from User class and settings
7. **routes/auth.py** - Simplify authentication
8. **routes/settings.py** - Simplify to global settings

### Phase 3: Admin Panel Cleanup
9. **routes/admin.py** - Remove tenant management routes
10. **templates/admin/dashboard.html** - Simplify to single-app dashboard
11. **templates/admin/ai_config.html** - Simplify AI config

### Phase 4: Service Layer Updates
12. **services/campaign_executor.py** - Remove workspace_id from campaign execution
13. **services/lead_scoring.py** - Remove workspace_id from scoring
14. **services/tracking.py** - Remove workspace_id from tracking
15. **services/automation_service.py** - Remove workspace_id from automation

### Phase 5: Route Updates
16. **routes/campaigns.py** - Remove workspace_id filtering
17. **routes/contacts.py** - Remove workspace_id filtering
18. **routes/inbox.py** - Remove workspace_id filtering
19. **routes/analytics.py** - Remove workspace_id filtering

### Phase 6: Template Updates
20. Remove: templates/admin/tenants.html
21. Remove: templates/admin/create_tenant.html
22. Remove: templates/admin/tenant_detail.html
23. Update: templates/admin/login.html
24. Update: templates/settings.html

### Phase 7: Task Updates
25. **tasks/email_tasks.py** - Remove workspace_id from Celery tasks
26. **tasks/enrichment_tasks.py** - Remove workspace_id
27. **tasks/automation_tasks.py** - Remove workspace_id

### Phase 8: Cleanup
28. Remove workspace-related imports
29. Update progress.md with changes
29. Create summary document

## Key Principles
- Keep workspace_id columns in database (for now)
- Simply ignore workspace_id in application code
- Use hardcoded workspace_id=1 or omit it entirely
- Remove all tenant management UI
- Simplify to single admin account
