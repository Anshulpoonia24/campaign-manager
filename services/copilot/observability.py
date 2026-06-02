"""
services/copilot/observability.py — Observability & Monitoring (Phase 10)
==========================================================================
AI usage tracking, cost estimation, latency monitoring, error rates.
"""
import time
import json
from datetime import datetime, timedelta
from collections import defaultdict
from utils.db import get_db

try:
    from utils.logger import app_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')


# ── IN-MEMORY METRICS ─────────────────────────────────────────
_metrics = {
    'requests': [],        # [{timestamp, latency_ms, model, tokens_est, success}]
    'errors': [],          # [{timestamp, error_type, message}]
    'costs': defaultdict(float),  # {date_str: estimated_cost}
}

# Cost per 1M tokens (estimates)
COST_PER_M_TOKENS = {
    'llama-3.3-70b-versatile': 0.59,
    'gemini-2.0-flash': 0.075,
    'default': 0.50,
}


def record_ai_call(model: str, latency_ms: int, tokens_est: int, success: bool):
    """Record an AI API call for monitoring."""
    entry = {
        'timestamp': datetime.now().isoformat(),
        'model': model,
        'latency_ms': latency_ms,
        'tokens_est': tokens_est,
        'success': success,
    }
    _metrics['requests'].append(entry)
    # Keep last 1000
    if len(_metrics['requests']) > 1000:
        _metrics['requests'] = _metrics['requests'][-1000:]
    # Estimate cost
    cost_per_token = COST_PER_M_TOKENS.get(model, COST_PER_M_TOKENS['default']) / 1_000_000
    cost = tokens_est * cost_per_token
    date_key = datetime.now().strftime('%Y-%m-%d')
    _metrics['costs'][date_key] += cost


def record_error(error_type: str, message: str):
    """Record an error event."""
    _metrics['errors'].append({
        'timestamp': datetime.now().isoformat(),
        'error_type': error_type,
        'message': message[:200],
    })
    if len(_metrics['errors']) > 500:
        _metrics['errors'] = _metrics['errors'][-500:]


# ── DASHBOARD DATA ────────────────────────────────────────────

def get_overview(workspace_id: int = None) -> dict:
    """Get monitoring overview dashboard data."""
    now = datetime.now()
    last_hour = (now - timedelta(hours=1)).isoformat()
    last_24h = (now - timedelta(hours=24)).isoformat()

    recent = [r for r in _metrics['requests'] if r['timestamp'] >= last_hour]
    daily = [r for r in _metrics['requests'] if r['timestamp'] >= last_24h]

    # Latency stats
    latencies = [r['latency_ms'] for r in recent if r['success']]
    avg_latency = round(sum(latencies) / max(1, len(latencies)), 0) if latencies else 0
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 5 else avg_latency

    # Success rate
    total_recent = len(recent)
    success_recent = sum(1 for r in recent if r['success'])
    success_rate = round(success_recent / max(1, total_recent) * 100, 1)

    # Cost
    today = now.strftime('%Y-%m-%d')
    today_cost = _metrics['costs'].get(today, 0)

    # Errors last hour
    recent_errors = [e for e in _metrics['errors'] if e['timestamp'] >= last_hour]

    return {
        'last_hour': {
            'requests': total_recent,
            'avg_latency_ms': int(avg_latency),
            'p95_latency_ms': int(p95_latency),
            'success_rate': success_rate,
            'errors': len(recent_errors),
        },
        'last_24h': {
            'requests': len(daily),
            'total_tokens': sum(r['tokens_est'] for r in daily),
            'estimated_cost_usd': round(today_cost, 4),
        },
        'models_used': _model_breakdown(daily),
        'recent_errors': recent_errors[-5:],
    }


def _model_breakdown(requests: list) -> dict:
    breakdown = defaultdict(lambda: {'count': 0, 'avg_latency': 0, 'total_latency': 0})
    for r in requests:
        m = r['model']
        breakdown[m]['count'] += 1
        breakdown[m]['total_latency'] += r['latency_ms']
    for m in breakdown:
        breakdown[m]['avg_latency'] = int(breakdown[m]['total_latency'] / max(1, breakdown[m]['count']))
        del breakdown[m]['total_latency']
    return dict(breakdown)


def get_cost_history(days: int = 7) -> list:
    """Get daily cost estimates."""
    history = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        history.append({'date': date, 'cost_usd': round(_metrics['costs'].get(date, 0), 4)})
    return list(reversed(history))


def get_latency_histogram() -> dict:
    """Get latency distribution."""
    buckets = {'<100ms': 0, '100-300ms': 0, '300-1000ms': 0, '1-3s': 0, '>3s': 0}
    for r in _metrics['requests'][-200:]:
        ms = r['latency_ms']
        if ms < 100: buckets['<100ms'] += 1
        elif ms < 300: buckets['100-300ms'] += 1
        elif ms < 1000: buckets['300-1000ms'] += 1
        elif ms < 3000: buckets['1-3s'] += 1
        else: buckets['>3s'] += 1
    return buckets


def get_usage_by_workspace() -> list:
    """Get usage grouped by workspace (from DB)."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT workspace_id, COUNT(*) as total_chats,
                   COUNT(CASE WHEN created_at >= datetime('now','-1 day') THEN 1 END) as today
            FROM copilot_logs GROUP BY workspace_id ORDER BY total_chats DESC LIMIT 20
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        conn.close()
        return []


def health_check() -> dict:
    """System health check."""
    checks = {}
    # DB connection
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        checks['database'] = 'ok'
    except Exception as e:
        checks['database'] = f'error: {str(e)[:50]}'

    # Recent AI success rate
    recent = _metrics['requests'][-20:]
    if recent:
        rate = sum(1 for r in recent if r['success']) / len(recent) * 100
        checks['ai_api'] = 'ok' if rate >= 80 else f'degraded ({rate:.0f}% success)'
    else:
        checks['ai_api'] = 'no data'

    # Error rate
    recent_errors = [e for e in _metrics['errors'] if e['timestamp'] >= (datetime.now() - timedelta(minutes=5)).isoformat()]
    checks['error_rate'] = f'{len(recent_errors)} errors in last 5min'

    overall = 'healthy' if all(v == 'ok' or v == 'no data' for v in checks.values()) else 'degraded'
    return {'status': overall, 'checks': checks, 'timestamp': datetime.now().isoformat()}
