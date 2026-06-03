import { NextResponse } from 'next/server';
import { createClient, createServiceClient } from '@/lib/supabase/server';
import { sendWelcomeEmail } from '@/lib/welcome-email';

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get('code');
  const next = searchParams.get('next') ?? '/dashboard';

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      // Check if this is a new user (first login) and send welcome email
      const { data: { user } } = await supabase.auth.getUser();
      if (user) {
        const serviceClient = await createServiceClient();
        const { data: profile } = await serviceClient
          .from('user_profiles')
          .select('created_at')
          .eq('user_id', user.id)
          .single();

        // Send welcome email if profile was created within last 60 seconds (new user)
        if (profile) {
          const createdAt = new Date(profile.created_at).getTime();
          const now = Date.now();
          if (now - createdAt < 60000) {
            const name = user.user_metadata?.full_name || user.user_metadata?.name || '';
            sendWelcomeEmail(user.email!, name).catch(console.error);
          }
        }
      }
      return NextResponse.redirect(`${origin}${next}`);
    }
  }

  return NextResponse.redirect(`${origin}/login?error=auth_failed`);
}
