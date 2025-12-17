# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Playbook(BaseModel):
    """
    剧本模型
    """

    name = fields.CharField(max_length=255, unique=True, description="剧本名称")
    remark = fields.TextField(description="描述")
    tags = fields.ManyToManyField("models.Tag", through="playbook_tag")
    scenes = fields.ManyToManyField("models.Scene", through="playbook_scene")
    internal = fields.BooleanField(default=False, description="是否内置剧本")
    creator = fields.ForeignKeyField("models.Account", related_name="created_playbook", description="创建人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "remark": self.remark,
            "tags": [tag.name for tag in self.tags] if self.tags else [],
            "scenes": [scene.name for scene in self.scenes] if self.scenes else [],
            "internal": self.internal,
        }

    class Meta:
        table = "playbook"
        ordering = ["-utime"]

        unique_together = (("name", "deleted"),)


class PlaybookVersion(BaseModel):
    """
    剧本版本模型
    """

    playbook = fields.ForeignKeyField("models.Playbook", related_name="playbook_versions")
    change_desc = fields.TextField(null=True, description="变更说明")
    version = fields.CharField(max_length=30, description="版本号，每次publish都会生成新纪录，用于版本回溯")
    status = fields.IntField(max_length=1, default=0, description="剧本状态,0草稿,1已发布")
    flow_timeout = fields.IntField(max_length=6, default=300, description="剧本运行超时时间，单位秒")
    flow_json = fields.JSONField(default={}, description="流程图flow数据，包含edges和nodes")
    creator = fields.ForeignKeyField("models.Account", related_name="created_playbooks", description="创建人")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_playbooks", description="更新人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "playbook_id": self.playbook.id,
            "flow_id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.playbook.name,
            "remark": self.playbook.remark,
            "change_desc": self.change_desc,
            "status": self.status,
            "flow_json": self.flow_json,
            "version": self.version,
            "creator": {"username": self.creator.username, "display_name": self.creator.display_name},
            "updator": {"username": self.updator.username, "display_name": self.updator.display_name},
            "internal": self.playbook.internal,
        }

    class Meta:
        table = "playbook_version"
        ordering = ["-utime"]

        unique_together = (("playbook", "version"),)


class PlaybookPermissions(BaseModel):
    """
    剧本权限表
    """

    pb = fields.ForeignKeyField("models.Playbook", unique=True, related_name="playbook_permissions", description="剧本")
    users = fields.ForeignKeyField("models.Account", null=True, related_name="playbook_permissions_user", description="用户")
    roles = fields.ForeignKeyField("models.Role", null=True, related_name="playbook_permissions_role", description="角色")
    permissions = fields.JSONField(null=True)

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "users": {"id": self.users_id, "username": self.users.username, "display_name": self.users.display_name} if self.users else None,
            "roles": {"id": self.roles_id, "name": self.roles.name} if self.roles else None,
            "permissions": self.permissions,
        }

    class Meta:
        table = "playbook_permissions"
        ordering = ["-utime"]
