'use client';

import { useState } from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import { Mail, Lock, User, Eye, EyeOff } from 'lucide-react';
import { AuthLayout } from '@/components/auth/auth-layout';
import { GoogleButton } from '@/components/auth/google-button';
import { Button, Input } from '@/components/ui';
import { signUpAction } from '@/app/actions';

export default function SignupPage() {
  const [loading, setLoading] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const result = await signUpAction(formData);

    if (result.error) {
      toast.error(result.error);
      setLoading(false);
      return;
    }

    setSuccess(true);
    toast.success('Account created! Check your email to verify.');
    setLoading(false);
  };

  if (success) {
    return (
      <AuthLayout title="Check your email" subtitle="We sent a verification link to your email">
        <div className="text-center space-y-4">
          <div className="w-16 h-16 mx-auto rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
            <Mail className="w-8 h-8 text-green-600" />
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Click the link in the email to activate your account, then come back to log in.
          </p>
          <Link href="/login" className="inline-block text-brand-600 hover:text-brand-500 font-semibold text-sm">
            ← Back to login
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Create your account" subtitle="Get started with OutreachOS for free">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          label="Full Name"
          name="fullName"
          type="text"
          placeholder="John Doe"
          required
          icon={<User className="w-4 h-4" />}
        />
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
            placeholder="Min 8 chars, uppercase, number"
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

        <Button type="submit" loading={loading}>Create Account</Button>
      </form>

      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-gray-200 dark:border-gray-700" /></div>
        <div className="relative flex justify-center text-xs">
          <span className="px-3 bg-white dark:bg-gray-900 text-gray-500">or continue with</span>
        </div>
      </div>

      <GoogleButton />

      <p className="text-center text-sm text-gray-500 dark:text-gray-400 mt-6">
        Already have an account?{' '}
        <Link href="/login" className="text-brand-600 hover:text-brand-500 font-semibold">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
