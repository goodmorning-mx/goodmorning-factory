from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CoolifyComposeTests(unittest.TestCase):
    def service_block(self, compose_text: str, service: str) -> str:
        lines = compose_text.splitlines()
        markers = [
            (line.strip()[:-1], index)
            for index, line in enumerate(lines)
            if line.startswith("  ")
            and not line.startswith("    ")
            and line.strip().endswith(":")
        ]
        starts = [index for name, index in markers if name == service]
        self.assertTrue(starts, f"missing {service} service in compose.yaml")
        start = starts[0]
        following = [index for _name, index in markers if index > start]
        end = min(following) if following else len(lines)
        return "\n".join(lines[start:end])

    def test_coolify_compose_uses_nginx_listen_port_without_host_publish(self):
        compose = (ROOT / "compose.yaml").read_text()

        self.assertNotIn("\n    ports:", compose)
        self.assertNotIn("\nports:", compose)
        self.assertIn('image: ${FRONTEND_IMAGE:-nginx:1.27-alpine}', compose)
        frontend = self.service_block(compose, "frontend")
        self.assertIn('listen 8080;', frontend)
        self.assertIn('      - "8080"', frontend)
        self.assertNotIn('      - "80"', frontend)

    def test_local_override_maps_host_8080_to_frontend_container_8080(self):
        local = (ROOT / "compose.local.yaml").read_text()

        self.assertEqual(local.count("ports:"), 2)
        self.assertIn('      - "8080:8080"', local)
        self.assertIn('      - "8000:8000"', local)
        self.assertNotIn("5432:5432", local)


if __name__ == "__main__":
    unittest.main()
