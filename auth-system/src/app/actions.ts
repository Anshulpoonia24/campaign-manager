'use server';

import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { createClient, createServiceClient } from '@/lib/supabase/server';
import { sendOtpEmail } from '@/lib/email';
import { rateLimit } from '@/lib/rate-limit';
import { generateOtp, getOtpExpiry, getClientIp } from '@/utils';
import { loginSchema, signupSchema, forgotPasswordSchema, resetPasswordSchema } from '@/lib/validations';

export async function signUpAction(formData: FormData) {
  const raw = {
    email: formData.get('email') as string,
    password: formData.get('password') as string,
    fullName: formData.get('fullName') as string,
  };

  const parsed = signupSchema.safeParse(raw);
  if (!parsed.success) {
    return { error: parsed.error.errors[0].message };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signUp({
    email: parsed.data.email,
    password: parsed.data.password,
    options: {
      data: { full_name: parsed.data.fullName },
      emailRedirectTo: `${process.env.NEXT_PUBLIC_APP_URL}/api/auth/callback`,
    },
  });

  if (error) {
    if (error.message.includes('already registered')) {
      return { error: 'An account with this email already exists' };
    }
    return { error: error.message };
  }

  // Log audit
  const serviceClient = await createServiceClient();
  const headersList = await headers();
  await serviceClient.from('audit_logs').insert({
    email: parsed.data.email,
    action: 'signup',
    status: 'success',
    ip_address: getClientIp(headersList),
    user_agent: headersList.get('user-agent'),
  });

  return { success: true, message: 'Check your email to verify your account' };
}

export async function signInAction(formData: FormData) {
  const raw = {
    email: formData.get('email') as string,
    password: formData.get('password') as string,
    rememberMe: formData.get('rememberMe') === 'on',
  };

  const parsed = loginSchema.safeParse(raw);
  if (!parsed.success) {
    return { error: parsed.error.errors[0].message };
  }

  const headersList = await headers();
  const ip = getClientIp(headersList);

  // Rate limit: 10 login attempts per 15 min per IP
  const { success: withinLimit } = rateLimit(`login:${ip}`, 10, 15 * 60 * 1000);
  if (!withinLimit) {
    return { error: 'Too many login attempts. Please try again later.' };
  }

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signInWithPassword({
    email: parsed.data.email,
    password: parsed.data.password,
  });

  if (error) {
    const serviceClient = await createServiceClient();
    await serviceClient.from('audit_logs').insert({
      email: parsed.data.email,
      action: 'login_failed',
      status: 'failure',
      ip_address: ip,
      user_agent: headersList.get('user-agent'),
      metadata: { reason: error.message },
    });
    return { error: 'Invalid email or password' };
  }

  // Check if email is verified
  if (!data.user.email_confirmed_at) {
    await supabase.auth.signOut();
    return { error: 'Please verify your email before logging in' };
  }

  // Check auth provider — Google users skip OTP
  const provider = data.user.app_metadata?.provider;
  if (provider === 'google') {
    redirect('/dashboard');
  }

  // Generate and send OTP for email/password users
  const otp = generateOtp();
  const expiresAt = getOtpExpiry();

  const serviceClient = await createServiceClient();

  // Invalidate existing OTPs
  await serviceClient.from('login_otps')
    .update({ verified: true })
    .eq('user_id', data.user.id)
    .eq('verified', false);

  // Create new OTP
  await serviceClient.from('login_otps').insert({
    user_id: data.user.id,
    email: data.user.email!,
    otp,
    expires_at: expiresAt.toISOString(),
    ip_address: ip,
    user_agent: headersList.get('user-agent'),
  });

  // Send OTP email
  await sendOtpEmail(data.user.email!, otp);

  // Sign out — user must verify OTP first
  await supabase.auth.signOut();

  // Log audit
  await serviceClient.from('audit_logs').insert({
    user_id: data.user.id,
    email: data.user.email,
    action: 'otp_sent',
    status: 'success',
    ip_address: ip,
    user_agent: headersList.get('user-agent'),
  });

  return {
    requiresOtp: true,
    email: data.user.email!,
    userId: data.user.id,
    expiresAt: expiresAt.toISOString(),
  };
}

export async function verifyOtpAction(userId: string, email: string, otp: string) {
  const headersList = await headers();
  const ip = getClientIp(headersList);

  // Rate limit: 5 OTP attempts per 5 min
  const { success: withinLimit } = rateLimit(`otp:${userId}`, 5, 5 * 60 * 1000);
  if (!withinLimit) {
    return { error: 'Too many attempts. Please request a new code.' };
  }

  const serviceClient = await createServiceClient();

  // Get latest unverified OTP
  const { data: otpRecord } = await serviceClient
    .from('login_otps')
    .select('*')
    .eq('user_id', userId)
    .eq('verified', false)
    .order('created_at', { ascending: false })
    .limit(1)
    .single();

  if (!otpRecord) {
    return { error: 'No active verification code found. Please login again.' };
  }

  // Check expiry
  if (new Date(otpRecord.expires_at) < new Date()) {
    return { error: 'Code expired. Please request a new one.', expired: true };
  }

  // Check max attempts
  if (otpRecord.attempts >= otpRecord.max_attempts) {
    return { error: 'Maximum attempts reached. Please request a new code.', maxAttempts: true };
  }

  // Increment attempts
  await serviceClient.from('login_otps')
    .update({ attempts: otpRecord.attempts + 1 })
    .eq('id', otpRecord.id);

  // Verify OTP
  if (otpRecord.otp !== otp) {
    const remaining = otpRecord.max_attempts - otpRecord.attempts - 1;
    return { error: `Incorrect code. ${remaining} attempts remaining.`, attemptsRemaining: remaining };
  }

  // Mark as verified
  await serviceClient.from('login_otps')
    .update({ verified: true })
    .eq('id', otpRecord.id);

  // Sign user in via service role (generate session)
  const supabase = await createClient();
  const { data: userData } = await serviceClient.auth.admin.getUserById(userId);
  if (!userData.user) {
    return { error: 'User not found' };
  }

  // Sign in with password again (we need the client to set cookies)
  // We use a magic link approach via service client
  const { data: sessionData, error: signInError } = await serviceClient.auth.admin.generateLink({
    type: 'magiclink',
    email: email,
  });

  if (signInError || !sessionData) {
    return { error: 'Authentication failed. Please try again.' };
  }

  // Log audit
  await serviceClient.from('audit_logs').insert({
    user_id: userId,
    email,
    action: 'otp_verified',
    status: 'success',
    ip_address: ip,
    user_agent: headersList.get('user-agent'),
  });

  return { success: true, redirectTo: '/dashboard' };
}

export async function resendOtpAction(userId: string, email: string) {
  const headersList = await headers();
  const ip = getClientIp(headersList);

  // Rate limit: 3 resends per 5 min
  const { success: withinLimit } = rateLimit(`resend:${userId}`, 3, 5 * 60 * 1000);
  if (!withinLimit) {
    return { error: 'Please wait before requesting another code.' };
  }

  const serviceClient = await createServiceClient();

  // Invalidate existing OTPs
  await serviceClient.from('login_otps')
    .update({ verified: true })
    .eq('user_id', userId)
    .eq('verified', false);

  // Generate new OTP
  const otp = generateOtp();
  const expiresAt = getOtpExpiry();

  await serviceClient.from('login_otps').insert({
    user_id: userId,
    email,
    otp,
    expires_at: expiresAt.toISOString(),
    ip_address: ip,
    user_agent: headersList.get('user-agent'),
  });

  await sendOtpEmail(email, otp);

  return { success: true, expiresAt: expiresAt.toISOString() };
}

export async function forgotPasswordAction(formData: FormData) {
  const raw = { email: formData.get('email') as string };
  const parsed = forgotPasswordSchema.safeParse(raw);
  if (!parsed.success) {
    return { error: parsed.error.errors[0].message };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.resetPasswordForEmail(parsed.data.email, {
    redirectTo: `${process.env.NEXT_PUBLIC_APP_URL}/reset-password`,
  });

  if (error) {
    return { error: 'Failed to send reset email. Please try again.' };
  }

  return { success: true, message: 'Check your email for a password reset link' };
}

export async function resetPasswordAction(formData: FormData) {
  const raw = {
    password: formData.get('password') as string,
    confirmPassword: formData.get('confirmPassword') as string,
  };
  const parsed = resetPasswordSchema.safeParse(raw);
  if (!parsed.success) {
    return { error: parsed.error.errors[0].message };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({ password: parsed.data.password });

  if (error) {
    return { error: 'Failed to reset password. Link may have expired.' };
  }

  redirect('/login?message=Password+reset+successful');
}

export async function signOutAction() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect('/login');
}
