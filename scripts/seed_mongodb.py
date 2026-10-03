#!/usr/bin/env python3
"""
InspectDB Document Store Seeder Script
======================================
Seeds the configured document store (local MongoDB, or Amazon DocumentDB when
STORAGE_MODE=documentdb) with realistic, variable-schema inspection reports
demonstrating document orientation, nested objects, arrays, nested arrays
($elemMatch targets), and polymorphic attributes.

Every API query is scoped to the signed-in user, so pass --user-email to give
a registered account its own copy of the sample reports.

Usage:
    python scripts/seed_mongodb.py                          # unowned reference copies
    python scripts/seed_mongodb.py --user-email you@example.com
"""

import argparse
import asyncio
import copy
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.db.document_store import get_document_store_config, redact_uri
from app.repositories.mongodb_repository import MongoDBInspectionRepository
from tests.fixtures.sample_reports import SAMPLE_REPORTS


def find_user_id(email: str):
    from app.db.session import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        return user.id if user else None
    finally:
        db.close()


def seed_reference_copies(repo: MongoDBInspectionRepository) -> None:
    """Upserts the sample reports by their fixed IDs, without an owner."""
    collection = repo._get_collection()
    inserted_count = 0
    updated_count = 0
    for report in SAMPLE_REPORTS:
        result = collection.update_one({"id": report["id"]}, {"$set": dict(report)}, upsert=True)
        if result.upserted_id is not None:
            inserted_count += 1
        else:
            updated_count += 1
    print(f"✓ Seeded reference copies: {inserted_count} inserted, {updated_count} updated.")


def seed_for_user(repo: MongoDBInspectionRepository, user_id: str, force: bool) -> None:
    """Creates owned copies of the sample reports with freshly allocated IDs."""
    collection = repo._get_collection()
    existing = collection.count_documents({"user_id": user_id, "is_sample": True})
    if existing and not force:
        print(f"• User already has {existing} sample report(s); skipping. Use --force to add another set.")
        return

    for report in SAMPLE_REPORTS:
        doc = copy.deepcopy(report)
        for key in ("id", "user_id", "created_at", "updated_at"):
            doc.pop(key, None)
        created = asyncio.run(repo.create(doc, user_id=user_id))
        collection.update_one({"id": created["id"]}, {"$set": {"is_sample": True}})
        print(f"  + {created['id']}  {created['title']}")
    print(f"✓ Created {len(SAMPLE_REPORTS)} sample reports for user {user_id}.")


def main() -> bool:
    parser = argparse.ArgumentParser(description="Seed InspectDB sample inspection reports.")
    parser.add_argument("--user-email", help="Registered account that should own the sample reports.")
    parser.add_argument("--force", action="store_true", help="Add samples even if the user already has some.")
    args = parser.parse_args()

    cfg = get_document_store_config()
    print("=" * 60)
    print("InspectDB Document Store Seeder")
    print("=" * 60)
    print(f"Engine:            {cfg.engine_label}")
    print(f"Target URI:        {redact_uri(cfg.uri)}")
    print(f"Target Database:   {cfg.database}")
    print(f"Target Collection: {cfg.collection}")
    print("-" * 60)

    repo = MongoDBInspectionRepository(config=cfg)
    if not repo.is_available():
        print(f"✕ Could not connect to {cfg.engine_label}. Check the connection settings in .env.")
        return False
    print(f"✓ Connected to {cfg.engine_label}.")

    try:
        repo.ensure_indexes()
        if args.user_email:
            user_id = find_user_id(args.user_email)
            if not user_id:
                print(f"✕ No registered user with email '{args.user_email}'. Register in the app first.")
                return False
            seed_for_user(repo, user_id, args.force)
        else:
            seed_reference_copies(repo)
        print(f"  Total collection count: {repo._get_collection().count_documents({})}")
        print("=" * 60)
        return True
    except Exception as e:
        print(f"\n✕ Failed to seed collection: {e}")
        return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
