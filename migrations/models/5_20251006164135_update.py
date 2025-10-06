from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE INDEX IF NOT EXISTS "idx_account_ctime_14704e" ON "account" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_account_utime_f6462a" ON "account" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_action_perm_ctime_b175f5" ON "action_permissions" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_action_perm_utime_850ea0" ON "action_permissions" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_approvement_ctime_cba21e" ON "approvement" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_approvement_utime_f51a54" ON "approvement" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_audit_log_ctime_fba851" ON "audit_log" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_audit_log_utime_204f58" ON "audit_log" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_config_ctime_73e03e" ON "config" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_config_utime_34b4ca" ON "config" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_event_relat_ctime_187d61" ON "event_related_script" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_event_relat_utime_8da293" ON "event_related_script" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_events_ctime_26c029" ON "events" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_events_utime_7eff22" ON "events" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_events_inge_utime_1ac87b" ON "events_ingestion" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_events_inge_ctime_01d0ab" ON "events_ingestion" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_room_msg_ctime_7b2b20" ON "room_msg" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_room_msg_utime_71a677" ON "room_msg" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_room_msg_ac_ctime_06a754" ON "room_msg_account" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_room_msg_ac_utime_0bb763" ON "room_msg_account" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_permissions_ctime_5e524f" ON "permissions_menu" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_permissions_utime_35bc56" ON "permissions_menu" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_playbook_ctime_e8fdb9" ON "playbook" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_playbook_utime_fd4ee7" ON "playbook" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_playbook_pe_ctime_17e36b" ON "playbook_permissions" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_playbook_pe_utime_21bd8b" ON "playbook_permissions" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_playbook_ve_ctime_4c93f1" ON "playbook_version" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_playbook_ve_utime_539931" ON "playbook_version" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_role_ctime_313ff5" ON "role" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_role_utime_e9d879" ON "role" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_room_ctime_ab8bd8" ON "room" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_room_utime_175a0b" ON "room" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_runlog_utime_8e8b48" ON "runlog" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_runlog_ctime_7c5f78" ON "runlog" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_scene_ctime_4ccfdc" ON "scene" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_scene_utime_3fb86d" ON "scene" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_tag_ctime_263ed5" ON "tag" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_tag_utime_ee2f5f" ON "tag" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_timed_utime_91bc4f" ON "timed" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_timed_ctime_ee353c" ON "timed" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_tool_ctime_40e216" ON "tool" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_tool_utime_222712" ON "tool" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_tool_action_ctime_cc0593" ON "tool_action" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_tool_action_utime_1c3794" ON "tool_action" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_tool_resour_ctime_95d686" ON "tool_resources" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_tool_resour_utime_706e69" ON "tool_resources" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_tool_resour_ctime_5e2f7c" ON "tool_resources_model" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_tool_resour_utime_a35d7e" ON "tool_resources_model" ("utime");
        CREATE INDEX IF NOT EXISTS "idx_variables_ctime_c05d5a" ON "variables" ("ctime");
        CREATE INDEX IF NOT EXISTS "idx_variables_utime_ef4175" ON "variables" ("utime");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_playbook_pe_utime_21bd8b";
        DROP INDEX IF EXISTS "idx_playbook_pe_ctime_17e36b";
        DROP INDEX IF EXISTS "idx_tool_resour_utime_a35d7e";
        DROP INDEX IF EXISTS "idx_tool_resour_ctime_5e2f7c";
        DROP INDEX IF EXISTS "idx_event_relat_utime_8da293";
        DROP INDEX IF EXISTS "idx_event_relat_ctime_187d61";
        DROP INDEX IF EXISTS "idx_action_perm_utime_850ea0";
        DROP INDEX IF EXISTS "idx_action_perm_ctime_b175f5";
        DROP INDEX IF EXISTS "idx_playbook_ve_utime_539931";
        DROP INDEX IF EXISTS "idx_playbook_ve_ctime_4c93f1";
        DROP INDEX IF EXISTS "idx_permissions_utime_35bc56";
        DROP INDEX IF EXISTS "idx_permissions_ctime_5e524f";
        DROP INDEX IF EXISTS "idx_events_inge_ctime_01d0ab";
        DROP INDEX IF EXISTS "idx_events_inge_utime_1ac87b";
        DROP INDEX IF EXISTS "idx_room_msg_ac_utime_0bb763";
        DROP INDEX IF EXISTS "idx_room_msg_ac_ctime_06a754";
        DROP INDEX IF EXISTS "idx_tool_resour_utime_706e69";
        DROP INDEX IF EXISTS "idx_tool_resour_ctime_95d686";
        DROP INDEX IF EXISTS "idx_approvement_utime_f51a54";
        DROP INDEX IF EXISTS "idx_approvement_ctime_cba21e";
        DROP INDEX IF EXISTS "idx_tool_action_utime_1c3794";
        DROP INDEX IF EXISTS "idx_tool_action_ctime_cc0593";
        DROP INDEX IF EXISTS "idx_variables_utime_ef4175";
        DROP INDEX IF EXISTS "idx_variables_ctime_c05d5a";
        DROP INDEX IF EXISTS "idx_playbook_utime_fd4ee7";
        DROP INDEX IF EXISTS "idx_playbook_ctime_e8fdb9";
        DROP INDEX IF EXISTS "idx_audit_log_utime_204f58";
        DROP INDEX IF EXISTS "idx_audit_log_ctime_fba851";
        DROP INDEX IF EXISTS "idx_room_msg_utime_71a677";
        DROP INDEX IF EXISTS "idx_room_msg_ctime_7b2b20";
        DROP INDEX IF EXISTS "idx_account_utime_f6462a";
        DROP INDEX IF EXISTS "idx_account_ctime_14704e";
        DROP INDEX IF EXISTS "idx_runlog_ctime_7c5f78";
        DROP INDEX IF EXISTS "idx_runlog_utime_8e8b48";
        DROP INDEX IF EXISTS "idx_events_utime_7eff22";
        DROP INDEX IF EXISTS "idx_events_ctime_26c029";
        DROP INDEX IF EXISTS "idx_config_utime_34b4ca";
        DROP INDEX IF EXISTS "idx_config_ctime_73e03e";
        DROP INDEX IF EXISTS "idx_timed_ctime_ee353c";
        DROP INDEX IF EXISTS "idx_timed_utime_91bc4f";
        DROP INDEX IF EXISTS "idx_scene_utime_3fb86d";
        DROP INDEX IF EXISTS "idx_scene_ctime_4ccfdc";
        DROP INDEX IF EXISTS "idx_tool_utime_222712";
        DROP INDEX IF EXISTS "idx_tool_ctime_40e216";
        DROP INDEX IF EXISTS "idx_room_utime_175a0b";
        DROP INDEX IF EXISTS "idx_room_ctime_ab8bd8";
        DROP INDEX IF EXISTS "idx_role_utime_e9d879";
        DROP INDEX IF EXISTS "idx_role_ctime_313ff5";
        DROP INDEX IF EXISTS "idx_tag_utime_ee2f5f";
        DROP INDEX IF EXISTS "idx_tag_ctime_263ed5";"""
