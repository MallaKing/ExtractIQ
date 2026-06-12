"""
Database migration patch — run once after pulling multi-tenant changes.

Adds:
  - users table (new)
  - extraction_jobs.user_id FK column (new)
  - extraction_jobs.file_id column (if not already present)
  - extraction_jobs.source column (if not already present)

Safe to run multiple times — all ops use IF NOT EXISTS / IF NOT EXIST guards.
"""
from sqlalchemy import text
from app.database.connection import engine


def run():
    with engine.connect() as conn:
        print("Running database patch...")

        # ── 1. Create users table ───────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id          SERIAL PRIMARY KEY,
                email       VARCHAR(255) UNIQUE NOT NULL,
                google_tokens JSON NOT NULL,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """))
        print("  ✓ users table")

        # ── 2. Add user_id FK to extraction_jobs ────────────────────────────
        conn.execute(text("""
            ALTER TABLE extraction_jobs
                ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
        """))
        print("  ✓ extraction_jobs.user_id")

        # ── 3. Add Drive tracking columns (idempotent) ───────────────────────
        conn.execute(text("""
            ALTER TABLE extraction_jobs
                ADD COLUMN IF NOT EXISTS file_id VARCHAR(255);
        """))
        conn.execute(text("""
            ALTER TABLE extraction_jobs
                ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'api';
        """))
        print("  ✓ extraction_jobs.file_id, source")

        conn.execute(text("""
            ALTER TABLE schemas
                ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
        """))
        conn.execute(text("""
            ALTER TABLE schemas
                DROP CONSTRAINT IF EXISTS schemas_schema_hash_key;
        """))
        print("  ✓ schemas.user_id")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS watch_channels (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id),
                folder_id    VARCHAR(255) NOT NULL,
                folder_name  VARCHAR(255),
                channel_id   VARCHAR(36) UNIQUE NOT NULL,
                resource_id  VARCHAR(255),
                expiration_ms VARCHAR(20),
                created_at   TIMESTAMP DEFAULT NOW()
            );
        """))
        print("  ✓ watch_channels table")

        conn.commit()

    print("\n✅ Patch complete.")


if __name__ == "__main__":
    run()
