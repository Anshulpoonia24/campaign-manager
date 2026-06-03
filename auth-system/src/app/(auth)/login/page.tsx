'use client';

import { useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { Mail, Lock, Eye, EyeOff } from 'lucide-react';
import { AuthLayout } from '@/components/auth/auth-layout';
import { GoogleButton } from '@/components/auth/google-button';
import { Button, Input } from '@/components/ui';
import { signInAction } from '@/app/actions';

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginContent />
    </Suspense>
  );
}

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [loading, setLoading] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const message = searchParams.get('message');

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const result = await signInAction(formData);

    if (result.error) {
      toast.error(result.error);
      setLoading(false);
      return;
    }

    if (result.requiresOtp) {
      // Store OTP session data and redirect to verify
      sessionStorage.setItem('otp_session', JSON.stringify({
        email: result.email,
        userId: result.userId,
        expiresAt: result.expiresAt,
      }));
      router.push('/verify-otp');
      return;
    }

    router.push('/dashboard');
  };

  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to your OutreachOS workspace">
      {message && (
        <div className="mb-4 p-3 rounded-xl bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 text-sm text-green-700 dark:text-green-300">
          {decodeURIComponent(message)}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          label="Email"
          name="email"
          type="email"
          placeholder="you@company.com"
          required
          icon={<Mail className="w-4 h-4" />}
        />
        <div className="relative">
          <Input
            label="Password"
            name="password"
            type={showPw ? 'text' : 'password'}
            placeholder="••••••••"
            required
            icon={<Lock className="w-4 h-4" />}
          />
          <button
            type="button"
            onClick={() => setShowPw(!showPw)}
            className="absolute right-3 top-9 text-gray-400 hover:text-gray-600"
          >
            {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 cursor-pointer">
            <input type="checkbox" name="rememberMe" className="rounded border-gray-300 text-brand-500 focus:ring-brand-500" />
            Remember me
          </label>
          <Link href="/forgot-password" className="text-sm text-brand-600 hover:text-brand-500 font-medium">
            Forgot password?
          </Link>
        </div>

        <Button type="submit" loading={loading}>Sign In</Button>
      </form>

      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-gray-200 dark:border-gray-700" /></div>
        <div className="relative flex justify-center text-xs">
          <span className="px-3 bg-white dark:bg-gray-900 text-gray-500">or continue with</span>
        </div>
      </div>

      <GoogleButton />

      <p className="text-center text-sm text-gray-500 dark:text-gray-400 mt-6">
        Don&apos;t have an account?{' '}
        <Link href="/signup" className="text-brand-600 hover:text-brand-500 font-semibold">
          Create account
        </Link>
      </p>
    </AuthLayout>
  );
}
