import { NextResponse } from 'next/server';
import { headers } from 'next/headers';
import { createServiceClient } from '@/lib/supabase/server';
import { rateLimit } from '@/lib/rate-limit';
import { getClientIp } from '@/utils';
import { sendWelcomeEmail } from '@/lib/welcome-email';

export async function POST(request: Request) {
  const headersList = await headers();
  const ip = getClientIp(headersList);
  const { userId, email, otp } = await request.json();

  if (!userId || !email || !otp) {
    return NextResponse.json({ error: 'Missing fields' }, { status: 400 });
  }

  const { success: withinLimit } = rateLimit(`otp-verify:${ip}`, 10, 5 * 60 * 1000);
  if (!withinLimit) {
    return NextResponse.json({ error: 'Rate limit exceeded' }, { status: 429 });
  }

  const serviceClient = await createServiceClient();

  const { data: otpRecord } = await serviceClient
    .from('login_otps')
    .select('*')
    .eq('user_id', userId)
    .eq('verified', false)
    .order('created_at', { ascending: false })
    .limit(1)
    .single();

  if (!otpRecord) {
    return NextResponse.json({ error: 'No active code found' }, { status: 400 });
  }

  if (new Date(otpRecord.expires_at) < new Date()) {
    return NextResponse.json({ error: 'Code expired', expired: true }, { status: 400 });
  }

  if (otpRecord.attempts >= otpRecord.max_attempts) {
    return NextResponse.json({ error: 'Max attempts reached', maxAttempts: true }, { status: 400 });
  }

  await serviceClient.from('login_otps')
    .update({ attempts: otpRecord.attempts + 1 })
    .eq('id', otpRecord.id);

  if (otpRecord.otp !== otp) {
    const remaining = otpRecord.max_attempts - otpRecord.attempts - 1;
    return NextResponse.json({ error: `Incorrect code. ${remaining} left.`, attemptsRemaining: remaining }, { status: 400 });
  }

  // Mark verified
  await serviceClient.from('login_otps')
    .update({ verified: true })
    .eq('id', otpRecord.id);

  // Generate a session link for the user
  const { data: linkData, error: linkError } = await serviceClient.auth.admin.generateLink({
    type: 'magiclink',
    email,
  });

  if (linkError || !linkData) {
    return NextResponse.json({ error: 'Auth failed' }, { status: 500 });
  }

  // Audit log
  await serviceClient.from('audit_logs').insert({
    user_id: userId,
    email,
    action: 'login_otp_verified',
    status: 'success',
    ip_address: ip,
    user_agent: headersList.get('user-agent'),
  });

  // Send welcome email on first verified login
  const { data: profile } = await serviceClient
    .from('user_profiles')
    .select('created_at, full_name, email_verified')
    .eq('user_id', userId)
    .single();

  if (profile && !profile.email_verified) {
    // Mark email as verified
    await serviceClient.from('user_profiles')
      .update({ email_verified: true })
      .eq('user_id', userId);
    // Send welcome email
    sendWelcomeEmail(email, profile.full_name || '').catch(console.error);
  }

  // Return the verification token for client-side session exchange
  const token_hash = linkData.properties?.hashed_token;

  return NextResponse.json({
    success: true,
    token_hash,
    email,
  });
}
