# Implementation Plan

## Overview

This task list implements the SMTP configuration error bugfix following the exploratory bugfix workflow. The fix addresses misleading "SMTP not configured" errors by providing context-specific messages when SMTP accounts exist but are temporarily unavailable.

---

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Misleading SMTP Not Configured Error
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to concrete failing cases where SMTP accounts exist but are unavailable
  - Create test file `tests/test_smtp_bug_condition.py`
  - Test Case 1: Create 2 SMTP accounts with `sent_today >= daily_limit`, call `_send_email()`, verify error message is NOT "SMTP not configured" (expect specific message about daily limits)
  - Test Case 2: Create 2 SMTP accounts with `health_score = 10` (below 20 threshold), call `_send_email()`, verify error message is NOT "SMTP not configured" (expect specific message about health)
  - Test Case 3: Create 2 SMTP accounts with `active = 0`, call `_send_email()`, verify error message is NOT "SMTP not configured" (expect specific message about inactive accounts)
  - Test Case 4: Mixed unavailability - create 3 accounts (1 at daily limit, 1 low health, 1 inactive), verify error message is NOT "SMTP not configured"
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct - they return "SMTP not configured" for all cases, proving the bug exists)
  - Document counterexamples found: all scenarios return generic "SMTP not configured" regardless of actual reason
  - Mark task complete when tests are written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Successful SMTP Selection and Send Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Create test file `tests/test_smtp_preservation.py`
  - Observe: `get_next_smtp_account()` returns valid account when accounts are available on unfixed code
  - Observe: `mark_send_success(account_id)` increments health_score by 1 (max 100) on unfixed code
  - Observe: `mark_send_failure(account_id)` decrements health_score by 10 on unfixed code
  - Observe: Account selection uses round-robin by `last_used ASC` order on unfixed code
  - Observe: Global settings fallback works when no accounts exist on unfixed code
  - Write property-based tests capturing observed behavior:
    - Property: For any set of accounts where at least one is active, within daily limit, and health_score > 20, `get_next_smtp_account()` returns a valid account
    - Property: For any successful send, health_score increases by exactly 1 (capped at 100)
    - Property: For any failed send, health_score decreases by exactly 10 (min 0)
    - Property: When no accounts exist AND global settings configured, fallback is used
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for misleading SMTP not configured error

  - [x] 3.1 Add SMTPUnavailableReason constants to smtp_rotation.py
    - Add class or module-level constants at top of `services/smtp_rotation.py`:
      ```python
      class SMTPUnavailableReason:
          NONE_CONFIGURED = 'none_configured'
          ALL_AT_DAILY_LIMIT = 'daily_limit_reached'
          ALL_LOW_HEALTH = 'low_health'
          ALL_INACTIVE = 'all_inactive'
          MIXED_UNAVAILABLE = 'temporarily_unavailable'
      ```
    - Place after existing imports and before `WARMUP_LIMITS`
    - _Bug_Condition: isBugCondition(input) where accounts.count > 0 AND available.count = 0_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.2 Add _get_unavailability_reason() helper function to smtp_rotation.py
    - Add new function after `get_next_smtp_account()`:
      ```python
      def _get_unavailability_reason():
          """Diagnose why no SMTP account is available."""
          conn = get_db()
          total = conn.execute("SELECT COUNT(*) FROM smtp_accounts").fetchone()[0]
          if total == 0:
              conn.close()
              return SMTPUnavailableReason.NONE_CONFIGURED
          
          active_count = conn.execute(
              "SELECT COUNT(*) FROM smtp_accounts WHERE active = 1"
          ).fetchone()[0]
          if active_count == 0:
              conn.close()
              return SMTPUnavailableReason.ALL_INACTIVE
          
          healthy_count = conn.execute(
              "SELECT COUNT(*) FROM smtp_accounts WHERE active = 1 AND health_score > 20"
          ).fetchone()[0]
          if healthy_count == 0:
              conn.close()
              return SMTPUnavailableReason.ALL_LOW_HEALTH
          
          available_count = conn.execute(
              "SELECT COUNT(*) FROM smtp_accounts WHERE active = 1 AND health_score > 20 AND sent_today < daily_limit"
          ).fetchone()[0]
          if available_count == 0:
              conn.close()
              return SMTPUnavailableReason.ALL_AT_DAILY_LIMIT
          
          conn.close()
          return SMTPUnavailableReason.MIXED_UNAVAILABLE
      ```
    - Diagnoses in priority order: no accounts → all inactive → all low health → all at limit → mixed
    - _Bug_Condition: Returns specific reason when accounts exist but none available_
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.3 Add get_next_smtp_account_with_status() function to smtp_rotation.py
    - Add new function after `_get_unavailability_reason()`:
      ```python
      def get_next_smtp_account_with_status():
          """
          Returns (account_dict, None) on success, or (None, reason_string) on failure.
          Use this function when you need to know WHY no account was available.
          """
          account = get_next_smtp_account()
          if account:
              return account, None
          return None, _get_unavailability_reason()
      ```
    - Maintains backward compatibility by keeping original `get_next_smtp_account()` unchanged
    - _Expected_Behavior: Returns (account, None) when available, (None, reason) when not_
    - _Preservation: Existing get_next_smtp_account() callers unaffected_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1_

  - [x] 3.4 Update _send_email() in sequence_tasks.py to use status-aware selection
    - Update import statement at line ~10 in `tasks/sequence_tasks.py`:
      ```python
      from services.smtp_rotation import (
          get_next_smtp_account_with_status, mark_send_success, mark_send_failure,
          SMTPUnavailableReason
      )
      ```
    - Modify `_send_email()` function (around line 295-350):
      - Replace `account = get_next_smtp_account()` with `account, unavailable_reason = get_next_smtp_account_with_status()`
      - Update error handling block to return context-specific messages:
        ```python
        if not smtp_server or not smtp_user or not smtp_pass:
            if unavailable_reason == SMTPUnavailableReason.NONE_CONFIGURED:
                return False, 'SMTP not configured'
            elif unavailable_reason == SMTPUnavailableReason.ALL_AT_DAILY_LIMIT:
                return False, 'Daily send limit reached — all SMTP accounts at capacity'
            elif unavailable_reason == SMTPUnavailableReason.ALL_LOW_HEALTH:
                return False, 'No healthy SMTP accounts available — all accounts have degraded health'
            elif unavailable_reason == SMTPUnavailableReason.ALL_INACTIVE:
                return False, 'No active SMTP accounts — all accounts are disabled'
            else:
                return False, 'SMTP accounts temporarily unavailable'
        ```
    - _Bug_Condition: isBugCondition(input) where accounts exist but unavailable_
    - _Expected_Behavior: Return specific error message matching unavailability reason_
    - _Preservation: Normal send flow unchanged when account is available_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2_

  - [x] 3.5 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Specific Error Messages
    - **IMPORTANT**: Re-run the SAME tests from task 1 - do NOT write new tests
    - The tests from task 1 encode the expected behavior
    - When these tests pass, it confirms the expected behavior is satisfied
    - Run `tests/test_smtp_bug_condition.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms bug is fixed)
    - Verify each scenario returns the correct specific error message:
      - Daily limit → "Daily send limit reached — all SMTP accounts at capacity"
      - Low health → "No healthy SMTP accounts available — all accounts have degraded health"
      - Inactive → "No active SMTP accounts — all accounts are disabled"
      - Mixed → "SMTP accounts temporarily unavailable"
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.6 Verify preservation tests still pass
    - **Property 2: Preservation** - Existing Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run `tests/test_smtp_preservation.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after fix:
      - Successful account selection works
      - Health score updates work correctly
      - Round-robin selection works
      - Global fallback works
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite to ensure no regressions
  - Verify both bug condition tests and preservation tests pass
  - Manual verification: Test each unavailability scenario returns correct error message
  - Ask user if any questions arise
