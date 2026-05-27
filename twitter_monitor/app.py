"""FastAPI app for the Twitter/X monitor dashboard."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bot import TelegramCommandBot
from .notifiers import TelegramNotifier
from .poller import MonitorPoller
from .scheduler import PollSchedule, poll_result_failed
from .settings import load_settings
from .storage import MonitorStorage

logger = logging.getLogger(__name__)

settings = load_settings()
storage = MonitorStorage(settings.db_path)
notifier = TelegramNotifier.from_settings(settings)
poller = MonitorPoller(storage, settings, notifier)

app = FastAPI(title="Twitter Monitor", version="0.1.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def no_cache_dashboard_assets(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class TargetCreate(BaseModel):
    handle: str
    group_name: str = ""
    remark_name: str = ""
    monitor_tweets: bool = True
    monitor_retweets: bool = True
    monitor_replies: bool = True
    monitor_following: bool = True
    tweet_fetch_count: int | None = None
    following_fetch_count: int | None = None


class TargetUpdate(BaseModel):
    group_name: str | None = None
    remark_name: str | None = None
    enabled: bool | None = None
    monitor_tweets: bool | None = None
    monitor_retweets: bool | None = None
    monitor_replies: bool | None = None
    monitor_following: bool | None = None
    tweet_fetch_count: int | None = None
    following_fetch_count: int | None = None


class TargetImportItem(BaseModel):
    handle: str
    group_name: str = ""
    remark_name: str = ""
    enabled: bool = True
    monitor_tweets: bool = True
    monitor_retweets: bool = True
    monitor_replies: bool = True
    monitor_following: bool = True
    tweet_fetch_count: int | None = None
    following_fetch_count: int | None = None


class TargetImport(BaseModel):
    targets: list[TargetImportItem]
    update_existing: bool = True


class NotificationSettingsUpdate(BaseModel):
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_proxy: str | None = None
    telegram_authorized_chats: list[dict[str, str]] | None = None
    clear_telegram_token: bool = False
    wxpusher_app_token: str | None = None
    wxpusher_uids: list[str] | None = None
    wxpusher_add_uid: str | None = None
    wxpusher_remove_uid: str | None = None
    wxpusher_enabled: bool | None = None
    wxpusher_hot_filter_enabled: bool | None = None
    wxpusher_hot_filter_min_common: int | None = None
    clear_wxpusher_app_token: bool = False
    bark_server_url: str | None = None
    bark_device_keys: list[str] | None = None
    bark_add_device_key: str | None = None
    bark_remove_device_key: str | None = None
    bark_level: str | None = None
    bark_sound: str | None = None
    bark_group: str | None = None
    bark_call: bool | None = None
    bark_volume: int | None = None
    bark_enabled: bool | None = None
    bark_hot_filter_enabled: bool | None = None
    bark_hot_filter_min_common: int | None = None


class NotificationTestRequest(BaseModel):
    channel: str = "all"


class UserResolve(BaseModel):
    query: str


class GroupRename(BaseModel):
    old_name: str
    new_name: str


class GroupCreate(BaseModel):
    name: str


class GroupClear(BaseModel):
    group_name: str


class PollSettingsUpdate(BaseModel):
    poll_interval_min_seconds: int
    poll_interval_max_seconds: int
    poll_backoff_max_seconds: int


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="服务端未配置 MONITOR_ADMIN_TOKEN，管理后台已锁定")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="请输入正确的管理密钥")


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "已保存"
    return "%s...%s" % (value[:4], value[-4:])


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _setting_bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "开", "开启"}


def _setting_int(value: Any, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _notification_config() -> dict[str, Any]:
    db_settings = storage.get_notification_settings()
    db_token = db_settings.get("telegram_bot_token", "")
    db_chat_id = db_settings.get("telegram_chat_id", "")
    token = db_token or settings.telegram_bot_token
    chat_id = db_chat_id or settings.telegram_chat_id
    authorized_chats = storage.get_telegram_authorized_chats()
    telegram_chat_ids = [value for value in [chat_id, *[chat["id"] for chat in authorized_chats]] if value]
    proxy = db_settings.get("telegram_proxy", "") or settings.telegram_proxy
    source = "dashboard" if db_token or db_chat_id else ("environment" if token or chat_id else "empty")
    wx_settings = storage.get_wxpusher_settings()
    wx_token = wx_settings.get("wxpusher_app_token") or settings.wxpusher_app_token
    wx_uids = wx_settings.get("wxpusher_uids") or _split_list(settings.wxpusher_uids)
    wx_enabled = _setting_bool(wx_settings.get("wxpusher_enabled"), True)
    wx_hot_filter_enabled = _setting_bool(wx_settings.get("wxpusher_hot_filter_enabled"), False)
    wx_hot_filter_min_common = max(_setting_int(wx_settings.get("wxpusher_hot_filter_min_common"), 2), 2)
    bark_settings = storage.get_bark_settings()
    bark_server_url = bark_settings.get("bark_server_url") or settings.bark_server_url
    bark_device_keys = bark_settings.get("bark_device_keys") or _split_list(settings.bark_device_keys)
    bark_level = bark_settings.get("bark_level") or settings.bark_level
    bark_sound = bark_settings.get("bark_sound") or settings.bark_sound
    bark_group = bark_settings.get("bark_group") or settings.bark_group
    bark_call = _setting_bool(bark_settings.get("bark_call"), settings.bark_call)
    bark_volume = min(max(_setting_int(bark_settings.get("bark_volume"), settings.bark_volume), 0), 10)
    bark_enabled = _setting_bool(bark_settings.get("bark_enabled"), True)
    bark_hot_filter_enabled = _setting_bool(bark_settings.get("bark_hot_filter_enabled"), False)
    bark_hot_filter_min_common = max(_setting_int(bark_settings.get("bark_hot_filter_min_common"), 2), 2)
    telegram_active = bool(token and telegram_chat_ids)
    wx_active = bool(wx_enabled and wx_token and wx_uids)
    bark_active = bool(bark_enabled and bark_server_url and bark_device_keys)
    return {
        "notificationConfigured": bool(telegram_active or wx_active or bark_active),
        "telegramConfigured": bool(token and telegram_chat_ids),
        "telegramActive": telegram_active,
        "telegramBotTokenSaved": bool(token),
        "telegramBotTokenPreview": _mask_secret(token),
        "telegramChatId": chat_id,
        "telegramAuthorizedChats": authorized_chats,
        "telegramAuthorizedChatCount": len(authorized_chats),
        "telegramRecipientChatIds": telegram_chat_ids,
        "telegramProxy": proxy,
        "telegramProxyConfigured": bool(proxy),
        "telegramCommandsEnabled": settings.telegram_commands_enabled,
        "wxpusherConfigured": bool(wx_token and wx_uids),
        "wxpusherEnabled": wx_enabled,
        "wxpusherActive": wx_active,
        "wxpusherAppTokenSaved": bool(wx_token),
        "wxpusherAppTokenPreview": _mask_secret(str(wx_token or "")),
        "wxpusherUids": wx_uids,
        "wxpusherHotFilterEnabled": wx_hot_filter_enabled,
        "wxpusherHotFilterMinCommon": wx_hot_filter_min_common,
        "barkConfigured": bool(bark_server_url and bark_device_keys),
        "barkEnabled": bark_enabled,
        "barkActive": bark_active,
        "barkServerUrl": bark_server_url,
        "barkDeviceKeys": bark_device_keys,
        "barkDeviceKeyCount": len(bark_device_keys),
        "barkDeviceKeyPreview": [_mask_secret(str(key)) for key in bark_device_keys],
        "barkLevel": bark_level,
        "barkSound": bark_sound,
        "barkGroup": bark_group,
        "barkCall": bark_call,
        "barkVolume": bark_volume,
        "barkHotFilterEnabled": bark_hot_filter_enabled,
        "barkHotFilterMinCommon": bark_hot_filter_min_common,
        "source": source,
    }


def _poll_config() -> dict[str, int]:
    return storage.get_poll_settings(
        default_min=settings.poll_interval_min_seconds,
        default_max=settings.poll_interval_max_seconds,
        default_backoff_max=settings.poll_backoff_max_seconds,
    )


@app.on_event("startup")
async def on_startup() -> None:
    storage.init()
    if settings.background_worker:
        app.state.worker_task = asyncio.create_task(_worker_loop())
    if settings.telegram_commands_enabled:
        app.state.telegram_bot_task = asyncio.create_task(_telegram_bot_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for task_name in ("worker_task", "telegram_bot_task"):
        task = getattr(app.state, task_name, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _worker_loop() -> None:
    poll_config = _poll_config()
    schedule = PollSchedule(
        min_seconds=poll_config["pollIntervalMinSeconds"],
        max_seconds=poll_config["pollIntervalMaxSeconds"],
        backoff_max_seconds=poll_config["pollBackoffMaxSeconds"],
    )
    await asyncio.sleep(3)
    while True:
        failed = False
        try:
            result = await asyncio.to_thread(poller.poll_all)
            failed = poll_result_failed(result)
        except Exception:
            logger.exception("Background poll failed")
            failed = True
        if failed:
            schedule.record_failure()
        else:
            schedule.record_success()
        poll_config = _poll_config()
        schedule.min_seconds = poll_config["pollIntervalMinSeconds"]
        schedule.max_seconds = poll_config["pollIntervalMaxSeconds"]
        schedule.backoff_max_seconds = poll_config["pollBackoffMaxSeconds"]
        delay = schedule.next_delay()
        app.state.poll_failures = schedule.failures
        app.state.next_poll_delay_seconds = delay
        app.state.next_poll_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        logger.info("Next background poll in %s seconds, failures=%s", delay, schedule.failures)
        await asyncio.sleep(delay)


async def _telegram_bot_loop() -> None:
    bot = TelegramCommandBot(storage, settings)
    await asyncio.sleep(3)
    while True:
        try:
            await asyncio.to_thread(bot.poll_once)
        except Exception:
            logger.exception("Telegram command polling failed")
            await asyncio.sleep(5)
        await asyncio.sleep(1)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "dbPath": settings.db_path}


@app.get("/api/config")
def config() -> dict[str, Any]:
    notification = _notification_config()
    poll_config = _poll_config()
    next_poll_at = getattr(app.state, "next_poll_at", None)
    next_poll_remaining = getattr(app.state, "next_poll_delay_seconds", None)
    if isinstance(next_poll_at, datetime):
        next_poll_remaining = max(int((next_poll_at - datetime.now(timezone.utc)).total_seconds()), 0)
    return {
        "adminRequired": settings.admin_required,
        "adminConfigured": settings.admin_configured,
        "notificationConfigured": notification["notificationConfigured"],
        "telegramConfigured": notification["telegramConfigured"],
        "wxpusherConfigured": notification["wxpusherConfigured"],
        "barkConfigured": notification["barkConfigured"],
        "telegramProxyConfigured": notification["telegramProxyConfigured"],
        "backgroundWorker": settings.background_worker,
        "pollIntervalSeconds": poll_config["pollIntervalMaxSeconds"],
        "pollIntervalMinSeconds": poll_config["pollIntervalMinSeconds"],
        "pollIntervalMaxSeconds": poll_config["pollIntervalMaxSeconds"],
        "pollBackoffMaxSeconds": poll_config["pollBackoffMaxSeconds"],
        "pollFailures": getattr(app.state, "poll_failures", 0),
        "nextPollDelaySeconds": next_poll_remaining,
        "nextPollAt": next_poll_at.isoformat() if isinstance(next_poll_at, datetime) else None,
        "defaultTweetFetchCount": settings.default_tweet_fetch_count,
        "defaultFollowingFetchCount": settings.default_following_fetch_count,
        "defaultInitialFollowingFetchCount": settings.default_initial_following_fetch_count,
        "notification": notification,
        "stats": storage.stats(),
    }


@app.get("/api/targets", dependencies=[Depends(require_admin)])
def list_targets() -> dict[str, Any]:
    return {"data": storage.list_targets()}


@app.get("/api/groups", dependencies=[Depends(require_admin)])
def list_groups() -> dict[str, Any]:
    return {"data": storage.list_groups()}


@app.get("/api/following-insights", dependencies=[Depends(require_admin)])
def following_insights(group: str = "", min_common: int = 2, limit: int = 80) -> dict[str, Any]:
    return {
        "data": storage.following_insights(
            group_name=group,
            min_common=min_common,
            limit=limit,
        )
    }


@app.post("/api/groups", dependencies=[Depends(require_admin)])
def add_group(payload: GroupCreate) -> dict[str, Any]:
    try:
        groups = storage.add_group(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": groups}


@app.post("/api/groups/rename", dependencies=[Depends(require_admin)])
def rename_group(payload: GroupRename) -> dict[str, Any]:
    try:
        changed = storage.rename_group(payload.old_name, payload.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "changed": changed, "data": storage.list_groups()}


@app.post("/api/groups/clear", dependencies=[Depends(require_admin)])
def clear_group(payload: GroupClear) -> dict[str, Any]:
    try:
        changed = storage.clear_group(payload.group_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "changed": changed, "data": storage.list_groups()}


@app.post("/api/poll-settings", dependencies=[Depends(require_admin)])
def update_poll_settings(payload: PollSettingsUpdate) -> dict[str, Any]:
    data = storage.update_poll_settings(
        min_seconds=payload.poll_interval_min_seconds,
        max_seconds=payload.poll_interval_max_seconds,
        backoff_max_seconds=payload.poll_backoff_max_seconds,
    )
    return {"ok": True, "data": data}


@app.post("/api/targets", dependencies=[Depends(require_admin)])
def add_target(payload: TargetCreate) -> dict[str, Any]:
    try:
        target = storage.add_target(
            payload.handle,
            group_name=payload.group_name,
            remark_name=payload.remark_name,
            monitor_tweets=payload.monitor_tweets,
            monitor_retweets=payload.monitor_retweets,
            monitor_replies=payload.monitor_replies,
            monitor_following=payload.monitor_following,
            tweet_fetch_count=payload.tweet_fetch_count or settings.default_tweet_fetch_count,
            following_fetch_count=payload.following_fetch_count or settings.default_following_fetch_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": target}


@app.post("/api/targets/import", dependencies=[Depends(require_admin)])
def import_targets(payload: TargetImport) -> dict[str, Any]:
    result: dict[str, Any] = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    for index, item in enumerate(payload.targets, start=1):
        try:
            existing = storage.get_target_by_handle(item.handle)
            updates = {
                "group_name": item.group_name,
                "remark_name": item.remark_name,
                "enabled": item.enabled,
                "monitor_tweets": item.monitor_tweets,
                "monitor_retweets": item.monitor_retweets,
                "monitor_replies": item.monitor_replies,
                "monitor_following": item.monitor_following,
                "tweet_fetch_count": item.tweet_fetch_count or settings.default_tweet_fetch_count,
                "following_fetch_count": item.following_fetch_count or settings.default_following_fetch_count,
            }
            if item.group_name:
                storage.add_group(item.group_name)
            if existing:
                if payload.update_existing:
                    storage.update_target(int(existing["id"]), updates)
                    result["updated"] += 1
                else:
                    result["skipped"] += 1
                continue
            storage.add_target(
                item.handle,
                group_name=item.group_name,
                remark_name=item.remark_name,
                monitor_tweets=item.monitor_tweets,
                monitor_retweets=item.monitor_retweets,
                monitor_replies=item.monitor_replies,
                monitor_following=item.monitor_following,
                tweet_fetch_count=item.tweet_fetch_count or settings.default_tweet_fetch_count,
                following_fetch_count=item.following_fetch_count or settings.default_following_fetch_count,
            )
            target = storage.get_target_by_handle(item.handle)
            if target is not None and not item.enabled:
                storage.update_target(int(target["id"]), {"enabled": False})
            result["created"] += 1
        except Exception as exc:
            result["errors"].append({"line": index, "handle": item.handle, "error": str(exc)})
    result["data"] = storage.list_targets()
    return result


@app.post("/api/users/resolve", dependencies=[Depends(require_admin)])
async def resolve_user(payload: UserResolve) -> dict[str, Any]:
    handle = payload.query.strip().lstrip("@")
    if not handle:
        raise HTTPException(status_code=400, detail="请填写要识别的 X 用户名")
    try:
        profile = await asyncio.to_thread(lambda: poller._make_client().fetch_user(handle))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="识别用户失败：%s" % exc) from exc
    return {
        "data": {
            "handle": profile.screen_name or handle,
            "displayName": profile.name,
            "userId": profile.id,
            "bio": profile.bio,
            "followers": profile.followers_count,
            "following": profile.following_count,
        }
    }


@app.patch("/api/targets/{target_id}", dependencies=[Depends(require_admin)])
def update_target(target_id: int, payload: TargetUpdate) -> dict[str, Any]:
    target = storage.update_target(target_id, payload.model_dump(exclude_unset=True))
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"data": target}


@app.delete("/api/targets/{target_id}", dependencies=[Depends(require_admin)])
def delete_target(target_id: int) -> dict[str, Any]:
    if not storage.delete_target(target_id):
        raise HTTPException(status_code=404, detail="Target not found")
    return {"ok": True}


@app.post("/api/targets/{target_id}/poll", dependencies=[Depends(require_admin)])
async def poll_one(target_id: int) -> dict[str, Any]:
    target = storage.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    result = await asyncio.to_thread(poller.poll_target, target)
    return {"data": result}


@app.post("/api/targets/{target_id}/following/backfill", dependencies=[Depends(require_admin)])
async def backfill_target_following(target_id: int) -> dict[str, Any]:
    target = storage.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    result = await asyncio.to_thread(poller.backfill_following, target)
    return {"data": result}


@app.post("/api/poll/run", dependencies=[Depends(require_admin)])
async def poll_all() -> dict[str, Any]:
    result = await asyncio.to_thread(poller.poll_all)
    return {"data": result}


@app.get("/api/events", dependencies=[Depends(require_admin)])
def list_events(limit: int = 100) -> dict[str, Any]:
    return {"data": storage.list_events(limit=limit)}


@app.get("/api/notification-settings", dependencies=[Depends(require_admin)])
def get_notification_settings() -> dict[str, Any]:
    return {"data": _notification_config()}


@app.post("/api/notification-settings", dependencies=[Depends(require_admin)])
def update_notification_settings(payload: NotificationSettingsUpdate) -> dict[str, Any]:
    storage.update_notification_settings(
        telegram_bot_token=payload.telegram_bot_token,
        telegram_chat_id=payload.telegram_chat_id,
        telegram_proxy=payload.telegram_proxy,
        clear_telegram_token=payload.clear_telegram_token,
    )
    if payload.telegram_authorized_chats is not None:
        storage.set_telegram_authorized_chats(payload.telegram_authorized_chats)
    storage.update_wxpusher_settings(
        wxpusher_app_token=payload.wxpusher_app_token,
        wxpusher_uids=payload.wxpusher_uids,
        wxpusher_add_uid=payload.wxpusher_add_uid,
        wxpusher_remove_uid=payload.wxpusher_remove_uid,
        wxpusher_enabled=payload.wxpusher_enabled,
        wxpusher_hot_filter_enabled=payload.wxpusher_hot_filter_enabled,
        wxpusher_hot_filter_min_common=payload.wxpusher_hot_filter_min_common,
        clear_wxpusher_app_token=payload.clear_wxpusher_app_token,
    )
    submitted_bark_keys = payload.bark_device_keys
    existing_bark = _notification_config()
    if submitted_bark_keys == [] and existing_bark.get("barkDeviceKeyCount", 0):
        submitted_bark_keys = None
    storage.update_bark_settings(
        bark_server_url=payload.bark_server_url,
        bark_device_keys=submitted_bark_keys,
        bark_add_device_key=payload.bark_add_device_key,
        bark_remove_device_key=payload.bark_remove_device_key,
        bark_level=payload.bark_level,
        bark_sound=payload.bark_sound,
        bark_group=payload.bark_group,
        bark_call=payload.bark_call,
        bark_volume=payload.bark_volume,
        bark_enabled=payload.bark_enabled,
        bark_hot_filter_enabled=payload.bark_hot_filter_enabled,
        bark_hot_filter_min_common=payload.bark_hot_filter_min_common,
    )
    return {"data": _notification_config()}


@app.post("/api/notification-settings/test", dependencies=[Depends(require_admin)])
async def test_notification(payload: NotificationTestRequest | None = None) -> dict[str, Any]:
    channel = (payload.channel if payload else "all").strip().lower()
    config_data = _notification_config()
    if not (config_data["telegramConfigured"] or config_data["wxpusherConfigured"] or config_data["barkConfigured"]):
        raise HTTPException(status_code=400, detail="请先配置 Telegram、WxPusher 或 Bark 通知")
    try:
        adapter = poller._notification_adapter(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await asyncio.to_thread(
        adapter.send_event,
        {
            "event_type": "test",
            "target_handle": "monitor",
            "title": "测试通知",
            "body": "如果你看到这条消息，说明监控平台通知已经配置成功。渠道：%s" % channel,
            "url": "",
        },
    )
    if not result.sent:
        if result.error == "notification_not_configured":
            raise HTTPException(status_code=400, detail="通知渠道未配置或已暂停")
        raise HTTPException(status_code=400, detail=result.error or "测试通知发送失败")
    return {"ok": True}


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("MONITOR_HOST") or os.environ.get("HOST") or "0.0.0.0"
    uvicorn.run("twitter_monitor.app:app", host=host, port=port)
