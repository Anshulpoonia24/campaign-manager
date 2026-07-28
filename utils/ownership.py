"""
utils/ownership.py — Resource ownership checks
================================================
Single-admin app: all resources belong to the one admin.
These helpers just verify the resource exists by id.
"""
from utils.db import get_db


def owns_contact(contact_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    conn.close()
    return row


def owns_campaign(campaign_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    conn.close()
    return row


def owns_smtp_account(account_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM smtp_accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    return row


def owns_email_sent(email_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM emails_sent WHERE id=?", (email_id,)).fetchone()
    conn.close()
    return row
