from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


# 知识空间作为「专家团队」下的二级菜单注册。
#
# 菜单树由 services/license/views.py 的 get_license() 组装：多条 permissions_menu
# 记录只要 url[language]["path"] 相同，children 会被自动合并去重。因此这里的顶层
# 结构与 command-center-permissions 保持一致（path=/command-center），children 只
# 放知识空间一项——这样许可与角色权限都能独立控制它的显隐，而不影响专家团队本身。
#
# bp=knowledge_bp 是许可门禁的匹配键，必须与 middleware/aioe_license 中签发的
# module 字段一致，否则 _menu_is_licensed() 永远匹配不到、菜单对所有租户隐藏。


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        INSERT INTO "public"."permissions_menu"
            ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            'knowledge-permissions',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            'f',
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'knowledge',
            '知识空间',
            '[{"label": "新增知识空间权限", "value": "c"}, {"label": "删除知识空间权限", "value": "d"}, {"label": "修改知识空间权限", "value": "u"}, {"label": "查看知识空间权限", "value": "r"}]',
            '{"CN": {"meta": {"icon": "ri:command-line", "rank": 15, "title": "专家团队"}, "path": "/command-center", "redirect": "/command-center/board", "component": "layout/index.vue", "children": [{"meta": {"icon": "ri:book-2-line", "title": "知识空间"}, "name": "KnowledgeSpaces", "path": "/command-center/knowledge", "component": "views/knowledge/index.vue"}]}, "HK": {"meta": {"icon": "ri:command-line", "rank": 15, "title": "專家團隊"}, "path": "/command-center", "redirect": "/command-center/board", "component": "layout/index.vue", "children": [{"meta": {"icon": "ri:book-2-line", "title": "知識空間"}, "name": "KnowledgeSpaces", "path": "/command-center/knowledge", "component": "views/knowledge/index.vue"}]}, "EN": {"meta": {"icon": "ri:command-line", "rank": 15, "title": "Expert Team"}, "path": "/command-center", "redirect": "/command-center/board", "component": "layout/index.vue", "children": [{"meta": {"icon": "ri:book-2-line", "title": "Knowledge Space"}, "name": "KnowledgeSpaces", "path": "/command-center/knowledge", "component": "views/knowledge/index.vue"}]}}',
            'knowledge_bp'
        )
        ON CONFLICT ("model") DO UPDATE SET
            "description" = EXCLUDED."description",
            "Permissions" = EXCLUDED."Permissions",
            "url" = EXCLUDED."url",
            "bp" = EXCLUDED."bp",
            "deleted" = EXCLUDED."deleted",
            "utime" = CURRENT_TIMESTAMP;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "public"."permissions_menu" WHERE "model" = 'knowledge';
    """
