# Product Overview

## Project Purpose
An AI-powered email outreach and campaign management platform built for Shiksha Infotech. It enables cold outreach at scale with AI-personalized emails, reply tracking, inbox management, and multi-tenant workspace support.

## Value Proposition
- Replaces manual cold email workflows with AI-driven personalization at scale
- Integrates SMTP rotation, open/click tracking, IMAP reply detection, and automated follow-up sequences
- Provides an AI copilot assistant to manage campaigns through natural language
- Multi-tenant SaaS architecture with per-workspace isolation

## Key Features

### Campaign Management
- Create and manage email campaigns with template or AI-generated emails
- Multi-step automated email sequences with configurable delays
- Browser-independent backend execution via Celery/threading
- Pause, resume, cancel running campaigns
- Duplicate send protection via send_reservations table and campaign locks

### Contact Management
- Bulk contact upload from Excel/CSV with smart column detection
- Email verification via MX + SMTP handshake (with TTL cache)
- Contact intelligence enrichment: industry, company size, tech stack, pain points, ICP score
- Lead scoring system with hot/warm/cold temperature tracking
- Suppression list (unsubscribes)

### AI Integration
- AI email generation using Groq (llama-3.3-70b) with Gemini fallback
- Multi-key Groq rotation with rate limit tracking
- Company context fetching with domain-level caching
- AI SDR Copilot with multi-agent architecture (research, campaign, analytics, inbox, deliverability agents)
- Intent detection and action registry for copilot commands

### Email Tracking
- 1x1 transparent pixel tracking for opens
- Click tracking with URL rewriting and redirect
- Signed tokens for verified tracking events
- tracking_events table for granular event history

### Inbox & Reply Management
- IMAP reply detection with configurable polling interval
- AI categorization of replies (interested, meeting, ooo, bounce, etc.)
- Thread-based inbox with unread counts
- AI-generated reply drafts

### Deliverability
- SMTP rotation across multiple sender accounts
- Email warmup stages (1–5) with health scoring
- Daily send limit enforcement with midnight reset
- Bounce/failure tracking with health penalties

### Automation
- Rule-based automation: no_reply_followup, opened_multiple_times, interested_pause, ooo_retry, bounce_pause
- Configurable delay days and max follow-up counts per rule
- Background worker runs every 30 minutes

### Analytics
- Dashboard: open rate, reply rate, click rate, bounce rate, meetings detected
- Per-campaign performance metrics
- Activity feed (replies, sends, clicks)
- AI usage tracking by provider and date
- Hot leads leaderboard with lead scores

## Target Users
- Sales teams running outbound email campaigns
- Founders doing founder-to-founder cold outreach
- Agencies managing email outreach for multiple clients (multi-tenant)

## Use Cases
- Cold outreach to founders/executives at tech companies
- Engineering staffing outreach (Shiksha Infotech's primary use case)
- Automated follow-up sequences for non-responders
- Inbox management for warm/interested replies
