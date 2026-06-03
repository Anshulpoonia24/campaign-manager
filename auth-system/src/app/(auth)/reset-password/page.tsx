'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { Lock, Eye, EyeOff } from 'lucide-react';
import { AuthLayout } from '@/components/auth/auth-layout';
import { Button, Input } from '@/components/ui';
import { resetPasswordAction } from '@/app/actions';

export default function ResetPasswordPage() {
  const [loading, setLoading] = useState(false);
  const [showPw, setShowPw] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);

    const formData = new FormData(e.currentTarget);
    const result = await resetPasswordAction(formData);

    if (result?.error) {
      toast.error(result.error);
      setLoading(false);
    }
  };

  return (
    <AuthLayout title="Set new password" subtitle="Choose a strong password for your account">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="relative">
          <Input
            label="New Password"
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
        <Input
          label="Confirm Password"
          name="confirmPassword"
          type="password"
          placeholder="Re-enter password"
          required
          icon={<Lock className="w-4 h-4" />}
        />
        <Button type="submit" loading={loading}>Reset Password</Button>
      </form>
    </AuthLayout>
  );
}
