# Optional automation metadata during original-run recovery

The live ownership guard raised AttributeError on JSON null metadata from an unrelated running legacy automation, before the positively unsubmitted governed original could reclaim its lease. SQLAlchemy permits Optional[dict], represented as either SQL NULL or JSON null. Normalize JSON null to an empty object. Other non-object shapes fail closed with a useful error; Task-bound and uncertain original ownership checks remain unchanged.

Ten focused SQLite ownership tests pass, including null/SQL NULL, other Task, exact Task veto, invalid shapes, frozen publication evidence and unresolved transport veto. The correction is SOURCE ONLY until the next controlled Main restart. Retry20 progressed after the unrelated legacy automation ended, not because this source was already active.
