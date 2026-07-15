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
        self.assertIn("image: ${FRONTEND_IMAGE:-nginx:1.27-alpine}", compose)
        frontend = self.service_block(compose, "frontend")
        self.assertNotIn("    command:", frontend)
        self.assertNotIn("listen 8080;", frontend)
        self.assertIn('      - "80"', frontend)
        self.assertNotIn('      - "8080"', frontend)

    def test_frontend_and_backend_define_container_healthchecks(self):
        compose = (ROOT / "compose.yaml").read_text()

        frontend = self.service_block(compose, "frontend")
        backend = self.service_block(compose, "backend")
        self.assertIn("healthcheck:", frontend)
        self.assertIn("http://127.0.0.1/", frontend)
        self.assertIn("healthcheck:", backend)
        self.assertIn("http://127.0.0.1:8000/health", backend)

    def test_internal_routing_ports_are_explicit_without_public_host_ports(self):
        compose = (ROOT / "compose.yaml").read_text()
        frontend = self.service_block(compose, "frontend")
        backend = self.service_block(compose, "backend")
        postgres = self.service_block(compose, "postgres")

        self.assertIn(
            "traefik.http.services.frontend.loadbalancer.server.port=80", frontend
        )
        self.assertIn(
            "traefik.http.services.backend.loadbalancer.server.port=8000", backend
        )
        self.assertIn('      - "5432"', postgres)
        self.assertNotIn("ports:", frontend + backend + postgres)

    def test_backend_health_and_postgres_connectivity_are_real(self):
        compose = (ROOT / "compose.yaml").read_text()
        backend = self.service_block(compose, "backend")
        postgres = self.service_block(compose, "postgres")

        self.assertNotIn("python -m http.server", backend)
        self.assertIn('if self.path == "/health":', backend)
        self.assertIn('HTTPServer(("0.0.0.0", 8000)', backend)
        self.assertIn("@postgres:5432/", backend)
        self.assertIn("condition: service_healthy", backend)
        self.assertIn("pg_isready", postgres)

    def test_postgres_is_only_on_internal_data_network(self):
        compose = (ROOT / "compose.yaml").read_text()
        postgres = self.service_block(compose, "postgres")

        self.assertIn("image: postgres:16-alpine", postgres)
        self.assertIn("/var/lib/postgresql/data", postgres)
        self.assertIn(
            "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set-in-coolify}", postgres
        )
        self.assertIn("networks:\n      - data", postgres)
        self.assertNotIn("- app", postgres)
        self.assertIn("internal: true", compose)

    def test_frontend_uses_real_nginx_configuration_without_unused_backend_url(self):
        compose = (ROOT / "compose.yaml").read_text()
        nginx = (ROOT / "frontend" / "nginx.conf").read_text()
        frontend = self.service_block(compose, "frontend")

        self.assertIn(
            "./frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro", frontend
        )
        self.assertIn("listen 80;", nginx)
        self.assertIn("location = /health", nginx)
        self.assertNotIn("BACKEND_URL", frontend)

    def test_no_local_compose_override_is_part_of_coolify_pattern(self):
        self.assertFalse((ROOT / "compose.local.yaml").exists())

    def test_factory_ci_validates_only_compose_yaml_healthchecks(self):
        workflow = (ROOT / ".github" / "workflows" / "factory-ci.yml").read_text()

        self.assertIn("Path('compose.yaml').read_text()", workflow)
        self.assertNotIn("Path('compose.local.yaml').read_text()", workflow)
        self.assertIn("http://127.0.0.1/", workflow)
        self.assertIn("http://127.0.0.1:8000/health", workflow)
        self.assertIn("must not publish host ports", workflow)


if __name__ == "__main__":
    unittest.main()
