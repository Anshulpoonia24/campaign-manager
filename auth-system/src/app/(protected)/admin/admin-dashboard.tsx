'use client';

import { useEffect, useState } from 'react';
import { Users, UserPlus, Shield, Activity, AlertTriangle, Mail, Globe, Clock, ArrowLeft } from 'lucide-react';
import Link from 'next/link';

interface UserProfile {
  id: string;
  user_id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  auth_provider: string;
  email_verified: boolean;
  is_admin: boolean;
  last_login_at: string | null;
  login_count: number;
  signup_ip: string | null;
  last_ip: string | null;
  status: string;
  created_at: string;
}

interface AuditLog {
  id: string;
  email: string;
  action: string;
  status: string;
  ip_address: string;
  user_agent: string;
  created_at: string;
}

interface Stats {
  totalUsers: number;
  usersToday: number;
  usersThisWeek: number;
  googleUsers: number;
  emailUsers: number;
  failedLoginsToday: number;
  otpsSentToday: number;
  dailySignups: Record<string, number>;
}

export function AdminDashboard() {
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'overview' | 'users' | 'activity'>('overview');

  useEffect(() => {
    Promise.all([
      fetch('/api/admin/users').then(r => r.json()),
      fetch('/api/admin/stats').then(r => r.json()),
    ]).then(([usersData, statsData]) => {
      setUsers(usersData.users || []);
      setLogs(usersData.recentLogins || []);
      setStats(statsData);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <div className="flex items-center gap-4">
            <Link href="/dashboard" className="text-gray-400 hover:text-gray-600 transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div className="flex items-center gap-2">
              <Shield className="w-5 h-5 text-brand-500" />
              <span className="font-bold text-lg text-gray-900 dark:text-white">Super Admin</span>
            </div>
          </div>
          <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
            {(['overview', 'users', 'activity'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  activeTab === tab
                    ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {activeTab === 'overview' && stats && <OverviewTab stats={stats} users={users} />}
        {activeTab === 'users' && <UsersTab users={users} />}
        {activeTab === 'activity' && <ActivityTab logs={logs} />}
      </main>
    </div>
  );
}

function OverviewTab({ stats, users }: { stats: Stats; users: UserProfile[] }) {
  return (
    <div className="space-y-6 animate-fade-in">
      {/* Stats cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Users} label="Total Users" value={stats.totalUsers} color="brand" />
        <StatCard icon={UserPlus} label="New Today" value={stats.usersToday} color="green" />
        <StatCard icon={Activity} label="This Week" value={stats.usersThisWeek} color="purple" />
        <StatCard icon={AlertTriangle} label="Failed Logins Today" value={stats.failedLoginsToday} color="red" />
      </div>

      {/* Provider breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="p-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800">
          <h3 className="font-semibold text-gray-900 dark:text-white mb-4">Auth Providers</h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Globe className="w-4 h-4 text-blue-500" />
                <span className="text-sm text-gray-600 dark:text-gray-400">Google OAuth</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-gray-900 dark:text-white">{stats.googleUsers}</span>
                <span className="text-xs text-gray-400">({stats.totalUsers ? Math.round(stats.googleUsers / stats.totalUsers * 100) : 0}%)</span>
              </div>
            </div>
            <div className="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2">
              <div className="bg-blue-500 h-2 rounded-full" style={{ width: `${stats.totalUsers ? stats.googleUsers / stats.totalUsers * 100 : 0}%` }} />
            </div>
            <div className="flex items-center justify-between mt-4">
              <div className="flex items-center gap-2">
                <Mail className="w-4 h-4 text-purple-500" />
                <span className="text-sm text-gray-600 dark:text-gray-400">Email/Password</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-gray-900 dark:text-white">{stats.emailUsers}</span>
                <span className="text-xs text-gray-400">({stats.totalUsers ? Math.round(stats.emailUsers / stats.totalUsers * 100) : 0}%)</span>
              </div>
            </div>
            <div className="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2">
              <div className="bg-purple-500 h-2 rounded-full" style={{ width: `${stats.totalUsers ? stats.emailUsers / stats.totalUsers * 100 : 0}%` }} />
            </div>
          </div>
        </div>

        <div className="p-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800">
          <h3 className="font-semibold text-gray-900 dark:text-white mb-4">Daily Signups (7 days)</h3>
          <div className="flex items-end gap-2 h-32">
            {Object.entries(stats.dailySignups).map(([day, count]) => (
              <div key={day} className="flex-1 flex flex-col items-center gap-1">
                <span className="text-xs font-bold text-gray-900 dark:text-white">{count}</span>
                <div
                  className="w-full bg-brand-500 rounded-t-md min-h-[4px]"
                  style={{ height: `${Math.max(4, (count / Math.max(...Object.values(stats.dailySignups), 1)) * 100)}%` }}
                />
                <span className="text-[10px] text-gray-400">{day.slice(5)}</span>
              </div>
            ))}
            {Object.keys(stats.dailySignups).length === 0 && (
              <p className="text-sm text-gray-400 w-full text-center">No signups yet</p>
            )}
          </div>
        </div>
      </div>

      {/* Recent signups */}
      <div className="p-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800">
        <h3 className="font-semibold text-gray-900 dark:text-white mb-4">Latest Signups</h3>
        <div className="space-y-3">
          {users.slice(0, 5).map(u => (
            <div key={u.id} className="flex items-center justify-between py-2 border-b border-gray-100 dark:border-gray-800 last:border-0">
              <div className="flex items-center gap-3">
                {u.avatar_url ? (
                  <img src={u.avatar_url} alt="" className="w-8 h-8 rounded-full" />
                ) : (
                  <div className="w-8 h-8 rounded-full bg-brand-100 dark:bg-brand-900/50 flex items-center justify-center text-xs font-bold text-brand-700">
                    {(u.full_name || u.email)[0].toUpperCase()}
                  </div>
                )}
                <div>
                  <p className="text-sm font-medium text-gray-900 dark:text-white">{u.full_name || 'No name'}</p>
                  <p className="text-xs text-gray-500">{u.email}</p>
                </div>
              </div>
              <div className="text-right">
                <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                  u.auth_provider === 'google' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'
                }`}>
                  {u.auth_provider}
                </span>
                <p className="text-[10px] text-gray-400 mt-1">{new Date(u.created_at).toLocaleDateString()}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function UsersTab({ users }: { users: UserProfile[] }) {
  const [search, setSearch] = useState('');
  const filtered = users.filter(u =>
    u.email.toLowerCase().includes(search.toLowerCase()) ||
    (u.full_name || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-gray-900 dark:text-white">All Users ({users.length})</h2>
        <input
          type="text"
          placeholder="Search users..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-sm outline-none focus:border-brand-500 w-64"
        />
      </div>

      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-800/50">
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">User</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Provider</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Verified</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Logins</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Last Login</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Signed Up</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">IP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {filtered.map(u => (
                <tr key={u.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      {u.avatar_url ? (
                        <img src={u.avatar_url} alt="" className="w-8 h-8 rounded-full" />
                      ) : (
                        <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-bold">
                          {(u.full_name || u.email)[0].toUpperCase()}
                        </div>
                      )}
                      <div>
                        <p className="text-sm font-medium text-gray-900 dark:text-white">{u.full_name || '-'}</p>
                        <p className="text-xs text-gray-500">{u.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                      u.auth_provider === 'google' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' : 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300'
                    }`}>
                      {u.auth_provider}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={u.email_verified ? 'text-green-600 text-xs font-medium' : 'text-red-500 text-xs font-medium'}>
                      {u.email_verified ? '✓ Yes' : '✗ No'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 dark:text-gray-300">{u.login_count || 0}</td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : '-'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                      u.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                    }`}>
                      {u.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">{new Date(u.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3 text-xs text-gray-400 font-mono">{u.last_ip || u.signup_ip || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ActivityTab({ logs }: { logs: AuditLog[] }) {
  const actionColors: Record<string, string> = {
    signup: 'bg-green-100 text-green-700',
    otp_sent: 'bg-blue-100 text-blue-700',
    login_otp_verified: 'bg-brand-100 text-brand-700',
    login_failed: 'bg-red-100 text-red-700',
  };

  return (
    <div className="space-y-4 animate-fade-in">
      <h2 className="text-lg font-bold text-gray-900 dark:text-white">Recent Activity</h2>
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 overflow-hidden">
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {logs.map(log => (
            <div key={log.id} className="px-5 py-3 flex items-center justify-between hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
              <div className="flex items-center gap-3">
                <div className="w-2 h-2 rounded-full bg-brand-500" />
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 dark:text-white">{log.email || 'Unknown'}</span>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${actionColors[log.action] || 'bg-gray-100 text-gray-600'}`}>
                      {log.action.replace(/_/g, ' ')}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className="text-[10px] text-gray-400 font-mono">{log.ip_address || '-'}</span>
                    <span className="text-[10px] text-gray-400">{log.user_agent?.slice(0, 50) || '-'}</span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1 text-xs text-gray-400">
                <Clock className="w-3 h-3" />
                {new Date(log.created_at).toLocaleString()}
              </div>
            </div>
          ))}
          {logs.length === 0 && (
            <p className="px-5 py-8 text-center text-sm text-gray-400">No activity yet</p>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon: Icon, label, value, color }: { icon: any; label: string; value: number; color: string }) {
  const colors: Record<string, string> = {
    brand: 'bg-brand-100 text-brand-600 dark:bg-brand-900/30 dark:text-brand-400',
    green: 'bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400',
    purple: 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400',
    red: 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400',
  };

  return (
    <div className="p-5 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800">
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center mb-3 ${colors[color]}`}>
        <Icon className="w-5 h-5" />
      </div>
      <p className="text-2xl font-bold text-gray-900 dark:text-white">{value}</p>
      <p className="text-xs text-gray-500 mt-0.5">{label}</p>
    </div>
  );
}
