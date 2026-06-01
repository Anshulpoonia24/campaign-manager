"""
utils/constants.py — Shared constants
"""

# Free/catch-all email domains — AI company cache should not use domain as cache key for these.
# Company context from gmail.com contact is meaningless for other gmail.com contacts.
CATCHALL_DOMAINS = frozenset({
    'gmail.com', 'googlemail.com',
    'outlook.com', 'hotmail.com', 'hotmail.co.uk', 'hotmail.fr',
    'yahoo.com', 'yahoo.in', 'yahoo.co.uk', 'yahoo.co.in',
    'live.com', 'live.in', 'live.co.uk',
    'icloud.com', 'me.com', 'mac.com',
    'aol.com',
    'protonmail.com', 'proton.me',
    'rediffmail.com',
    'zoho.com',
    'yandex.com', 'yandex.ru',
    'gmx.com', 'gmx.de', 'gmx.net',
    'msn.com',
})
