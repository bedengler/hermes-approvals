"""Copy this file to a Hermes profile's scripts/ directory for cron cleanup."""
from hermes_approval.maintenance import main

if __name__ == "__main__":
    raise SystemExit(main())
