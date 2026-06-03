import { NextResponse } from 'next/server';
import { createClient, createServiceClient } from '@/lib/supabase/server';

export async function GET() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const serviceClient = await createServiceClient();
  const { data: profile } = await serviceClient
    .from('user_profiles')
    .select('is_admin')
    .eq('user_id', user.id)
    .single();

  if (!profile?.is_admin) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  }

  // Total users
  const { count: totalUsers } = await serviceClient
    .from('user_profiles')
    .select('*', { count: 'exact', head: true });

  // Users today
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const { count: usersToday } = await serviceClient
    .from('user_profiles')
    .select('*', { count: 'exact', head: true })
    .gte('created_at', today.toISOString());

  // Users this week
  const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
  const { count: usersThisWeek } = await serviceClient
    .from('user_profiles')
    .select('*', { count: 'exact', head: true })
    .gte('created_at', weekAgo.toISOString());

  // Google vs Email users
  const { count: googleUsers } = await serviceClient
    .from('user_profiles')
    .select('*', { count: 'exact', head: true })
    .eq('auth_provider', 'google');

  const { count: emailUsers } = await serviceClient
    .from('user_profiles')
    .select('*', { count: 'exact', head: true })
    .eq('auth_provider', 'email');

  // Failed logins today
  const { count: failedLoginsToday } = await serviceClient
    .from('audit_logs')
    .select('*', { count: 'exact', head: true })
    .eq('action', 'login_failed')
    .gte('created_at', today.toISOString());

  // OTPs sent today
  const { count: otpsSentToday } = await serviceClient
    .from('audit_logs')
    .select('*', { count: 'exact', head: true })
    .eq('action', 'otp_sent')
    .gte('created_at', today.toISOString());

  // Signups by day (last 7 days)
  const { data: signupsByDay } = await serviceClient
    .from('user_profiles')
    .select('created_at')
    .gte('created_at', weekAgo.toISOString())
    .order('created_at', { ascending: true });

  const dailySignups: Record<string, number> = {};
  signupsByDay?.forEach(u => {
    const day = u.created_at.split('T')[0];
    dailySignups[day] = (dailySignups[day] || 0) + 1;
  });

  return NextResponse.json({
    totalUsers: totalUsers || 0,
    usersToday: usersToday || 0,
    usersThisWeek: usersThisWeek || 0,
    googleUsers: googleUsers || 0,
    emailUsers: emailUsers || 0,
    failedLoginsToday: failedLoginsToday || 0,
    otpsSentToday: otpsSentToday || 0,
    dailySignups,
  });
}
