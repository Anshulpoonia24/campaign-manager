"""
services/sdr_researcher.py — SDR Deep Research Engine
======================================================
Professional multi-phase company research pipeline.
Produces a verified research brief before any email personalization.

Phases:
  1a  Homepage crawl — meta, og, hero text, tech fingerprints
  1b  Deep page crawl — About, Products, Pricing, Careers, Blog, etc.
  1c  Structured signal extraction — jobs, customers, integrations, social proof
  1d  Signal aggregation — keyword hits with surrounding sentence context
  2a  Crunchbase + Product Hunt
  2b  Google News RSS + press release search
  2c  G2 / Capterra review data
  2d  LinkedIn company hints + engineering blog detection
  3   Tech stack deep detection (50+ fingerprints)
  4   Buying signal scoring with evidence strings
  5   Decision maker research
  6   AI synthesis → research brief → personalization hooks
"""
import re
import time
import json
from urllib.parse import urljoin, urlparse

try:
    import requests as _req
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

from utils.logger import app_logger, error_logger

# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

FREE_PROVIDERS = {
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
    'live.com', 'icloud.com', 'protonmail.com', 'aol.com',
    'mail.com', 'zoho.com', 'yandex.com',
}

# Ordered list of subpaths to attempt on every domain
CRAWL_PATHS = [
    'about', 'about-us', 'about/company', 'company', 'our-story',
    'product', 'products', 'platform', 'features',
    'services', 'solutions', 'what-we-do',
    'industries', 'use-cases', 'customers', 'case-studies', 'success-stories',
    'pricing', 'plans',
    'blog', 'engineering', 'engineering-blog', 'tech-blog', 'insights',
    'careers', 'jobs', 'join-us', 'work-with-us',
    'team', 'leadership',
    'contact', 'contact-us',
    'integrations', 'partners', 'ecosystem',
    'docs', 'developers', 'api',
    'press', 'news', 'newsroom',
]

_BROWSER_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


# ══════════════════════════════════════════════════════════════
# SHARED HTTP HELPERS
# ══════════════════════════════════════════════════════════════

def _headers(referer: str = '') -> dict:
    h = {
        'User-Agent': _BROWSER_UA,
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    if referer:
        h['Referer'] = referer
    return h


def _fetch(url: str, timeout: int = 8) -> tuple[str | None, dict]:
    """Fetch URL. Returns (html_text, response_headers). Both None/{} on failure."""
    try:
        r = _req.get(url, headers=_headers(), timeout=timeout,
                     allow_redirects=True)
        if r.status_code == 200:
            return r.text, dict(r.headers)
    except Exception:
        pass
    return None, {}


def _soup(html: str) -> 'BeautifulSoup | None':
    if not html or not BS4_AVAILABLE:
        return None
    return BeautifulSoup(html, 'html.parser')


# Boilerplate phrases to strip from extracted text
_BOILERPLATE = re.compile(
    r'(cookie|privacy policy|terms of service|all rights reserved|'
    r'subscribe to our newsletter|sign up for updates|follow us on|'
    r'copyright \d{4}|\bnavigation\b|skip to content|back to top)',
    re.IGNORECASE
)

# Generic low-value phrases — if output is mostly these, it's useless
_GENERIC_PHRASES = [
    'technology company', 'provides email services', 'offers online solutions',
    'focuses on communication', 'software solutions', 'innovative solutions',
    'cutting-edge technology', 'world-class solutions', 'leading provider',
    'comprehensive platform', 'state-of-the-art', 'best-in-class',
    'empowering businesses', 'transforming industries', 'digital transformation',
    'end-to-end solutions', 'seamless experience', 'robust platform',
]


def _is_generic_output(text: str) -> bool:
    """Return True if text contains 3+ generic filler phrases — output is useless."""
    text_lower = text.lower()
    hits = sum(1 for p in _GENERIC_PHRASES if p in text_lower)
    return hits >= 3


def _clean_text(html: str, max_chars: int = 2500) -> str:
    """
    Extract meaningful text from HTML.
    Handles both <p>-heavy and <div>/<section>-heavy layouts (modern SPAs).
    Strips nav, footer, cookie banners, boilerplate.
    """
    s = _soup(html)
    if not s:
        return ''

    # Remove noise tags entirely
    for tag in s(['script', 'style', 'nav', 'footer', 'header',
                  'noscript', 'iframe', 'svg', 'form', 'aside',
                  'cookie', 'banner']):
        tag.decompose()

    # Also remove elements with boilerplate class/id names
    for tag in s.find_all(True):
        if not hasattr(tag, 'attrs') or tag.attrs is None:
            continue
        cls = ' '.join(tag.get('class', []))
        tid = tag.get('id', '')
        if re.search(r'(cookie|banner|popup|modal|overlay|toast|alert|nav|menu|footer|header)',
                     f'{cls} {tid}', re.I):
            tag.decompose()

    # Priority 1: headings (most signal-dense)
    headings = []
    for h in s.find_all(['h1', 'h2', 'h3'])[:15]:
        t = h.get_text(' ', strip=True)
        if len(t) > 4 and not _BOILERPLATE.search(t):
            headings.append(t)

    # Priority 2: paragraphs
    paras = []
    for p in s.find_all('p')[:25]:
        t = p.get_text(' ', strip=True)
        if len(t) > 20 and not _BOILERPLATE.search(t):
            paras.append(t)

    # Priority 3: list items (features, benefits, integrations)
    li_items = []
    for li in s.find_all('li')[:30]:
        t = li.get_text(' ', strip=True)
        if len(t) > 10 and len(t) < 200 and not _BOILERPLATE.search(t):
            li_items.append(t)

    # Priority 4: fallback — div/section text for SPA sites with no <p> tags
    div_text = []
    if len(paras) < 3:
        for tag in s.find_all(['div', 'section', 'span']):
            # Only leaf-ish nodes with meaningful text
            children_text = tag.get_text(' ', strip=True)
            direct_text = ''.join(
                str(c) for c in tag.children
                if isinstance(c, str)
            ).strip()
            if len(direct_text) > 40 and len(direct_text) < 400 and not _BOILERPLATE.search(direct_text):
                div_text.append(direct_text)
            if len(div_text) >= 15:
                break

    parts = []
    if headings:
        parts.append('HEADINGS: ' + ' | '.join(headings))
    if paras:
        parts.append('CONTENT: ' + ' '.join(paras))
    if li_items:
        parts.append('FEATURES/LIST: ' + ' | '.join(li_items[:15]))
    if div_text:
        parts.append('PAGE TEXT: ' + ' '.join(div_text[:10]))

    combined = '\n'.join(parts)

    # Final boilerplate strip
    lines = [l for l in combined.split('\n')
             if not _BOILERPLATE.search(l) and len(l.strip()) > 5]
    return '\n'.join(lines)[:max_chars].strip()


# ══════════════════════════════════════════════════════════════
# PHASE 1a — HOMEPAGE CRAWL
# ══════════════════════════════════════════════════════════════

def phase_1a_homepage(domain: str) -> dict:
    """
    Fetch homepage. Extract:
    - title, meta description, og:title, og:description, og:type
    - canonical URL, detected base_url (https vs http)
    - hero text (h1 + first 3 h2s + first 3 paragraphs)
    - raw HTML stored for tech detection in Phase 3
    - response headers stored for server/CDN detection
    """
    result = {
        'base_url': '',
        'title': '',
        'meta_description': '',
        'og_title': '',
        'og_description': '',
        'og_type': '',
        'canonical': '',
        'hero_text': '',
        'homepage_html': '',
        'response_headers': {},
        'reachable': False,
    }

    if not domain or not BS4_AVAILABLE:
        return result

    for scheme in ['https://', 'http://']:
        html, resp_headers = _fetch(f'{scheme}{domain}')
        if not html:
            continue

        result['base_url'] = f'{scheme}{domain}'
        result['homepage_html'] = html
        result['response_headers'] = resp_headers
        result['reachable'] = True

        s = _soup(html)
        if not s:
            break

        # Title
        if s.title and s.title.string:
            result['title'] = s.title.string.strip()[:200]

        # Meta description
        m = s.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
        if m:
            result['meta_description'] = m.get('content', '')[:400]

        # OG tags
        og_title = s.find('meta', attrs={'property': 'og:title'})
        if og_title:
            result['og_title'] = og_title.get('content', '')[:200]
        og_desc = s.find('meta', attrs={'property': 'og:description'})
        if og_desc:
            result['og_description'] = og_desc.get('content', '')[:400]
        og_type = s.find('meta', attrs={'property': 'og:type'})
        if og_type:
            result['og_type'] = og_type.get('content', '')[:50]

        # Canonical
        canon = s.find('link', attrs={'rel': 'canonical'})
        if canon:
            result['canonical'] = canon.get('href', '')[:200]

        # Hero text — h1 + first few h2s + first few paragraphs
        hero_parts = []
        h1s = s.find_all('h1')
        for h in h1s[:2]:
            t = h.get_text(' ', strip=True)
            if t:
                hero_parts.append(f'H1: {t}')
        h2s = s.find_all('h2')
        for h in h2s[:4]:
            t = h.get_text(' ', strip=True)
            if t and len(t) > 5:
                hero_parts.append(f'H2: {t}')
        ps = s.find_all('p')
        count = 0
        for p in ps:
            t = p.get_text(' ', strip=True)
            if len(t) > 40:
                hero_parts.append(t)
                count += 1
                if count >= 4:
                    break
        result['hero_text'] = '\n'.join(hero_parts)[:1500]
        break

    app_logger.info(
        f'[RESEARCH 1a] {domain} | reachable={result["reachable"]} '
        f'title="{result["title"][:60]}"'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 1b — DEEP PAGE CRAWL
# ══════════════════════════════════════════════════════════════

def phase_1b_deep_crawl(base_url: str, homepage_html: str) -> dict:
    """
    Discover and crawl up to 18 subpages.
    Strategy:
      1. Parse homepage nav/footer for internal links matching CRAWL_PATHS keywords
      2. Also try direct path probing for paths not found via nav
      3. Priority pages (about, products, solutions) are always attempted first
    Returns {page_label: text_content} dict + raw_html per page for Phase 3.
    """
    result = {
        'pages': {},        # {label: clean_text}
        'pages_html': {},   # {label: raw_html} — for tech detection
        'crawled_urls': [],
        'page_char_counts': {},  # {label: char_count} — for debug logging
    }

    if not base_url or not BS4_AVAILABLE:
        return result

    base_domain = urlparse(base_url).netloc

    # ── Step 1: Discover links from homepage nav/footer ──
    discovered = {}   # {url: label}
    s = _soup(homepage_html or '')
    if s:
        for a in s.find_all('a', href=True):
            href = a['href'].strip()
            if not href or href.startswith('#') or href.startswith('mailto:'):
                continue
            full = urljoin(base_url, href)
            parsed = urlparse(full)
            if parsed.netloc and parsed.netloc != base_domain:
                continue
            path_lower = parsed.path.lower().strip('/')
            for target in CRAWL_PATHS:
                if target in path_lower and full not in discovered:
                    label = target.replace('-', '_').replace('/', '_')
                    discovered[full] = label
                    break

    # ── Step 2: Direct path probing — always probe these regardless of nav ──
    # Split into priority (always probe first) and secondary
    PRIORITY_PATHS = [
        'about', 'about-us', 'company', 'product', 'products',
        'platform', 'services', 'solutions', 'features', 'pricing',
    ]
    SECONDARY_PATHS = [
        'customers', 'case-studies', 'use-cases', 'industries',
        'blog', 'careers', 'team', 'integrations', 'docs',
        'press', 'newsroom', 'our-story', 'what-we-do',
    ]
    found_labels = set(discovered.values())
    ordered_paths = PRIORITY_PATHS + SECONDARY_PATHS
    for path in ordered_paths:
        label = path.replace('-', '_').replace('/', '_')
        if label not in found_labels:
            url = f'{base_url}/{path}'
            if url not in discovered:
                discovered[url] = label

    # ── Step 3: Crawl — priority pages first, up to 18 total ──
    # Reorder so priority paths come first
    priority_labels = set(p.replace('-', '_') for p in PRIORITY_PATHS)
    ordered_items = (
        [(u, l) for u, l in discovered.items() if l in priority_labels] +
        [(u, l) for u, l in discovered.items() if l not in priority_labels]
    )

    crawled = set()
    count = 0
    for url, label in ordered_items:
        if count >= 18:
            break
        if url in crawled:
            continue
        crawled.add(url)

        html, _ = _fetch(url, timeout=7)
        if not html:
            app_logger.info(f'[RESEARCH 1b] MISS {url} (no response)')
            continue

        text = _clean_text(html, max_chars=1500)
        char_count = len(text)
        result['page_char_counts'][label] = char_count

        if char_count < 30:   # lowered from 80 — even sparse pages have signal
            app_logger.info(f'[RESEARCH 1b] SKIP {label} ({char_count} chars — too sparse)')
            continue

        result['pages'][label] = text
        result['pages_html'][label] = html
        result['crawled_urls'].append(url)
        count += 1
        app_logger.info(f'[RESEARCH 1b] OK {label} <- {url} ({char_count} chars)')
        time.sleep(0.25)

    app_logger.info(
        f'[RESEARCH 1b] {base_url} | crawled {len(result["pages"])} pages: '
        f'{list(result["pages"].keys())} | '
        f'char_counts={result["page_char_counts"]}'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 1c — STRUCTURED SIGNAL EXTRACTION
# ══════════════════════════════════════════════════════════════

_CUSTOMER_LOGO_PATTERNS = re.compile(
    r'(trusted by|used by|customers include|our customers|clients include'
    r'|powered by|as seen in|featured in|works with)',
    re.IGNORECASE
)
_SOCIAL_PROOF_NUMBERS = re.compile(
    r'(\d[\d,\.]+\s*(?:\+|k|m|b)?)\s*'
    r'(customers|users|companies|teams|businesses|clients|organizations|startups|enterprises)',
    re.IGNORECASE
)
_PRICING_TIER = re.compile(
    r'\b(free|starter|basic|pro|professional|business|enterprise|growth|scale|team|plus|premium)\b',
    re.IGNORECASE
)
_JOB_DEPT = re.compile(
    r'\b(engineer|engineering|developer|backend|frontend|full.?stack|devops|sre|'
    r'data|ml|ai|product|design|sales|marketing|finance|operations|support)\b',
    re.IGNORECASE
)
_INTEGRATION_KEYWORDS = re.compile(
    r'\b(integrates? with|connects? with|works? with|compatible with|'
    r'built on|powered by|via api|rest api|webhook|sdk|plugin|extension)\b',
    re.IGNORECASE
)


def phase_1c_extract_signals(pages: dict, pages_html: dict) -> dict:
    """
    From crawled page text + HTML, extract structured signals:
    - job_listings: list of job title strings found on careers/jobs pages
    - job_departments: set of departments hiring
    - customer_mentions: company/brand names mentioned near trust signals
    - social_proof: numbers like '500+ customers', '10k users'
    - pricing_tiers: tier names found on pricing page
    - integration_partners: tools/platforms mentioned as integrations
    - has_api_docs: bool — docs/api/developers page found
    - has_engineering_blog: bool
    - customer_industries: industries mentioned in case studies / customers page
    """
    result = {
        'job_listings': [],
        'job_departments': [],
        'customer_mentions': [],
        'social_proof': [],
        'pricing_tiers': [],
        'integration_partners': [],
        'has_api_docs': False,
        'has_engineering_blog': False,
        'customer_industries': [],
    }

    # ── Job listings from careers/jobs pages ──
    for label in ('careers', 'jobs', 'join_us', 'work_with_us'):
        if label in pages:
            text = pages[label]
            depts = list(set(_JOB_DEPT.findall(text)))
            result['job_departments'] = [d.lower() for d in depts[:10]]
            # Extract lines that look like job titles (short, title-case-ish)
            lines = [l.strip() for l in text.split('\n') if 5 < len(l.strip()) < 80]
            result['job_listings'] = lines[:15]

    # ── Social proof numbers ──
    all_text = '\n'.join(pages.values())
    for m in _SOCIAL_PROOF_NUMBERS.finditer(all_text):
        proof = f'{m.group(1)} {m.group(2)}'
        if proof not in result['social_proof']:
            result['social_proof'].append(proof)
    result['social_proof'] = result['social_proof'][:8]

    # ── Pricing tiers ──
    for label in ('pricing', 'plans'):
        if label in pages:
            tiers = list(set(_PRICING_TIER.findall(pages[label])))
            result['pricing_tiers'] = [t.lower() for t in tiers[:8]]

    # ── Integration partners ──
    for label in ('integrations', 'partners', 'ecosystem', 'platform', 'features'):
        if label in pages:
            html = pages_html.get(label, '')
            s = _soup(html)
            if s:
                # Look for integration names near integration keywords
                for tag in s.find_all(['h3', 'h4', 'li', 'span', 'a']):
                    t = tag.get_text(strip=True)
                    if 3 < len(t) < 40 and not t.startswith('http'):
                        parent_text = tag.parent.get_text(' ', strip=True) if tag.parent else ''
                        if _INTEGRATION_KEYWORDS.search(parent_text):
                            if t not in result['integration_partners']:
                                result['integration_partners'].append(t)
            result['integration_partners'] = result['integration_partners'][:15]

    # ── API / docs / engineering blog ──
    result['has_api_docs'] = any(
        k in pages for k in ('docs', 'developers', 'api')
    )
    result['has_engineering_blog'] = any(
        k in pages for k in ('engineering', 'engineering_blog', 'tech_blog')
    )

    # ── Customer industries from case studies ──
    for label in ('case_studies', 'customers', 'success_stories', 'use_cases', 'industries'):
        if label in pages:
            text = pages[label]
            industry_hints = re.findall(
                r'\b(fintech|healthcare|retail|e.?commerce|logistics|saas|'
                r'manufacturing|education|real estate|media|gaming|'
                r'insurance|banking|telecom|hospitality|legal)\b',
                text, re.IGNORECASE
            )
            result['customer_industries'] = list(set(h.lower() for h in industry_hints))[:8]

    app_logger.info(
        f'[RESEARCH 1c] jobs={len(result["job_listings"])} '
        f'social_proof={result["social_proof"]} '
        f'pricing_tiers={result["pricing_tiers"]} '
        f'api_docs={result["has_api_docs"]}'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 1d — SIGNAL AGGREGATION
# ══════════════════════════════════════════════════════════════

# Each entry: (keyword_pattern, signal_type, weight)
_SIGNAL_PATTERNS = [
    # Funding
    (r'series [abcde]\b',                    'funding',   9),
    (r'seed round',                           'funding',   7),
    (r'raised \$[\d\.]+\s*[mb]illion',        'funding',   10),
    (r'raised \$[\d,]+',                      'funding',   8),
    (r'secured funding',                      'funding',   7),
    (r'venture.backed',                       'funding',   6),
    # Hiring
    (r"we'?re hiring",                        'hiring',    8),
    (r'join our team',                        'hiring',    6),
    (r'open (?:positions|roles|jobs)',        'hiring',    7),
    (r'growing (?:team|fast)',                'hiring',    6),
    (r'now hiring',                           'hiring',    8),
    (r'\d+\s*open (?:roles|positions)',       'hiring',    9),
    # Growth
    (r'launched',                             'growth',    5),
    (r'new product',                          'growth',    6),
    (r'expanding',                            'growth',    5),
    (r'new office',                           'growth',    7),
    (r'partnership with',                     'growth',    6),
    (r'acquired by',                          'acquisition', 8),
    (r'acqui.?hired',                         'acquisition', 7),
    (r'\d+x growth',                          'growth',    8),
    (r'fastest.growing',                      'growth',    7),
    # Product
    (r'just launched',                        'launch',    8),
    (r'announcing',                           'launch',    6),
    (r'introducing',                          'launch',    5),
    (r'beta',                                 'launch',    4),
    (r'general availability',                 'launch',    7),
    # Scale
    (r'\d[\d,]+\s*(?:customers|users)',       'scale',     7),
    (r'enterprise',                           'scale',     4),
    (r'fortune 500',                          'scale',     8),
]


def _extract_sentence(text: str, match_start: int, match_end: int,
                       window: int = 120) -> str:
    """Extract the sentence surrounding a regex match."""
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    snippet = text[start:end].strip()
    # Try to trim to sentence boundaries
    snippet = re.sub(r'\s+', ' ', snippet)
    return snippet[:250]


def phase_1d_aggregate_signals(pages: dict, homepage_data: dict) -> dict:
    """
    Scan all page text for buying/growth/hiring signals.
    Returns list of {type, keyword, evidence, weight, source_page} dicts.
    Deduplicates by evidence string.
    """
    signals = []
    seen_evidence = set()

    # Combine all text sources with page labels
    sources = {label: text for label, text in pages.items()}
    sources['homepage'] = homepage_data.get('hero_text', '')
    if homepage_data.get('meta_description'):
        sources['meta'] = homepage_data['meta_description']

    for page_label, text in sources.items():
        if not text:
            continue
        text_lower = text.lower()
        for pattern, sig_type, weight in _SIGNAL_PATTERNS:
            for m in re.finditer(pattern, text_lower):
                evidence = _extract_sentence(text, m.start(), m.end())
                key = evidence[:60].lower()
                if key in seen_evidence:
                    continue
                seen_evidence.add(key)
                signals.append({
                    'type':        sig_type,
                    'keyword':     m.group(),
                    'evidence':    evidence,
                    'weight':      weight,
                    'source_page': page_label,
                })

    # Sort by weight descending, keep top 20
    signals.sort(key=lambda x: x['weight'], reverse=True)
    signals = signals[:20]

    app_logger.info(
        f'[RESEARCH 1d] {len(signals)} signals found | '
        f'types={list(set(s["type"] for s in signals))}'
    )
    return {'signals': signals, 'signal_count': len(signals)}


# ══════════════════════════════════════════════════════════════
# PHASE 2a — CRUNCHBASE + PRODUCT HUNT
# ══════════════════════════════════════════════════════════════

def _crunchbase_slug(company: str) -> str:
    """Convert company name to likely Crunchbase slug."""
    slug = company.lower().strip()
    slug = re.sub(r'\b(inc|llc|ltd|corp|co|the|a|an)\b', '', slug)
    slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
    return slug


def phase_2a_crunchbase_producthunt(company: str, domain: str) -> dict:
    """
    Scrape Crunchbase public org page + Product Hunt listing.
    Both use public HTML only — no API keys needed.
    Crunchbase is JS-rendered so we rely on server-side meta tags.
    Product Hunt uses og meta which is server-rendered.
    """
    result = {
        'crunchbase_description': '',
        'crunchbase_founded': '',
        'crunchbase_employees': '',
        'crunchbase_funding': '',
        'crunchbase_investors': '',
        'producthunt_description': '',
        'producthunt_tagline': '',
        'producthunt_votes': '',
        'producthunt_topics': '',
    }

    # ── Crunchbase ──
    slug = _crunchbase_slug(company)
    cb_url = f'https://www.crunchbase.com/organization/{slug}'
    html, _ = _fetch(cb_url, timeout=10)
    if html and BS4_AVAILABLE:
        s = _soup(html)
        if s:
            # Server-rendered meta description contains key facts
            for attr in [{'name': 'description'}, {'property': 'og:description'}]:
                m = s.find('meta', attrs=attr)
                if m:
                    content = m.get('content', '')
                    if content and len(content) > 20:
                        result['crunchbase_description'] = content[:500]
                        # Extract structured facts from description
                        founded = re.search(r'founded in (\d{4})', content, re.I)
                        if founded:
                            result['crunchbase_founded'] = founded.group(1)
                        emp = re.search(
                            r'(\d[\d,]+)\s*employees', content, re.I
                        )
                        if emp:
                            result['crunchbase_employees'] = emp.group(1)
                        funding = re.search(
                            r'raised\s+\$?([\d\.]+\s*(?:million|billion|[mb]))',
                            content, re.I
                        )
                        if funding:
                            result['crunchbase_funding'] = funding.group(1)
                        break
            # Try to find investor names in page text
            page_text = s.get_text(' ', separator=' ')
            investors = re.findall(
                r'(?:backed by|investors?|led by)\s+([A-Z][a-zA-Z\s&,]+?)(?:\.|,|\n)',
                page_text
            )
            if investors:
                result['crunchbase_investors'] = investors[0][:200]

    # ── Product Hunt ──
    # Try slug variations: company-name, companyname
    ph_slugs = [
        re.sub(r'[^a-z0-9]+', '-', company.lower()).strip('-'),
        re.sub(r'[^a-z0-9]', '', company.lower()),
        domain.split('.')[0] if domain else '',
    ]
    for ph_slug in ph_slugs:
        if not ph_slug:
            continue
        ph_url = f'https://www.producthunt.com/products/{ph_slug}'
        html, _ = _fetch(ph_url, timeout=8)
        if not html:
            continue
        s = _soup(html)
        if not s:
            continue
        og_desc = s.find('meta', attrs={'property': 'og:description'})
        if og_desc:
            result['producthunt_description'] = og_desc.get('content', '')[:400]
        og_title = s.find('meta', attrs={'property': 'og:title'})
        if og_title:
            result['producthunt_tagline'] = og_title.get('content', '')[:200]
        # Votes and topics from page text
        page_text = s.get_text(' ')
        votes = re.search(r'(\d[\d,]+)\s*(?:upvotes?|votes?)', page_text, re.I)
        if votes:
            result['producthunt_votes'] = votes.group(1)
        if result['producthunt_description']:
            break

    app_logger.info(
        f'[RESEARCH 2a] {company} | '
        f'cb_desc={bool(result["crunchbase_description"])} '
        f'ph_desc={bool(result["producthunt_description"])} '
        f'funding="{result["crunchbase_funding"]}"'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 2b — GOOGLE NEWS RSS + PRESS RELEASES
# ══════════════════════════════════════════════════════════════

def phase_2b_news_press(company: str, domain: str) -> dict:
    """
    Fetch recent news via Google News RSS (no API key).
    Also check company /press and /newsroom pages for press releases.
    Returns headlines with dates and sources.
    """
    result = {
        'news_items': [],       # [{title, source, date, snippet}]
        'press_releases': [],   # [{title, date, snippet}]
    }

    if not company:
        return result

    # ── Google News RSS ──
    queries = [
        company.replace(' ', '+'),
        f'{company.replace(" ", "+")}+funding',
        f'{company.replace(" ", "+")}+launch',
    ]
    seen_titles = set()
    for query in queries[:2]:
        url = f'https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en'
        html, _ = _fetch(url, timeout=8)
        if not html:
            continue
        # RSS items: <item><title>...</title><source>...</source><pubDate>...</pubDate>
        items = re.findall(
            r'<item>(.*?)</item>', html, re.DOTALL
        )
        for item in items[:8]:
            title_m = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            source_m = re.search(r'<source[^>]*>(.*?)</source>', item)
            date_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
            desc_m = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item)

            title = re.sub(r'<[^>]+>', '', title_m.group(1) if title_m else '').strip()
            source = re.sub(r'<[^>]+>', '', source_m.group(1) if source_m else '').strip()
            date = (date_m.group(1) if date_m else '').strip()[:30]
            snippet = re.sub(r'<[^>]+>', '', desc_m.group(1) if desc_m else '').strip()[:200]

            if not title or title in seen_titles:
                continue
            # Only include if company name appears in title
            if company.lower()[:5] not in title.lower() and (domain or '')[:6] not in title.lower():
                continue
            seen_titles.add(title)
            result['news_items'].append({
                'title': title[:200],
                'source': source[:80],
                'date': date,
                'snippet': snippet,
            })

    result['news_items'] = result['news_items'][:8]

    # ── Press releases from company website ──
    if domain:
        for path in ('press', 'newsroom', 'news', 'press-releases', 'media'):
            url = f'https://{domain}/{path}'
            html, _ = _fetch(url, timeout=7)
            if not html:
                continue
            s = _soup(html)
            if not s:
                continue
            # Extract article/press release titles
            for tag in s.find_all(['h2', 'h3', 'article'])[:10]:
                t = tag.get_text(' ', strip=True)
                if 20 < len(t) < 200:
                    result['press_releases'].append({
                        'title': t[:200],
                        'date': '',
                        'snippet': '',
                    })
            if result['press_releases']:
                result['press_releases'] = result['press_releases'][:6]
                break

    app_logger.info(
        f'[RESEARCH 2b] {company} | '
        f'news={len(result["news_items"])} '
        f'press={len(result["press_releases"])}'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 2c — G2 + CAPTERRA REVIEW DATA
# ══════════════════════════════════════════════════════════════

def phase_2c_reviews(company: str, domain: str) -> dict:
    """
    Scrape G2 and Capterra for:
    - Star rating, review count
    - Product category
    - Top keywords from review snippets (what users praise/complain about)
    Both sites server-render meta tags — no JS needed.
    """
    result = {
        'g2_rating': '',
        'g2_review_count': '',
        'g2_category': '',
        'g2_description': '',
        'capterra_rating': '',
        'capterra_review_count': '',
        'capterra_category': '',
        'capterra_description': '',
        'review_keywords': [],
    }

    slug_hyphen = re.sub(r'[^a-z0-9]+', '-', company.lower()).strip('-')
    slug_clean = re.sub(r'[^a-z0-9]', '', company.lower())
    domain_slug = domain.split('.')[0] if domain else ''

    # ── G2 ──
    for slug in [slug_hyphen, slug_clean, domain_slug]:
        if not slug:
            continue
        g2_url = f'https://www.g2.com/products/{slug}/reviews'
        html, _ = _fetch(g2_url, timeout=9)
        if not html:
            continue
        s = _soup(html)
        if not s:
            continue
        og_desc = s.find('meta', attrs={'property': 'og:description'})
        if og_desc:
            content = og_desc.get('content', '')
            if content and ('review' in content.lower() or 'rating' in content.lower()):
                result['g2_description'] = content[:400]
                rating = re.search(r'(\d\.\d)\s*(?:out of|\/)\s*5', content)
                if rating:
                    result['g2_rating'] = rating.group(1)
                rev_count = re.search(r'(\d[\d,]+)\s*reviews?', content, re.I)
                if rev_count:
                    result['g2_review_count'] = rev_count.group(1)
                break
        # Try title for category
        if s.title and s.title.string:
            cat_m = re.search(r'Reviews\s*\|\s*(.+?)(?:\s*-|\s*\|)', s.title.string)
            if cat_m:
                result['g2_category'] = cat_m.group(1).strip()[:80]
        if result['g2_description']:
            break

    # ── Capterra ──
    for slug in [slug_hyphen, slug_clean, domain_slug]:
        if not slug:
            continue
        cap_url = f'https://www.capterra.com/p/search/?q={slug}'
        html, _ = _fetch(cap_url, timeout=9)
        if not html:
            continue
        s = _soup(html)
        if not s:
            continue
        og_desc = s.find('meta', attrs={'property': 'og:description'})
        if og_desc:
            content = og_desc.get('content', '')
            if content and len(content) > 30:
                result['capterra_description'] = content[:400]
                rating = re.search(r'(\d\.\d)\s*(?:out of|\/)\s*5', content)
                if rating:
                    result['capterra_rating'] = rating.group(1)
                rev_count = re.search(r'(\d[\d,]+)\s*reviews?', content, re.I)
                if rev_count:
                    result['capterra_review_count'] = rev_count.group(1)
                break

    # ── Extract review keywords from any description found ──
    combined_review_text = (
        result['g2_description'] + ' ' + result['capterra_description']
    ).lower()
    keyword_patterns = [
        'easy to use', 'easy to set up', 'great support', 'customer support',
        'integrations', 'api', 'automation', 'reporting', 'analytics',
        'expensive', 'pricing', 'onboarding', 'documentation', 'mobile app',
        'scalable', 'reliable', 'fast', 'slow', 'buggy', 'intuitive',
    ]
    result['review_keywords'] = [
        kw for kw in keyword_patterns if kw in combined_review_text
    ][:8]

    app_logger.info(
        f'[RESEARCH 2c] {company} | '
        f'g2={result["g2_rating"]} ({result["g2_review_count"]} reviews) '
        f'capterra={result["capterra_rating"]}'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 2d — LINKEDIN HINTS + ENGINEERING BLOG
# ══════════════════════════════════════════════════════════════

def phase_2d_linkedin_engblog(company: str, domain: str,
                               pages: dict, pages_html: dict) -> dict:
    """
    LinkedIn: We cannot scrape LinkedIn directly (blocks bots).
    Strategy: check company website /about page for employee count,
    founding year, LinkedIn URL. Also check if they link to LinkedIn.

    Engineering blog: check if /engineering, /tech-blog, /blog pages
    contain technical content (code snippets, tech terms).
    Extract recent post titles as signals of technical depth.
    """
    result = {
        'employee_count': '',
        'founded_year': '',
        'linkedin_url': '',
        'hq_location': '',
        'engineering_blog_posts': [],
        'engineering_topics': [],
        'has_open_source': False,
        'github_url': '',
    }

    # ── Extract from About page ──
    about_text = pages.get('about', '') or pages.get('about_us', '') or pages.get('company', '')
    about_html = pages_html.get('about', '') or pages_html.get('about_us', '') or pages_html.get('company', '')

    if about_text:
        # Employee count
        emp = re.search(
            r'(\d[\d,]+)\s*(?:\+\s*)?(?:employees|team members|people|staff)',
            about_text, re.I
        )
        if emp:
            result['employee_count'] = emp.group(1).replace(',', '')

        # Founded year
        founded = re.search(
            r'(?:founded|established|started|incorporated)\s+(?:in\s+)?(\d{4})',
            about_text, re.I
        )
        if founded:
            result['founded_year'] = founded.group(1)

        # HQ location
        hq = re.search(
            r'(?:headquartered|based|located|offices?)\s+in\s+([A-Z][a-zA-Z\s,]+?)(?:\.|,|\n)',
            about_text
        )
        if hq:
            result['hq_location'] = hq.group(1).strip()[:80]

    # ── LinkedIn URL from homepage/about HTML ──
    for html in [about_html] + list(pages_html.values())[:3]:
        if not html:
            continue
        s = _soup(html)
        if not s:
            continue
        for a in s.find_all('a', href=True):
            href = a['href']
            if 'linkedin.com/company/' in href:
                result['linkedin_url'] = href.split('?')[0][:150]
                break
        if result['linkedin_url']:
            break

    # ── GitHub URL ──
    for html in list(pages_html.values())[:5]:
        if not html:
            continue
        s = _soup(html)
        if not s:
            continue
        for a in s.find_all('a', href=True):
            href = a['href']
            if 'github.com/' in href and '/github.com' not in href:
                result['github_url'] = href.split('?')[0][:150]
                result['has_open_source'] = True
                break
        if result['github_url']:
            break

    # ── Engineering blog posts ──
    eng_text = (
        pages.get('engineering', '') or
        pages.get('engineering_blog', '') or
        pages.get('tech_blog', '') or
        pages.get('blog', '')
    )
    if eng_text:
        # Extract post-title-like lines
        lines = [l.strip() for l in eng_text.split('\n') if 15 < len(l.strip()) < 120]
        result['engineering_blog_posts'] = lines[:8]

        # Detect engineering topics
        tech_terms = re.findall(
            r'\b(kubernetes|docker|microservices|graphql|grpc|kafka|redis|'
            r'postgres|mongodb|elasticsearch|react|typescript|rust|golang|'
            r'python|machine learning|llm|ai|ml|data pipeline|ci.?cd|'
            r'infrastructure|cloud|aws|gcp|azure|serverless|api gateway)\b',
            eng_text, re.I
        )
        result['engineering_topics'] = list(set(t.lower() for t in tech_terms))[:10]

    app_logger.info(
        f'[RESEARCH 2d] {company} | '
        f'employees={result["employee_count"]} '
        f'founded={result["founded_year"]} '
        f'linkedin={bool(result["linkedin_url"])} '
        f'github={bool(result["github_url"])} '
        f'eng_posts={len(result["engineering_blog_posts"])}'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 3 — TECH STACK DEEP DETECTION
# ══════════════════════════════════════════════════════════════

# Fingerprints organized by category.
# Each entry: pattern string (searched case-insensitively in combined HTML + headers)
TECH_STACK = {
    # ── Frontend Frameworks ──
    'frontend': {
        'React':        ['__NEXT_DATA__', 'react-root', 'data-reactroot', '_reactFiber'],
        'Next.js':      ['__NEXT_DATA__', '_next/static', 'next/dist'],
        'Vue.js':       ['vue.js', '__vue__', 'data-v-', 'vue.min.js'],
        'Angular':      ['ng-version', 'angular.js', 'ng-app', 'angular.min.js'],
        'Svelte':       ['svelte', '__svelte'],
        'Nuxt.js':      ['__nuxt', '_nuxt/'],
        'Remix':        ['__remixContext', 'remix.run'],
        'Gatsby':       ['gatsby-', '___gatsby'],
        'Ember.js':     ['ember.js', 'ember-application'],
        'Backbone.js':  ['backbone.js', 'backbone.min.js'],
    },
    # ── CMS / Website Builders ──
    'cms': {
        'WordPress':    ['wp-content', 'wp-includes', 'wordpress'],
        'Shopify':      ['cdn.shopify.com', 'shopify.com/s/', 'Shopify.theme'],
        'Webflow':      ['webflow.com', 'wf-form', 'webflow.js'],
        'Framer':       ['framer.com', 'framerusercontent'],
        'Squarespace':  ['squarespace.com', 'static.squarespace'],
        'Wix':          ['wix.com', 'wixstatic.com', 'wix-code'],
        'Ghost':        ['ghost.io', 'ghost.org', 'content.ghost.io'],
        'Contentful':   ['contentful.com', 'ctfassets.net'],
        'Sanity':       ['sanity.io', 'cdn.sanity.io'],
        'Strapi':       ['strapi.io'],
    },
    # ── Analytics ──
    'analytics': {
        'Google Analytics': ['google-analytics.com', 'gtag(', 'UA-', 'G-', 'ga('],
        'Google Tag Manager': ['googletagmanager.com', 'GTM-'],
        'Mixpanel':     ['mixpanel.com', 'mixpanel.init', 'mp_'],
        'Amplitude':    ['amplitude.com', 'amplitude.getInstance'],
        'Segment':      ['segment.com', 'analytics.js', 'analytics.load'],
        'Heap':         ['heap.io', 'heap.load', 'heapanalytics'],
        'Hotjar':       ['hotjar.com', 'hj(', 'hjid'],
        'FullStory':    ['fullstory.com', 'FS.identify'],
        'PostHog':      ['posthog.com', 'posthog.init'],
        'Plausible':    ['plausible.io', 'plausible/script'],
        'Clarity':      ['clarity.ms', 'microsoft clarity'],
    },
    # ── CRM / Marketing ──
    'crm_marketing': {
        'HubSpot':      ['hs-scripts.com', 'hubspot.com', 'hbspt', 'hs-analytics'],
        'Salesforce':   ['salesforce.com', 'force.com', 'pardot', 'sfdcstatic'],
        'Marketo':      ['marketo.com', 'munchkin.js', 'mktoForms'],
        'Intercom':     ['intercom.io', 'widget.intercom.io', 'intercomSettings'],
        'Drift':        ['drift.com', 'js.driftt.com', 'drift.load'],
        'Zendesk':      ['zendesk.com', 'zdassets.com', 'zE('],
        'Freshdesk':    ['freshdesk.com', 'freshworks.com', 'freshchat'],
        'Klaviyo':      ['klaviyo.com', 'klaviyo.init'],
        'ActiveCampaign': ['activecampaign.com', 'activehosted.com'],
        'Mailchimp':    ['mailchimp.com', 'list-manage.com', 'mc.js'],
        'Customer.io':  ['customer.io', 'cio.js'],
        'Braze':        ['braze.com', 'appboy.com'],
    },
    # ── Payments ──
    'payments': {
        'Stripe':       ['js.stripe.com', 'stripe.com/v3', 'Stripe('],
        'Braintree':    ['braintreepayments.com', 'braintree-web'],
        'PayPal':       ['paypal.com', 'paypalobjects.com'],
        'Paddle':       ['paddle.com', 'paddle.js'],
        'Chargebee':    ['chargebee.com', 'js.chargebee.com'],
        'Recurly':      ['recurly.com', 'js.recurly.com'],
        'Zuora':        ['zuora.com'],
    },
    # ── Infrastructure / Hosting ──
    'infra': {
        'AWS':          ['amazonaws.com', 'cloudfront.net', 'awsstatic.com'],
        'Google Cloud': ['googleapis.com', 'gstatic.com', 'googleusercontent'],
        'Azure':        ['azure.com', 'azurewebsites.net', 'azureedge.net'],
        'Cloudflare':   ['cloudflare.com', '__cf_bm', 'cf-ray'],
        'Vercel':       ['vercel.app', '_vercel', 'vercel.com'],
        'Netlify':      ['netlify.app', 'netlify.com', 'netlify-identity'],
        'Heroku':       ['heroku.com', 'herokuapp.com'],
        'Fastly':       ['fastly.net', 'fastly.com'],
        'Akamai':       ['akamai.net', 'akamaized.net'],
        'Supabase':     ['supabase.co', 'supabase.io'],
        'Firebase':     ['firebase.google.com', 'firebaseapp.com', 'firestore'],
    },
    # ── Hiring / HR Tech ──
    'hiring': {
        'Greenhouse':   ['greenhouse.io', 'boards.greenhouse.io'],
        'Lever':        ['lever.co', 'jobs.lever.co'],
        'Workday':      ['workday.com', 'myworkdayjobs.com'],
        'BambooHR':     ['bamboohr.com'],
        'Rippling':     ['rippling.com'],
        'Ashby':        ['ashbyhq.com', 'jobs.ashbyhq.com'],
        'Workable':     ['workable.com', 'apply.workable.com'],
        'SmartRecruiters': ['smartrecruiters.com'],
        'Jobvite':      ['jobvite.com'],
        'iCIMS':        ['icims.com'],
    },
    # ── Developer / API Tools ──
    'devtools': {
        'Twilio':       ['twilio.com', 'twilio.js'],
        'SendGrid':     ['sendgrid.com', 'sendgrid.net'],
        'Algolia':      ['algolia.com', 'algolianet.com', 'algoliasearch'],
        'LaunchDarkly': ['launchdarkly.com', 'ld-relay'],
        'Sentry':       ['sentry.io', 'browser.sentry-cdn.com'],
        'Datadog':      ['datadoghq.com', 'datadog-rum'],
        'PagerDuty':    ['pagerduty.com'],
        'Auth0':        ['auth0.com', 'cdn.auth0.com'],
        'Okta':         ['okta.com', 'okta-hosted'],
        'Typeform':     ['typeform.com', 'embed.typeform.com'],
        'Calendly':     ['calendly.com', 'assets.calendly.com'],
        'Loom':         ['loom.com', 'cdn.loom.com'],
    },
}


def phase_3_tech_detection(homepage_data: dict, pages_html: dict) -> dict:
    """
    Scan homepage HTML + response headers + all subpage HTML
    for 50+ technology fingerprints across 8 categories.
    Returns {category: [detected_techs], all_techs: [...], raw_categories: {...}}
    """
    # Build combined corpus: homepage HTML + headers + all subpage HTML
    corpus_parts = [
        homepage_data.get('homepage_html', ''),
        str(homepage_data.get('response_headers', {})),
    ]
    for html in pages_html.values():
        corpus_parts.append(html or '')

    corpus = '\n'.join(corpus_parts).lower()

    detected_by_category = {}
    all_detected = []

    for category, techs in TECH_STACK.items():
        found = []
        for tech_name, patterns in techs.items():
            for pattern in patterns:
                if pattern.lower() in corpus:
                    found.append(tech_name)
                    break
        if found:
            detected_by_category[category] = found
            all_detected.extend(found)

    # Deduplicate
    all_detected = list(dict.fromkeys(all_detected))

    app_logger.info(
        f'[RESEARCH 3] Tech detected: {len(all_detected)} tools | '
        f'categories={list(detected_by_category.keys())}'
    )
    return {
        'by_category': detected_by_category,
        'all_techs': all_detected,
        'frontend':   detected_by_category.get('frontend', []),
        'cms':        detected_by_category.get('cms', []),
        'analytics':  detected_by_category.get('analytics', []),
        'crm':        detected_by_category.get('crm_marketing', []),
        'payments':   detected_by_category.get('payments', []),
        'infra':      detected_by_category.get('infra', []),
        'hiring_ats': detected_by_category.get('hiring', []),
        'devtools':   detected_by_category.get('devtools', []),
    }


# ══════════════════════════════════════════════════════════════
# PHASE 4 — BUYING SIGNAL SCORING
# ══════════════════════════════════════════════════════════════

# Signal definitions: (label, patterns, weight, category)
# weight 1-10: higher = stronger buying signal for engineering staffing
_BUYING_SIGNAL_DEFS = [
    # Funding — strongest signal (they have money to hire)
    ('Series A raised',         [r'series a'],                          10, 'funding'),
    ('Series B raised',         [r'series b'],                          10, 'funding'),
    ('Series C+ raised',        [r'series [cde]'],                      9,  'funding'),
    ('Seed funding',            [r'seed (?:round|funding|stage)'],      8,  'funding'),
    ('Funding announced',       [r'raised \$[\d]', r'secured \$[\d]'],  9,  'funding'),
    ('Venture backed',          [r'venture.backed', r'vc.backed'],      7,  'funding'),
    # Hiring — direct signal
    ('Actively hiring engineers', [r'engineer.*hiring', r'hiring.*engineer',
                                   r'software.*engineer.*open',
                                   r'backend.*engineer', r'frontend.*engineer',
                                   r'full.?stack.*engineer'],           10, 'hiring'),
    ('Open engineering roles',  [r'\d+\s*open.*(?:role|position)',
                                  r'join.*engineering.*team'],          9,  'hiring'),
    ('General hiring',          [r"we'?re hiring", r'now hiring',
                                  r'join our team', r'open positions'], 7,  'hiring'),
    ('Growing team',            [r'growing (?:team|fast|quickly)',
                                  r'expanding (?:team|globally)'],      7,  'hiring'),
    # Product launches — they need engineers to build
    ('New product launched',    [r'just launched', r'announcing.*product',
                                  r'introducing.*platform',
                                  r'general availability'],             8,  'launch'),
    ('Beta / early access',     [r'(?:public|open|private)\s+beta',
                                  r'early access', r'waitlist'],        6,  'launch'),
    # Growth signals
    ('Rapid growth',            [r'\d+x growth', r'fastest.growing',
                                  r'hypergrowth'],                      8,  'growth'),
    ('New office / expansion',  [r'new office', r'expanding to',
                                  r'opening.*office'],                  7,  'growth'),
    ('Partnership announced',   [r'partnership with', r'partnered with',
                                  r'strategic.*partner'],               6,  'growth'),
    ('Acquisition',             [r'acquired by', r'acqui.?hired',
                                  r'merger'],                           7,  'acquisition'),
    # Scale signals
    ('Large customer base',     [r'\d{3,}[\d,]*\s*(?:customers|clients)',
                                  r'fortune 500', r'enterprise customers'], 7, 'scale'),
    ('High user count',         [r'\d{4,}[\d,]*\s*users',
                                  r'millions of users'],                6,  'scale'),
    # Tech signals — they have engineering teams
    ('Has engineering blog',    [r'engineering blog', r'tech blog',
                                  r'engineering\..*\.com'],             6,  'tech'),
    ('Open source',             [r'open.?source', r'github\.com/',
                                  r'contribute'],                       5,  'tech'),
    ('Public API',              [r'public api', r'rest api', r'graphql api',
                                  r'api documentation', r'developer docs'], 6, 'tech'),
]


def phase_4_buying_signals(pages: dict, homepage_data: dict,
                            signals_1d: dict, external_data: dict,
                            tech_data: dict, linkedin_data: dict) -> dict:
    """
    Score all buying signals with evidence strings.
    Combines:
    - Phase 1d raw signals (already extracted with context)
    - Tech stack signals (ATS detected = hiring)
    - External data (news headlines, Crunchbase funding)
    - LinkedIn data (employee count growth)

    Returns {scored_signals, total_score, top_signals, hiring_score, funding_score}
    """
    scored = []
    seen = set()

    # ── From Phase 1d raw signals ──
    for sig in signals_1d.get('signals', []):
        key = sig['evidence'][:50].lower()
        if key not in seen:
            seen.add(key)
            scored.append({
                'label':    sig['type'].title(),
                'evidence': sig['evidence'],
                'weight':   sig['weight'],
                'category': sig['type'],
                'source':   f'website/{sig["source_page"]}',
            })

    # ── Scan all text sources with buying signal defs ──
    all_text_sources = {
        'homepage': homepage_data.get('hero_text', '') + ' ' + homepage_data.get('meta_description', ''),
    }
    all_text_sources.update(pages)
    # Add external text
    for item in external_data.get('news_items', []):
        all_text_sources['news'] = all_text_sources.get('news', '') + ' ' + item.get('title', '')
    all_text_sources['crunchbase'] = external_data.get('crunchbase_description', '')
    all_text_sources['producthunt'] = external_data.get('producthunt_description', '')

    for source_label, text in all_text_sources.items():
        if not text:
            continue
        text_lower = text.lower()
        for label, patterns, weight, category in _BUYING_SIGNAL_DEFS:
            for pattern in patterns:
                m = re.search(pattern, text_lower)
                if m:
                    evidence = _extract_sentence(text, m.start(), m.end(), window=100)
                    key = evidence[:50].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    scored.append({
                        'label':    label,
                        'evidence': evidence,
                        'weight':   weight,
                        'category': category,
                        'source':   source_label,
                    })
                    break

    # ── ATS detected = hiring signal ──
    ats_tools = tech_data.get('hiring_ats', [])
    if ats_tools:
        scored.append({
            'label':    'ATS / Hiring tool detected',
            'evidence': f'Hiring platform detected: {", ".join(ats_tools)}',
            'weight':   8,
            'category': 'hiring',
            'source':   'tech_detection',
        })

    # ── Engineering blog = active engineering team ──
    if linkedin_data.get('has_open_source') or linkedin_data.get('github_url'):
        scored.append({
            'label':    'Open source / GitHub presence',
            'evidence': f'GitHub: {linkedin_data.get("github_url", "detected")}',
            'weight':   6,
            'category': 'tech',
            'source':   'website',
        })

    # ── Employee count from LinkedIn data ──
    emp = linkedin_data.get('employee_count', '')
    if emp:
        try:
            emp_int = int(emp.replace(',', ''))
            if emp_int > 50:
                scored.append({
                    'label':    f'Company size: {emp} employees',
                    'evidence': f'{emp} employees detected on about page',
                    'weight':   5,
                    'category': 'scale',
                    'source':   'about_page',
                })
        except ValueError:
            pass

    # Sort by weight, deduplicate, keep top 15
    scored.sort(key=lambda x: x['weight'], reverse=True)
    scored = scored[:15]

    # Category scores
    hiring_score  = sum(s['weight'] for s in scored if s['category'] == 'hiring')
    funding_score = sum(s['weight'] for s in scored if s['category'] == 'funding')
    growth_score  = sum(s['weight'] for s in scored if s['category'] in ('growth', 'launch'))
    total_score   = sum(s['weight'] for s in scored)

    top_signals = [s['label'] for s in scored[:5]]

    app_logger.info(
        f'[RESEARCH 4] total_score={total_score} '
        f'hiring={hiring_score} funding={funding_score} '
        f'top={top_signals[:3]}'
    )
    return {
        'scored_signals': scored,
        'total_score':    total_score,
        'hiring_score':   hiring_score,
        'funding_score':  funding_score,
        'growth_score':   growth_score,
        'top_signals':    top_signals,
    }


# ══════════════════════════════════════════════════════════════
# PHASE 5 — DECISION MAKER RESEARCH
# ══════════════════════════════════════════════════════════════

# Role → likely responsibilities mapping
_ROLE_RESPONSIBILITIES = {
    'cto':          'Owns engineering org, tech stack decisions, hiring engineers, architecture',
    'ceo':          'Owns company direction, fundraising, partnerships, team growth',
    'coo':          'Owns operations, scaling processes, vendor relationships',
    'vp engineering': 'Manages engineering teams, hiring, delivery, technical roadmap',
    'head of engineering': 'Manages engineering teams, hiring, delivery, technical roadmap',
    'engineering manager': 'Manages a squad/team, hiring, delivery, technical roadmap',
    'vp product':   'Owns product roadmap, works closely with engineering',
    'cpo':          'Owns product strategy, works closely with engineering',
    'founder':      'Owns everything — product, hiring, fundraising, strategy',
    'co-founder':   'Owns everything — product, hiring, fundraising, strategy',
    'director of engineering': 'Manages engineering org, hiring, delivery',
    'software engineer': 'Individual contributor, builds product features',
    'staff engineer': 'Senior IC, influences architecture and hiring',
    'principal engineer': 'Senior IC, influences architecture and hiring',
    'vp sales':     'Owns revenue, works with marketing and product',
    'cmo':          'Owns marketing, growth, brand',
    'cfo':          'Owns finance, fundraising, vendor contracts',
    'hr':           'Owns hiring, people ops, culture',
    'talent':       'Owns recruiting, hiring pipelines',
}

_ROLE_PAIN_POINTS = {
    'cto':          'Scaling engineering team fast enough, tech debt, hiring senior engineers',
    'ceo':          'Execution speed, team scaling, cost of hiring full-time engineers',
    'coo':          'Operational efficiency, vendor reliability, cost control',
    'vp engineering': 'Hiring pipeline, team velocity, retaining senior engineers',
    'head of engineering': 'Hiring pipeline, team velocity, retaining senior engineers',
    'engineering manager': 'Backlog pressure, hiring junior vs senior, team bandwidth',
    'founder':      'Moving fast with limited budget, finding reliable engineering talent',
    'co-founder':   'Moving fast with limited budget, finding reliable engineering talent',
    'director of engineering': 'Org scaling, hiring, delivery predictability',
    'vp product':   'Engineering bandwidth to ship roadmap, cross-functional alignment',
    'cpo':          'Engineering bandwidth to ship roadmap, cross-functional alignment',
}


def _infer_role_context(role: str) -> tuple[str, str]:
    """Return (responsibilities, pain_points) for a given role string."""
    role_lower = (role or '').lower().strip()
    for key, resp in _ROLE_RESPONSIBILITIES.items():
        if key in role_lower:
            pain = _ROLE_PAIN_POINTS.get(key, 'Scaling team, finding reliable talent')
            return resp, pain
    # Fallback
    if any(w in role_lower for w in ['engineer', 'developer', 'tech', 'architect']):
        return (_ROLE_RESPONSIBILITIES['cto'],
                _ROLE_PAIN_POINTS['cto'])
    if any(w in role_lower for w in ['product', 'pm', 'program']):
        return (_ROLE_RESPONSIBILITIES['vp product'],
                _ROLE_PAIN_POINTS['vp product'])
    return (
        'Likely involved in team growth, vendor decisions, and technical direction',
        'Scaling team efficiently, finding reliable engineering talent'
    )


def phase_5_decision_maker(contact_name: str, contact_role: str,
                            contact_email: str, domain: str,
                            pages: dict, pages_html: dict) -> dict:
    """
    Research the specific person being contacted.
    Since we can't scrape LinkedIn directly, we:
    1. Infer role responsibilities + pain points from title
    2. Search company team/leadership page for their bio
    3. Check if they're mentioned in blog posts or press releases
    4. Look for public speaking / podcast mentions on company site
    5. Detect seniority level from title
    """
    result = {
        'role_responsibilities': '',
        'role_pain_points': '',
        'seniority': '',
        'bio_snippet': '',
        'mentioned_in_blog': False,
        'mentioned_in_press': False,
        'public_content_hints': [],
        'outreach_angle': '',
    }

    resp, pain = _infer_role_context(contact_role)
    result['role_responsibilities'] = resp
    result['role_pain_points'] = pain

    # ── Seniority detection ──
    role_lower = (contact_role or '').lower()
    if any(w in role_lower for w in ['chief', 'cto', 'ceo', 'coo', 'cfo', 'cmo', 'cpo']):
        result['seniority'] = 'C-Suite'
    elif any(w in role_lower for w in ['vp', 'vice president', 'head of', 'director']):
        result['seniority'] = 'VP / Director'
    elif any(w in role_lower for w in ['manager', 'lead', 'principal', 'staff']):
        result['seniority'] = 'Manager / Senior IC'
    elif any(w in role_lower for w in ['founder', 'co-founder', 'owner']):
        result['seniority'] = 'Founder'
    else:
        result['seniority'] = 'Individual Contributor'

    # ── Search team/leadership page for bio ──
    first_name = contact_name.split()[0].lower() if contact_name else ''
    for label in ('team', 'leadership', 'about', 'about_us', 'company'):
        text = pages.get(label, '')
        if not text or not first_name:
            continue
        # Find paragraph containing their name
        sentences = re.split(r'[.\n]', text)
        for sent in sentences:
            if first_name in sent.lower() and len(sent.strip()) > 30:
                result['bio_snippet'] = sent.strip()[:300]
                break
        if result['bio_snippet']:
            break

    # ── Check blog/press mentions ──
    blog_text = pages.get('blog', '') + pages.get('engineering', '')
    if first_name and first_name in blog_text.lower():
        result['mentioned_in_blog'] = True
        result['public_content_hints'].append(f'{contact_name} has authored blog posts')

    press_text = pages.get('press', '') + pages.get('newsroom', '') + pages.get('news', '')
    if first_name and first_name in press_text.lower():
        result['mentioned_in_press'] = True
        result['public_content_hints'].append(f'{contact_name} mentioned in press/news')

    # ── Outreach angle based on seniority + company stage ──
    seniority = result['seniority']
    if seniority == 'C-Suite':
        result['outreach_angle'] = (
            'Focus on business outcomes: speed of hiring, cost vs full-time, '
            'risk reduction. Keep it short — they get 100s of emails.'
        )
    elif seniority == 'Founder':
        result['outreach_angle'] = (
            'Founder-to-founder tone. Focus on moving fast, '
            'trusted engineering partner, not a vendor pitch.'
        )
    elif seniority in ('VP / Director', 'Manager / Senior IC'):
        result['outreach_angle'] = (
            'Focus on team bandwidth, hiring pipeline speed, '
            'quality of engineers. They feel the pain directly.'
        )
    else:
        result['outreach_angle'] = (
            'Reference their company\'s tech stack or product. '
            'Keep it relevant and specific.'
        )

    app_logger.info(
        f'[RESEARCH 5] {contact_name} | role={contact_role} '
        f'seniority={result["seniority"]} '
        f'bio={bool(result["bio_snippet"])} '
        f'blog_mention={result["mentioned_in_blog"]}'
    )
    return result


# ══════════════════════════════════════════════════════════════
# PHASE 6 — AI SYNTHESIS → RESEARCH BRIEF → PERSONALIZATION
# ══════════════════════════════════════════════════════════════

def _call_ai(prompt: str, max_tokens: int = 1500) -> str | None:
    """Call Groq (all keys, round-robin) with Gemini fallback."""
    from utils.db import get_setting
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    for key in keys:
        try:
            r = _req.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}',
                         'Content-Type': 'application/json'},
                json={
                    'model': 'openai/gpt-oss-20b',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': max_tokens,
                    'temperature': 0.15,
                },
                timeout=35
            )
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content'].strip()
            if r.status_code == 429:
                # If daily limit exhausted, skip this key immediately — sleep won't help
                err_body = r.text
                if 'per day' in err_body or 'tokens per day' in err_body or 'TPD' in err_body:
                    error_logger.warning(f'[AI] Groq key daily TPD exhausted — skipping key')
                    continue
                time.sleep(2)
                continue
        except Exception:
            continue

    gemini_key = get_setting('gemini_api_key') or ''
    if gemini_key:
        try:
            r = _req.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/'
                f'gemini-2.0-flash:generateContent?key={gemini_key}',
                json={'contents': [{'parts': [{'text': prompt}]}]},
                timeout=35
            )
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        except Exception:
            pass
    return None


def _build_fallback_prompt(
    contact_name: str, contact_role: str, company: str, domain: str,
    homepage: dict, pages: dict,
    external_cb_ph: dict, external_news: dict,
) -> str:
    """
    Simpler, more direct prompt used when main synthesis returns low confidence
    or generic output. Dumps all raw text and asks AI to extract only what it
    can verify — no guessing allowed.
    """
    # Dump all available text without truncation
    all_text = ''
    if homepage.get('title'):
        all_text += f'TITLE: {homepage["title"]}\n'
    if homepage.get('meta_description'):
        all_text += f'META: {homepage["meta_description"]}\n'
    if homepage.get('og_description'):
        all_text += f'OG DESC: {homepage["og_description"]}\n'
    if homepage.get('hero_text'):
        all_text += f'HOMEPAGE:\n{homepage["hero_text"]}\n'
    for label, text in pages.items():
        all_text += f'\n[{label.upper()}]\n{text}\n'
    if external_cb_ph.get('crunchbase_description'):
        all_text += f'\nCRUNCHBASE: {external_cb_ph["crunchbase_description"]}\n'
    if external_news.get('news_items'):
        for item in external_news['news_items'][:3]:
            all_text += f'NEWS: {item["title"]}\n'

    return f"""You are a B2B research analyst. Extract ONLY verified facts from the text below.
Do NOT invent, assume, or generalize. If you cannot find a specific fact, leave that field empty.

COMPANY: {company} | DOMAIN: {domain}
CONTACT: {contact_name} ({contact_role})

ALL AVAILABLE TEXT:
{all_text[:4000]}

Return ONLY this JSON (no markdown, no explanation):
{{
  "company_summary": "1-2 sentences using ONLY facts found above. If nothing found, write: Insufficient public information found.",
  "business_model": "B2B/B2C/marketplace/agency/etc. — only if determinable from text",
  "products": "Specific product or service names found in text, or empty string",
  "icp": "Who their customers are, only if explicitly mentioned",
  "tech_stack_summary": "Technologies mentioned in text, or empty string",
  "growth_signals": "Any funding, hiring, launch, or growth facts found verbatim",
  "personalization_opportunities": ["Only include if a specific verifiable fact was found"],
  "contact_pain_points": "Based on role '{contact_role}' at a company of this type",
  "industry": "",
  "company_size": "",
  "country": "",
  "founded_year": "",
  "buying_signals_summary": "",
  "top_buying_signals": [],
  "contact_role_context": "",
  "outreach_angle": "",
  "pain_points": "",
  "tech_stack_by_category": {{}},
  "icp_score": 0,
  "confidence_score": 0,
  "confidence_reason": "State exactly what was and was not found",
  "research_gaps": ""
}}"""


def _build_synthesis_prompt(
    contact_name: str, contact_role: str, company: str, domain: str,
    homepage: dict, pages: dict, signals_1c: dict, signals_1d: dict,
    external_cb_ph: dict, external_news: dict, external_reviews: dict,
    linkedin: dict, tech: dict, buying: dict, decision_maker: dict,
) -> str:
    from services.industry_detector import INDUSTRIES
    industries_list = ', '.join(INDUSTRIES)

    # ── Compile all gathered evidence into sections ──
    website_section = ''
    if homepage.get('title'):
        website_section += f'Site Title: {homepage["title"]}\n'
    if homepage.get('meta_description'):
        website_section += f'Meta Description: {homepage["meta_description"]}\n'
    if homepage.get('og_description'):
        website_section += f'OG Description: {homepage["og_description"]}\n'
    if homepage.get('hero_text'):
        website_section += f'\nHero Text:\n{homepage["hero_text"][:800]}\n'

    pages_section = ''
    priority_pages = ['about', 'about_us', 'product', 'products', 'platform',
                      'solutions', 'pricing', 'customers', 'case_studies']
    for label in priority_pages:
        if label in pages:
            pages_section += f'\n[{label.upper()} PAGE]\n{pages[label][:1000]}\n'
    # Add remaining pages up to limit
    for label, text in pages.items():
        if label not in priority_pages and pages_section.count('\n[') < 10:
            pages_section += f'\n[{label.upper()} PAGE]\n{text[:600]}\n'

    tech_section = ''
    if tech.get('all_techs'):
        tech_section += f'All detected: {", ".join(tech["all_techs"])}\n'
    for cat in ('frontend', 'cms', 'analytics', 'crm', 'payments', 'infra', 'hiring_ats', 'devtools'):
        if tech.get(cat):
            tech_section += f'{cat.title()}: {", ".join(tech[cat])}\n'

    signals_section = ''
    for sig in buying.get('scored_signals', [])[:10]:
        signals_section += f'[{sig["category"].upper()}] {sig["label"]}: "{sig["evidence"][:150]}"\n'

    external_section = ''
    if external_cb_ph.get('crunchbase_description'):
        external_section += f'Crunchbase: {external_cb_ph["crunchbase_description"][:300]}\n'
    if external_cb_ph.get('crunchbase_funding'):
        external_section += f'Funding: {external_cb_ph["crunchbase_funding"]}\n'
    if external_cb_ph.get('crunchbase_founded'):
        external_section += f'Founded: {external_cb_ph["crunchbase_founded"]}\n'
    if external_cb_ph.get('producthunt_description'):
        external_section += f'Product Hunt: {external_cb_ph["producthunt_description"][:200]}\n'
    if external_news.get('news_items'):
        external_section += '\nRecent News:\n'
        for item in external_news['news_items'][:5]:
            external_section += f'- {item["title"]} ({item.get("source","")}, {item.get("date","")})\n'
    if external_reviews.get('g2_description'):
        external_section += f'\nG2: {external_reviews["g2_description"][:200]}\n'
    if external_reviews.get('review_keywords'):
        external_section += f'Review themes: {", ".join(external_reviews["review_keywords"])}\n'

    structured_section = ''
    if signals_1c.get('social_proof'):
        structured_section += f'Social proof: {", ".join(signals_1c["social_proof"])}\n'
    if signals_1c.get('pricing_tiers'):
        structured_section += f'Pricing tiers: {", ".join(signals_1c["pricing_tiers"])}\n'
    if signals_1c.get('job_departments'):
        structured_section += f'Hiring departments: {", ".join(signals_1c["job_departments"])}\n'
    if signals_1c.get('job_listings'):
        structured_section += f'Job listings found: {", ".join(signals_1c["job_listings"][:5])}\n'
    if signals_1c.get('integration_partners'):
        structured_section += f'Integrations: {", ".join(signals_1c["integration_partners"][:8])}\n'
    if signals_1c.get('customer_industries'):
        structured_section += f'Customer industries: {", ".join(signals_1c["customer_industries"])}\n'
    if signals_1c.get('has_api_docs'):
        structured_section += 'Has public API/developer docs: Yes\n'
    if signals_1c.get('has_engineering_blog'):
        structured_section += 'Has engineering blog: Yes\n'

    linkedin_section = ''
    if linkedin.get('employee_count'):
        linkedin_section += f'Employees: {linkedin["employee_count"]}\n'
    if linkedin.get('founded_year'):
        linkedin_section += f'Founded: {linkedin["founded_year"]}\n'
    if linkedin.get('hq_location'):
        linkedin_section += f'HQ: {linkedin["hq_location"]}\n'
    if linkedin.get('linkedin_url'):
        linkedin_section += f'LinkedIn: {linkedin["linkedin_url"]}\n'
    if linkedin.get('github_url'):
        linkedin_section += f'GitHub: {linkedin["github_url"]}\n'
    if linkedin.get('engineering_topics'):
        linkedin_section += f'Engineering topics: {", ".join(linkedin["engineering_topics"])}\n'
    if linkedin.get('engineering_blog_posts'):
        linkedin_section += f'Blog posts: {", ".join(linkedin["engineering_blog_posts"][:3])}\n'

    dm_section = (
        f'Role: {contact_role}\n'
        f'Seniority: {decision_maker.get("seniority","")}\n'
        f'Responsibilities: {decision_maker.get("role_responsibilities","")}\n'
        f'Pain points: {decision_maker.get("role_pain_points","")}\n'
        f'Outreach angle: {decision_maker.get("outreach_angle","")}\n'
    )
    if decision_maker.get('bio_snippet'):
        dm_section += f'Bio: {decision_maker["bio_snippet"]}\n'
    if decision_maker.get('public_content_hints'):
        dm_section += f'Public content: {", ".join(decision_maker["public_content_hints"])}\n'

    return f"""You are a senior SDR research analyst at a B2B engineering staffing firm (Shiksha Infotech).
You have completed a full multi-phase research process on a prospect company.
Your job is to synthesize ALL gathered evidence into a structured research brief, then derive personalization.

CONTACT: {contact_name or 'Unknown'} | ROLE: {contact_role or 'Unknown'} | COMPANY: {company} | DOMAIN: {domain or 'NOT AVAILABLE'}

DATA AVAILABILITY:
- Website crawled: {'YES — ' + str(len(pages)) + ' pages' if pages else 'NO — website unreachable or domain unknown'}
- External data (Crunchbase/news/G2): {'YES' if (external_section.strip()) else 'NO'}
- Tech stack detected: {'YES — ' + str(len(tech.get('all_techs',[]))) + ' tools' if tech.get('all_techs') else 'NO'}
- Buying signals found: {len(buying.get('scored_signals', []))}

IMPORTANT: If website was NOT crawled and external data is thin, you MUST set confidence_score below 30 and company_summary to "Insufficient public information found for {company}." Do NOT invent facts.

═══ WEBSITE DATA ═══
{website_section or 'Not available — website unreachable.'}

═══ SUBPAGE CONTENT ═══
{pages_section[:3000] or 'Not available — no subpages crawled.'}

═══ TECHNOLOGY STACK ═══
{tech_section or 'Not detected.'}

═══ BUYING SIGNALS (with evidence) ═══
{signals_section or 'None detected.'}

═══ STRUCTURED SIGNALS ═══
{structured_section or 'None.'}

═══ COMPANY PROFILE ═══
{linkedin_section or 'Not available.'}

═══ EXTERNAL INTELLIGENCE ═══
{external_section or 'Not available — no Crunchbase/news/G2 data found.'}

═══ DECISION MAKER PROFILE ═══
{dm_section}

═══ YOUR TASK ═══
Produce a JSON research brief. Base EVERY field ONLY on evidence above. Do NOT invent facts.
If a field has no evidence, use empty string or 0.

{{
  "company_summary": "2-3 factual sentences about what the company does, who they serve, and their stage. If no data found, write: Insufficient public information found for {company}.",
  "business_model": "B2B SaaS / B2C / marketplace / agency / services / etc.",
  "icp": "Who their customers are — industries, company sizes, buyer roles",
  "products": "Main products or services with specific names if found",
  "industry": "MUST be one of: {industries_list}",
  "company_size": "Estimated headcount range based on evidence",
  "country": "Country/HQ location if found",
  "founded_year": "Year founded if found, else empty string",
  "tech_stack_summary": "Comma-separated list of all detected technologies",
  "tech_stack_by_category": {{
    "frontend": "...",
    "analytics": "...",
    "crm": "...",
    "infra": "...",
    "hiring_ats": "...",
    "payments": "..."
  }},
  "growth_signals": "Specific evidence of growth, funding, hiring, expansion — quote actual findings",
  "buying_signals_summary": "Why this company likely needs engineering talent — based on evidence only",
  "top_buying_signals": ["signal 1 with evidence", "signal 2 with evidence", "signal 3 with evidence"],
  "personalization_opportunities": [
    "Specific verifiable fact 1 that can open an email naturally",
    "Specific verifiable fact 2 that can open an email naturally",
    "Specific verifiable fact 3 that can open an email naturally"
  ],
  "contact_role_context": "What {contact_name}'s role at {company} likely involves day-to-day",
  "contact_pain_points": "Specific pain points this person faces based on role + company stage. If no company data, leave empty.",
  "outreach_angle": "The single best angle for cold outreach to this specific person at this company. If no company data, leave empty.",
  "pain_points": "Engineering/tech challenges based on their stack, stage, and signals. If no data, leave empty.",
  "icp_score": 0,
  "confidence_score": 0,
  "confidence_reason": "Explain what data was found and what was missing",
  "research_gaps": "What information was NOT found that would improve personalization"
}}

SCORING RULES:
- icp_score 0-100: 80+ if tech company actively hiring engineers with funding. 60+ if tech company growing. 40+ if tech company. <40 if non-tech. 0 if no data.
- confidence_score 0-100: 80+ if multiple pages crawled + external data found. 50+ if homepage only. <30 if almost nothing found. 0 if no website and no external data.
- personalization_opportunities: ONLY facts actually found above. If nothing specific found, return [].
- Do NOT invent funding amounts, employee counts, product names, or customer names not in the data.
- Return ONLY valid JSON. No markdown fences, no explanation outside the JSON."""


def _empty_research(company: str, domain: str) -> dict:
    return {
        'company_summary': f'Research unavailable for {company or domain}.',
        'business_model': '', 'icp': '', 'products': '',
        'industry': '', 'company_size': '', 'country': '', 'founded_year': '',
        'tech_stack_summary': '',
        'tech_stack_by_category': {},
        'growth_signals': '', 'buying_signals_summary': '',
        'top_buying_signals': [],
        'personalization_opportunities': [],
        'contact_role_context': '', 'contact_pain_points': '',
        'outreach_angle': '', 'pain_points': '',
        'icp_score': 0, 'confidence_score': 0,
        'confidence_reason': 'No data could be gathered.',
        'research_gaps': 'Website unreachable and no external data found.',
    }


def phase_6_synthesize(
    contact_name: str, contact_role: str, company: str, domain: str,
    homepage: dict, pages: dict, signals_1c: dict, signals_1d: dict,
    external_cb_ph: dict, external_news: dict, external_reviews: dict,
    linkedin: dict, tech: dict, buying: dict, decision_maker: dict,
) -> dict:
    """
    Phase 6: Feed all gathered evidence to AI.
    - Logs exact prompt sent to LLM
    - Measures total context chars before sending
    - Detects generic/low-quality output and retries with fallback prompt
    - If confidence still low after retry, returns explicit insufficient-data result
    """
    # ── Measure context quality before sending ──
    total_website_chars = sum(len(t) for t in pages.values())
    homepage_chars = len(homepage.get('hero_text', '')) + len(homepage.get('meta_description', ''))
    external_chars = (
        len(external_cb_ph.get('crunchbase_description', '')) +
        len(external_news.get('news_items', []).__str__()) +
        len(external_reviews.get('g2_description', ''))
    )
    total_context_chars = total_website_chars + homepage_chars + external_chars

    app_logger.info(
        f'[RESEARCH 6] Context quality | company={company} '
        f'website_chars={total_website_chars} '
        f'homepage_chars={homepage_chars} '
        f'external_chars={external_chars} '
        f'total={total_context_chars} '
        f'pages_crawled={list(pages.keys())}'
    )

    # ── Warn if context is very thin ──
    if total_context_chars < 200:
        app_logger.warning(
            f'[RESEARCH 6] THIN CONTEXT for {company} ({total_context_chars} chars) '
            f'— fallback triggered. Reachable={homepage.get("reachable")}'
        )

    prompt = _build_synthesis_prompt(
        contact_name, contact_role, company, domain,
        homepage, pages, signals_1c, signals_1d,
        external_cb_ph, external_news, external_reviews,
        linkedin, tech, buying, decision_maker,
    )

    # ── Log exact prompt (truncated for readability) ──
    app_logger.info(
        f'[RESEARCH 6] PROMPT SENT ({len(prompt)} chars) | '
        f'first 300: {prompt[:300].replace(chr(10), " ")}'
    )

    raw = _call_ai(prompt, max_tokens=1800)
    if not raw:
        app_logger.warning(f'[RESEARCH 6] AI returned nothing for {company}')
        return _empty_research(company, domain)

    app_logger.info(f'[RESEARCH 6] AI response ({len(raw)} chars): {raw[:200].replace(chr(10), " ")}')

    # ── Parse JSON ──
    data = None
    try:
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            defaults = _empty_research(company, domain)
            for k, v in defaults.items():
                data.setdefault(k, v)
    except (json.JSONDecodeError, AttributeError) as e:
        error_logger.warning(f'[RESEARCH 6] JSON parse failed: {e} | raw[:200]={raw[:200]}')

    if not data:
        return _empty_research(company, domain)

    conf = int(data.get('confidence_score', 0) or 0)
    summary = data.get('company_summary', '')

    # ── Generic output detection ──
    is_generic = _is_generic_output(summary + ' ' + data.get('products', '') + ' ' + data.get('buying_signals_summary', ''))
    if is_generic:
        app_logger.warning(
            f'[RESEARCH 6] GENERIC OUTPUT detected for {company} — '
            f'summary="{summary[:100]}" — triggering retry'
        )

    # ── Retry if: confidence < 25 OR generic output detected ──
    if (conf < 25 or is_generic) and total_context_chars > 100:
        app_logger.info(f'[RESEARCH 6] RETRY with focused prompt | conf={conf} generic={is_generic}')
        retry_prompt = _build_fallback_prompt(
            contact_name, contact_role, company, domain,
            homepage, pages, external_cb_ph, external_news
        )
        app_logger.info(
            f'[RESEARCH 6] RETRY PROMPT ({len(retry_prompt)} chars) | '
            f'first 300: {retry_prompt[:300].replace(chr(10), " ")}'
        )
        raw2 = _call_ai(retry_prompt, max_tokens=1200)
        if raw2:
            app_logger.info(f'[RESEARCH 6] RETRY response: {raw2[:200].replace(chr(10), " ")}')
            try:
                m2 = re.search(r'\{.*\}', raw2, re.DOTALL)
                if m2:
                    data2 = json.loads(m2.group())
                    conf2 = int(data2.get('confidence_score', 0) or 0)
                    # Only use retry result if it's better
                    if conf2 > conf or not _is_generic_output(
                        data2.get('company_summary', '') + data2.get('products', '')
                    ):
                        defaults = _empty_research(company, domain)
                        for k, v in defaults.items():
                            data2.setdefault(k, v)
                        data = data2
                        conf = conf2
                        app_logger.info(f'[RESEARCH 6] Using retry result | conf={conf2}')
            except Exception as e:
                error_logger.warning(f'[RESEARCH 6] Retry JSON parse failed: {e}')

    # ── Final confidence check ──
    final_conf = int(data.get('confidence_score', 0) or 0)
    if final_conf < 15 and total_context_chars < 150:
        app_logger.warning(
            f'[RESEARCH 6] INSUFFICIENT DATA for {company} '
            f'(conf={final_conf}, chars={total_context_chars}) — marking as low-confidence'
        )
        data['company_summary'] = (
            f'Insufficient public information found for {company}. '
            f'Website was {"reachable" if homepage.get("reachable") else "unreachable"} '
            f'but contained minimal business content.'
        )
        data['personalization_opportunities'] = []
        data['confidence_reason'] = (
            f'Only {total_context_chars} characters of content extracted across '
            f'{len(pages)} pages. Cannot produce reliable research.'
        )

    app_logger.info(
        f'[RESEARCH 6] FINAL | company={company} '
        f'conf={data.get("confidence_score")}/100 '
        f'icp={data.get("icp_score")}/100 '
        f'generic={_is_generic_output(data.get("company_summary",""))} '
        f'personalization_hooks={len(data.get("personalization_opportunities",[]))}'
    )
    return data


def _build_context_string(research: dict, buying: dict,
                           tech: dict, decision_maker: dict) -> str:
    """
    Build the rich context string stored in contacts.context.
    Used by campaign_executor._generate_ai_body() for email personalization.
    """
    parts = []

    if research.get('company_summary'):
        parts.append(f'COMPANY: {research["company_summary"]}')
    if research.get('business_model'):
        parts.append(f'MODEL: {research["business_model"]}')
    if research.get('products'):
        parts.append(f'PRODUCTS: {research["products"]}')
    if research.get('icp'):
        parts.append(f'THEIR CUSTOMERS: {research["icp"]}')
    if research.get('tech_stack_summary'):
        parts.append(f'TECH STACK: {research["tech_stack_summary"]}')
    if research.get('growth_signals'):
        parts.append(f'GROWTH SIGNALS: {research["growth_signals"]}')
    if research.get('buying_signals_summary'):
        parts.append(f'WHY THEY NEED ENGINEERS: {research["buying_signals_summary"]}')
    if research.get('pain_points'):
        parts.append(f'PAIN POINTS: {research["pain_points"]}')

    # Top buying signals with evidence
    top_sigs = buying.get('scored_signals', [])[:4]
    if top_sigs:
        sig_lines = '\n'.join(
            f'- [{s["category"].upper()}] {s["label"]}: {s["evidence"][:120]}'
            for s in top_sigs
        )
        parts.append(f'VERIFIED SIGNALS:\n{sig_lines}')

    # Personalization hooks — the most important section
    opps = research.get('personalization_opportunities', [])
    if opps:
        parts.append('PERSONALIZATION HOOKS:\n' + '\n'.join(f'- {o}' for o in opps[:4]))

    # Decision maker context — only include if we have real company data
    conf = research.get('confidence_score', 0)
    has_real_data = bool(
        research.get('company_summary', '').lower() not in ('', 'research unavailable') and
        'research unavailable' not in research.get('company_summary', '').lower() and
        'insufficient public information' not in research.get('company_summary', '').lower()
    )
    if has_real_data:
        if research.get('contact_pain_points'):
            parts.append(f'CONTACT PAIN POINTS: {research["contact_pain_points"]}')
        elif decision_maker.get('role_pain_points'):
            parts.append(f'CONTACT PAIN POINTS: {decision_maker["role_pain_points"]}')
        if research.get('outreach_angle'):
            parts.append(f'OUTREACH ANGLE: {research["outreach_angle"]}')
        elif decision_maker.get('outreach_angle'):
            parts.append(f'OUTREACH ANGLE: {decision_maker["outreach_angle"]}')
    if decision_maker.get('bio_snippet'):
        parts.append(f'CONTACT BIO: {decision_maker["bio_snippet"]}')

    # Confidence note
    if conf < 30:
        parts.append(
            f'NOTE: Low confidence ({conf}/100) — limited data found. '
            f'{research.get("research_gaps", "")}'
        )

    return '\n\n'.join(parts)


# ══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def research_contact(contact_id: int) -> dict:
    """
    Full 6-phase deep research pipeline for a single contact.
    Orchestrates all phases, persists results to DB.

    Returns: {success, research, context, confidence, icp_score}
    """
    from utils.db import get_db
    from datetime import datetime

    conn = get_db()
    try:
        contact = conn.execute(
            'SELECT * FROM contacts WHERE id=?', (contact_id,)
        ).fetchone()
        if not contact:
            return {'success': False, 'error': 'Contact not found'}

        email   = contact['email'] or ''
        company = contact['company'] or ''
        name    = contact['name'] or ''
        role    = contact['designation'] if 'designation' in contact.keys() else ''
        website = contact['website'] if 'website' in contact.keys() else ''

        # Resolve domain — website always wins
        # Email domain only used if it plausibly matches the company name
        domain = ''
        if website:
            domain = re.sub(r'https?://', '', str(website)).split('/')[0].strip()
        elif '@' in email:
            email_domain = email.split('@')[1]
            if email_domain not in FREE_PROVIDERS:
                # Only use email domain if it loosely matches company name
                # e.g. company="Checkr" email="x@checkr.com" → use it
                # e.g. company="Elyra" email="x@cresnd.com" → skip it
                company_slug = re.sub(r'[^a-z0-9]', '', (company or '').lower())
                domain_slug  = re.sub(r'[^a-z0-9]', '', email_domain.split('.')[0].lower())
                if company_slug and domain_slug and (
                    company_slug in domain_slug or domain_slug in company_slug
                ):
                    domain = email_domain
                else:
                    # Domain doesn't match company — don't crawl wrong site
                    app_logger.info(
                        f'[RESEARCH] Skipping email domain {email_domain!r} '
                        f'— does not match company {company!r}'
                    )

        app_logger.info(
            f'[RESEARCH] START | contact={contact_id} '
            f'company={company} domain={domain} role={role}'
        )

        # Mark as processing
        conn.execute(
            "UPDATE contacts SET enrichment_status='processing' WHERE id=?",
            (contact_id,)
        )
        conn.commit()

        # ── Phase 1a: Homepage ──
        homepage = phase_1a_homepage(domain) if domain else {
            'base_url': '', 'title': '', 'meta_description': '',
            'og_title': '', 'og_description': '', 'og_type': '',
            'canonical': '', 'hero_text': '', 'homepage_html': '',
            'response_headers': {}, 'reachable': False,
        }

        # ── Phase 1b: Deep page crawl ──
        crawl = phase_1b_deep_crawl(
            homepage.get('base_url', ''),
            homepage.get('homepage_html', '')
        ) if homepage.get('reachable') else {'pages': {}, 'pages_html': {}, 'crawled_urls': []}

        pages      = crawl['pages']
        pages_html = crawl['pages_html']

        # ── Phase 1c: Structured signal extraction ──
        signals_1c = phase_1c_extract_signals(pages, pages_html)

        # ── Phase 1d: Signal aggregation ──
        signals_1d = phase_1d_aggregate_signals(pages, homepage)

        # ── Phase 2a: Crunchbase + Product Hunt — runs even without domain ──
        ext_cb_ph = phase_2a_crunchbase_producthunt(company, domain) if company else {}

        # ── Phase 2b: News + press — runs even without domain ──
        ext_news = phase_2b_news_press(company, domain) if company else {}

        # ── Phase 2c: G2 + Capterra — runs even without domain ──
        ext_reviews = phase_2c_reviews(company, domain) if company else {}

        # ── Phase 2d: LinkedIn + engineering blog ──
        linkedin = phase_2d_linkedin_engblog(company, domain, pages, pages_html)

        # ── Phase 3: Tech stack detection ──
        tech = phase_3_tech_detection(homepage, pages_html)

        # ── Phase 4: Buying signal scoring ──
        buying = phase_4_buying_signals(
            pages, homepage, signals_1d, ext_cb_ph, tech, linkedin
        )

        # ── Phase 5: Decision maker research ──
        decision_maker = phase_5_decision_maker(
            name, role, email, domain, pages, pages_html
        )

        # ── Phase 6: AI synthesis ──
        research = phase_6_synthesize(
            name, role, company, domain,
            homepage, pages, signals_1c, signals_1d,
            ext_cb_ph, ext_news, ext_reviews,
            linkedin, tech, buying, decision_maker,
        )

        # ── Build context string ──
        context_str = _build_context_string(research, buying, tech, decision_maker)

        # ── Validate industry ──
        from services.industry_detector import INDUSTRIES
        industry = research.get('industry', '')
        if industry not in INDUSTRIES:
            industry = ''

        # ── Persist to DB ──
        icp_score  = int(research.get('icp_score', 0) or 0)
        conf_score = int(research.get('confidence_score', 0) or 0)
        score_delta = max(0, icp_score - 50)

        conn.execute("""
            UPDATE contacts SET
                context=?, industry=?, company_size=?, country=?,
                company_description=?, technologies=?,
                enrichment_status=?, last_enriched_at=?,
                lead_score=COALESCE(lead_score,0) + ?
            WHERE id=?
        """, (
            context_str,
            industry,
            research.get('company_size', ''),
            research.get('country', ''),
            research.get('company_summary', ''),
            research.get('tech_stack_summary', ''),
            'enriched',
            datetime.now(),
            score_delta,
            contact_id,
        ))
        conn.commit()

        app_logger.info(
            f'[RESEARCH] DONE | contact={contact_id} '
            f'confidence={conf_score}/100 icp={icp_score}/100 '
            f'industry={industry} signals={buying["signal_count"] if "signal_count" in buying else len(buying.get("scored_signals",[]))}'
        )

        return {
            'success':    True,
            'research':   research,
            'context':    context_str,
            'confidence': conf_score,
            'icp_score':  icp_score,
        }

    except Exception as e:
        error_logger.error(f'[RESEARCH] research_contact failed for {contact_id}: {e}')
        try:
            conn.execute(
                "UPDATE contacts SET enrichment_status='failed' WHERE id=?",
                (contact_id,)
            )
            conn.commit()
        except Exception:
            pass
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def research_contacts_bulk(contact_ids: list) -> dict:
    """Run deep research on multiple contacts. 2s delay between each."""
    enriched = failed = 0
    for cid in contact_ids:
        result = research_contact(cid)
        if result.get('success'):
            enriched += 1
        else:
            failed += 1
        time.sleep(2)
    return {'enriched': enriched, 'failed': failed, 'total': len(contact_ids)}
