-- ============================================
-- Super Admin Tracking Tables
-- ============================================

-- User activity tracking
CREATE TABLE IF NOT EXISTS public.user_activity (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  action TEXT NOT NULL,
  page TEXT,
  metadata JSONB DEFAULT '{}',
  ip_address TEXT,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User sessions tracking
CREATE TABLE IF NOT EXISTS public.user_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  login_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  logout_at TIMESTAMPTZ,
  duration_seconds INTEGER,
  ip_address TEXT,
  user_agent TEXT,
  country TEXT,
  device TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_user_activity_user_id ON public.user_activity(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created_at ON public.user_activity(created_at);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON public.user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_login_at ON public.user_sessions(login_at);

-- RLS
ALTER TABLE public.user_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;

-- Only service role can access these (admin-only)
CREATE POLICY "Service full access activity" ON public.user_activity FOR ALL USING (true);
CREATE POLICY "Service full access sessions" ON public.user_sessions FOR ALL USING (true);

-- Add is_admin column to user_profiles
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Add extra tracking fields to user_profiles
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS login_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS signup_ip TEXT;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS last_ip TEXT;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
