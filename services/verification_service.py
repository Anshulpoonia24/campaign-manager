import dns.resolver
import smtplib

# MX cache to avoid re-checking same domain
mx_cache = {}


def reset_mx_cache():
    global mx_cache
    mx_cache = {}


def verify_email(email):
    if ';' in email or ',' in email:
        parts = [e.strip().lower() for e in email.replace(',', ';').split(';') if '@' in e.strip()]
        if not parts:
            return False, "No valid email found"
        email = parts[0]

    try:
        domain = email.split('@')[1]

        if domain in mx_cache:
            mx_valid, mx_reason = mx_cache[domain]
            if not mx_valid:
                return False, mx_reason
        else:
            try:
                mx_records = dns.resolver.resolve(domain, 'MX', lifetime=5)
                mx_hosts = sorted(mx_records, key=lambda x: x.preference)
                mx_cache[domain] = (True, str(mx_hosts[0].exchange).rstrip('.'))
            except dns.resolver.NXDOMAIN:
                mx_cache[domain] = (False, "Domain does not exist")
                return False, "Domain does not exist"
            except dns.resolver.NoAnswer:
                mx_cache[domain] = (False, "No MX record")
                return False, "No MX record"
            except dns.resolver.LifetimeTimeout:
                mx_cache[domain] = (True, domain)
                return True, "Valid - DNS timeout but domain likely exists"
            except Exception as e:
                mx_cache[domain] = (False, f"DNS error: {str(e)[:40]}")
                return False, f"DNS error: {str(e)[:40]}"

        mx_host = mx_cache[domain][1]

        catchall_domains = ['gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'live.com', 'icloud.com', 'me.com', 'aol.com', 'protonmail.com', 'proton.me']
        if domain in catchall_domains:
            return True, f"Valid - {domain} (catch-all, unverifiable)"

        try:
            smtp = smtplib.SMTP(timeout=8)
            smtp.connect(mx_host, 25)
            smtp.helo('verify.local')
            smtp.mail('verify@verify.local')
            code, msg = smtp.rcpt(email)
            smtp.quit()

            if code == 250:
                return True, "Valid - mailbox exists (SMTP verified)"
            elif code == 550 or code == 551 or code == 553:
                return False, f"Mailbox does not exist ({code})"
            elif code == 452 or code == 421:
                return True, "Valid - server busy but domain OK"
            else:
                return True, f"Likely valid - server responded {code}"
        except smtplib.SMTPServerDisconnected:
            return True, "Valid - MX exists (SMTP blocked)"
        except smtplib.SMTPConnectError:
            return True, "Valid - MX exists (connection refused)"
        except (ConnectionRefusedError, OSError, TimeoutError):
            return True, "Valid - MX exists (port 25 blocked)"
        except Exception as e:
            return True, f"Valid - MX exists ({str(e)[:30]})"

    except Exception as e:
        return False, "Invalid email format"
