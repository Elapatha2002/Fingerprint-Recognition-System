import unittest

from setup_supabase import ROLE, _role_statement, _runtime_url


class SupabaseSetupTests(unittest.TestCase):
    def test_create_role_uses_a_quoted_literal_not_bind_parameter(self):
        statement = _role_statement("CREATE", "contains'quote")
        rendered = statement.as_string()
        self.assertIn(f'CREATE ROLE "{ROLE}"', rendered)
        self.assertIn("PASSWORD 'contains''quote'", rendered)
        self.assertNotIn("$1", rendered)
        self.assertNotIn("%s", rendered)

    def test_alter_role_uses_same_safe_quoting(self):
        rendered = _role_statement("ALTER", "safe-password").as_string()
        self.assertIn(f'ALTER ROLE "{ROLE}"', rendered)
        self.assertIn("PASSWORD 'safe-password'", rendered)

    def test_unknown_role_action_is_rejected(self):
        with self.assertRaises(ValueError):
            _role_statement("DROP", "unused")

    def test_runtime_url_percent_encodes_credentials(self):
        result = _runtime_url(
            "postgresql://postgres.project:admin@pooler.example.com:5432/postgres",
            "runtime:/?#@ password",
        )
        self.assertIn("fingerprint_demo_runtime.project", result)
        self.assertIn("runtime%3A%2F%3F%23%40%20password", result)
        self.assertIn("sslmode=require", result)


if __name__ == "__main__":
    unittest.main()
