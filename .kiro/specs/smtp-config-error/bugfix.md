# Bugfix Requirements Document

## Introduction

This document addresses a bug where the campaign_manager Flask application incorrectly reports "SMTP not configured" even when SMTP accounts exist in the database but are temporarily unavailable due to daily limits, low health scores, or inactive status. The error message is misleading and prevents users from understanding the actual issue.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN SMTP accounts exist in the `smtp_accounts` table but all have `sent_today >= daily_limit` THEN the system returns the error "SMTP not configured" instead of indicating the daily send limit has been reached

1.2 WHEN SMTP accounts exist but all have `health_score <= 20` THEN the system returns "SMTP not configured" instead of indicating accounts are unhealthy

1.3 WHEN SMTP accounts exist but all have `active = 0` THEN the system returns "SMTP not configured" instead of indicating no active accounts are available

1.4 WHEN SMTP accounts exist but are temporarily unavailable (any combination of limits/health/inactive) AND global fallback settings are empty THEN the system returns "SMTP not configured" which incorrectly suggests SMTP was never configured

### Expected Behavior (Correct)

2.1 WHEN SMTP accounts exist but all have exhausted their daily limits THEN the system SHALL return "Daily send limit reached — all SMTP accounts at capacity" (or similar descriptive message)

2.2 WHEN SMTP accounts exist but all have `health_score <= 20` THEN the system SHALL return "No healthy SMTP accounts available — all accounts have degraded health" (or similar descriptive message)

2.3 WHEN SMTP accounts exist but all have `active = 0` THEN the system SHALL return "No active SMTP accounts — all accounts are disabled" (or similar descriptive message)

2.4 WHEN no SMTP accounts exist in the database AND global fallback settings are empty THEN the system SHALL return "SMTP not configured" (this is the only case where this message is accurate)

### Unchanged Behavior (Regression Prevention)

3.1 WHEN at least one SMTP account is active, within daily limits, and has health_score > 20 THEN the system SHALL CONTINUE TO select and return that account for sending

3.2 WHEN no SMTP accounts exist AND global settings (smtp_server, smtp_username, smtp_password) are fully configured THEN the system SHALL CONTINUE TO use the global settings as fallback

3.3 WHEN an SMTP account is successfully used for sending THEN the system SHALL CONTINUE TO increment `sent_today` and update `last_used`

3.4 WHEN sending fails via an SMTP account THEN the system SHALL CONTINUE TO decrement the account's health_score

3.5 WHEN sending succeeds via an SMTP account THEN the system SHALL CONTINUE TO increment the account's health_score
