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

    def test_coolify_compose_uses_real_nginx_port_without_host_publish(self):
        compose = (ROOT / "compose.yaml").read_text()

        self.assertNotIn("\n    ports:", compose)
        self.assertNotIn("\nports:", compose)
        self.assertIn('image: ${FRONTEND_IMAGE:-nginx:1.27-alpine}', compose)
        frontend = self.service_block(compose, "frontend")
        self.assertNotIn('    command:', frontend)
        self.assertNotIn('listen 8080;', frontend)
        self.assertIn('      - "80"', frontend)
        self.assertNotIn('      - "8080"', frontend)

    def test_frontend_and_backend_define_container_healthchecks(self):
        compose = (ROOT / "compose.yaml").read_text()

        frontend = self.service_block(compose, "frontend")
        backend = self.service_block(compose, "backend")
        self.assertIn("healthcheck:", frontend)
        self.assertIn("http://127.0.0.1/", frontend)
        self.assertIn("healthcheck:", backend)
        self.assertIn("http://127.0.0.1:8000/", backend)

    def test_no_local_compose_override_is_part_of_coolify_pattern(self):
        self.assertFalse((ROOT / "compose.local.yaml").exists())


if __name__ == "__main__":
    unittest.main()
