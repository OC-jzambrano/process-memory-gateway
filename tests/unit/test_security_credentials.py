import pytest
import inspect
from src.integrations.odoo17_xmlrpc import Odoo17XmlRpcExecutor, OdooExecutionError, OdooAccessDeniedError
from src.models.schemas import OdooConnectionConfig

def test_executor_construction_fails_without_credentials():
    """Executor must fail closed if any credential parameter is empty or missing."""
    with pytest.raises(TypeError):
        # Missing required positional arguments
        Odoo17XmlRpcExecutor()

    with pytest.raises(ValueError, match="Odoo URL is required"):
        Odoo17XmlRpcExecutor(url="", db="db", username="user", password="pwd")

    with pytest.raises(ValueError, match="Odoo Database name is required"):
        Odoo17XmlRpcExecutor(url="https://odoo.com", db="", username="user", password="pwd")

    with pytest.raises(ValueError, match="Odoo username/login is required"):
        Odoo17XmlRpcExecutor(url="https://odoo.com", db="db", username="", password="pwd")

    with pytest.raises(ValueError, match="Odoo password or API key is required"):
        Odoo17XmlRpcExecutor(url="https://odoo.com", db="db", username="user", password="")

def test_executor_init_has_no_credential_defaults():
    """Executor constructor signature must not have default values for credentials."""
    sig = inspect.signature(Odoo17XmlRpcExecutor.__init__)
    for param_name in ["url", "db", "username", "password"]:
        param = sig.parameters[param_name]
        assert param.default == inspect.Parameter.empty, f"Parameter '{param_name}' must not have a default value."

def test_odoo_connection_config_has_no_password_defaults():
    """OdooConnectionConfig model must not have default passwords or api keys."""
    sig = inspect.signature(OdooConnectionConfig.__init__)
    assert "password" not in sig.parameters, "OdooConnectionConfig must not store plaintext passwords."

def test_authentication_error_redacts_credentials():
    """Authentication failure messages must not leak password or sensitive connection fragments."""
    executor = Odoo17XmlRpcExecutor(
        url="http://localhost:9999",
        db="test_db",
        username="test_user",
        password="super_secret_password_123"
    )
    with pytest.raises(OdooExecutionError) as exc_info:
        executor._authenticate()
    
    err_msg = str(exc_info.value)
    assert "super_secret_password_123" not in err_msg
    assert "password" not in err_msg.lower() or "credentials" in err_msg.lower()
