"""
services/copilot/context_builder.py — Rich Context Assembly
=============================================================
Builds multi-layer context for AI prompt injection.
Layers: page → workspace snapshot → alerts → memory
"""
from utils.db import get_db
from utils.logger import error_logger


class ContextBuilder:
    def __init__(self, workspace_id: int, user_id: int):
        self.wid = workspace_id
        self.uid = user_id

    def build(self, page_type: str, page_id: int) -> dict:
        ctx = {
            'page': self._page_context(page_type, page_id),
            'workspace': self._workspace_snapshot(),
            'alerts': self._active_alerts(),
        }
        return ctx

    # ── PAGE CONTEXT ──────────────────────────────────────────

    def _page_context(self, page_type: str, page_id: int) -> dict:
        handlers = {
            'dashboard': self._ctx_dashboard,
            'campaign_status': self._ctx_campaign_status,
            'campaign_detail': self._ctx_campaign_detail,
            'inbox_thread': self._ctx_inbox_thread,
            'inbox': self._ctx_inbox,
            'contacts': self._ctx_contacts,
            'sequence_builder': self._ctx_sequence,
            'deliverability': self._ctx_deliverability,
            'analytics': self._ctx_analytics,
        }
        handler = handlers.get(page_type)
        if handler:
            try:
                return handler(page_id)
            except Exception as e:
                error_logger.error(f'[COPILOT] context build error: {e}')
        return {'page_type': page_type, 'page_id': page_id}

    def _ctx_dashboard(self, _):
        conn = get_db()
        ctx = {
            'page_type': 'dashboard',
            'total_sent': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=?", (self.wid,)).fetchone()[0],
            'total_contacts': conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (self.wid,)).fetchone()[0],
            'total_bounced': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed') AND workspace_id=?", (self.wid,)).fetchone()[0],
            'total_opened': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1 AND workspace_id=?", (self.wid,)).fetchone()[0],
            'total_replied': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1 AND workspace_id=?", (self.wid,)).fetchone()[0],
            'unread_threads': conn.execute("SELECT COUNT(*) FROM threads WHERE unread_count>0 AND workspace_id=?", (self.wid,)).fetchone()[0],
            'active_campaigns': conn.execute("SELECT COUNT(*) FROM campaigns WHERE job_status='running' AND workspace_id=?", (self.wid,)).fetchone()[0],
            'hot_leads': conn.execute("SELECT COUNT(*) FROM contacts WHERE lead_score>=50 AND workspace_id=?", (self.wid,)).fetchone()[0],
        }
        conn.close()
        return ctx

    def _ctx_campaign_status(self, campaign_id):
        conn = get_db()
        camp = conn.execute(
            "SELECT id, name, job_status, send_mode, total_contacts, sent_count, failed_count, started_at, completed_at "
            "FROM campaigns WHERE id=? AND workspace_id=?", (campaign_id, self.wid)
        ).fetchone()
        if not camp:
            conn.close()
            return {'page_type': 'campaign_status', 'error': 'not_found'}

        failures = conn.execute(
            "SELECT c.name, c.email, es.bounce_reason FROM emails_sent es "
            "JOIN contacts c ON es.contact_id=c.id "
            "WHERE es.campaign_id=? AND es.status IN ('failed','bounced') "
            "ORDER BY es.sent_at DESC LIMIT 5", (campaign_id,)
        ).fetchall()

        smtp = conn.execute(
            "SELECT email, health_score, sent_today, daily_limit, active "
            "FROM smtp_accounts WHERE workspace_id=? AND active=1", (self.wid,)
        ).fetchall()

        logs = conn.execute(
            "SELECT level, message, created_at FROM campaign_logs "
            "WHERE campaign_id=? ORDER BY created_at DESC LIMIT 8", (campaign_id,)
        ).fetchall()

        conn.close()
        return {
            'page_type': 'campaign_status',
            'campaign': dict(camp),
            'recent_failures': [dict(f) for f in failures],
            'smtp_accounts': [dict(s) for s in smtp],
            'recent_logs': [dict(l) for l in logs],
        }

    def _ctx_campaign_detail(self, campaign_id):
        conn = get_db()
        camp = conn.execute(
            "SELECT id, name, status, send_mode, total_contacts, sent_count, failed_count "
            "FROM campaigns WHERE id=? AND workspace_id=?", (campaign_id, self.wid)
        ).fetchone()
        if not camp:
            conn.close()
            return {'page_type': 'campaign_detail', 'error': 'not_found'}

        available = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE email_valid=1 AND workspace_id=? "
            "AND id NOT IN (SELECT contact_id FROM emails_sent WHERE campaign_id=? AND status='sent')",
            (self.wid, campaign_id)
        ).fetchone()[0]

        conn.close()
        return {
            'page_type': 'campaign_detail',
            'campaign': dict(camp),
            'available_contacts': available,
        }

    def _ctx_inbox_thread(self, thread_id):
        conn = get_db()
        thread = conn.execute("""
            SELECT t.id, t.status, t.subject, t.unread_count,
                   c.name as contact_name, c.company as contact_company,
                   c.email as contact_email, c.context as contact_context,
                   c.lead_score, c.status as contact_status
            FROM threads t
            LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.id=? AND t.workspace_id=?
        """, (thread_id, self.wid)).fetchone()
        if not thread:
            conn.close()
            return {'page_type': 'inbox_thread', 'error': 'not_found'}

        msgs = conn.execute("""
            SELECT direction, body, ai_category, created_at
            FROM messages WHERE thread_id=?
            ORDER BY created_at DESC LIMIT 6
        """, (thread_id,)).fetchall()

        conn.close()
        return {
            'page_type': 'inbox_thread',
            'thread': dict(thread),
            'messages': [
                {'direction': m['direction'], 'body': (m['body'] or '')[:300],
                 'ai_category': m['ai_category'], 'time': str(m['created_at'])}
                for m in reversed(msgs)
            ],
        }

    def _ctx_inbox(self, _):
        conn = get_db()
        ctx = {
            'page_type': 'inbox',
            'total_threads': conn.execute("SELECT COUNT(*) FROM threads WHERE workspace_id=?", (self.wid,)).fetchone()[0],
            'unread': conn.execute("SELECT COUNT(*) FROM threads WHERE unread_count>0 AND workspace_id=?", (self.wid,)).fetchone()[0],
            'interested': conn.execute("SELECT COUNT(*) FROM threads WHERE status='interested' AND workspace_id=?", (self.wid,)).fetchone()[0],
            'meeting': conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting' AND workspace_id=?", (self.wid,)).fetchone()[0],
        }
        conn.close()
        return ctx

    def _ctx_contacts(self, _):
        conn = get_db()
        ctx = {
            'page_type': 'contacts',
            'total': conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (self.wid,)).fetchone()[0],
            'valid': conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND email_valid=1", (self.wid,)).fetchone()[0],
            'invalid': conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND email_valid=0", (self.wid,)).fetchone()[0],
            'enriched': conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND context IS NOT NULL AND context!=''", (self.wid,)).fetchone()[0],
            'hot_leads': conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND lead_score>=50", (self.wid,)).fetchone()[0],
        }
        conn.close()
        return ctx

    def _ctx_sequence(self, campaign_id):
        conn = get_db()
        steps = conn.execute(
            "SELECT id, step_order, step_type, delay_days, subject, ai_enabled, active "
            "FROM sequence_steps WHERE campaign_id=? AND workspace_id=? ORDER BY step_order",
            (campaign_id, self.wid)
        ).fetchall()
        enrolled = conn.execute(
            "SELECT COUNT(*) FROM contact_sequence_state WHERE campaign_id=?", (campaign_id,)
        ).fetchone()[0]
        conn.close()
        return {
            'page_type': 'sequence_builder',
            'campaign_id': campaign_id,
            'steps': [dict(s) for s in steps],
            'enrolled_contacts': enrolled,
        }

    def _ctx_deliverability(self, _):
        conn = get_db()
        accounts = conn.execute(
            "SELECT id, email, health_score, warmup_stage, active, sent_today, daily_limit "
            "FROM smtp_accounts WHERE workspace_id=? ORDER BY active DESC, health_score DESC",
            (self.wid,)
        ).fetchall()
        total_sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=?", (self.wid,)).fetchone()[0]
        total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed') AND workspace_id=?", (self.wid,)).fetchone()[0]
        conn.close()
        return {
            'page_type': 'deliverability',
            'smtp_accounts': [dict(a) for a in accounts],
            'total_sent': total_sent,
            'total_bounced': total_bounced,
            'bounce_rate': round(total_bounced / total_sent * 100, 1) if total_sent else 0,
        }

    def _ctx_analytics(self, _):
        conn = get_db()
        ctx = {
            'page_type': 'analytics',
            'total_sent': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=?", (self.wid,)).fetchone()[0],
            'total_opened': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1 AND workspace_id=?", (self.wid,)).fetchone()[0],
            'total_replied': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1 AND workspace_id=?", (self.wid,)).fetchone()[0],
            'total_clicks': conn.execute("SELECT COUNT(*) FROM email_clicks WHERE workspace_id=?", (self.wid,)).fetchone()[0],
        }
        conn.close()
        return ctx

    # ── WORKSPACE SNAPSHOT ────────────────────────────────────

    def _workspace_snapshot(self) -> dict:
        conn = get_db()
        try:
            smtp_accounts = conn.execute(
                "SELECT health_score, active, sent_today, daily_limit FROM smtp_accounts WHERE workspace_id=?",
                (self.wid,)
            ).fetchall()
            active_smtp = [a for a in smtp_accounts if a['active']]
            avg_health = round(sum(a['health_score'] for a in active_smtp) / len(active_smtp)) if active_smtp else 0
            remaining_capacity = sum(max(0, a['daily_limit'] - a['sent_today']) for a in active_smtp)

            return {
                'smtp_accounts_active': len(active_smtp),
                'smtp_health_avg': avg_health,
                'daily_send_capacity_remaining': remaining_capacity,
                'campaigns_running': conn.execute("SELECT COUNT(*) FROM campaigns WHERE job_status='running' AND workspace_id=?", (self.wid,)).fetchone()[0],
            }
        except Exception:
            return {}
        finally:
            conn.close()

    # ── ALERTS ────────────────────────────────────────────────

    def _active_alerts(self) -> list:
        """Detect real-time issues that need attention."""
        alerts = []
        conn = get_db()
        try:
            # High bounce rate
            sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=?", (self.wid,)).fetchone()[0]
            bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed') AND workspace_id=?", (self.wid,)).fetchone()[0]
            if sent > 20 and bounced / sent > 0.05:
                alerts.append({'type': 'high_bounce', 'severity': 'critical',
                               'message': f'Bounce rate {round(bounced/sent*100,1)}% — risk of domain blacklisting'})

            # SMTP at risk
            sick_smtp = conn.execute(
                "SELECT email, health_score FROM smtp_accounts WHERE workspace_id=? AND active=1 AND health_score<40",
                (self.wid,)
            ).fetchall()
            for s in sick_smtp:
                alerts.append({'type': 'smtp_health', 'severity': 'warning',
                               'message': f'{s["email"]} health at {s["health_score"]}/100'})

            # Stalled campaigns
            stalled = conn.execute(
                "SELECT id, name FROM campaigns WHERE job_status='stalled' AND workspace_id=?", (self.wid,)
            ).fetchall()
            for c in stalled:
                alerts.append({'type': 'stalled_campaign', 'severity': 'critical',
                               'message': f'Campaign "{c["name"]}" stalled — worker may have died'})

            # Hot leads waiting
            hot_unread = conn.execute("""
                SELECT COUNT(*) FROM threads t
                JOIN contacts c ON t.contact_id=c.id
                WHERE t.workspace_id=? AND t.unread_count>0 AND c.lead_score>=50
            """, (self.wid,)).fetchone()[0]
            if hot_unread > 0:
                alerts.append({'type': 'hot_leads_waiting', 'severity': 'info',
                               'message': f'{hot_unread} hot lead(s) waiting for reply'})
        except Exception as e:
            error_logger.error(f'[COPILOT] alert detection error: {e}')
        finally:
            conn.close()
        return alerts
