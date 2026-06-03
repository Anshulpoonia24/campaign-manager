# OutreachOS Auth System

Production-ready authentication system built with Next.js 15, TypeScript, Tailwind CSS, and Supabase.

## Features

- ✅ Google OAuth login/signup (auto-login, no OTP needed)
- ✅ Email + password signup with email verification
- ✅ 6-digit OTP verification on email/password login
- ✅ OTP expiry (5 min), max 5 attempts, resend with cooldown
- ✅ Rate limiting on all auth endpoints
- ✅ Audit logging
- ✅ Dark/light mode
- ✅ Responsive UI
- ✅ Protected routes via middleware
- ✅ User profile dropdown with sign out
- ✅ Secure HTTP-only cookies (Supabase SSR)

## Setup

### 1. Install dependencies

```bash
npm install
```

### 2. Configure Supabase

1. Create a project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** → paste contents of `supabase/schema.sql` → Run
3. Go to **Auth > Providers** → Enable Google (add client ID/secret)
4. Go to **Auth > URL Configuration** → Add `http://localhost:3000/api/auth/callback` to redirect URLs

### 3. Configure Resend

1. Sign up at [resend.com](https://resend.com)
2. Verify your domain or use the test domain
3. Get API key

### 4. Set environment variables

```bash
cp .env.example .env.local
```

Fill in all values in `.env.local`.

### 5. Run dev server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

## Auth Flow

### Email/Password:
1. User signs up → email verification sent
2. User verifies email → can now login
3. User logs in with password → OTP sent to email
4. User enters OTP → session created → redirect to dashboard

### Google OAuth:
1. User clicks "Continue with Google"
2. Google authenticates → callback creates session
3. Redirect to dashboard (no OTP needed)

## Project Structure

```
src/
├── app/
│   ├── (auth)/           # Public auth pages
│   │   ├── login/
│   │   ├── signup/
│   │   ├── forgot-password/
│   │   ├── reset-password/
│   │   ├── verify-email/
│   │   └── verify-otp/
│   ├── (protected)/      # Auth-required pages
│   │   ├── dashboard/
│   │   ├── settings/
│   │   └── billing/
│   ├── api/auth/         # API routes
│   ├── actions.ts        # Server actions
│   ├── layout.tsx
│   └── globals.css
├── components/
│   ├── auth/             # Auth-specific components
│   ├── layout/           # Theme provider
│   └── ui/               # Reusable UI components
├── hooks/                # useAuth hook
├── lib/
│   ├── supabase/         # Supabase client/server/middleware
│   ├── email.ts          # Resend integration
│   ├── rate-limit.ts     # In-memory rate limiter
│   └── validations.ts    # Zod schemas
├── types/                # TypeScript types
├── utils/                # Utility functions
└── middleware.ts         # Route protection
```

## Deploy to Vercel

1. Push to GitHub
2. Import to [vercel.com](https://vercel.com)
3. Add environment variables in Vercel dashboard
4. Set **Framework**: Next.js
5. Update Supabase redirect URLs to `https://yourdomain.com/api/auth/callback`
6. Deploy

## Database Tables

| Table | Purpose |
|-------|---------|
| `user_profiles` | User metadata, auth provider, verification status |
| `login_otps` | OTP records with expiry, attempts tracking |
| `audit_logs` | Login events, OTP events, security audit trail |

## Security

- HTTP-only cookies via Supabase SSR
- Rate limiting: 10 login/15min, 5 OTP verify/5min, 3 resend/5min
- OTP brute-force protection (max 5 attempts per code)
- CSRF protection via SameSite cookies
- Password requirements: 8+ chars, uppercase, lowercase, number
- Auto session refresh via Supabase middleware
- RLS policies on all tables
