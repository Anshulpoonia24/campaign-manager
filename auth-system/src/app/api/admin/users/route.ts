import { NextResponse } from 'next/server';
import { createClient, createServiceClient } from '@/lib/supabase/server';

export async function GET() {
  // Verify admin
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

  // Fetch all users with profiles
  const { data: users } = await serviceClient
    .from('user_profiles')
    .select('*')
    .order('created_at', { ascending: false });

  // Fetch recent audit logs
  const { data: recentLogins } = await serviceClient
    .from('audit_logs')
    .select('*')
    .in('action', ['login_otp_verified', 'otp_sent', 'signup', 'login_failed'])
    .order('created_at', { ascending: false })
    .limit(50);

  // Fetch user sessions
  const { data: sessions } = await serviceClient
    .from('user_sessions')
    .select('*')
    .order('login_at', { ascending: false })
    .limit(100);

  return NextResponse.json({ users: users || [], recentLogins: recentLogins || [], sessions: sessions || [] });
}
