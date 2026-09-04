import sys
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState
from advisor.orchestrator.runner import orchestrate

logger = StructuredLogger("advisor.cli")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m advisor.cli ask \"<query>\"")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "ask":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Who is on reserve at BLR tomorrow?"
        state = OpsState(db_path=DEFAULT_DB_PATH)
        repo = OpsRepository(DEFAULT_DB_PATH)
        logger.info("Executing CLI query", query=query)

        print(f"\n✈️ Crew Ops Advisor CLI: '{query}'\n" + "=" * 60)

        for stage, payload in orchestrate(query, state, repo):
            if stage == "status":
                print(f"[*] {payload}")
            elif stage == "abstain":
                print(f"\n[!] ABSTENTION ({payload['reason']}): {payload['message']}\n")
                break
            elif stage == "prose":
                print("\n" + "=" * 60)
                print(payload)
                print("=" * 60 + "\n")
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
