import pytest
from src.integrations.mock_executor import MockTaskExecutor
from src.integrations.odoo17_xmlrpc import Odoo17XmlRpcExecutor, OdooAccessDeniedError, OdooExecutionError
from src.models.schemas import TaskRecord

def test_safe_html_escapes_scripts_and_tags():
    """Executor must strictly escape user-provided script tags and HTML injection."""
    executor = Odoo17XmlRpcExecutor(url="http://mock", db="mock", username="mock", password="mock")
    unsafe_desc = "Task with <script>alert('pwned')</script> and <b>bold</b> text."
    unsafe_dod = ["DoD 1 with <img src=x onerror=alert(1)>", "Normal DoD item"]

    rendered_html = executor._render_safe_html(unsafe_desc, unsafe_dod)
    
    assert "<script>" not in rendered_html
    assert "&lt;script&gt;alert(&#x27;pwned&#x27;)&lt;/script&gt;" in rendered_html
    assert "<img" not in rendered_html
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered_html
    assert "Definition of Done" in rendered_html

def test_mock_executor_read_back_verification():
    """Mock executor creates and reads back task successfully."""
    executor = MockTaskExecutor(default_project_id=142)
    task = executor.create_project_task(
        title="[PM-PILOT] Readback test",
        description="Testing readback verification",
        definition_of_done=["Item 1", "Item 2"]
    )
    assert task.id >= 1001
    assert task.name == "[PM-PILOT] Readback test"
    assert task.project_id == 142

    read_back = executor.get_project_task(task.id)
    assert read_back is not None
    assert read_back.id == task.id
    assert read_back.name == task.name

def test_mock_executor_simulates_access_error():
    """When Odoo denies access, OdooAccessDeniedError is raised."""
    executor = MockTaskExecutor()
    executor.simulate_access_error = True

    with pytest.raises(OdooAccessDeniedError):
        executor.create_project_task(
            title="Unauthorized task",
            description="Should fail"
        )
