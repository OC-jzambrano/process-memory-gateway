from src.models.schemas import Client, BusinessProcess

def test_upsert_client_creates(repo):
    fetched = repo.get_client("test_client")
    assert fetched is not None
    assert fetched.client_name == "Test Corp"

def test_upsert_client_updates(repo):
    repo.upsert_client(Client(client_id="test_client", client_name="Updated Corp", industry="Tech"))
    fetched = repo.get_client("test_client")
    assert fetched.client_name == "Updated Corp"
    assert fetched.industry == "Tech"

def test_get_nonexistent_client_returns_none(repo):
    assert repo.get_client("nonexistent_id") is None

def test_add_and_get_business_process(repo):
    proc = BusinessProcess(
        process_id="proc_inv_01",
        client_id="test_client",
        process_name="inventory_control",
        description="Inventory cycle counts and valuation"
    )
    repo.add_process(proc)

    # Upsert with new description
    proc_updated = BusinessProcess(
        process_id="proc_inv_01",
        client_id="test_client",
        process_name="inventory_control",
        description="Updated inventory valuation policy"
    )
    repo.add_process(proc_updated)
