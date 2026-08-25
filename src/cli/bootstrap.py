import argparse
import sys
import uuid
from src.storage.repository import MemoryRepository
from src.models.schemas import Company, User, Membership, OdooConnectionConfig
from src.models.enums import RoleType, CompanyStatus, MembershipStatus

def bootstrap_company(
    company_slug: str,
    company_name: str,
    owner_user_id: str,
    owner_email: str,
    odoo_url: str,
    odoo_db: str,
    default_project_id: int = 142
) -> None:
    repo = MemoryRepository()

    print(f"[*] Bootstrapping tenant: {company_slug} ('{company_name}')...")

    # 1. Upsert Company
    company = repo.upsert_company(
        Company(
            company_id=company_slug,
            company_slug=company_slug,
            name=company_name,
            status=CompanyStatus.ACTIVE
        )
    )
    print(f"  [+] Company created: {company.company_id} ({company.name})")

    # 2. Upsert Owner User
    user = repo.upsert_user(
        User(
            user_id=owner_user_id,
            email=owner_email,
            name=owner_user_id,
            status="active"
        )
    )
    print(f"  [+] Owner User created: {user.user_id} ({user.email})")

    # 3. Upsert Membership
    membership = repo.upsert_membership(
        Membership(
            membership_id=f"mem_{company_slug}_{owner_user_id}",
            company_id=company.company_id,
            user_id=user.user_id,
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE
        )
    )
    print(f"  [+] Owner Membership established: {membership.membership_id} (Role: {membership.role.value})")

    # 4. Upsert Odoo Connection
    odoo_conn = repo.upsert_odoo_connection(
        OdooConnectionConfig(
            connection_id=f"conn_{company_slug}",
            company_id=company.company_id,
            odoo_url=odoo_url,
            odoo_db=odoo_db,
            default_project_id=default_project_id
        )
    )
    print(f"  [+] Odoo Connection registered: {odoo_conn.odoo_url} (DB: {odoo_conn.odoo_db}, Default Project: {odoo_conn.default_project_id})")

    print("\n[SUCCESS] Company bootstrap complete. Ready for remote MCP connections at:")
    print(f"  --> https://mcp.example.com/companies/{company_slug}/mcp\n")

def main():
    parser = argparse.ArgumentParser(description="Process Memory Operator Bootstrap CLI")
    parser.add_argument("--company", required=True, help="Company Slug / Identifier")
    parser.add_argument("--name", required=True, help="Display Name")
    parser.add_argument("--owner", required=True, help="Owner User ID")
    parser.add_argument("--email", required=True, help="Owner Email")
    parser.add_argument("--odoo-url", default="https://community.odooconcept.com", help="Odoo Server URL")
    parser.add_argument("--odoo-db", default="community", help="Odoo Database Name")
    parser.add_argument("--project", type=int, default=142, help="Default Odoo Project ID")

    args = parser.parse_args()
    bootstrap_company(
        company_slug=args.company,
        company_name=args.name,
        owner_user_id=args.owner,
        owner_email=args.email,
        odoo_url=args.odoo_url,
        odoo_db=args.odoo_db,
        default_project_id=args.project
    )

if __name__ == "__main__":
    main()
