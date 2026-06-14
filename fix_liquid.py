import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:\\Users\\user\\PycharmProjects\\bilirec\\core\\recorder.py"
content = open(path, "r", encoding="utf-8").read()

old_start = content.find("def _send_notify")
old_end = content.find("\ndef ", old_start + 1)

new_func = '''def _send_notify(event: str, room_id: str, uname: str, title: str, extra: dict = None):
    """使用 apprise 发送推送通知。event: recording_started / recording_stopped / error"""
    if not get_global_setting("notify_enabled"):
        return
    if event == "recording_stopped" and not get_global_setting("notify_on_live_end"):
        return
    if event == "error" and not get_global_setting("notify_on_error"):
        return

    notify_url = (get_global_setting("notify_url") or "").strip()
    if not notify_url:
        notify_url = (get_global_setting("webhooks") or "").strip()
    if not notify_url:
        return

    # 映射 apprise 模板变量
    event_map = {"recording_started": "live_start", "recording_stopped": "live_end", "error": "error"}
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    liquid_ctx = {
        "event": event_map.get(event, event),
        "platform": "bilibili",
        "user_name": uname,
        "channel": room_id,
        "user_id": room_id,
        "title": title,
        "live_id": room_id,
        "categories": [],
        "url": f"https://live.bilibili.com/{room_id}",
        "service_url": notify_url,
        "start_time": now_str,
        "avatar": "",
        "cover": "",
        "error": extra.get("error", "") if extra else "",
    }

    default_titles = {"recording_started": "\\U0001f534 \\u5f00\\u59cb\\u5f55\\u5236", "recording_stopped": "\\u23f9\\ufe0f \\u5f55\\u5236\\u7ed3\\u675f", "error": "\\u26a0\\ufe0f \\u5f55\\u5236\\u51fa\\u9519"}
    default_bodies = {"recording_started": "{uname} \\u5f00\\u59cb\\u76f4\\u64ad\\uff1a{title}", "recording_stopped": "{uname} \\u5df2\\u4e0b\\u64ad\\uff0c\\u5f55\\u5236\\u7ed3\\u675f", "error": "{uname}\\uff08\\u623f\\u95f4 {room_id}\\uff09\\u5f55\\u5236\\u51fa\\u9519"}

    title_tpl_raw = (get_global_setting("notify_title_template") or "").strip()
    body_tpl_raw = (get_global_setting("notify_body_template") or "").strip()

    from liquid import Template as LqTpl
    try:
        if title_tpl_raw:
            notify_title = LqTpl(title_tpl_raw).render(**liquid_ctx)
        else:
            notify_title = default_titles.get(event, event)
    except Exception:
        notify_title = default_titles.get(event, event)

    try:
        if body_tpl_raw:
            notify_body = LqTpl(body_tpl_raw).render(**liquid_ctx)
        else:
            ctx = {"uname": uname, "room_id": room_id, "title": title, "time": now_str}
            notify_body = default_bodies.get(event, event).format(**ctx)
    except Exception:
        notify_body = default_bodies.get(event, event)

    def _fire():
        try:
            import apprise
            ap = apprise.Apprise()
            for url in [u.strip() for u in notify_url.splitlines() if u.strip()]:
                ap.add(url)
            ap.notify(title=notify_title, body=notify_body)
            logging.info(f"\\U0001f4e3 \\u901a\\u77e5\\u5df2\\u53d1\\u9001 [{event}] {uname}")
        except ImportError:
            logging.info(f"apprise \\u672a\\u5b89\\u88c5\\uff0c\\u5df2\\u964d\\u7ea7\\u4f7f\\u7528 urllib \\u53d1\\u9001\\u901a\\u77e5 [{event}] {uname}")
            payload = {"event": event, "room_id": room_id, "uname": uname, "title": notify_title, "body": notify_body, "time": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            for url in [u.strip() for u in notify_url.splitlines() if u.strip()]:
                try:
                    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        logging.debug(f"Notify {url} -> {resp.status}")
                except Exception as e2:
                    logging.warning(f"\\u901a\\u77e5\\u53d1\\u9001\\u5931\\u8d25 {url}: {e2}")
        except Exception as e:
            logging.warning(f"\\u901a\\u77e5\\u53d1\\u9001\\u5931\\u8d25: {e}")
    threading.Thread(target=_fire, daemon=True).start()'''

content = content[:old_start] + new_func + content[old_end:]
open(path, "w", encoding="utf-8").write(content)
print("Done")
