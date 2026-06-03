import { Mail } from 'lucide-react';
import Link from 'next/link';
import { AuthLayout } from '@/components/auth/auth-layout';

export default function VerifyEmailPage() {
  return (
    <AuthLayout title="Verify your email" subtitle="We sent a verification link to your email address">
      <div className="text-center space-y-4">
        <div className="w-16 h-16 mx-auto rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center animate-pulse-slow">
          <Mail className="w-8 h-8 text-brand-600" />
        </div>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Click the verification link in your email to activate your account. Check your spam folder if you don&apos;t see it.
        </p>
        <div className="pt-4 space-y-2">
          <Link href="/login" className="block text-brand-600 hover:text-brand-500 font-semibold text-sm">
            I&apos;ve verified my email → Sign in
          </Link>
          <Link href="/signup" className="block text-gray-500 hover:text-gray-700 text-sm">
            Didn&apos;t receive it? Sign up again
          </Link>
        </div>
      </div>
    </AuthLayout>
  );
}
