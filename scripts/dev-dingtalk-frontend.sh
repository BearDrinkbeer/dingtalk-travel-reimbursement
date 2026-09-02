#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
CONFIG_FILE=${DINGTALK_DEV_ENV_FILE:-"$PROJECT_DIR/.env.dingtalk-dev"}

if [ -f "$CONFIG_FILE" ]; then
    set -a
    # This is a local, gitignored shell environment file controlled by the developer.
    # shellcheck disable=SC1090
    . "$CONFIG_FILE"
    set +a
elif [ -n "${DINGTALK_DEV_ENV_FILE:-}" ]; then
    echo "指定的钉钉联调配置文件不存在：$CONFIG_FILE" >&2
    exit 2
fi

# The frontend only needs public host/port settings. Do not leave backend
# credentials in the Vite process environment even though Vite only exposes
# variables with its public prefix.
unset DINGTALK_CLIENT_SECRET SESSION_SECRET ADMIN_USER_IDS

DINGTALK_DEV_BIND_ADDRESS=${DINGTALK_DEV_BIND_ADDRESS:-127.0.0.1}
DINGTALK_DEV_FRONTEND_PORT=${DINGTALK_DEV_FRONTEND_PORT:-5173}

if [ -n "${DINGTALK_DEV_PUBLIC_HOST:-}" ]; then
    case "$DINGTALK_DEV_PUBLIC_HOST" in
        *://*|*/*|*:*|*[[:space:]]*)
            echo "DINGTALK_DEV_PUBLIC_HOST 只能填写不带协议、端口和路径的主机名。" >&2
            exit 2
            ;;
    esac
    export DINGTALK_DEV_PUBLIC_HOST
else
    unset DINGTALK_DEV_PUBLIC_HOST
fi
case "$DINGTALK_DEV_BIND_ADDRESS" in
    127.0.0.1|0.0.0.0)
        ;;
    *)
        echo "DINGTALK_DEV_BIND_ADDRESS 只能是 127.0.0.1 或 0.0.0.0。" >&2
        exit 2
        ;;
esac
case "$DINGTALK_DEV_FRONTEND_PORT" in
    ''|*[!0-9]*)
        echo "DINGTALK_DEV_FRONTEND_PORT 必须是端口数字。" >&2
        exit 2
        ;;
esac
if [ "$DINGTALK_DEV_FRONTEND_PORT" -lt 1 ] || [ "$DINGTALK_DEV_FRONTEND_PORT" -gt 65535 ]; then
    echo "DINGTALK_DEV_FRONTEND_PORT 必须在 1 到 65535 之间。" >&2
    exit 2
fi

export VITE_DINGTALK_REMOTE_DEBUG=true
cd "$PROJECT_DIR/frontend"
exec ./node_modules/.bin/vite \
    --host "$DINGTALK_DEV_BIND_ADDRESS" \
    --port "$DINGTALK_DEV_FRONTEND_PORT"
