from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_playbook_126a9048";
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_timed_2b26d96a";
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_events_69eabc33";
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_playbook_27d2dbff";
        ALTER TABLE "approvement" RENAME COLUMN "next_node_id" TO "next_node";
        ALTER TABLE "approvement" RENAME COLUMN "playbook_version_id" TO "flow_id";
        ALTER TABLE "approvement" RENAME COLUMN "audit_node_id" TO "audit_node";
        ALTER TABLE "runlog" RENAME COLUMN "event_id_id" TO "event_id";
        ALTER TABLE "runlog" ADD "flow_id" VARCHAR(36);
        ALTER TABLE "runlog" RENAME COLUMN "timer_id_id" TO "timer_id";
        ALTER TABLE "runlog" DROP COLUMN "playbook_version_id";
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_playbook_9c671a97" FOREIGN KEY ("flow_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_events_c50db212" FOREIGN KEY ("event_id") REFERENCES "events" ("id") ON DELETE CASCADE;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_playbook_31ff23a5" FOREIGN KEY ("flow_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_timed_88f213bb" FOREIGN KEY ("timer_id") REFERENCES "timed" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_playbook_9c671a97";
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_timed_88f213bb";
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_playbook_31ff23a5";
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_events_c50db212";
        ALTER TABLE "runlog" ADD "playbook_version_id" VARCHAR(36);
        ALTER TABLE "runlog" RENAME COLUMN "event_id" TO "event_id_id";
        ALTER TABLE "runlog" RENAME COLUMN "timer_id" TO "timer_id_id";
        ALTER TABLE "runlog" DROP COLUMN "flow_id";
        ALTER TABLE "approvement" RENAME COLUMN "audit_node" TO "audit_node_id";
        ALTER TABLE "approvement" RENAME COLUMN "next_node" TO "next_node_id";
        ALTER TABLE "approvement" RENAME COLUMN "flow_id" TO "playbook_version_id";
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_events_69eabc33" FOREIGN KEY ("event_id_id") REFERENCES "events" ("id") ON DELETE CASCADE;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_timed_2b26d96a" FOREIGN KEY ("timer_id_id") REFERENCES "timed" ("id") ON DELETE CASCADE;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_playbook_126a9048" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_playbook_27d2dbff" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""
