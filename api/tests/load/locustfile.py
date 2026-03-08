from locust import HttpUser, between, task  # pyright: ignore[reportUnknownVariableType]


class SurfUser(HttpUser):
    """Load test user simulating interactions with the Surf API."""

    wait_time = between(1, 5)  # pyright: ignore[reportUnknownVariableType]

    @task(3)
    def send_hr_query(self):
        self.client.post("/api/v1/chat", json={"message": "What is my annual leave entitlement?"})

    @task(2)
    def send_it_query(self):
        self.client.post("/api/v1/chat", json={"message": "How do I connect to the VPN?"})

    @task(1)
    def send_general_query(self):
        self.client.post("/api/v1/chat", json={"message": "What are the office hours?"})

    @task(1)
    def multi_turn_conversation(self):
        resp = self.client.post("/api/v1/chat", json={"message": "What is annual leave?"})
        if resp.status_code == 200:
            conv_id = resp.json()["conversation_id"]
            self.client.post(
                "/api/v1/chat",
                json={"conversation_id": conv_id, "message": "How do I apply for it?"},
            )

    @task(1)
    def health_check(self):
        self.client.get("/api/v1/health")
