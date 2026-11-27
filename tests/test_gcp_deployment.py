"""Offline safeguards for the optional Google Cloud e2-micro installer."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GCPDeploymentTests(unittest.TestCase):
    def test_install_script_targets_x86_64_and_preserves_secrets(self):
        installer = (ROOT / "deploy" / "gcp-install.sh").read_text(encoding="utf-8")
        self.assertIn("x86_64", installer)
        self.assertIn(".env.gcp.example", installer)
        self.assertIn('if [[ ! -e "$destination/.env" ]]', installer)
        self.assertIn("systemctl enable voicehub.service", installer)
        self.assertNotIn("systemctl start voicehub.service", installer)

    def test_dashboard_is_local_and_database_is_persistent(self):
        env = (ROOT / ".env.gcp.example").read_text(encoding="utf-8")
        self.assertIn("HOST=127.0.0.1", env)
        self.assertIn("PORT=8000", env)
        self.assertIn("COOKIE_SECURE=true", env)
        self.assertIn("DATABASE_PATH=/var/lib/voice-hub/voice_hub.sqlite3", env)
        self.assertIn("DISCORD_REDIRECT_URI=https://", env)
        self.assertIn("replace_with_bot_token", env)

    def test_cost_warnings_and_zone_constraints_are_documented(self):
        docs = (ROOT / "docs" / "GOOGLE_CLOUD_E2_MICRO_JA.md").read_text(encoding="utf-8")
        for term in ("us-central1", "us-west1", "us-east1", "pd-standard", "IPv4", "1GB", "Tailscale Funnel"):
            self.assertIn(term, docs)


if __name__ == "__main__":
    unittest.main()
