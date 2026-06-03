'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { ShieldCheck, RotateCcw } from 'lucide-react';
import { AuthLayout } from '@/components/auth/auth-layout';
import { OtpInput } from '@/components/auth/otp-input';
import { Button } from '@/components/ui';
import { createClient } from '@/lib/supabase/client';
import { maskEmail } from '@/utils';

export default function VerifyOtpPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [timeLeft, setTimeLeft] = useState(300); // 5 min
  const [session, setSession] = useState<{ email: string; userId: string; expiresAt: string } | null>(null);

  useEffect(() => {
    const stored = sessionStorage.getItem('otp_session');
    if (!stored) {
      router.push('/login');
      return;
    }
    const data = JSON.parse(stored);
    setSession(data);

    const expiresIn = Math.max(0, Math.floor((new Date(data.expiresAt).getTime() - Date.now()) / 1000));
    setTimeLeft(expiresIn);
  }, [router]);

  // Countdown timer
  useEffect(() => {
    if (timeLeft <= 0) return;
    const interval = setInterval(() => setTimeLeft(t => Math.max(0, t - 1)), 1000);
    return () => clearInterval(interval);
  }, [timeLeft]);

  // Resend cooldown
  useEffect(() => {
    if (resendCooldown <= 0) return;
    const interval = setInterval(() => setResendCooldown(c => Math.max(0, c - 1)), 1000);
    return () => clearInterval(interval);
  }, [resendCooldown]);

  const handleVerify = useCallback(async (otp: string) => {
    if (!session) return;
    setLoading(true);

    try {
      const res = await fetch('/api/auth/otp/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userId: session.userId, email: session.email, otp }),
      });

      const data = await res.json();

      if (!res.ok) {
        toast.error(data.error);
        if (data.expired || data.maxAttempts) {
          setTimeLeft(0);
        }
        setLoading(false);
        return;
      }

      // Exchange token for session
      if (data.token_hash) {
        const supabase = createClient();
        const { error } = await supabase.auth.verifyOtp({
          token_hash: data.token_hash,
          type: 'magiclink',
        });
        if (error) {
          toast.error('Session creation failed. Please login again.');
          setLoading(false);
          return;
        }
      }

      sessionStorage.removeItem('otp_session');
      toast.success('Verified! Redirecting...');
      router.push('/dashboard');
    } catch {
      toast.error('Network error. Please try again.');
      setLoading(false);
    }
  }, [session, router]);

  const handleResend = async () => {
    if (!session || resendCooldown > 0) return;

    try {
      const res = await fetch('/api/auth/otp/resend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userId: session.userId, email: session.email }),
      });

      const data = await res.json();

      if (!res.ok) {
        toast.error(data.error);
        return;
      }

      setResendCooldown(60);
      setTimeLeft(300);
      toast.success('New code sent!');
    } catch {
      toast.error('Failed to resend');
    }
  };

  const formatTime = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;

  if (!session) return null;

  return (
    <AuthLayout title="Verify your identity" subtitle={`Enter the 6-digit code sent to ${maskEmail(session.email)}`}>
      <div className="space-y-6">
        <div className="flex justify-center">
          <div className="w-14 h-14 rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center">
            <ShieldCheck className="w-7 h-7 text-brand-600" />
          </div>
        </div>

        <OtpInput onComplete={handleVerify} disabled={loading || timeLeft === 0} />

        {/* Timer */}
        <div className="text-center">
          {timeLeft > 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Code expires in <span className="font-mono font-semibold text-brand-600">{formatTime(timeLeft)}</span>
            </p>
          ) : (
            <p className="text-sm text-red-500 font-medium">Code expired. Please request a new one.</p>
          )}
        </div>

        {/* Resend */}
        <div className="text-center">
          <button
            onClick={handleResend}
            disabled={resendCooldown > 0}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-500 disabled:text-gray-400 disabled:cursor-not-allowed transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend code'}
          </button>
        </div>

        {loading && (
          <div className="text-center">
            <p className="text-sm text-gray-500 animate-pulse">Verifying...</p>
          </div>
        )}

        <Button variant="ghost" onClick={() => { sessionStorage.removeItem('otp_session'); router.push('/login'); }} className="w-full text-center">
          ← Back to login
        </Button>
      </div>
    </AuthLayout>
  );
}
