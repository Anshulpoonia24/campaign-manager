"""
tests/test_smtp_preservation.py — Preservation Property Tests
===============================================================
**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

This test file captures the BASELINE behavior of the SMTP rotation system
that MUST remain unchanged after the bugfix is implemented.

These tests SHOULD PASS on UNFIXED code, confirming the behavior we need to preserve.
After the fix is implemented, these tests should still pass, confirming no regressions.

Preservation Properties:
- Property: For any set of accounts where at least one is active, within daily limit, 
  and health_score > 20, `get_next_smtp_account()` returns a valid account
- Property: For any successful send, health_score increases by exactly 1 (capped at 100)
- Property: For any failed send, health_score decreases by exactly 10 (min 0)
- Property: When no accounts exist AND global settings configured, fallback is used
"""
import os
import sys
import sqlite3
import tempfile
import shutil
from datetime import datetime, timedelta
from unittest.mock import patch
import random

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSMTPPreservation:
    """
    Preservation Property Tests
    
    These tests capture and verify the baseline behavior of the SMTP rotation
    system that must remain unchanged after the bugfix.
    
    EXPECTED: All tests PASS on UNFIXED code (confirms baseline behavior)
    EXPECTED: All tests still PASS after the fix (confirms no regressions)
    """
    
    @classmethod
    def setup_class(cls):
        """Create a temporary test database with required schema."""
        cls.temp_dir = tempfile.mkdtemp()
        cls.test_db_path = os.path.join(cls.temp_dir, 'test_campaigns.db')
        
        # Create test database with schema
        conn = sqlite3.connect(cls.test_db_path)
        conn.row_factory = sqlite3.Row
        
        # Create smtp_accounts table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS smtp_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                smtp_server TEXT NOT NULL,
                smtp_port INTEGER DEFAULT 587,
                login_username TEXT,
                from_name TEXT,
                reply_to TEXT,
                bcc_emails TEXT,
                signature TEXT,
                daily_limit INTEGER DEFAULT 50,
                sent_today INTEGER DEFAULT 0,
                health_score INTEGER DEFAULT 100,
                warmup_stage INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                last_used DATETIME
            )
        """)
        
        # Create settings table for global fallback test
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    @classmethod
    def teardown_class(cls):
        """Clean up temporary database."""
        if hasattr(cls, 'temp_dir') and os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir)
    
    def setup_method(self):
        """Reset database state before each test."""
        conn = sqlite3.connect(self.test_db_path)
        conn.execute("DELETE FROM smtp_accounts")
        conn.execute("DELETE FROM settings")
        conn.commit()
        conn.close()
    
    def _get_test_db(self):
        """Return a connection to the test database."""
        conn = sqlite3.connect(self.test_db_path, timeout=60, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _add_smtp_account(self, email, daily_limit=50, sent_today=0, 
                          health_score=100, active=1, last_used=None):
        """Add an SMTP account to the test database and return its ID."""
        conn = self._get_test_db()
        conn.execute("""
            INSERT INTO smtp_accounts 
            (email, password, smtp_server, smtp_port, daily_limit, 
             sent_today, health_score, active, warmup_stage, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (email, 'test_password', 'smtp.test.com', 587, 
              daily_limit, sent_today, health_score, active, 1, last_used))
        conn.commit()
        account_id = conn.execute("SELECT id FROM smtp_accounts WHERE email=?", (email,)).fetchone()[0]
        conn.close()
        return account_id
    
    def _get_account_health(self, account_id):
        """Get current health_score for an account."""
        conn = self._get_test_db()
        row = conn.execute("SELECT health_score FROM smtp_accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        return row[0] if row else None
    
    def _set_global_setting(self, key, value):
        """Set a global setting in the settings table."""
        conn = self._get_test_db()
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
        """, (key, value))
        conn.commit()
        conn.close()
    
    # ══════════════════════════════════════════════════════════════
    # Property 1: Successful SMTP Selection
    # ══════════════════════════════════════════════════════════════
    
    def test_returns_valid_account_when_available(self):
        """
        **Validates: Requirements 3.1**
        
        Property: For any set of accounts where at least one is active, 
        within daily limit, and health_score > 20, get_next_smtp_account() 
        returns a valid account.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        # Create an available account: active=1, within daily limit, health > 20
        account_id = self._add_smtp_account(
            'available@test.com', 
            daily_limit=50, 
            sent_today=10, 
            health_score=80, 
            active=1
        )
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected a valid account to be returned"
            assert account['email'] == 'available@test.com', "Expected the available account to be selected"
            assert account['id'] == account_id, "Expected the correct account ID"
    
    def test_selects_account_among_multiple_available(self):
        """
        **Validates: Requirements 3.1**
        
        Property: When multiple accounts are available, one is selected.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        # Create multiple available accounts
        self._add_smtp_account('smtp1@test.com', daily_limit=50, sent_today=10, 
                               health_score=80, active=1, last_used=datetime.now() - timedelta(hours=2))
        self._add_smtp_account('smtp2@test.com', daily_limit=100, sent_today=20, 
                               health_score=90, active=1, last_used=datetime.now() - timedelta(hours=1))
        self._add_smtp_account('smtp3@test.com', daily_limit=75, sent_today=5, 
                               health_score=70, active=1, last_used=datetime.now())
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            assert account['email'] in ['smtp1@test.com', 'smtp2@test.com', 'smtp3@test.com'], \
                "Expected one of the available accounts to be selected"
    
    def test_excludes_accounts_at_daily_limit(self):
        """
        **Validates: Requirements 3.1**
        
        Property: Accounts with sent_today >= daily_limit are excluded from selection.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        # Create one account at daily limit, one available
        self._add_smtp_account('at_limit@test.com', daily_limit=50, sent_today=50, 
                               health_score=100, active=1)
        account_id = self._add_smtp_account('available@test.com', daily_limit=50, sent_today=10, 
                                            health_score=100, active=1)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            assert account['email'] == 'available@test.com', \
                "Expected the available account to be selected, not the one at daily limit"
    
    def test_excludes_accounts_with_low_health(self):
        """
        **Validates: Requirements 3.1**
        
        Property: Accounts with health_score <= 20 are excluded from selection.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        # Create one account with low health, one available
        self._add_smtp_account('low_health@test.com', daily_limit=50, sent_today=0, 
                               health_score=15, active=1)
        account_id = self._add_smtp_account('healthy@test.com', daily_limit=50, sent_today=0, 
                                            health_score=80, active=1)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            assert account['email'] == 'healthy@test.com', \
                "Expected the healthy account to be selected, not the one with low health"
    
    def test_excludes_inactive_accounts(self):
        """
        **Validates: Requirements 3.1**
        
        Property: Accounts with active = 0 are excluded from selection.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        # Create one inactive account, one active
        self._add_smtp_account('inactive@test.com', daily_limit=50, sent_today=0, 
                               health_score=100, active=0)
        account_id = self._add_smtp_account('active@test.com', daily_limit=50, sent_today=0, 
                                            health_score=100, active=1)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            assert account['email'] == 'active@test.com', \
                "Expected the active account to be selected, not the inactive one"
    
    def test_returns_none_when_all_unavailable(self):
        """
        **Validates: Requirements 3.1**
        
        Property: When all accounts are unavailable (various reasons), 
        get_next_smtp_account() returns None.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        (Note: The FIX will add status information, but the return of None for 
        the account itself must remain unchanged in get_next_smtp_account())
        """
        # Create only unavailable accounts
        self._add_smtp_account('at_limit@test.com', daily_limit=50, sent_today=50, 
                               health_score=100, active=1)
        self._add_smtp_account('low_health@test.com', daily_limit=50, sent_today=0, 
                               health_score=10, active=1)
        self._add_smtp_account('inactive@test.com', daily_limit=50, sent_today=0, 
                               health_score=100, active=0)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is None, \
                "Expected None when all accounts are unavailable"
    
    # ══════════════════════════════════════════════════════════════
    # Property 2: Health Score Updates on Success
    # ══════════════════════════════════════════════════════════════
    
    def test_mark_send_success_increments_health_by_1(self):
        """
        **Validates: Requirements 3.5**
        
        Property: For any successful send, health_score increases by exactly 1.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        initial_health = 50
        account_id = self._add_smtp_account('test@test.com', health_score=initial_health)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import mark_send_success
            
            mark_send_success(account_id)
            
            new_health = self._get_account_health(account_id)
            
            assert new_health == initial_health + 1, \
                f"Expected health to increase by 1 (from {initial_health} to {initial_health + 1}), got {new_health}"
    
    def test_mark_send_success_caps_at_100(self):
        """
        **Validates: Requirements 3.5**
        
        Property: health_score is capped at 100 (cannot exceed max).
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        account_id = self._add_smtp_account('test@test.com', health_score=100)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import mark_send_success
            
            mark_send_success(account_id)
            
            new_health = self._get_account_health(account_id)
            
            assert new_health == 100, \
                f"Expected health to stay at 100 (max cap), got {new_health}"
    
    def test_mark_send_success_increments_near_cap(self):
        """
        **Validates: Requirements 3.5**
        
        Property: When health is 99, it increases to 100 (not beyond).
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        account_id = self._add_smtp_account('test@test.com', health_score=99)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import mark_send_success
            
            mark_send_success(account_id)
            
            new_health = self._get_account_health(account_id)
            
            assert new_health == 100, \
                f"Expected health to increase from 99 to 100, got {new_health}"
    
    # ══════════════════════════════════════════════════════════════
    # Property 3: Health Score Updates on Failure
    # ══════════════════════════════════════════════════════════════
    
    def test_mark_send_failure_decrements_health_by_10(self):
        """
        **Validates: Requirements 3.4**
        
        Property: For any failed send, health_score decreases by exactly 10.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        initial_health = 80
        account_id = self._add_smtp_account('test@test.com', health_score=initial_health)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import mark_send_failure
            
            mark_send_failure(account_id)
            
            new_health = self._get_account_health(account_id)
            
            assert new_health == initial_health - 10, \
                f"Expected health to decrease by 10 (from {initial_health} to {initial_health - 10}), got {new_health}"
    
    def test_mark_send_failure_floors_at_0(self):
        """
        **Validates: Requirements 3.4**
        
        Property: health_score cannot go below 0 (floored at min).
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        account_id = self._add_smtp_account('test@test.com', health_score=5)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import mark_send_failure
            
            mark_send_failure(account_id)
            
            new_health = self._get_account_health(account_id)
            
            assert new_health == 0, \
                f"Expected health to floor at 0, got {new_health}"
    
    def test_mark_send_failure_deactivates_at_zero_health(self):
        """
        **Validates: Requirements 3.4**
        
        Property: When health reaches 0, account is auto-deactivated.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        account_id = self._add_smtp_account('test@test.com', health_score=10, active=1)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import mark_send_failure
            
            mark_send_failure(account_id)
            
            conn = self._get_test_db()
            row = conn.execute(
                "SELECT health_score, active FROM smtp_accounts WHERE id=?", 
                (account_id,)
            ).fetchone()
            conn.close()
            
            assert row['health_score'] == 0, \
                f"Expected health to be 0, got {row['health_score']}"
            assert row['active'] == 0, \
                "Expected account to be deactivated when health reaches 0"
    
    # ══════════════════════════════════════════════════════════════
    # Property 4: Round-Robin Selection by last_used
    # ══════════════════════════════════════════════════════════════
    
    def test_round_robin_selects_least_recently_used(self):
        """
        **Validates: Requirements 3.1**
        
        Property: Account selection uses round-robin by `last_used ASC` order,
        selecting the account with the oldest (or NULL) last_used timestamp.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        now = datetime.now()
        
        # Create accounts with specific last_used times
        # Account 3 has oldest last_used, should be selected first
        self._add_smtp_account('smtp1@test.com', health_score=80, active=1, 
                               last_used=now - timedelta(minutes=5))
        self._add_smtp_account('smtp2@test.com', health_score=80, active=1, 
                               last_used=now - timedelta(minutes=10))
        self._add_smtp_account('smtp3@test.com', health_score=80, active=1, 
                               last_used=now - timedelta(minutes=30))  # Oldest - should be selected
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            assert account['email'] == 'smtp3@test.com', \
                f"Expected smtp3@test.com (oldest last_used) to be selected, got {account['email']}"
    
    def test_round_robin_prefers_null_last_used(self):
        """
        **Validates: Requirements 3.1**
        
        Property: Accounts with NULL last_used are selected first (never used).
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        now = datetime.now()
        
        # Create accounts - one never used (NULL last_used), others with timestamps
        self._add_smtp_account('used1@test.com', health_score=80, active=1, 
                               last_used=now - timedelta(hours=1))
        self._add_smtp_account('never_used@test.com', health_score=80, active=1, 
                               last_used=None)  # NULL - should be selected first
        self._add_smtp_account('used2@test.com', health_score=80, active=1, 
                               last_used=now - timedelta(minutes=30))
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            assert account['email'] == 'never_used@test.com', \
                f"Expected never_used@test.com (NULL last_used) to be selected first, got {account['email']}"
    
    # ══════════════════════════════════════════════════════════════
    # Property 5: sent_today Increment
    # ══════════════════════════════════════════════════════════════
    
    def test_sent_today_increments_on_selection(self):
        """
        **Validates: Requirements 3.3**
        
        Property: When an account is selected, sent_today is incremented by 1.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        account_id = self._add_smtp_account('test@test.com', daily_limit=50, 
                                            sent_today=10, health_score=80, active=1)
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is not None, "Expected an account to be returned"
            
            # Check sent_today was incremented
            conn = self._get_test_db()
            row = conn.execute(
                "SELECT sent_today FROM smtp_accounts WHERE id=?", 
                (account_id,)
            ).fetchone()
            conn.close()
            
            assert row['sent_today'] == 11, \
                f"Expected sent_today to increment from 10 to 11, got {row['sent_today']}"
    
    # ══════════════════════════════════════════════════════════════
    # Property 6: Global Settings Fallback
    # ══════════════════════════════════════════════════════════════
    
    def test_returns_none_when_no_accounts_exist(self):
        """
        **Validates: Requirements 3.2**
        
        Property: When no SMTP accounts exist, get_next_smtp_account() returns None.
        The calling code then falls back to global settings.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        # No accounts created - database is empty
        
        with patch('utils.db.get_db', self._get_test_db), \
             patch('utils.db.DB_PATH', self.test_db_path), \
             patch('utils.db.USE_POSTGRES', False), \
             patch('services.smtp_rotation.get_db', self._get_test_db):
            
            from services.smtp_rotation import get_next_smtp_account
            
            account = get_next_smtp_account()
            
            assert account is None, \
                "Expected None when no accounts exist in the database"
    
    # ══════════════════════════════════════════════════════════════
    # Property-Based Test: Randomized Account Selection
    # ══════════════════════════════════════════════════════════════
    
    def test_property_available_account_always_selected(self):
        """
        **Validates: Requirements 3.1**
        
        Property-Based Test: For any random configuration where at least one 
        account meets all criteria (active, within daily limit, health > 20),
        get_next_smtp_account() returns a valid account.
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        random.seed(42)  # For reproducibility
        
        for iteration in range(20):  # Run 20 random configurations
            # Reset database for each iteration
            self.setup_method()
            
            # Generate random accounts with at least one available
            num_accounts = random.randint(1, 5)
            available_created = False
            
            for i in range(num_accounts):
                email = f'smtp{i}_{iteration}@test.com'
                daily_limit = random.randint(10, 100)
                
                # Ensure at least one account is available
                if i == num_accounts - 1 and not available_created:
                    # Last account - make it available
                    sent_today = random.randint(0, daily_limit - 1)
                    health_score = random.randint(21, 100)
                    active = 1
                    available_created = True
                else:
                    # Random configuration
                    sent_today = random.randint(0, daily_limit + 10)
                    health_score = random.randint(0, 100)
                    active = random.choice([0, 1])
                    
                    if (active == 1 and sent_today < daily_limit and health_score > 20):
                        available_created = True
                
                self._add_smtp_account(email, daily_limit=daily_limit, 
                                       sent_today=sent_today, 
                                       health_score=health_score, 
                                       active=active)
            
            with patch('utils.db.get_db', self._get_test_db), \
                 patch('utils.db.DB_PATH', self.test_db_path), \
                 patch('utils.db.USE_POSTGRES', False), \
                 patch('services.smtp_rotation.get_db', self._get_test_db):
                
                from services.smtp_rotation import get_next_smtp_account
                
                account = get_next_smtp_account()
                
                assert account is not None, \
                    f"Iteration {iteration}: Expected an account to be returned when at least one is available"
                
                # Verify selected account meets criteria
                assert account['active'] == 1, \
                    f"Iteration {iteration}: Selected account should be active"
                # Note: sent_today is incremented by selection, so check it was < daily_limit before
                assert account['sent_today'] <= account['daily_limit'], \
                    f"Iteration {iteration}: Selected account should be within daily limit"
                assert account['health_score'] > 20, \
                    f"Iteration {iteration}: Selected account should have health > 20"
    
    def test_property_health_changes_bounded(self):
        """
        **Validates: Requirements 3.4, 3.5**
        
        Property-Based Test: For any initial health_score:
        - mark_send_success increases by 1, capped at 100
        - mark_send_failure decreases by 10, floored at 0
        
        PRESERVATION: This behavior MUST remain unchanged after the fix.
        """
        random.seed(42)  # For reproducibility
        
        for initial_health in [0, 1, 5, 10, 15, 20, 50, 90, 95, 99, 100]:
            # Test mark_send_success
            self.setup_method()
            account_id = self._add_smtp_account('test@test.com', health_score=initial_health)
            
            with patch('utils.db.get_db', self._get_test_db), \
                 patch('utils.db.DB_PATH', self.test_db_path), \
                 patch('utils.db.USE_POSTGRES', False), \
                 patch('services.smtp_rotation.get_db', self._get_test_db):
                
                from services.smtp_rotation import mark_send_success, mark_send_failure
                
                mark_send_success(account_id)
                new_health = self._get_account_health(account_id)
                expected_health = min(100, initial_health + 1)
                
                assert new_health == expected_health, \
                    f"mark_send_success: From {initial_health}, expected {expected_health}, got {new_health}"
            
            # Test mark_send_failure
            self.setup_method()
            account_id = self._add_smtp_account('test@test.com', health_score=initial_health)
            
            with patch('utils.db.get_db', self._get_test_db), \
                 patch('utils.db.DB_PATH', self.test_db_path), \
                 patch('utils.db.USE_POSTGRES', False), \
                 patch('services.smtp_rotation.get_db', self._get_test_db):
                
                from services.smtp_rotation import mark_send_failure
                
                mark_send_failure(account_id)
                new_health = self._get_account_health(account_id)
                expected_health = max(0, initial_health - 10)
                
                assert new_health == expected_health, \
                    f"mark_send_failure: From {initial_health}, expected {expected_health}, got {new_health}"


def run_tests():
    """Run all preservation property tests and report results."""
    import traceback
    
    print("=" * 70)
    print("SMTP Preservation Property Tests")
    print("=" * 70)
    print("\nThese tests capture baseline behavior that MUST be preserved.")
    print("EXPECTED: All tests PASS on UNFIXED code.")
    print("-" * 70)
    
    test_class = TestSMTPPreservation()
    test_class.setup_class()
    
    tests = [
        ('test_returns_valid_account_when_available', 
         'Property 1a: Returns valid account when available'),
        ('test_selects_account_among_multiple_available', 
         'Property 1b: Selects account among multiple available'),
        ('test_excludes_accounts_at_daily_limit', 
         'Property 1c: Excludes accounts at daily limit'),
        ('test_excludes_accounts_with_low_health', 
         'Property 1d: Excludes accounts with low health'),
        ('test_excludes_inactive_accounts', 
         'Property 1e: Excludes inactive accounts'),
        ('test_returns_none_when_all_unavailable', 
         'Property 1f: Returns None when all unavailable'),
        ('test_mark_send_success_increments_health_by_1', 
         'Property 2a: Success increments health by 1'),
        ('test_mark_send_success_caps_at_100', 
         'Property 2b: Success caps health at 100'),
        ('test_mark_send_success_increments_near_cap', 
         'Property 2c: Success increments from 99 to 100'),
        ('test_mark_send_failure_decrements_health_by_10', 
         'Property 3a: Failure decrements health by 10'),
        ('test_mark_send_failure_floors_at_0', 
         'Property 3b: Failure floors health at 0'),
        ('test_mark_send_failure_deactivates_at_zero_health', 
         'Property 3c: Deactivates account at zero health'),
        ('test_round_robin_selects_least_recently_used', 
         'Property 4a: Round-robin selects least recently used'),
        ('test_round_robin_prefers_null_last_used', 
         'Property 4b: Round-robin prefers NULL last_used'),
        ('test_sent_today_increments_on_selection', 
         'Property 5: sent_today increments on selection'),
        ('test_returns_none_when_no_accounts_exist', 
         'Property 6: Returns None when no accounts exist'),
        ('test_property_available_account_always_selected', 
         'Property-Based: Available account always selected'),
        ('test_property_health_changes_bounded', 
         'Property-Based: Health changes bounded correctly'),
    ]
    
    results = []
    failures = []
    
    for test_name, description in tests:
        test_class.setup_method()
        print(f"\n{description}")
        print(f"  Running: {test_name}")
        
        try:
            getattr(test_class, test_name)()
            print(f"  Result: PASS ✓")
            results.append(('PASS', test_name))
        except AssertionError as e:
            print(f"  Result: FAIL ✗")
            print(f"  Assertion: {str(e)[:200]}")
            results.append(('FAIL', test_name))
            failures.append({
                'test': test_name,
                'description': description,
                'error': str(e)
            })
        except Exception as e:
            print(f"  Result: ERROR")
            print(f"  Exception: {str(e)}")
            traceback.print_exc()
            results.append(('ERROR', test_name))
            failures.append({
                'test': test_name,
                'description': description,
                'error': str(e)
            })
    
    test_class.teardown_class()
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for r, _ in results if r == 'PASS')
    failed = sum(1 for r, _ in results if r == 'FAIL')
    errors = sum(1 for r, _ in results if r == 'ERROR')
    
    print(f"Total: {len(tests)} tests")
    print(f"  PASS: {passed} ✓")
    print(f"  FAIL: {failed} ✗")
    print(f"  ERROR: {errors}")
    
    if failures:
        print("\n" + "-" * 70)
        print("FAILURES:")
        print("-" * 70)
        for f in failures:
            print(f"\n  {f['description']}:")
            print(f"    {f['error'][:300]}")
    
    print("\n" + "=" * 70)
    if passed == len(tests):
        print("All tests PASSED - Baseline behavior captured successfully!")
        print("These behaviors must remain unchanged after implementing the fix.")
    else:
        print(f"WARNING: {failed + errors}/{len(tests)} tests did not pass!")
        print("Investigate failures before proceeding with the fix.")
    print("=" * 70)
    
    return passed, failed, errors


if __name__ == '__main__':
    run_tests()
