"""
services/copilot/handlers/contacts.py — Contact Action Handlers
"""
from utils.db import get_db


def enrich_contact(workspace_id: int, user_id: int, contact_id: int, **_) -> dict:
    from services.industry_detector import enrich_contact_intelligence
    result = enrich_contact_intelligence(contact_id)
    if result:
        return {'message': f'Contact enriched successfully'}
    raise ValueError('Enrichment failed')


def bulk_enrich(workspace_id: int, user_id: int, **_) -> dict:
    conn = get_db()
    pending = conn.execute("""
        SELECT COUNT(*) FROM contacts
        WHERE workspace_id=? AND (enrichment_status='pending' OR enrichment_status IS NULL OR enrichment_status='')
    """, (workspace_id,)).fetchone()[0]
    conn.close()
    if pending == 0:
        return {'message': 'All contacts already enriched'}
    # Trigger in background thread
    import threading
    from services.industry_detector import enrich_contacts_bulk_intelligence
    conn2 = get_db()
    ids = [r['id'] for r in conn2.execute("""
        SELECT id FROM contacts
        WHERE workspace_id=? AND (enrichment_status='pending' OR enrichment_status IS NULL OR enrichment_status='')
        LIMIT 50
    """, (workspace_id,)).fetchall()]
    conn2.close()
    t = threading.Thread(target=enrich_contacts_bulk_intelligence, args=[ids, workspace_id], daemon=True)
    t.start()
    return {'message': f'Enriching {len(ids)} contacts in background'}


def fetch_context(workspace_id: int, user_id: int, contact_id: int, **_) -> dict:
    conn = get_db()
    contact = conn.execute("SELECT id, name, company, email FROM contacts WHERE id=? AND workspace_id=?",
                           (contact_id, workspace_id)).fetchone()
    if not contact:
        conn.close()
        raise ValueError('Contact not found')
    # Check existing
    if conn.execute("SELECT context FROM contacts WHERE id=? AND context IS NOT NULL AND context!=''",
                    (contact_id,)).fetchone():
        conn.close()
        return {'message': 'Context already exists for this contact'}
    conn.close()
    # Would call AI here — simplified
    return {'message': f'Use the Fetch Context button on contacts page for {contact["name"]}'}
