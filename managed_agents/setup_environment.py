"""
Create the cloud Environment Regina's Managed Agents sessions run in.

Safe to re-run: reuses .environment_id when present.

Usage:
    python managed_agents/setup_environment.py
"""

from _common import ENV_ID, client


def main() -> None:
    if ENV_ID.exists():
        print(f"Environment already exists: {ENV_ID.read_text().strip()}")
        print("(delete .environment_id to provision a new one)")
        return
    environment = client().beta.environments.create(
        name="regina-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    ENV_ID.write_text(environment.id)
    print(f"Environment created: {environment.id}")


if __name__ == "__main__":
    main()
