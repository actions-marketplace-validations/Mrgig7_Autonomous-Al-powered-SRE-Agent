from app.main import app
from fastapi.testclient import TestClient


def test_create_and_list_tasks() -> None:
    with TestClient(app) as client:
        create = client.post(
            "/api/v1/tasks",
            json={"title": "Write tests", "description": "CI should pass", "completed": False},
        )
        assert create.status_code == 201
        created_task = create.json()
        assert created_task["title"] == "Write tests"

        listing = client.get("/api/v1/tasks")
        assert listing.status_code == 200
        body = listing.json()
        assert isinstance(body, list)
        assert any(task["title"] == "Write tests" for task in body)
