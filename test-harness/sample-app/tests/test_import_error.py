from app.main import app


def test_app_import_is_available() -> None:
    assert app.title == "Task Manager Platform"
