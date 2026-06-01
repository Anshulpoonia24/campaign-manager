"""Patch get_smtp_creds fallback to use workspace-only settings."""
with open('app.py', 'rb') as f:
    raw = f.read()

old = (
    b"    # Try rotation first, fallback to settings\r\n"
    b"    def get_smtp_creds():\r\n"
    b"        from services.smtp_rotation import get_next_smtp_account, append_signature\r\n"
    b"        account = get_next_smtp_account(workspace_id=wid)\r\n"
    b"        if account:\r\n"
    b"            return account  # Full identity object\r\n"
    b"        # Fallback \xe2\x80\x94 backup SMTP only, no identity override\r\n"
    b"        return {\r\n"
    b"            'server':     get_setting('smtp_server'),\r\n"
    b"            'port':       int(get_setting('smtp_port') or 587),\r\n"
    b"            'username':   get_setting('smtp_username'),\r\n"
    b"            'password':   get_setting('smtp_password'),\r\n"
    b"            'from_email': get_setting('from_email') or get_setting('smtp_username'),\r\n"
    b"            'from_name':  get_setting('from_name'),\r\n"
    b"            'reply_to':   get_setting('reply_to'),\r\n"
    b"            'bcc_emails': get_setting('bcc_emails'),\r\n"
    b"            'signature':  '',\r\n"
    b"            'account_id': None,\r\n"
    b"            'email':      get_setting('from_email') or get_setting('smtp_username'),\r\n"
    b"            'smtp_server': get_setting('smtp_server'),\r\n"
    b"            'smtp_port':  int(get_setting('smtp_port') or 587),\r\n"
    b"        }\r\n"
)

new = (
    b"    # Try rotation first, fallback to workspace-only settings\r\n"
    b"    def get_smtp_creds():\r\n"
    b"        from services.smtp_rotation import get_next_smtp_account, append_signature\r\n"
    b"        account = get_next_smtp_account(workspace_id=wid)\r\n"
    b"        if account:\r\n"
    b"            return account  # Full identity object\r\n"
    b"        # Fallback \xe2\x80\x94 strict workspace-only, no global bleed\r\n"
    b"        from utils.db import get_workspace_only_setting as _ws\r\n"
    b"        fb_server   = _ws('smtp_server',   wid)\r\n"
    b"        fb_port     = _ws('smtp_port',     wid)\r\n"
    b"        fb_username = _ws('smtp_username', wid)\r\n"
    b"        fb_password = _ws('smtp_password', wid)\r\n"
    b"        if not fb_server or not fb_username or not fb_password:\r\n"
    b"            return None  # No SMTP configured for this workspace\r\n"
    b"        return {\r\n"
    b"            'server':         fb_server,\r\n"
    b"            'port':           int(fb_port or 587),\r\n"
    b"            'username':       fb_username,\r\n"
    b"            'login_username': _ws('smtp_login_username', wid) or fb_username,\r\n"
    b"            'password':       fb_password,\r\n"
    b"            'from_email':     _ws('from_email', wid) or fb_username,\r\n"
    b"            'from_name':      _ws('from_name',  wid),\r\n"
    b"            'reply_to':       _ws('reply_to',   wid),\r\n"
    b"            'bcc_emails':     _ws('bcc_emails', wid),\r\n"
    b"            'signature':      '',\r\n"
    b"            'account_id':     None,\r\n"
    b"            'email':          _ws('from_email', wid) or fb_username,\r\n"
    b"            'smtp_server':    fb_server,\r\n"
    b"            'smtp_port':      int(fb_port or 587),\r\n"
    b"        }\r\n"
)

if old in raw:
    raw = raw.replace(old, new, 1)
    print('FIXED')
else:
    print('NOT FOUND')

with open('app.py', 'wb') as f:
    f.write(raw)
