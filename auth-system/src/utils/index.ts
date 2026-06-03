import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function generateOtp(): string {
  return Math.floor(100000 + Math.random() * 900000).toString();
}

export function getOtpExpiry(): Date {
  return new Date(Date.now() + 5 * 60 * 1000); // 5 minutes
}

export function isOtpExpired(expiresAt: string): boolean {
  return new Date(expiresAt) < new Date();
}

export function getClientIp(headers: Headers): string {
  return (
    headers.get('x-forwarded-for')?.split(',')[0]?.trim() ||
    headers.get('x-real-ip') ||
    'unknown'
  );
}

export function maskEmail(email: string): string {
  const [name, domain] = email.split('@');
  const masked = name.length > 3
    ? name.slice(0, 2) + '***' + name.slice(-1)
    : name[0] + '***';
  return `${masked}@${domain}`;
}
