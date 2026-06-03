'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { LogOut, Settings, User, ChevronDown, Shield, BarChart3, Zap } from 'lucide-react';
import { createClient } from '@/lib/supabase/client';
import type { User as SupaUser } from '@supabase/supabase-js';
import type { UserProfile } from '@/types';

interface Props {
  user: SupaUser;
  profile: UserProfile | null;
}

export function DashboardContent({ user, profile }: Props) {
  const router = useRouter();
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const handleSignOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push('/login');
  };

  const displayName = profile?.full_name || user.email?.split('@')[0] || 'User';
  const initials = displayName.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-brand-600 flex items-center justify-center">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-lg text-gray-900 dark:text-white">OutreachOS</span>
          </div>

          {/* Profile dropdown */}
          <div className="relative">
            <button
              onClick={() => setDropdownOpen(!dropdownOpen)}
              className="flex items-center gap-2 px-3 py-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              {profile?.avatar_url ? (
                <img src={profile.avatar_url} alt="" className="w-8 h-8 rounded-full" />
              ) : (
                <div className="w-8 h-8 rounded-full bg-brand-100 dark:bg-brand-900/50 flex items-center justify-center text-xs font-bold text-brand-700 dark:text-brand-300">
                  {initials}
                </div>
              )}
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300 hidden sm:block">{displayName}</span>
              <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
            </button>

            {dropdownOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setDropdownOpen(false)} />
                <div className="absolute right-0 mt-2 w-56 rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl z-50 animate-fade-in overflow-hidden">
                  <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800">
                    <p className="text-sm font-medium text-gray-900 dark:text-white">{displayName}</p>
                    <p className="text-xs text-gray-500 truncate">{user.email}</p>
                  </div>
                  <div className="py-1">
                    <button onClick={() => router.push('/settings')} className="w-full px-4 py-2 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 flex items-center gap-2">
                      <Settings className="w-4 h-4" /> Settings
                    </button>
                    <button onClick={() => router.push('/billing')} className="w-full px-4 py-2 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 flex items-center gap-2">
                      <User className="w-4 h-4" /> Billing
                    </button>
                    {profile?.is_admin && (
                      <button onClick={() => router.push('/admin')} className="w-full px-4 py-2 text-left text-sm text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/10 flex items-center gap-2">
                        <Shield className="w-4 h-4" /> Super Admin
                      </button>
                    )}
                  </div>
                  <div className="border-t border-gray-100 dark:border-gray-800 py-1">
                    <button onClick={handleSignOut} className="w-full px-4 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/10 flex items-center gap-2">
                      <LogOut className="w-4 h-4" /> Sign out
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Welcome back, {displayName.split(' ')[0]}!</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Here&apos;s your outreach overview.</p>
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {[
            { label: 'Emails Sent', value: '12,458', icon: BarChart3, change: '+12.5%' },
            { label: 'Open Rate', value: '67.2%', icon: Zap, change: '+3.1%' },
            { label: 'Reply Rate', value: '14.8%', icon: Shield, change: '+1.4%' },
            { label: 'Active Campaigns', value: '6', icon: Settings, change: '+2 new' },
          ].map(stat => (
            <div key={stat.label} className="p-5 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 shadow-sm">
              <div className="flex items-center justify-between mb-3">
                <stat.icon className="w-5 h-5 text-gray-400" />
                <span className="text-xs font-medium text-green-600 bg-green-50 dark:bg-green-900/20 px-2 py-0.5 rounded-full">{stat.change}</span>
              </div>
              <p className="text-2xl font-bold text-gray-900 dark:text-white">{stat.value}</p>
              <p className="text-xs text-gray-500 mt-1">{stat.label}</p>
            </div>
          ))}
        </div>

        {/* Auth info card */}
        <div className="p-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800">
          <h3 className="font-semibold text-gray-900 dark:text-white mb-4">Session Info</h3>
          <div className="grid gap-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">User ID</span>
              <span className="font-mono text-xs text-gray-700 dark:text-gray-300">{user.id.slice(0, 8)}...</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Email</span>
              <span className="text-gray-700 dark:text-gray-300">{user.email}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Provider</span>
              <span className="text-gray-700 dark:text-gray-300 capitalize">{profile?.auth_provider || 'email'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Email Verified</span>
              <span className={profile?.email_verified ? 'text-green-600' : 'text-red-500'}>
                {profile?.email_verified ? '✓ Verified' : '✗ Not verified'}
              </span>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
