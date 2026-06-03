import { Resend } from 'resend';

function getResend() {
  const key = process.env.RESEND_API_KEY;
  if (!key) throw new Error('RESEND_API_KEY not configured');
  return new Resend(key);
}

export async function sendWelcomeEmail(email: string, name: string): Promise<boolean> {
  try {
    const resend = getResend();
    const { error } = await resend.emails.send({
      from: process.env.RESEND_FROM_EMAIL || 'OutreachOS <onboarding@resend.dev>',
      to: email,
      subject: 'Welcome to OutreachOS — Your AI outreach engine is ready 🚀',
      html: getWelcomeTemplate(name),
    });

    if (error) {
      console.error('Welcome email error:', error);
      return false;
    }
    return true;
  } catch (err) {
    console.error('Welcome email failed:', err);
    return false;
  }
}

function getWelcomeTemplate(name: string): string {
  const firstName = name?.split(' ')[0] || 'there';
  return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:48px 20px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:16px;border:1px solid #e5e7eb;overflow:hidden;">
        
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:40px 32px;text-align:center;">
          <div style="font-size:28px;font-weight:800;color:#fff;margin-bottom:8px;">Welcome to OutreachOS</div>
          <div style="font-size:14px;color:rgba(255,255,255,0.85);">Your AI-powered outreach engine is ready</div>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding:32px;">
          <p style="font-size:16px;color:#111827;margin:0 0 20px;line-height:1.6;">
            Hey ${firstName} 👋
          </p>
          <p style="font-size:15px;color:#374151;margin:0 0 24px;line-height:1.7;">
            You're in! Your OutreachOS workspace is live and ready to send. Here's how to get your first campaign running in under 5 minutes:
          </p>

          <!-- Steps -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 28px;">
            <tr><td style="padding:12px 16px;background:#f8fafc;border-radius:10px;border-left:3px solid #6366f1;margin-bottom:8px;">
              <div style="font-size:13px;font-weight:700;color:#6366f1;margin-bottom:2px;">Step 1</div>
              <div style="font-size:14px;color:#374151;">Connect your SMTP accounts (Gmail, Outlook, SES, any provider)</div>
            </td></tr>
            <tr><td style="height:8px;"></td></tr>
            <tr><td style="padding:12px 16px;background:#f8fafc;border-radius:10px;border-left:3px solid #8b5cf6;">
              <div style="font-size:13px;font-weight:700;color:#8b5cf6;margin-bottom:2px;">Step 2</div>
              <div style="font-size:14px;color:#374151;">Upload your leads (CSV) or connect your CRM</div>
            </td></tr>
            <tr><td style="height:8px;"></td></tr>
            <tr><td style="padding:12px 16px;background:#f8fafc;border-radius:10px;border-left:3px solid #10b981;">
              <div style="font-size:13px;font-weight:700;color:#10b981;margin-bottom:2px;">Step 3</div>
              <div style="font-size:14px;color:#374151;">Let AI generate personalized campaigns and hit send</div>
            </td></tr>
          </table>

          <!-- CTA Button -->
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding:8px 0 28px;">
              <a href="${process.env.NEXT_PUBLIC_APP_URL || 'https://ertyui.online'}/dashboard" style="display:inline-block;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;font-size:15px;font-weight:700;text-decoration:none;padding:14px 36px;border-radius:10px;box-shadow:0 4px 14px rgba(99,102,241,0.3);">
                Go to Dashboard →
              </a>
            </td></tr>
          </table>

          <!-- Features highlight -->
          <div style="padding:20px;background:#fafafe;border-radius:12px;border:1px solid #f3f4f6;margin-bottom:24px;">
            <div style="font-size:13px;font-weight:700;color:#111827;margin-bottom:12px;">What you get:</div>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:4px 0;font-size:13px;color:#374151;">✦ AI Copilot — manage campaigns with natural language</td>
              </tr>
              <tr>
                <td style="padding:4px 0;font-size:13px;color:#374151;">✦ Smart SMTP rotation & deliverability protection</td>
              </tr>
              <tr>
                <td style="padding:4px 0;font-size:13px;color:#374151;">✦ AI-powered personalization per contact</td>
              </tr>
              <tr>
                <td style="padding:4px 0;font-size:13px;color:#374151;">✦ Real-time analytics & inbox intelligence</td>
              </tr>
              <tr>
                <td style="padding:4px 0;font-size:13px;color:#374151;">✦ Multi-step sequences with auto follow-ups</td>
              </tr>
            </table>
          </div>

          <p style="font-size:14px;color:#6B7280;margin:0;line-height:1.6;">
            Need help? Just reply to this email or use the AI Copilot inside your dashboard — it knows everything about your account.
          </p>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:20px 32px;border-top:1px solid #f3f4f6;text-align:center;">
          <p style="font-size:11px;color:#9CA3AF;margin:0 0 8px;">
            © ${new Date().getFullYear()} OutreachOS — AI-Powered Cold Email Infrastructure
          </p>
          <p style="font-size:11px;color:#9CA3AF;margin:0;">
            <a href="${process.env.NEXT_PUBLIC_APP_URL || 'https://ertyui.online'}" style="color:#6366f1;text-decoration:none;">Website</a> · 
            <a href="${process.env.NEXT_PUBLIC_APP_URL || 'https://ertyui.online'}/dashboard" style="color:#6366f1;text-decoration:none;">Dashboard</a> · 
            <a href="#" style="color:#9CA3AF;text-decoration:none;">Unsubscribe</a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>`;
}
