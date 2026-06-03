import { Resend } from 'resend';

function getResend() {
  const key = process.env.RESEND_API_KEY;
  if (!key) throw new Error('RESEND_API_KEY not configured');
  return new Resend(key);
}

export async function sendOtpEmail(email: string, otp: string): Promise<boolean> {
  try {
    const resend = getResend();
    const { error } = await resend.emails.send({
      from: process.env.RESEND_FROM_EMAIL || 'OutreachOS <noreply@yourdomain.com>',
      to: email,
      subject: 'Your login verification code',
      html: getOtpEmailTemplate(otp),
    });

    if (error) {
      console.error('Resend error:', error);
      return false;
    }
    return true;
  } catch (err) {
    console.error('Email send failed:', err);
    return false;
  }
}

function getOtpEmailTemplate(otp: string): string {
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
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:440px;background:#fff;border-radius:16px;border:1px solid #e5e7eb;overflow:hidden;">
        <tr><td style="padding:32px 32px 0;">
          <div style="font-size:20px;font-weight:800;color:#111827;margin-bottom:4px;">OutreachOS</div>
          <div style="font-size:12px;color:#6366f1;font-weight:600;">VERIFICATION CODE</div>
        </td></tr>
        <tr><td style="padding:24px 32px;">
          <p style="font-size:15px;color:#374151;margin:0 0 20px;line-height:1.6;">
            Enter this code to complete your login:
          </p>
          <div style="background:#f8fafc;border:2px dashed #e5e7eb;border-radius:12px;padding:20px;text-align:center;margin:0 0 20px;">
            <span style="font-size:36px;font-weight:800;letter-spacing:8px;color:#111827;">${otp}</span>
          </div>
          <p style="font-size:13px;color:#6B7280;margin:0;line-height:1.5;">
            This code expires in <strong>5 minutes</strong>. If you didn't request this, please ignore this email.
          </p>
        </td></tr>
        <tr><td style="padding:20px 32px;border-top:1px solid #f3f4f6;">
          <p style="font-size:11px;color:#9CA3AF;margin:0;text-align:center;">
            © ${new Date().getFullYear()} OutreachOS. Sent securely.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
}
