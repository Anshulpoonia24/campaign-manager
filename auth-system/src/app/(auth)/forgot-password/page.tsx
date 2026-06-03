'use client';

import { useState } from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import { Mail, ArrowLeft } from 'lucide-react';
import { AuthLayout } from '@/components/auth/auth-layout';
import { Button, Input } from '@/components/ui';
import { forgotPasswordAction } from '@/app/actions';

export default function ForgotPasswordPage() {
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const result = await forgotPasswordAction(formData);

    if (result.error) {
      toast.error(result.error);
      setLoading(false);
      return;
    }

    setSent(true);
    setLoading(false);
  };

  if (sent) {
    return (
      <AuthLayout title="Email sent" subtitle="Check your inbox for a reset link">
        <div className="text-center space-y-4">
          <div className="w-16 h-16 mx-auto rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center">
            <Mail className="w-8 h-8 text-brand-600" />
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            If an account exists with that email, you&apos;ll receive a password reset link shortly.
          </p>
          <Link href="/login" className="inline-flex items-center gap-1 text-brand-600 hover:text-brand-500 font-semibold text-sm">
            <ArrowLeft className="w-3 h-3" /> Back to login
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Reset your password" subtitle="Enter your email and we'll send a reset link">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          label="Email"
          name="email"
          type="email"
          placeholder="you@company.com"
          required
          icon={<Mail className="w-4 h-4" />}
        />
        <Button type="submit" loading={loading}>Send Reset Link</Button>
      </form>

      <p className="text-center text-sm text-gray-500 dark:text-gray-400 mt-6">
        <Link href="/login" className="inline-flex items-center gap-1 text-brand-600 hover:text-brand-500 font-semibold">
          <ArrowLeft className="w-3 h-3" /> Back to login
        </Link>
      </p>
    </AuthLayout>
  );
}
