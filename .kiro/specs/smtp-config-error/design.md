# SMTP Configuration Error Bugfix Design

## Overview

This design addresses a bug where the campaign_manager incorrectly reports "SMTP not configured" when SMTP accounts exist but are temporarily unavailable. The root cause is that `get_next_smtp_account()` returns `None` for multiple distinct scenarios (no accounts, daily limits reached, low health, inactive accounts), but the calling code treats all `None` returns identically with a generic error message. The fix will modify the function to return structured information about why no account was available, enabling context-specific error messages.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the misleading error — `get_next_smtp_account()` returns `None` when SMTP accounts exist but are temporarily unavailable
- **Property (P)**: The desired behavior — return specific error messages based on the actual unavailability reason
- **Preservation**: Existing SMTP selection logic, health tracking, and successful send behavior that must remain unchanged
- **get_next_smtp_account()**: The function in `services/smtp_rotation.py` that selects the next available SMTP account using round-robin rotation
- **_send_email()**: The function in `tasks/sequence_tasks.py` that uses `get_next_smtp_account()` and falls back to global settings
- **health_score**: A 0-100 score tracking SMTP account reliability; accounts with score ≤ 20 are excluded from selection
- **daily_limit**: Maximum emails an account can send per day; `sent_today >= daily_limit` excludes the account
- **active**: Boolean flag; inactive accounts (active=0) are excluded from selection

## Bug Details

### Bug Condition

The bug manifests when SMTP accounts exist in the database but none are available for selection due to temporary conditions. The `get_next_smtp_account()` function returns `None` without distinguishing between "no accounts configured" and "accounts exist but are temporarily unavailable."

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type SMTPSelectionRequest
  OUTPUT: boolean
  
  accounts := SELECT * FROM smtp_accounts
  available := SELECT * FROM smtp_accounts 
               WHERE active = 1 
               AND sent_today < daily_limit 
               AND health_score > 20
  
  RETURN accounts.count > 0
         AND available.count = 0
         AND get_next_smtp_account() returns None
         AND error_message = "SMTP not configured"
END FUNCTION
```

### Examples

- **Daily Limit**: 3 SMTP accounts exist, all with `sent_today >= daily_limit`. User sees "SMTP not configured" instead of "Daily send limit reached"
- **Low Health**: 2 SMTP accounts exist, both with `health_score = 15`. User sees "SMTP not configured" instead of "No healthy SMTP accounts"
- **Inactive**: 4 SMTP accounts exist, all with `active = 0`. User sees "SMTP not configured" instead of "No active SMTP accounts"
- **Mixed Unavailability**: 5 accounts exist — 2 hit daily limits, 2 have low health, 1 is inactive. User sees "SMTP not configured" instead of a meaningful status
- **No Accounts (Correct)**: 0 SMTP accounts exist. User correctly sees "SMTP not configured"

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- When at least one SMTP account is active, within daily limits, and has health_score > 20, the system SHALL continue to select and return that account
- When sending succeeds, `sent_today` SHALL continue to increment and `last_used` SHALL continue to update
- When sending fails, health_score SHALL continue to decrement by 10
- When sending succeeds, health_score SHALL continue to increment by 1 (max 100)
- When no SMTP accounts exist AND global settings are configured, the system SHALL continue to use global settings as fallback
- The atomic UPDATE+SELECT pattern for Postgres SHALL continue to prevent race conditions
- The round-robin selection by `last_used ASC` SHALL continue to distribute load

**Scope:**
All inputs that result in successful SMTP account selection should be completely unaffected by this fix. This includes:
- Normal account selection when accounts are available
- Health score updates on success/failure
- Daily count increments
- Global settings fallback when no accounts exist

## Hypothesized Root Cause

Based on the code analysis, the root causes are:

1. **Undifferentiated Return Value**: `get_next_smtp_account()` returns `None` for all failure scenarios without indicating why:
   - No accounts exist at all
   - All accounts hit daily limit (`sent_today >= daily_limit`)
   - All accounts have low health (`health_score <= 20`)
   - All accounts are inactive (`active = 0`)
   - Combination of the above

2. **Generic Error Handling**: In `_send_email()` (lines 331-349), when `account` is `None`:
   ```python
   account = get_next_smtp_account()
   if account:
       # use account settings
   else:
       # fallback to global settings
   
   if not smtp_server or not smtp_user or not smtp_pass:
       return False, 'SMTP not configured'
   ```
   The fallback to global settings happens silently, and the error message doesn't reflect why the rotation accounts were unavailable.

3. **Missing Account Status Query**: There's no mechanism to query why accounts are unavailable before returning the generic error.

## Correctness Properties

Property 1: Bug Condition - Specific Error Messages for Unavailable SMTP

_For any_ request where SMTP accounts exist in the database but none are available for selection (isBugCondition returns true), the system SHALL return a specific error message indicating the actual reason for unavailability (daily limit reached, low health scores, or inactive accounts).

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Successful SMTP Selection Unchanged

_For any_ request where at least one SMTP account is active, within daily limits, and has health_score > 20, the system SHALL continue to select and return that account exactly as before, with all existing selection logic, health tracking, and send behavior unchanged.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `services/smtp_rotation.py`

**Function**: `get_next_smtp_account()`

**Specific Changes**:

1. **Add Status Enumeration**: Create a new return type or tuple that includes both the account (if found) and the reason (if not found):
   ```python
   class SMTPUnavailableReason:
       NONE_CONFIGURED = 'none_configured'
       ALL_AT_DAILY_LIMIT = 'daily_limit_reached'
       ALL_LOW_HEALTH = 'low_health'
       ALL_INACTIVE = 'all_inactive'
       MIXED_UNAVAILABLE = 'temporarily_unavailable'
   ```

2. **Add Diagnostic Query**: Before returning `None`, query to determine why no account is available:
   ```python
   def _get_unavailability_reason():
       conn = get_db()
       total = conn.execute("SELECT COUNT(*) FROM smtp_accounts").fetchone()[0]
       if total == 0:
           return SMTPUnavailableReason.NONE_CONFIGURED
       
       active_count = conn.execute(
           "SELECT COUNT(*) FROM smtp_accounts WHERE active = 1"
       ).fetchone()[0]
       if active_count == 0:
           return SMTPUnavailableReason.ALL_INACTIVE
       
       healthy_count = conn.execute(
           "SELECT COUNT(*) FROM smtp_accounts WHERE active = 1 AND health_score > 20"
       ).fetchone()[0]
       if healthy_count == 0:
           return SMTPUnavailableReason.ALL_LOW_HEALTH
       
       available_count = conn.execute(
           "SELECT COUNT(*) FROM smtp_accounts WHERE active = 1 AND health_score > 20 AND sent_today < daily_limit"
       ).fetchone()[0]
       if available_count == 0:
           return SMTPUnavailableReason.ALL_AT_DAILY_LIMIT
       
       return SMTPUnavailableReason.MIXED_UNAVAILABLE
   ```

3. **Create New Function**: Add `get_next_smtp_account_with_status()` that returns `(account, reason)`:
   ```python
   def get_next_smtp_account_with_status():
       """Returns (account_dict, None) on success, or (None, reason_string) on failure."""
       account = get_next_smtp_account()
       if account:
           return account, None
       return None, _get_unavailability_reason()
   ```

4. **Maintain Backward Compatibility**: Keep existing `get_next_smtp_account()` unchanged for other callers.

**File**: `tasks/sequence_tasks.py`

**Function**: `_send_email()`

**Specific Changes**:

1. **Import New Function**: 
   ```python
   from services.smtp_rotation import (
       get_next_smtp_account_with_status, mark_send_success, mark_send_failure
   )
   ```

2. **Use Status-Aware Selection**: Replace the current logic with:
   ```python
   account, unavailable_reason = get_next_smtp_account_with_status()
   if account:
       # existing account setup logic
   else:
       # fallback to global settings
       smtp_server = get_setting('smtp_server')
       # ... existing fallback logic ...
       
       if not smtp_server or not smtp_user or not smtp_pass:
           # Return specific error based on reason
           if unavailable_reason == 'none_configured':
               return False, 'SMTP not configured'
           elif unavailable_reason == 'daily_limit_reached':
               return False, 'Daily send limit reached — all SMTP accounts at capacity'
           elif unavailable_reason == 'low_health':
               return False, 'No healthy SMTP accounts available — all accounts have degraded health'
           elif unavailable_reason == 'all_inactive':
               return False, 'No active SMTP accounts — all accounts are disabled'
           else:
               return False, 'SMTP accounts temporarily unavailable'
   ```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that create SMTP accounts in various unavailable states and verify the error message returned by `_send_email()`. Run these tests on the UNFIXED code to observe the generic "SMTP not configured" error.

**Test Cases**:
1. **Daily Limit Test**: Create 2 accounts with `sent_today >= daily_limit`, verify error message (will show "SMTP not configured" on unfixed code)
2. **Low Health Test**: Create 2 accounts with `health_score = 10`, verify error message (will show "SMTP not configured" on unfixed code)
3. **Inactive Test**: Create 2 accounts with `active = 0`, verify error message (will show "SMTP not configured" on unfixed code)
4. **Mixed Unavailability Test**: Create 4 accounts with different unavailability reasons, verify error message (will show "SMTP not configured" on unfixed code)

**Expected Counterexamples**:
- All test cases return "SMTP not configured" regardless of the actual unavailability reason
- Possible causes: undifferentiated `None` return, generic error string in `_send_email()`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _send_email_fixed(input)
  ASSERT result.error_message IN [
    'Daily send limit reached — all SMTP accounts at capacity',
    'No healthy SMTP accounts available — all accounts have degraded health',
    'No active SMTP accounts — all accounts are disabled',
    'SMTP accounts temporarily unavailable'
  ]
  ASSERT result.error_message != 'SMTP not configured'
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT get_next_smtp_account_original(input) = get_next_smtp_account_fixed(input)
  ASSERT _send_email_original(input) = _send_email_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for successful SMTP selection, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Successful Selection Preservation**: Observe that `get_next_smtp_account()` returns valid accounts on unfixed code when available, verify this continues after fix
2. **Health Score Preservation**: Observe that `mark_send_success()` and `mark_send_failure()` update scores correctly on unfixed code, verify this continues after fix
3. **Round-Robin Preservation**: Observe that accounts are selected in `last_used ASC` order on unfixed code, verify this continues after fix
4. **Global Fallback Preservation**: Observe that global settings are used when no accounts exist on unfixed code, verify this continues after fix

### Unit Tests

- Test `get_next_smtp_account_with_status()` returns correct reason for each unavailability scenario
- Test `_get_unavailability_reason()` correctly diagnoses account states
- Test `_send_email()` returns appropriate error messages for each unavailability reason
- Test that "SMTP not configured" is only returned when no accounts exist AND global settings are empty

### Property-Based Tests

- Generate random sets of SMTP accounts with various `active`, `health_score`, `sent_today`, `daily_limit` values
- Verify that when at least one account passes all filters, it is selected
- Verify that error messages match the actual unavailability reason
- Test across many account configurations to ensure correct categorization

### Integration Tests

- Test full email sending flow with accounts at daily limit, verify error message
- Test full email sending flow with low health accounts, verify error message
- Test full email sending flow with inactive accounts, verify error message
- Test that copilot alerts (`services/copilot/alerts.py`) continue to work correctly for SMTP status detection
