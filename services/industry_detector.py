"""
services/industry_detector.py — AI Industry Detection Engine
=============================================================
Detects industry, company size, country, technologies from:
- website content (homepage scrape)
- email domain
- company name
- AI analysis

Returns structured intelligence dict.
"""
import re
import json
from datetime import datetime
from utils.db import get_db, get_setting
from utils.logger import app_logger, error_logger

# ── INDUSTRY TAXONOMY ─────────────────────────────────────────
INDUSTRIES = [
    'AI / Machine Learning', 'SaaS', 'E-commerce', 'Healthcare',
    'Finance / Fintech', 'Education / EdTech', 'Marketing / Agency',
    'Real Estate', 'Manufacturing', 'Recruiting / HR',
    'Legal', 'Consulting', 'Media / Entertainment',
    'Logistics / Supply Chain', 'Cybersecurity', 'DevTools',
    'Gaming', 'Travel / Hospitality', 'Non-profit', 'Government',
    'Retail', 'Food & Beverage', 'Automotive', 'Energy / CleanTech',
    'Telecommunications', 'Other'
]

COMPANY_SIZES = [
    '1-10', '11-50', '51-200', '201-500', '501-1000',
    '1001-5000', '5000+'
]

# ── INDUSTRY COLOR MAP ────────────────────────────────────────
INDUSTRY_COLORS = {
    'AI / Machine Learning': {'bg': '#f5f3ff', 'color': '#6d28d9', 'border': '#ddd6fe'},
    'SaaS':                  {'bg': '#eff6ff', 'color': '#1d4ed8', 'border': '#bfdbfe'},
    'E-commerce':            {'bg': '#fff7ed', 'color': '#c2410c', 'border': '#fed7aa'},
    'Healthcare':            {'bg': '#fef2f2', 'color': '#dc2626', 'border': '#fecaca'},
    'Finance / Fintech':     {'bg': '#f0fdf4', 'color': '#15803d', 'border': '#bbf7d0'},
    'Education / EdTech':    {'bg': '#fefce8', 'color': '#ca8a04', 'border': '#fef08a'},
    'Marketing / Agency':    {'bg': '#fff7ed', 'color': '#ea580c', 'border': '#fed7aa'},
    'Real Estate':           {'bg': '#f0fdf4', 'color': '#166534', 'border': '#bbf7d0'},
    'Manufacturing':         {'bg': '#f8fafc', 'color': '#475569', 'border': '#e2e8f0'},
    'Recruiting / HR':       {'bg': '#fdf4ff', 'color': '#a21caf', 'border': '#f0abfc'},
    'Legal':                 {'bg': '#f8fafc', 'color': '#334155', 'border': '#e2e8f0'},
    'Consulting':            {'bg': '#eff6ff', 'color': '#2563eb', 'border': '#bfdbfe'},
    'Cybersecurity':         {'bg': '#fef2f2', 'color': '#b91c1c', 'border': '#fecaca'},
    'DevTools':              {'bg': '#f0f9ff', 'color': '#0369a1', 'border': '#bae6fd'},
    'Media / Entertainment': {'bg': '#fdf4ff', 'color': '#9333ea', 'border': '#e9d5ff'},
    'Logistics / Supply Chain': {'bg': '#f8fafc', 'color': '#64748b', 'border': '#e2e8f0'},
}
DEFAULT_COLOR = {'bg': '#f1f5f9', 'color': '#475569', 'border': '#e2e8f0'}


def get_industry_style(industry: str) -> dict:
    """Return CSS colors for an industry badge."""
    if not industry:
        return DEFAULT_COLOR
    for key in INDUSTRY_COLORS:
        if key.lower() in (industry or '').lower() or (industry or '').lower() in key.lower():
            return INDUSTRY_COLORS[key]
    return DEFAULT_COLOR


# ── DOMAIN-BASED QUICK DETECTION ─────────────────────────────
DOMAIN_HINTS = {
    'shopify': 'E-commerce', 'stripe': 'Finance / Fintech',
    'openai': 'AI / Machine Learning', 'anthropic': 'AI / Machine Learning',
    'salesforce': 'SaaS', 'hubspot': 'SaaS', 'notion': 'SaaS',
    'figma': 'SaaS', 'slack': 'SaaS', 'zoom': 'SaaS',
    'github': 'DevTools', 'gitlab': 'DevTools', 'vercel': 'DevTools',
    'aws': 'SaaS', 'azure': 'SaaS', 'google': 'SaaS',
    'hospital': 'Healthcare', 'clinic': 'Healthcare', 'health': 'Healthcare',
    'bank': 'Finance / Fintech', 'finance': 'Finance / Fintech',
    'school': 'Education / EdTech', 'university': 'Education / EdTech',
    'edu': 'Education / EdTech', 'academy': 'Education / EdTech',
    'agency': 'Marketing / Agency', 'media': 'Media / Entertainment',
    'realty': 'Real Estate', 'estate': 'Real Estate',
    'recruit': 'Recruiting / HR', 'talent': 'Recruiting / HR',
    'law': 'Legal', 'legal': 'Legal', 'attorney': 'Legal',
    'consult': 'Consulting', 'advisory': 'Consulting',
    'security': 'Cybersecurity', 'cyber': 'Cybersecurity',
    'game': 'Gaming', 'gaming': 'Gaming',
    'travel': 'Travel / Hospitality', 'hotel': 'Travel / Hospitality',
    'food': 'Food & Beverage', 'restaurant': 'Food & Beverage',
    'auto': 'Automotive', 'motor': 'Automotive',
    'energy': 'Energy / CleanTech', 'solar': 'Energy / CleanTech',
    'telecom': 'Telecommunications', 'wireless': 'Telecommunications',
}


def detect_industry_from_domain(domain: str) -> str | None:
    """Quick domain-based industry detection without AI."""
    domain_lower = domain.lower()
    for hint, industry in DOMAIN_HINTS.items():
        if hint in domain_lower:
            return industry
    return None


# ── WEBSITE SCRAPER ───────────────────────────────────────────
def scrape_website(domain: str) -> dict:
    """
    Scrape homepage for company intelligence.
    Returns dict with title, description, keywords, text.
    """
    import requests
    result = {'title': '', 'description': '', 'text': '', 'domain': domain}
    if not domain:
        return result
    for scheme in ['https://', 'http://']:
        try:
            r = requests.get(
                f'{scheme}{domain}',
                timeout=8,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; OutreachOS/1.0)'},
                allow_redirects=True
            )
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, 'html.parser')
                result['title'] = soup.title.string.strip() if soup.title else ''
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc:
                    result['description'] = meta_desc.get('content', '')[:300]
                meta_kw = soup.find('meta', attrs={'name': 'keywords'})
                if meta_kw:
                    result['keywords'] = meta_kw.get('content', '')[:200]
                # Get meaningful text
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                paras = [p.get_text().strip() for p in soup.find_all('p')[:8] if len(p.get_text().strip()) > 30]
                result['text'] = ' '.join(paras)[:600]
                return result
        except Exception:
            continue
    return result


# ── AI ANALYSIS ───────────────────────────────────────────────
def analyze_with_ai(contact_name: str, company: str, domain: str,
                    website_data: dict) -> dict:
    """
    Use AI to extract structured intelligence from website data.
    Returns dict with industry, company_size, country, description, technologies.
    """
    try:
        keys_str = get_setting('groq_api_keys') or ''
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        if not keys:
            return {}

        website_context = ''
        if website_data.get('title'):
            website_context += f"Title: {website_data['title']}\n"
        if website_data.get('description'):
            website_context += f"Description: {website_data['description']}\n"
        if website_data.get('text'):
            website_context += f"Content: {website_data['text'][:400]}\n"

        industries_list = ', '.join(INDUSTRIES)
        sizes_list = ', '.join(COMPANY_SIZES)

        prompt = f"""Analyze this company and return a JSON object with intelligence data.

Company: {company or 'Unknown'}
Domain: {domain or 'Unknown'}
Contact: {contact_name or 'Unknown'}
{f'Website data:{chr(10)}{website_context}' if website_context else ''}

Return ONLY a valid JSON object with these exact keys:
{{
  "industry": "one of: {industries_list}",
  "company_size": "one of: {sizes_list} or empty string",
  "country": "country name or empty string",
  "company_description": "1-2 sentence company summary under 100 words",
  "technologies": "comma-separated tech stack if detectable, else empty string",
  "employee_range": "estimated employee range or empty string",
  "icp_score": "0-100 score for B2B engineering staffing relevance"
}}

Rules:
- industry MUST be one of the provided options
- company_description should be factual and concise
- icp_score: high (70+) if tech company that hires engineers, low if not
- Return ONLY the JSON, no other text"""

        import requests as _req
        r = _req.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {keys[0]}', 'Content-Type': 'application/json'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 400,
                'temperature': 0.1,
            },
            timeout=20
        )
        if r.status_code == 200:
            content = r.json()['choices'][0]['message']['content'].strip()
            # Extract JSON from response
            json_match = re.search(r'\{[^{}]+\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return data
    except Exception as e:
        error_logger.warning(f'[INDUSTRY] AI analysis failed: {e}')
    return {}


# ── MAIN ENRICHMENT FUNCTION ──────────────────────────────────
def enrich_contact_intelligence(contact_id: int) -> dict:
    """
    Full intelligence enrichment for a contact.
    Scrapes website + AI analysis + stores results.
    Returns enriched data dict.
    """
    conn = get_db()
    try:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        if not contact:
            return {}

        # Mark as processing
        conn.execute(
            "UPDATE contacts SET enrichment_status='processing' WHERE id=?",
            (contact_id,)
        )
        conn.commit()

        email = contact['email'] or ''
        company = contact['company'] or ''
        name = contact['name'] or ''
        domain = ''

        # Extract domain from email or website
        if contact['website'] if 'website' in contact.keys() else '':
            website = contact['website']
            domain = re.sub(r'https?://', '', website).split('/')[0].strip()
        elif '@' in email:
            email_domain = email.split('@')[1]
            # Skip common free email providers
            free_providers = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                              'live.com', 'icloud.com', 'protonmail.com', 'aol.com'}
            if email_domain not in free_providers:
                domain = email_domain

        result = {
            'industry': contact['industry'] if 'industry' in contact.keys() else '',
            'company_size': '',
            'country': '',
            'company_description': contact['context'] if 'context' in contact.keys() else '',
            'technologies': '',
            'employee_range': '',
            'enrichment_status': 'enriched',
        }

        # Quick domain detection
        if domain:
            quick_industry = detect_industry_from_domain(domain)
            if quick_industry:
                result['industry'] = quick_industry

        # Scrape website
        website_data = {}
        if domain:
            website_data = scrape_website(domain)

        # AI analysis
        ai_data = analyze_with_ai(name, company, domain, website_data)
        if ai_data:
            if ai_data.get('industry') and ai_data['industry'] in INDUSTRIES:
                result['industry'] = ai_data['industry']
            if ai_data.get('company_size'):
                result['company_size'] = ai_data['company_size']
            if ai_data.get('country'):
                result['country'] = ai_data['country']
            if ai_data.get('company_description'):
                result['company_description'] = ai_data['company_description']
            if ai_data.get('technologies'):
                result['technologies'] = ai_data['technologies']
            if ai_data.get('employee_range'):
                result['employee_range'] = ai_data['employee_range']
            # Update lead score based on ICP
            icp_score = int(ai_data.get('icp_score', 0) or 0)
            if icp_score > 0:
                current_score = contact['lead_score'] if 'lead_score' in contact.keys() else 0
                new_score = min(500, (current_score or 0) + max(0, icp_score - 50))
                conn.execute(
                    "UPDATE contacts SET lead_score=? WHERE id=?",
                    (new_score, contact_id)
                )

        # Also update context if we got a better description
        if result.get('company_description') and not (contact['context'] if 'context' in contact.keys() else ''):
            conn.execute(
                "UPDATE contacts SET context=? WHERE id=?",
                (result['company_description'], contact_id)
            )

        # Save all intelligence fields
        conn.execute("""
            UPDATE contacts SET
                industry=?, company_size=?, country=?,
                company_description=?, technologies=?, employee_range=?,
                enrichment_status=?, last_enriched_at=?
            WHERE id=?
        """, (
            result.get('industry', ''),
            result.get('company_size', ''),
            result.get('country', ''),
            result.get('company_description', ''),
            result.get('technologies', ''),
            result.get('employee_range', ''),
            'enriched',
            datetime.now(),
            contact_id
        ))
        conn.commit()

        app_logger.info(f'[INDUSTRY] Enriched contact {contact_id}: industry={result.get("industry")} country={result.get("country")}')
        return result

    except Exception as e:
        error_logger.error(f'[INDUSTRY] enrich_contact_intelligence error: {e}')
        try:
            conn.execute(
                "UPDATE contacts SET enrichment_status='failed' WHERE id=?",
                (contact_id,)
            )
            conn.commit()
        except Exception:
            pass
        return {}
    finally:
        conn.close()


# ── BULK ENRICHMENT ───────────────────────────────────────────
def enrich_contacts_bulk_intelligence(contact_ids: list, workspace_id: int) -> dict:
    """Enrich multiple contacts. Returns {enriched, failed}."""
    import time
    enriched = failed = 0
    for cid in contact_ids:
        result = enrich_contact_intelligence(cid)
        if result:
            enriched += 1
        else:
            failed += 1
        time.sleep(1.5)  # Rate limit AI calls
    return {'enriched': enriched, 'failed': failed}


# ── GET INDUSTRY STATS ────────────────────────────────────────
def get_industry_breakdown(workspace_id: int) -> list:
    """Get contact count by industry for a workspace."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                COALESCE(NULLIF(industry,''), 'Unknown') as industry,
                COUNT(*) as count
            FROM contacts
            WHERE workspace_id=?
            GROUP BY industry
            ORDER BY count DESC
        """, (workspace_id,)).fetchall()
        return [{'industry': r['industry'], 'count': r['count'],
                 'style': get_industry_style(r['industry'])} for r in rows]
    finally:
        conn.close()


# ── FILTER CONTACTS ───────────────────────────────────────────
def filter_contacts(workspace_id: int, filters: dict, page: int = 1,
                    per_page: int = 50) -> dict:
    """
    Filter contacts with multiple criteria.
    Returns {contacts, total, pages}.
    """
    conn = get_db()
    try:
        sql = "SELECT * FROM contacts WHERE workspace_id=?"
        params = [workspace_id]

        if filters.get('industry'):
            sql += " AND industry=?"
            params.append(filters['industry'])
        if filters.get('country'):
            sql += " AND LOWER(country) LIKE ?"
            params.append(f"%{filters['country'].lower()}%")
        if filters.get('company_size'):
            sql += " AND company_size=?"
            params.append(filters['company_size'])
        if filters.get('min_score'):
            sql += " AND COALESCE(lead_score,0) >= ?"
            params.append(int(filters['min_score']))
        if filters.get('enriched') == '1':
            sql += " AND enrichment_status='enriched'"
        elif filters.get('enriched') == '0':
            sql += " AND (enrichment_status='pending' OR enrichment_status IS NULL OR enrichment_status='')"
        if filters.get('email_valid') == '1':
            sql += " AND email_valid=1"
        elif filters.get('email_valid') == '0':
            sql += " AND email_valid=0"
        if filters.get('status'):
            sql += " AND status=?"
            params.append(filters['status'])
        if filters.get('search'):
            q = f"%{filters['search'].lower()}%"
            sql += " AND (LOWER(name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(company) LIKE ?)"
            params.extend([q, q, q])

        # Count total
        count_sql = sql.replace("SELECT *", "SELECT COUNT(*)")
        total = conn.execute(count_sql, params).fetchone()[0]

        # Paginate
        offset = (page - 1) * per_page
        sql += f" ORDER BY COALESCE(lead_score,0) DESC, created_at DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])

        rows = conn.execute(sql, params).fetchall()
        return {
            'contacts': [dict(r) for r in rows],
            'total': total,
            'pages': max(1, (total + per_page - 1) // per_page),
            'page': page,
        }
    finally:
        conn.close()
