from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
import os
import subprocess
import sys

from config.storage import validate_storage_project


class StorageProjectTests(SimpleTestCase):
    def test_matching_pooler_project_is_allowed(self):
        validate_storage_project({'USER': 'postgres.devref', 'HOST': 'pooler.supabase.com'},
                                 'https://devref.supabase.co')

    def test_matching_direct_database_project_is_allowed(self):
        validate_storage_project({'USER': 'postgres', 'HOST': 'db.devref.supabase.co'},
                                 'https://devref.supabase.co')

    def test_different_projects_are_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_storage_project({'USER': 'postgres.devref'}, 'https://prodref.supabase.co')

    def test_unknown_database_or_endpoint_is_rejected(self):
        for database, url in [({}, 'https://devref.supabase.co'),
                              ({'USER': 'postgres.devref'}, 'https://example.com'),
                              ({'USER': 'postgres.devref'}, 'http://devref.supabase.co')]:
            with self.subTest(database=database, url=url), self.assertRaises(ImproperlyConfigured):
                validate_storage_project(database, url)


class StorageStartupTests(SimpleTestCase):
    def test_storage_mode_is_enforced_at_startup(self):
        script = (
            "from unittest.mock import patch; "
            "patch('dotenv.load_dotenv', return_value=False).start(); "
            "from config import settings; "
            "print(settings.STORAGES['default']['BACKEND'])"
        )
        for mode, storage_ref, backend in [
            ('local', 'prodref', 'FileSystemStorage'),
            ('supabase', 'devref', 'PrivateS3Storage'),
            ('auto', 'prodref', None),
        ]:
            env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP') if key in os.environ}
            env.update(SECRET_KEY='synthetic-test-key', DB_USER='postgres.devref',
                       SHIPMENT_STORAGE_MODE=mode, SUPABASE_URL=f'https://{storage_ref}.supabase.co',
                       SUPABASE_S3_ACCESS_KEY_ID='synthetic',
                       SUPABASE_S3_SECRET_ACCESS_KEY='synthetic')
            with self.subTest(mode=mode, storage_ref=storage_ref):
                result = subprocess.run([sys.executable, '-B', '-c', script], env=env,
                                        capture_output=True, text=True, timeout=20)
                if backend:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(backend, result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('storage must match the database project', result.stderr)
