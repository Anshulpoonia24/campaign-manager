export interface UserProfile {
  id: string;
  user_id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  auth_provider: 'email' | 'google';
  email_verified: boolean;
  is_admin: boolean;
  last_login_at: string | null;
  login_count: number;
  signup_ip: string | null;
  last_ip: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface LoginOtp {
  id: string;
  user_id: string;
  email: string;
  otp: string;
  expires_at: string;
  attempts: number;
  max_attempts: number;
  verified: boolean;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface AuditLog {
  id: string;
  user_id: string | null;
  email: string | null;
  action: string;
  status: 'success' | 'failure';
  ip_address: string | null;
  user_agent: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AuthState {
  user: UserProfile | null;
  isLoading: boolean;
  isAuthenticated: boolean;
}

export interface OtpVerificationState {
  email: string;
  userId: string;
  expiresAt: string;
  attemptsRemaining: number;
}
