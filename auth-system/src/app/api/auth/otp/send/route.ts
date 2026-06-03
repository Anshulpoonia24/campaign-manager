import { NextResponse } from 'next/server';
import { headers } from 'next/headers';
import { createServiceClient } from '@/lib/supabase/server';
import { sendOtpEmail } from '@/lib/email';
import { rateLimit } from '@/lib/rate-limit';
import { generateOtp, getOtpExpiry, getClientIp } from '@/utils';

export async function POST(request: Request) {
  const headersList = await headers();
  const ip = getClientIp(headersList);
  const { userId, email } = await request.json();

  if (!userId || !email) {
    return NextResponse.json({ error: 'Missing fields' }, { status: 400 });
  }

  const { success } = rateLimit(`otp-send:${ip}`, 5, 5 * 60 * 1000);
  if (!success) {
    return NextResponse.json({ error: 'Rate limit exceeded' }, { status: 429 });
  }

  const serviceClient = await createServiceClient();
  const otp = generateOtp();
  const expiresAt = getOtpExpiry();

  // Invalidate old OTPs
  await serviceClient.from('login_otps')
    .update({ verified: true })
    .eq('user_id', userId)
    .eq('verified', false);

  await serviceClient.from('login_otps').insert({
    user_id: userId,
    email,
    otp,
    expires_at: expiresAt.toISOString(),
    ip_address: ip,
    user_agent: headersList.get('user-agent'),
  });

  const sent = await sendOtpEmail(email, otp);
  if (!sent) {
    return NextResponse.json({ error: 'Failed to send email' }, { status: 500 });
  }

  return NextResponse.json({ success: true, expiresAt: expiresAt.toISOString() });
}
