#!/usr/bin/env bash
# 工单系统在线端到端验收脚本。

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

API_BASE="${API_BASE:-http://127.0.0.1:8000}"
AUTH_TOKEN="${AUTH_TOKEN:-}"
APPROVER_ID="${APPROVER_ID:-}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-5}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-30}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
TYPE_CODE="${TYPE_CODE:-E2E$(date +%m%d%H%M%S)$$}"

RESPONSE=""
HTTP_STATUS=""
TICKET_TYPE_ID=""
FLOW_ID=""
FLOW_VERSION_ID=""
TICKET_ID=""
TICKET_NO=""

usage() {
    cat <<'EOF'
用法:
  AUTH_TOKEN=<token> APPROVER_ID=<account-id> scripts/e2e_acceptance_test.sh

环境变量:
  API_BASE          API 地址，默认 http://127.0.0.1:8000
  AUTH_TOKEN        必填，需具备 ticket:*、ticket_flow:* 和 ticket:all 权限
  APPROVER_ID       必填，应为 AUTH_TOKEN 对应账号 ID，用于审批节点
  CONNECT_TIMEOUT   curl 连接超时秒数，默认 5
  REQUEST_TIMEOUT   单次请求超时秒数，默认 30
  RUN_ID            可选，本次验收数据标识
  TYPE_CODE         可选，工单类型编码，默认自动生成

说明:
  脚本会创建可识别的验收类型、流程和工单。系统没有工单删除 API，
  因此不会自动删除数据；验收后请按 RUN_ID 在隔离环境中人工清理。
EOF
}

log() {
    printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
    printf '\n验收失败: %s\n' "$*" >&2
    if [[ -n "$RESPONSE" ]]; then
        printf 'HTTP %s 响应: %s\n' "$HTTP_STATUS" "$RESPONSE" >&2
    fi
    printf '已创建数据: type=%s flow=%s version=%s ticket=%s\n' \
        "${TICKET_TYPE_ID:-未创建}" "${FLOW_ID:-未创建}" \
        "${FLOW_VERSION_ID:-未创建}" "${TICKET_ID:-未创建}" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "缺少依赖命令: $1"
}

api_request() {
    local method="$1"
    local path="$2"
    local body="${3:-}"
    local output_file
    output_file="$(mktemp)"

    local -a curl_args=(
        --silent
        --show-error
        --connect-timeout "$CONNECT_TIMEOUT"
        --max-time "$REQUEST_TIMEOUT"
        --request "$method"
        --header "Authorization: Bearer $AUTH_TOKEN"
        --header "Accept: application/json"
        --output "$output_file"
        --write-out '%{http_code}'
    )
    if [[ -n "$body" ]]; then
        curl_args+=(--header "Content-Type: application/json" --data "$body")
    fi

    if ! HTTP_STATUS="$(curl "${curl_args[@]}" "${API_BASE%/}$path")"; then
        RESPONSE="$(<"$output_file")"
        rm -f "$output_file"
        fail "请求失败: $method $path"
    fi
    RESPONSE="$(<"$output_file")"
    rm -f "$output_file"

    [[ "$HTTP_STATUS" =~ ^2[0-9][0-9]$ ]] || fail "HTTP 状态异常: $method $path"
    jq -e . >/dev/null 2>&1 <<<"$RESPONSE" || fail "响应不是合法 JSON: $method $path"
    jq -e '(.code == 0) or (.code == "0")' >/dev/null <<<"$RESPONSE" \
        || fail "业务响应失败: $method $path"
}

response_value() {
    local expression="$1"
    jq -er "$expression | select(. != null and . != \"\")" <<<"$RESPONSE" \
        || fail "响应缺少字段: $expression"
}

assert_ticket_in_list() {
    local view="$1"
    api_request GET "/ticket/list?view=$view&page=1&size=100"
    jq -e --arg id "$TICKET_ID" '.data.data | any((.id | tostring) == $id)' \
        >/dev/null <<<"$RESPONSE" || fail "$view 未返回本次工单"
    log "$view 列表校验通过，共 $(jq -r '.data.total' <<<"$RESPONSE") 条"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
[[ $# -eq 0 ]] || {
    usage >&2
    exit 2
}

require_command curl
require_command jq
[[ -n "$AUTH_TOKEN" ]] || fail "请设置 AUTH_TOKEN"
[[ -n "$APPROVER_ID" ]] || fail "请设置 APPROVER_ID"

log "工单系统端到端验收开始"
log "API: $API_BASE"
log "RUN_ID: $RUN_ID"

log "1/11 检查 License、认证和基础读权限"
api_request GET "/ticket/type/list"

log "2/11 创建工单类型"
TYPE_PAYLOAD="$(jq -nc \
    --arg name "E2E 验收工单 $RUN_ID" \
    --arg code "$TYPE_CODE" \
    --arg description "Task 23 自动化验收数据，RUN_ID=$RUN_ID" \
    '{name:$name,code:$code,description:$description,sla_config:{"1":{respond:900,resolve:7200},warn_before:300}}')"
api_request POST "/ticket/type/save" "$TYPE_PAYLOAD"
TICKET_TYPE_ID="$(response_value '.data.id | tostring')"
log "工单类型创建成功: $TICKET_TYPE_ID"

log "3/11 创建流程草稿"
FLOW_PAYLOAD="$(jq -nc \
    --arg name "E2E 验收流程 $RUN_ID" \
    --arg type_id "$TICKET_TYPE_ID" \
    --arg approver_id "$APPROVER_ID" \
    --arg remark "Task 23 自动化验收流程，RUN_ID=$RUN_ID" \
    '{
        name:$name,
        remark:$remark,
        ticket_type_id:$type_id,
        flow_json:{
            nodes:[
                {id:"start",type:"start",name:"开始"},
                {id:"approve-1",type:"approve",name:"验收审批",approve_type:1,approvers:[$approver_id]},
                {id:"end-approved",type:"end",name:"完成",terminal_status:1}
            ],
            edges:[
                {source:"start",target:"approve-1"},
                {source:"approve-1",target:"end-approved",on:"approve"}
            ]
        },
        form_schema:{fields:[{key:"acceptance_note",type:"text",label:"验收说明",required:true}]}
    }')"
api_request POST "/ticket/flow/save" "$FLOW_PAYLOAD"
FLOW_ID="$(response_value '.data.id | tostring')"
FLOW_VERSION_ID="$(response_value '.data.version.id | tostring')"
log "流程草稿创建成功: $FLOW_ID ($FLOW_VERSION_ID)"

log "4/11 发布流程并触发结构/引用完整性校验"
PUBLISH_PAYLOAD="$(jq -nc --arg flow_id "$FLOW_ID" \
    --arg desc "Task 23 E2E publish $RUN_ID" '{flow_id:$flow_id,change_desc:$desc}')"
api_request POST "/ticket/flow/publish" "$PUBLISH_PAYLOAD"
jq -e '.data.status == 1' >/dev/null <<<"$RESPONSE" || fail "流程发布后状态不是 1"

log "5/11 将已发布流程设为类型默认流程"
TYPE_UPDATE_PAYLOAD="$(jq -nc \
    --arg id "$TICKET_TYPE_ID" \
    --arg name "E2E 验收工单 $RUN_ID" \
    --arg code "$TYPE_CODE" \
    --arg flow_id "$FLOW_ID" \
    --arg description "Task 23 自动化验收数据，RUN_ID=$RUN_ID" \
    '{id:$id,name:$name,code:$code,description:$description,default_flow_id:$flow_id,sla_config:{"1":{respond:900,resolve:7200},warn_before:300}}')"
api_request POST "/ticket/type/save" "$TYPE_UPDATE_PAYLOAD"
jq -e --arg id "$FLOW_ID" '(.data.default_flow_id | tostring) == $id' \
    >/dev/null <<<"$RESPONSE" || fail "默认流程回写失败"

log "6/11 创建工单并校验 SLA"
TICKET_PAYLOAD="$(jq -nc \
    --arg type_id "$TICKET_TYPE_ID" \
    --arg title "E2E 验收工单 $RUN_ID" \
    --arg note "Task 23 online acceptance $RUN_ID" \
    '{ticket_type_id:$type_id,title:$title,priority:1,form_data:{acceptance_note:$note}}')"
api_request POST "/ticket/create" "$TICKET_PAYLOAD"
TICKET_ID="$(response_value '.data.id | tostring')"
TICKET_NO="$(response_value '.data.ticket_no | tostring')"

api_request GET "/ticket/detail/$TICKET_ID"
jq -e '.data.sla_respond_at != null and .data.sla_resolve_at != null' \
    >/dev/null <<<"$RESPONSE" || fail "工单 SLA 时间未生成"
ACTIVE_NODE_ID="$(response_value '.data.nodes | map(select(.status == 0 or .status == 4)) | first | .id | tostring')"
jq -e '.data.available_actions | index("approve") != null' \
    >/dev/null <<<"$RESPONSE" || fail "当前账号没有 approve 可用操作，请核对 APPROVER_ID"
log "工单创建成功: $TICKET_NO ($TICKET_ID)"

log "7/11 校验 my_todo、my_apply 和 todolist"
assert_ticket_in_list my_todo
assert_ticket_in_list my_apply
api_request GET "/ticket/todolist"
jq -e --arg id "$TICKET_ID" '.data | any((.id | tostring) == $id)' \
    >/dev/null <<<"$RESPONSE" || fail "todolist 未返回本次工单"

log "8/11 校验 all 视图的 ticket:all 权限链路"
assert_ticket_in_list all

log "9/11 审批工单"
ACTION_PAYLOAD="$(jq -nc \
    --arg ticket_id "$TICKET_ID" \
    --arg node_id "$ACTIVE_NODE_ID" \
    '{ticket_id:$ticket_id,node_instance_id:$node_id,action:"approve",comment:"Task 23 自动化验收通过"}')"
api_request POST "/ticket/action" "$ACTION_PAYLOAD"

log "10/11 校验终态和 available_actions"
api_request GET "/ticket/detail/$TICKET_ID"
jq -e '.data.status == 1' >/dev/null <<<"$RESPONSE" || fail "审批后工单未进入完成态"
jq -e '.data.available_actions == []' >/dev/null <<<"$RESPONSE" \
    || fail "终态工单仍返回可用操作"

log "11/11 校验 my_history"
assert_ticket_in_list my_history

printf '\n工单系统在线端到端验收通过\n'
printf '  RUN_ID: %s\n' "$RUN_ID"
printf '  工单类型: %s\n' "$TICKET_TYPE_ID"
printf '  流程: %s\n' "$FLOW_ID"
printf '  流程版本: %s\n' "$FLOW_VERSION_ID"
printf '  工单: %s (%s)\n' "$TICKET_NO" "$TICKET_ID"
printf '  注意: 验收数据未自动删除，请按 RUN_ID 人工清理。\n'
