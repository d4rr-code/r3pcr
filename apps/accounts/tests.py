"""Characterization tests for apps.accounts views + helpers.

Locks the behavior of the auth surface (login/OTP, registration, password &
username recovery, account settings) and the validation helpers BEFORE the
views.py god-file is split into a package. Everything is exercised through the
public `apps.accounts.views` surface so the post-split re-export shim is covered
too.

Run:  python manage.py test apps.accounts --settings=config.settings_test
"""
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.cache import cache

from apps.accounts.models import User, OTP
from apps.accounts.views import (
    _normalize_phone_number, _validate_phone_number, _validate_profile_fields,
    _validate_password_strength, _generate_username, redirect_by_role,
)


# ─── Validation helpers (pure) ────────────────────────────────────────────────

class ValidatorTests(TestCase):
    def test_normalize_phone_number(self):
        self.assertEqual(_normalize_phone_number('+639171234567'), '09171234567')
        self.assertEqual(_normalize_phone_number('63 917 123 4567'), '09171234567')
        self.assertEqual(_normalize_phone_number('0917-123-4567'), '09171234567')
        self.assertEqual(_normalize_phone_number(''), '')
        self.assertEqual(_normalize_phone_number(None), '')

    def test_validate_phone_number(self):
        self.assertIsNone(_validate_phone_number('09171234567'))
        self.assertIsNone(_validate_phone_number('+639171234567'))
        self.assertIsNone(_validate_phone_number(''))          # optional → no error
        self.assertIsNotNone(_validate_phone_number('12345'))  # too short
        self.assertIsNotNone(_validate_phone_number('08171234567'))  # wrong prefix

    def test_validate_profile_fields_valid(self):
        self.assertEqual(
            _validate_profile_fields('Juan', 'Dela Cruz', 'juan@example.com',
                                     '09171234567', 'Acme'),
            [],
        )

    def test_validate_profile_fields_accented_name_ok(self):
        # Guards the À-ÿ regex range (see ftfy-on-code-files feedback): an
        # accented name must remain valid and not be rejected.
        self.assertEqual(
            _validate_profile_fields('José', 'Muñoz', 'jose@example.com'),
            [],
        )

    def test_validate_profile_fields_rejects_non_latin_name_scripts(self):
        samples = [
            ('Ψψ', 'Santos'),
            ('Juan', 'Ωmega'),
            ('大鹿', 'Santos'),
        ]

        for first_name, last_name in samples:
            with self.subTest(first_name=first_name, last_name=last_name):
                errors = _validate_profile_fields(first_name, last_name, 'juan@example.com')
                self.assertTrue(any('may only contain letters' in e for e in errors))

    def test_validate_profile_fields_errors(self):
        errors = _validate_profile_fields('', 'X', 'not-an-email')
        self.assertTrue(any('First name' in e for e in errors))
        self.assertTrue(any('at least 2' in e for e in errors))   # last name too short
        self.assertTrue(any('valid email' in e for e in errors))

    def test_validate_profile_fields_rejects_email_alias_symbols(self):
        errors = _validate_profile_fields('Juan', 'Dela Cruz', 'juan+1@example.com')

        self.assertTrue(any('letters, numbers, dots, underscores, and hyphens' in e for e in errors))

    def test_validate_profile_fields_rejects_non_latin_company_text(self):
        errors = _validate_profile_fields('Juan', 'Dela Cruz', 'juan@example.com', company='大鹿 Trading')

        self.assertTrue(any('Company name may only contain' in e for e in errors))

    def test_validate_password_strength(self):
        self.assertEqual(_validate_password_strength('Str0ng!Pass'), [])
        weak = _validate_password_strength('abc')
        self.assertTrue(any('at least 8' in e for e in weak))
        self.assertTrue(any('uppercase' in e for e in weak))
        self.assertTrue(any('number' in e for e in weak))
        self.assertTrue(any('special' in e for e in weak))

    def test_generate_username(self):
        self.assertEqual(_generate_username('Juan', 'Dela Cruz'), 'jdelacruz')

    def test_generate_username_uniqueness(self):
        User.objects.create_user(username='jdelacruz', password='x',
                                 email='a@test.local')
        self.assertEqual(_generate_username('Juan', 'Dela Cruz'), 'jdelacruz2')


class RedirectByRoleTests(TestCase):
    def _user(self, role):
        return User.objects.create_user(username=f'u_{role}', password='x',
                                        role=role, email=f'{role}@test.local')

    def test_redirect_by_role(self):
        self.assertEqual(redirect_by_role(self._user('consignee')).url,
                         '/consignee/dashboard/')
        self.assertEqual(redirect_by_role(self._user('declarant')).url,
                         '/declarant/dashboard/')
        self.assertEqual(redirect_by_role(self._user('supervisor')).url,
                         '/supervisor/dashboard/')


# ─── Login / OTP ──────────────────────────────────────────────────────────────

class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='consignee_x', password='Secret123!', role='consignee',
            email='cx@test.local', is_active=True,
        )

    def test_login_page_get(self):
        self.assertEqual(self.client.get(reverse('accounts:login')).status_code, 200)

    def test_login_bad_credentials_stays_on_page(self):
        resp = self.client.post(reverse('accounts:login'),
                                {'username': 'consignee_x', 'password': 'wrong'})
        self.assertEqual(resp.status_code, 200)        # re-render, not redirect
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_otp_disabled_logs_in_directly(self):
        self.user.otp_enabled = False
        self.user.save()
        resp = self.client.post(reverse('accounts:login'),
                                {'username': 'consignee_x', 'password': 'Secret123!'})
        self.assertRedirects(resp, '/consignee/dashboard/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)

    def test_login_accepts_email_identifier(self):
        self.user.otp_enabled = False
        self.user.save()
        resp = self.client.post(reverse('accounts:login'),
                                {'username': 'cx@test.local', 'password': 'Secret123!'})
        self.assertRedirects(resp, '/consignee/dashboard/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)

    def test_login_email_identifier_is_case_insensitive(self):
        self.user.otp_enabled = False
        self.user.save()
        resp = self.client.post(reverse('accounts:login'),
                                {'username': 'CX@Test.Local', 'password': 'Secret123!'})
        self.assertRedirects(resp, '/consignee/dashboard/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)

    def test_login_duplicate_email_identifier_is_ambiguous(self):
        self.user.otp_enabled = False
        self.user.save()
        User.objects.create_user(
            username='other_cx', password='Secret123!', role='consignee',
            email='cx@test.local', is_active=True,
        )
        resp = self.client.post(reverse('accounts:login'),
                                {'username': 'cx@test.local', 'password': 'Secret123!'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_otp_enabled_goes_to_verify(self):
        resp = self.client.post(reverse('accounts:login'),
                                {'username': 'consignee_x', 'password': 'Secret123!'})
        self.assertRedirects(resp, reverse('accounts:verify_otp'),
                             fetch_redirect_response=False)
        self.assertIn('pre_auth_user_id', self.client.session)
        self.assertEqual(OTP.objects.filter(user=self.user, is_used=False).count(), 1)

    def test_login_brute_force_lockout(self):
        url = reverse('accounts:login')
        for _ in range(8):
            self.client.post(url, {'username': 'consignee_x', 'password': 'wrong'})
        resp = self.client.post(url, {'username': 'consignee_x', 'password': 'Secret123!'})
        self.assertEqual(resp.status_code, 200)        # locked → not logged in
        self.assertNotIn('_auth_user_id', self.client.session)


class VerifyOtpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='otp_user', password='Secret123!', role='declarant',
            email='otp@test.local', is_active=True,
        )

    def test_verify_otp_without_session_redirects_to_login(self):
        self.assertRedirects(self.client.get(reverse('accounts:verify_otp')),
                             reverse('accounts:login'), fetch_redirect_response=False)

    def test_verify_otp_success(self):
        otp = OTP.objects.create(user=self.user, code='123456')
        session = self.client.session
        session['pre_auth_user_id'] = self.user.id
        session.save()
        resp = self.client.post(reverse('accounts:verify_otp'), {'otp_code': '123456'})
        self.assertRedirects(resp, '/declarant/dashboard/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        REGISTRATION_EMAIL_DEV_LINKS=False,
        LOGIN_OTP_SCREEN_HINT=False,
    )
    def test_verify_otp_hides_code_by_default(self):
        OTP.objects.create(user=self.user, code='123456')
        session = self.client.session
        session['pre_auth_user_id'] = self.user.id
        session.save()

        resp = self.client.get(reverse('accounts:verify_otp'))

        self.assertNotContains(resp, '123456')
        self.assertNotContains(resp, 'Testing Mode - OTP Code')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        REGISTRATION_EMAIL_DEV_LINKS=False,
        LOGIN_OTP_SCREEN_HINT=True,
    )
    def test_verify_otp_can_show_code_for_survey_testing(self):
        OTP.objects.create(user=self.user, code='123456')
        session = self.client.session
        session['pre_auth_user_id'] = self.user.id
        session.save()

        resp = self.client.get(reverse('accounts:verify_otp'))

        self.assertContains(resp, '123456')
        self.assertContains(resp, 'Testing Mode - OTP Code')


# ─── Registration ─────────────────────────────────────────────────────────────

class RegistrationTests(TestCase):
    def test_register_page_get(self):
        self.assertEqual(self.client.get(reverse('accounts:register')).status_code, 200)

    def test_register_valid_creates_inactive_pending_user(self):
        resp = self.client.post(reverse('accounts:register'), {
            'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'email': 'newuser@example.com', 'password': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass', 'company_name': 'Acme',
        })
        self.assertRedirects(resp, reverse('accounts:verify_registration_email'), fetch_redirect_response=False)
        u = User.objects.get(email='newuser@example.com')
        self.assertFalse(u.is_active)
        self.assertTrue(u.is_pending_approval)
        self.assertFalse(u.email_verified)
        self.assertEqual(u.role, 'consignee')
        self.assertIsNone(u.phone_number)
        self.assertTrue(OTP.objects.filter(user=u, is_used=False).exists())
        self.assertEqual(self.client.session['pending_registration_user_id'], u.id)

    def test_registration_email_otp_marks_user_verified(self):
        self.client.post(reverse('accounts:register'), {
            'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'email': 'verifyme@example.com', 'password': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass', 'company_name': 'Acme',
        })
        u = User.objects.get(email='verifyme@example.com')
        otp = OTP.objects.filter(user=u, is_used=False).latest('created_at')

        resp = self.client.post(reverse('accounts:verify_registration_email'), {
            'otp_code': otp.code,
        })

        self.assertRedirects(resp, reverse('accounts:login'), fetch_redirect_response=False)
        u.refresh_from_db()
        otp.refresh_from_db()
        self.assertTrue(u.email_verified)
        self.assertTrue(u.is_pending_approval)
        self.assertFalse(u.is_active)
        self.assertTrue(otp.is_used)
        self.assertNotIn('pending_registration_user_id', self.client.session)

    def test_register_password_mismatch(self):
        resp = self.client.post(reverse('accounts:register'), {
            'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'email': 'mismatch@example.com', 'password': 'Str0ng!Pass',
            'password2': 'Different1!',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email='mismatch@example.com').exists())

    def test_register_rejects_email_alias_symbols(self):
        resp = self.client.post(reverse('accounts:register'), {
            'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'email': 'hansmlbbacc1+1@gmail.com', 'password': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass',
        })

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email='hansmlbbacc1+1@gmail.com').exists())
        self.assertNotIn('pending_registration_user_id', self.client.session)

    def test_register_rejects_non_latin_name_scripts(self):
        resp = self.client.post(reverse('accounts:register'), {
            'first_name': '大鹿', 'last_name': 'Dela Cruz',
            'email': 'nonlatin@example.com', 'password': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass',
        })

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email='nonlatin@example.com').exists())

    def test_register_duplicate_email(self):
        User.objects.create_user(username='existing', password='x',
                                 email='dupe@example.com')
        resp = self.client.post(reverse('accounts:register'), {
            'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'email': 'dupe@example.com', 'password': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(email='dupe@example.com').count(), 1)

    def test_register_duplicate_email_is_case_insensitive(self):
        User.objects.create_user(username='existing_case', password='x',
                                 email='dupecase@example.com')
        resp = self.client.post(reverse('accounts:register'), {
            'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'email': 'DupeCase@Example.com', 'password': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass',
        })

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(email__iexact='dupecase@example.com').count(), 1)


# ─── Password / username recovery ─────────────────────────────────────────────

class RecoveryPageTests(TestCase):
    def test_forgot_password_get(self):
        self.assertEqual(
            self.client.get(reverse('accounts:forgot_password')).status_code, 200)

    def test_forgot_username_get(self):
        self.assertEqual(
            self.client.get(reverse('accounts:forgot_username')).status_code, 200)

    def test_reset_password_without_session_redirects(self):
        self.assertRedirects(self.client.get(reverse('accounts:reset_password')),
                             reverse('accounts:forgot_password'),
                             fetch_redirect_response=False)

    def test_forgot_username_emails_existing_user(self):
        User.objects.create_user(username='findme', password='x',
                                 email='findme@example.com', is_active=True)
        resp = self.client.post(reverse('accounts:forgot_username'),
                                {'email': 'findme@example.com'})
        self.assertEqual(resp.status_code, 200)


# ─── Account settings ─────────────────────────────────────────────────────────

class AccountSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='settings_user', password='Secret123!', role='consignee',
            email='su@test.local', is_active=True, first_name='Old', last_name='Name',
        )

    def test_settings_requires_login(self):
        resp = self.client.get(reverse('accounts:settings'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login', resp.url)

    def test_settings_get_authenticated(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('accounts:settings')).status_code, 200)

    def test_settings_profile_update(self):
        self.client.force_login(self.user)
        self.client.post(reverse('accounts:settings'), {
            'action': 'profile', 'first_name': 'New', 'last_name': 'Person',
            'email': 'su@test.local', 'phone_number': '09171234567',
            'company_name': 'NewCo',
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'New')
        self.assertEqual(self.user.company_name, 'NewCo')

    def test_settings_rejects_duplicate_email_case_insensitive(self):
        User.objects.create_user(username='settings_other', password='x',
                                 email='other@test.local', is_active=True)
        self.client.force_login(self.user)

        self.client.post(reverse('accounts:settings'), {
            'action': 'profile', 'first_name': 'Old', 'last_name': 'Name',
            'email': 'Other@Test.Local', 'phone_number': '',
            'company_name': '',
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'su@test.local')

    def test_settings_password_change_wrong_old(self):
        self.client.force_login(self.user)
        self.client.post(reverse('accounts:settings'), {
            'action': 'password', 'old_password': 'wrong',
            'new_password': 'BrandNew1!', 'confirm_password': 'BrandNew1!',
        })
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('BrandNew1!'))  # unchanged


class LogoutTests(TestCase):
    def test_logout_redirects_to_login(self):
        user = User.objects.create_user(username='bye', password='x',
                                        email='bye@test.local', is_active=True)
        self.client.force_login(user)
        resp = self.client.get(reverse('accounts:logout'))
        self.assertRedirects(resp, reverse('accounts:login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
