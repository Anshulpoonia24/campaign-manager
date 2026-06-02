"""
services/copilot/ab_testing.py — A/B Testing Engine (Phase 8)
==============================================================
Test different email variants, track performance, pick winners.
"""
import json
import random
from datetime import datetime
from utils.db import get_db

try:
    from utils.logger import app_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')


def create_ab_test(workspace_id: int, campaign_id: int, test_type: str,
                   variants: list, split_pct: int = 50) -> dict:
    """Create an A/B test for a campaign.
    test_type: 'subject' | 'body' | 'send_time'
    variants: [{'name':'A','value':'...'}, {'name':'B','value':'...'}]
    """
    conn = get_db()
    conn.execute("""
        INSERT INTO ab_tests (workspace_id, campaign_id, test_type, variants, split_pct, status, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (workspace_id, campaign_id, test_type, json.dumps(variants), split_pct, 'active', datetime.now()))
    conn.commit()
    test_id = conn.execute("SELECT id FROM ab_tests WHERE workspace_id=? ORDER BY id DESC LIMIT 1", (workspace_id,)).fetchone()
    conn.close()
    return {'test_id': test_id[0] if test_id else 0, 'status': 'active', 'variants': len(variants)}


def get_variant_for_contact(workspace_id: int, campaign_id: int, contact_id: int) -> dict:
    """Pick which variant to send to a contact (deterministic by contact_id)."""
    conn = get_db()
    test = conn.execute("""
        SELECT id, variants, split_pct FROM ab_tests
        WHERE workspace_id=? AND campaign_id=? AND status='active' LIMIT 1
    """, (workspace_id, campaign_id)).fetchone()
    conn.close()
    if not test:
        return {'variant': None}
    variants = json.loads(test['variants'])
    # Deterministic assignment based on contact_id
    idx = contact_id % len(variants)
    return {'test_id': test['id'], 'variant': variants[idx], 'variant_index': idx}


def record_variant_event(test_id: int, variant_index: int, event_type: str):
    """Record an open/reply/click for a variant."""
    conn = get_db()
    conn.execute("""
        INSERT INTO ab_test_events (test_id, variant_index, event_type, created_at)
        VALUES (?,?,?,?)
    """, (test_id, variant_index, event_type, datetime.now()))
    conn.commit()
    conn.close()


def get_test_results(workspace_id: int, test_id: int) -> dict:
    """Get A/B test results with winner detection."""
    conn = get_db()
    test = conn.execute("SELECT * FROM ab_tests WHERE id=? AND workspace_id=?", (test_id, workspace_id)).fetchone()
    if not test:
        conn.close()
        return {'error': 'Test not found'}

    variants = json.loads(test['variants'])
    results = []
    for idx, v in enumerate(variants):
        sent = conn.execute("SELECT COUNT(*) FROM ab_test_events WHERE test_id=? AND variant_index=? AND event_type='sent'", (test_id, idx)).fetchone()[0]
        opened = conn.execute("SELECT COUNT(*) FROM ab_test_events WHERE test_id=? AND variant_index=? AND event_type='open'", (test_id, idx)).fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM ab_test_events WHERE test_id=? AND variant_index=? AND event_type='reply'", (test_id, idx)).fetchone()[0]
        clicked = conn.execute("SELECT COUNT(*) FROM ab_test_events WHERE test_id=? AND variant_index=? AND event_type='click'", (test_id, idx)).fetchone()[0]
        results.append({
            'variant': v['name'],
            'value': v['value'][:80],
            'sent': sent, 'opened': opened, 'replied': replied, 'clicked': clicked,
            'open_rate': round(opened / max(1, sent) * 100, 1),
            'reply_rate': round(replied / max(1, sent) * 100, 1),
        })
    conn.close()

    # Determine winner
    winner = None
    if all(r['sent'] >= 10 for r in results):
        winner = max(results, key=lambda r: r['open_rate'] * 0.6 + r['reply_rate'] * 0.4)

    return {
        'test_id': test_id, 'test_type': test['test_type'],
        'status': test['status'], 'results': results,
        'winner': winner['variant'] if winner else None,
        'confidence': 'high' if all(r['sent'] >= 30 for r in results) else 'low',
    }


def end_test(workspace_id: int, test_id: int, winning_variant: int = None) -> dict:
    """End a test and optionally apply the winner."""
    conn = get_db()
    conn.execute("UPDATE ab_tests SET status='completed' WHERE id=? AND workspace_id=?", (test_id, workspace_id))
    conn.commit()
    conn.close()
    return {'message': 'Test completed', 'winner_applied': winning_variant is not None}


def list_tests(workspace_id: int) -> list:
    """List all A/B tests for workspace."""
    conn = get_db()
    tests = conn.execute("""
        SELECT t.id, t.campaign_id, t.test_type, t.status, t.created_at, c.name as campaign_name
        FROM ab_tests t LEFT JOIN campaigns c ON t.campaign_id = c.id
        WHERE t.workspace_id=? ORDER BY t.created_at DESC LIMIT 20
    """, (workspace_id,)).fetchall()
    conn.close()
    return [dict(t) for t in tests]
