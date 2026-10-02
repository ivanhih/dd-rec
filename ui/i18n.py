# ui/i18n.py
"""应用文案国际化：三套语言字典、t() 取词与进程级语言管理。

约定：
- 配置层仍使用中文展示字符串（"简体中文" / "繁體中文" / "English"），
  本模块负责展示名 ↔ 稳定 code（zh-CN / zh-TW / en）的映射。
- LanguageManager 只改当前语言并发射信号，不写配置；持久化由 core.config 负责。
- 文案 key 用点分层级（"nav.channels" / "settings.appearance"）；
  缺失 key 回退到简体中文，再退化为 key 本身，绝不让 UI 崩。
"""
from PySide6.QtCore import QObject, Signal

ZH_CN = "zh-CN"
ZH_TW = "zh-TW"
EN = "en"

# 展示名（写入 config）↔ code（内部逻辑）
LANG_DISPLAY = {
    "简体中文": ZH_CN,
    "繁體中文": ZH_TW,
    "English": EN,
}
LANG_CODE_TO_DISPLAY = {v: k for k, v in LANG_DISPLAY.items()}

DEFAULT_LANG = ZH_CN


# ---------------------------------------------------------------------------
# 文案表：三语条目 (zh-CN, zh-TW, en)
# ---------------------------------------------------------------------------
_ENTRIES = {
    # app / nav
    "app.title": ("DD录播机", "DD錄播機", "DD Rec"),
    "nav.channels": ("频道", "頻道", "Channels"),
    "nav.history": ("最近录制", "最近錄製", "History"),
    "nav.settings": ("全局设置", "全域設定", "Settings"),
    "nav.about": ("关于", "關於", "About"),

    # in-app help
    "help.title": ("使用说明", "使用說明", "User guide"),
    "help.open": ("使用说明", "使用說明", "User guide"),
    "help.close": ("关闭", "關閉", "Close"),
    "help.open_docs_dir": ("打开文档目录", "開啟文件目錄", "Open docs folder"),
    "help.open_wiki": ("GitHub Wiki", "GitHub Wiki", "GitHub Wiki"),
    "help.missing": ("未找到本地文档", "找不到本機文件", "Local docs not found"),
    "help.missing_detail": ("读取文档失败: {error}", "讀取文件失敗: {error}", "Failed to read docs: {error}"),
    "help.missing_toc": ("(无文档)", "（無文件）", "(no docs)"),
    "help.status_loaded": ("当前: {name}", "目前: {name}", "Now: {name}"),
    "help.setting_tip": ("查看该设置的说明", "查看此設定的說明", "Open help for this setting"),
    "help.toc.home": ("首页", "首頁", "Home"),
    "help.toc.quickstart": ("快速开始", "快速開始", "Quick start"),
    "help.toc.ui": ("界面导览", "介面導覽", "UI tour"),
    "help.toc.record": ("录制与切割", "錄製與切割", "Recording"),
    "help.toc.paths": ("路径与数据", "路徑與資料", "Paths & data"),
    "help.toc.config": ("配置参考", "設定參考", "Config"),
    "help.toc.webhook": ("Webhook 与通知", "Webhook 與通知", "Webhook"),
    "help.toc.troubleshoot": ("故障排查", "故障排查", "Troubleshooting"),
    "help.toc.dev_run": ("源码运行与构建", "原始碼執行與建置", "Build from source"),
    "help.toc.release": ("发布与更新", "發佈與更新", "Release & update"),
    "help.toc.arch": ("架构概览", "架構概覽", "Architecture"),

    # Optional B站 account and QR login
    "account.title": ("B站账号", "B站帳號", "Bilibili account"),
    "account.sidebar_login": ("登录", "登入", "Log in"),
    "account.sidebar_profile": ("{name} · B站账号", "{name} · B站帳號", "{name} · Bilibili account"),
    "account.login": ("扫码登录", "掃碼登入", "Scan to log in"),
    "account.relogin": ("重新扫码", "重新掃碼", "Log in again"),
    "account.logout": ("退出账号", "登出帳號", "Log out"),
    "account.check": ("检查登录状态", "檢查登入狀態", "Check login"),
    "account.checking": ("检查中…", "檢查中…", "Checking…"),
    "account.anonymous": ("未登录 · 匿名录制", "未登入 · 匿名錄製", "Signed out · anonymous recording"),
    "account.anonymous_hint": ("无需登录也可以录制公开直播", "無需登入也可以錄製公開直播", "Login is optional for public streams"),
    "account.active": ("已登录 · 默认房间使用此账号", "已登入 · 預設房間使用此帳號", "Signed in · used by default rooms"),
    "account.expired_account": ("登录已失效，默认账号回退匿名，请重新扫码", "登入已失效，預設帳號回退匿名，請重新掃碼", "Login expired; recording falls back to anonymous. Scan again"),
    "account.description": ("登录可选。新房间自动继承账号；已有手填凭据保持有效，房间可单独选择匿名录制。", "登入可選。新房間自動繼承帳號；已有手填憑證保持有效，房間可單獨選擇匿名錄製。", "Login is optional. New rooms inherit this account. Existing manual credentials are preserved; each room can choose anonymous recording."),
    "account.apply_all": ("全部房间使用此账号", "全部房間使用此帳號", "Use this account for all rooms"),
    "account.applied": ("全部现有房间已使用全局账号，手填凭据已保留", "全部現有房間已使用全域帳號，手填憑證已保留", "All existing rooms use this account. Manual credentials were preserved"),
    "account.scan_hint": ("使用哔哩哔哩 App 扫码，并在手机上确认登录", "使用嗶哩嗶哩 App 掃碼，並在手機上確認登入", "Scan with the Bilibili app and confirm on your phone"),
    "account.login_purpose": ("登录后，DD 可使用你的 B站账号请求直播和弹幕数据，并获取账号有权访问的直播画质。不登录也可匿名录制公开直播。", "登入後，DD 可使用你的 B站帳號請求直播與彈幕資料，並取得帳號有權存取的直播畫質。不登入也可匿名錄製公開直播。", "Logging in lets DD use your Bilibili account to request streams and chat data, including quality levels your account can access. Public streams can also be recorded anonymously."),
    "account.local_storage_title": ("登录信息仅保存在本地", "登入資訊僅儲存在本機", "Login information stays on this device"),
    "account.local_storage_notice": ("Cookie、刷新令牌和账号信息仅保存在本机，不会同步到 DD 服务器。请求 B站时会使用必要的登录凭据；退出账号可清除本地登录信息。", "Cookie、更新權杖與帳號資訊僅儲存在本機，不會同步到 DD 伺服器。請求 B站時會使用必要的登入憑證；登出帳號可清除本機登入資訊。", "Cookies, refresh tokens and account details are saved only on this device, without syncing to DD servers. Necessary credentials are sent to Bilibili for account requests. Logging out clears the saved login information."),
    "account.refresh_qr": ("刷新二维码", "重新整理 QR 碼", "Refresh QR code"),
    "account.generating": ("正在获取二维码…", "正在取得 QR 碼…", "Generating QR code…"),
    "account.pending": ("等待扫码", "等待掃碼", "Waiting for scan"),
    "account.scanned": ("已扫码，请在手机上确认", "已掃碼，請在手機上確認", "Scanned; confirm on your phone"),
    "account.expired_qr": ("二维码已过期，请刷新", "QR 碼已過期，請重新整理", "QR code expired. Refresh to try again"),
    "account.network": ("网络请求失败，请检查网络后重试", "網路請求失敗，請檢查網路後重試", "Network request failed. Check your connection and retry"),
    "account.response": ("凭据或接口响应异常，请重试", "憑證或介面回應異常，請重試", "Invalid credentials or API response. Please retry"),
    "account.storage_error": ("账号保存失败，请检查用户数据目录权限", "帳號儲存失敗，請檢查使用者資料目錄權限", "Could not save account. Check user data folder permissions"),
    "account.room_mode": ("录制账号", "錄製帳號", "Recording account"),
    "account.room_mode_hint": ("匿名模式对视频和弹幕都不发送账号 Cookie", "匿名模式對影片和彈幕都不傳送帳號 Cookie", "Anonymous mode sends no account cookies for video or chat"),
    "account.mode.inherit": ("默认（手填优先）", "預設（手填優先）", "Default (manual first)"),
    "account.mode.account": ("使用全局账号", "使用全域帳號", "Global account"),
    "account.mode.anonymous": ("匿名录制", "匿名錄製", "Anonymous"),
    "account.mode.manual": ("仅使用手填凭据", "僅使用手填憑證", "Manual credentials only"),

    # common
    "common.save": ("保存", "儲存", "Save"),
    "common.cancel": ("取消", "取消", "Cancel"),
    "common.close": ("关闭", "關閉", "Close"),
    "common.add": ("添加", "新增", "Add"),
    "common.retry": ("重试", "重試", "Retry"),
    "common.save_settings": ("保存设置", "儲存設定", "Save Settings"),
    "common.tip": ("提示", "提示", "Notice"),
    "common.error": ("错误", "錯誤", "Error"),
    "common.success": ("成功", "成功", "Success"),
    "common.room": ("房间", "房間", "Room"),
    "common.loading": ("获取中", "取得中", "Loading"),

    # channels
    "channels.title": ("频道", "頻道", "Channels"),
    "channels.search_placeholder": ("🔍 搜索", "🔍 搜尋", "🔍 Search"),
    "channels.add": ("添加", "新增", "Add"),
    "channels.add_tip": ("添加直播间", "新增直播間", "Add channel"),
    "channels.filter": ("过滤", "過濾", "Filter"),
    "channels.sort": ("排序", "排序", "Sort"),
    "channels.filter.all": ("全部频道", "全部頻道", "All channels"),
    "channels.filter.monitoring": ("仅监控中", "僅監控中", "Monitoring only"),
    "channels.filter.recording": ("仅录制中", "僅錄製中", "Recording only"),
    "channels.filter.live": ("仅直播中", "僅直播中", "Live only"),
    "channels.filter.paused": ("仅已暂停", "僅已暫停", "Paused only"),
    "channels.filter_tip.all": ("过滤: 全部", "過濾: 全部", "Filter: All"),
    "channels.filter_tip.monitoring": ("过滤: 监控中", "過濾: 監控中", "Filter: Monitoring"),
    "channels.filter_tip.recording": ("过滤: 录制中", "過濾: 錄製中", "Filter: Recording"),
    "channels.filter_tip.live": ("过滤: 直播中", "過濾: 直播中", "Filter: Live"),
    "channels.filter_tip.paused": ("过滤: 已暂停", "過濾: 已暫停", "Filter: Paused"),
    "channels.sort.default": ("默认顺序", "預設順序", "Default order"),
    "channels.sort.status": ("按状态优先", "依狀態優先", "Status first"),
    "channels.sort.name": ("按名称排序", "依名稱排序", "Sort by name"),
    "channels.sort.room_id": ("按房间号排序", "依房間號排序", "Sort by room ID"),
    "channels.sort_tip.default": ("排序: 默认", "排序: 預設", "Sort: Default"),
    "channels.sort_tip.status": ("排序: 状态优先", "排序: 狀態優先", "Sort: Status"),
    "channels.sort_tip.name": ("排序: 名称", "排序: 名稱", "Sort: Name"),
    "channels.sort_tip.room_id": ("排序: 房间号", "排序: 房間號", "Sort: Room ID"),

    # card
    "card.tip.folder": ("打开文件夹", "開啟資料夾", "Open folder"),
    "card.tip.cut": ("立即切割", "立即切割", "Split now"),
    "card.tip.cut_disabled": ("未在录制", "未在錄製", "Not recording"),
    "card.tip.delete": ("删除该频道", "刪除該頻道", "Delete channel"),
    "card.tip.settings": ("频道设置", "頻道設定", "Channel settings"),
    "card.no_title": ("暂无标题", "暫無標題", "No title"),
    "card.unknown_area": ("未知分区", "未知分區", "Unknown category"),
    "card.unknown_content": ("未知内容", "未知內容", "Unknown content"),
    "card.monitor.active": ("⏯ 监控中", "⏯ 監控中", "⏯ Monitoring"),
    "card.monitor.paused": ("⏸ 已暂停", "⏸ 已暫停", "⏸ Paused"),
    "card.monitor.error": ("❌ 出错", "❌ 出錯", "❌ Error"),
    "card.live.on": ("🔴 直播中", "🔴 直播中", "🔴 Live"),
    "card.live.off": ("🌙 未开播", "🌙 未開播", "🌙 Offline"),
    "card.rec.idle": ("⏳ 闲置中", "⏳ 閒置中", "⏳ Idle"),
    "card.rec.recording": ("📹 录制中", "📹 錄製中", "📹 Recording"),
    "card.rec.filtered": ("⏸ 条件过滤", "⏸ 條件過濾", "⏸ Filtered"),
    "card.rec.waiting": ("⏳ 等待防抖", "⏳ 等待防抖", "⏳ Debouncing"),
    "card.rec.interrupted": ("⏸ 录制中断", "⏸ 錄製中斷", "⏸ Interrupted"),
    "card.rec.error": ("❌ 出错了", "❌ 出錯了", "❌ Error"),
    "card.rec.disabled": ("⏸ 录制已停用", "⏸ 錄製已停用", "⏸ Recording off"),
    "card.rec.confirming": ("⏳ 确认中", "⏳ 確認中", "⏳ Confirming"),
    "card.health.healthy": ("✓ 健康", "✓ 健康", "✓ Healthy"),
    "card.health.degraded": ("⚠ 降级", "⚠ 降級", "⚠ Degraded"),
    "card.health.failed": ("✗ 异常", "✗ 異常", "✗ Failed"),
    "card.health.tip": ("分段健康: {detail}", "分段健康: {detail}", "Segment health: {detail}"),

    # custom room tags
    "settings.tags.title": ("自定义标签", "自訂標籤", "Custom tags"),
    "settings.tags.desc": ("创建可复用标签，自定义文字和颜色。删除标签会从所有房间解绑。", "建立可重用標籤，自訂文字和顏色。刪除標籤會從所有房間解除。", "Create reusable tags with custom names and colors. Deleting a tag unbinds it from all rooms."),
    "settings.tags.add": ("添加标签", "新增標籤", "Add tag"),
    "settings.tags.add_title": ("添加标签", "新增標籤", "Add tag"),
    "settings.tags.edit_title": ("编辑标签", "編輯標籤", "Edit tag"),
    "settings.tags.name": ("标签名称", "標籤名稱", "Tag name"),
    "settings.tags.color": ("选择标签颜色", "選擇標籤顏色", "Choose tag color"),
    "settings.tags.name_required": ("标签名称不能为空。", "標籤名稱不能為空。", "Tag name is required."),
    "settings.tags.empty": ("还没有标签，点击“添加标签”创建。", "尚無標籤，點擊「新增標籤」建立。", "No tags yet. Click Add tag to create one."),
    "settings.tags.delete": ("删除", "刪除", "Delete"),
    "settings.tags.delete_title": ("删除标签", "刪除標籤", "Delete tag"),
    "settings.tags.delete_confirm": ("确定删除“{name}”？所有房间将解除该标签。", "確定刪除「{name}」？所有房間將解除此標籤。", "Delete “{name}”? It will be removed from all rooms."),
    "settings.tags.room_title": ("房间标签", "房間標籤", "Room tags"),
    "settings.tags.room_item": ("绑定标签", "綁定標籤", "Assign tags"),
    "settings.tags.room_desc": ("选中的标签会显示在房间卡片右上角，也可以用于搜索和筛选。", "選取的標籤會顯示於房間卡片右上角，也可用於搜尋和篩選。", "Selected tags appear at the top-right of the room card and can be searched or filtered."),
    "settings.tags.empty_room": ("请先在全局设置 → 自定义标签中创建标签。", "請先在全域設定 → 自訂標籤中建立標籤。", "Create tags first under Settings → Custom tags."),
    "settings.tags.create_bind": ("新建并绑定", "新增並綁定", "Create & assign"),
    "channels.filter.tags": ("标签", "標籤", "Tags"),
    "channels.filter.untagged": ("无标签", "無標籤", "Untagged"),
    "channels.filter_tip.tag": ("筛选标签: {name}", "篩選標籤: {name}", "Filter tag: {name}"),
    "channels.filter_tip.untagged": ("仅显示无标签房间", "僅顯示無標籤房間", "Show untagged rooms"),

    # recent recordings
    "history.title": ("最近录制", "最近錄製", "Recent recordings"),
    "history.desc": ("展示最近关闭的录制分段。可搜索、按房间/状态筛选，打开目录或复制路径。", "顯示最近關閉的錄製分段。可搜尋、依房間/狀態篩選，開啟目錄或複製路徑。", "Closed recording parts. Search, filter by room/status, open the folder, or copy the path."),
    "history.refresh": ("刷新", "重新整理", "Refresh"),
    "history.search_placeholder": ("搜索主播 / 标题 / 房间号 / 路径…", "搜尋主播 / 標題 / 房間號 / 路徑…", "Search host / title / room / path…"),
    "history.filter_room": ("房间", "房間", "Room"),
    "history.filter_status": ("状态", "狀態", "Status"),
    "history.filter_status_all": ("全部状态", "全部狀態", "All statuses"),
    "history.filter_status_ok": ("成功", "成功", "Succeeded"),
    "history.filter_status_failed": ("失败", "失敗", "Failed"),
    "history.all_rooms": ("全部房间", "全部房間", "All rooms"),
    "history.empty": ("还没有录制历史。完成一段录制后会显示在这里。", "尚無錄製歷史。完成一段錄製後會顯示在這裡。", "No history yet. Closed recordings will appear here."),
    "history.count": ("共 {count} 条", "共 {count} 筆", "{count} items"),
    "history.open_folder": ("打开位置", "開啟位置", "Open location"),
    "history.copy_path": ("复制路径", "複製路徑", "Copy path"),
    "history.copied": ("路径已复制", "路徑已複製", "Path copied"),
    "history.no_path": ("(无路径)", "（無路徑）", "(no path)"),
    "history.missing_file": ("文件可能已移动或删除", "檔案可能已移動或刪除", "File may have been moved or deleted"),
    "history.reason": ("原因: {reason}", "原因: {reason}", "Reason: {reason}"),
    "history.disk.ok": (
        "保存目录所在盘 {path}：已用 {used} / 共 {total}，剩余 {free}",
        "儲存目錄所在碟 {path}：已用 {used} / 共 {total}，剩餘 {free}",
        "Save disk {path}: used {used} / {total}, free {free}",
    ),
    "history.disk.warn": (
        "保存目录所在盘空间偏低 {path}：已用 {used} / 共 {total}，剩余 {free}（预警 {warn}）",
        "儲存目錄所在碟空間偏低 {path}：已用 {used} / 共 {total}，剩餘 {free}（預警 {warn}）",
        "Save disk low {path}: used {used} / {total}, free {free} (warn at {warn})",
    ),
    "history.disk.critical": (
        "保存目录所在盘空间紧急 {path}：已用 {used} / 共 {total}，剩余 {free}（低于 {min} 会停录）",
        "儲存目錄所在碟空間緊急 {path}：已用 {used} / 共 {total}，剩餘 {free}（低於 {min} 會停錄）",
        "Save disk critical {path}: used {used} / {total}, free {free} (stops below {min})",
    ),
    "history.disk.unknown": (
        "无法读取保存目录磁盘空间：{path}",
        "無法讀取儲存目錄磁碟空間：{path}",
        "Cannot read free space for save dir: {path}",
    ),
    "history.disk.used_pct": (
        "已用 {pct}%",
        "已用 {pct}%",
        "Used {pct}%",
    ),

    # hang / diagnostics settings
    "settings.automation.webhook_log_verbose": ("Webhook 详细失败日志", "Webhook 詳細失敗日誌", "Verbose webhook failure logs"),
    "settings.automation.webhook_log_verbose_desc": ("开启后每次 webhook 失败都写 warning；关闭后同地址失败会合并限频。", "開啟後每次 webhook 失敗都寫 warning；關閉後同網址失敗會合併限頻。", "When on, every webhook failure is logged. When off, repeated failures for the same URL are rate-limited."),
    "settings.automation.webhook_fail_log_interval": ("Webhook 失败日志间隔（秒）", "Webhook 失敗日誌間隔（秒）", "Webhook fail log interval (sec)"),
    "settings.automation.webhook_fail_log_interval_desc": ("关闭详细日志时，同一 URL 连续失败至少间隔这么久才再记一条。", "關閉詳細日誌時，同一 URL 連續失敗至少間隔這麼久才再記一筆。", "Minimum seconds between repeated failure logs for the same URL when verbose logging is off."),
    "settings.system.disk_min_free": ("最小剩余磁盘（GB）", "最小剩餘磁碟（GB）", "Minimum free disk (GB)"),
    "settings.system.disk_min_free_desc": ("保存盘剩余空间低于该值时，停止新开录并安全收尾当前段。", "儲存碟剩餘空間低於該值時，停止新開錄並安全收尾當前段。", "Stop new recordings and safely finish the current part when free space is below this value."),
    "settings.system.disk_check_interval": ("磁盘检查间隔（秒）", "磁碟檢查間隔（秒）", "Disk check interval (sec)"),
    "settings.system.disk_check_interval_desc": ("录制中多久检查一次磁盘剩余空间。", "錄製中多久檢查一次磁碟剩餘空間。", "How often to re-check free disk space while recording."),
    "settings.system.heartbeat_interval": ("挂机心跳日志间隔（秒）", "掛機心跳日誌間隔（秒）", "Heartbeat log interval (sec)"),
    "settings.system.heartbeat_interval_desc": ("录制中每隔多久写一条 [heartbeat] 状态日志，便于排查崩溃前状态。", "錄製中每隔多久寫一條 [heartbeat] 狀態日誌，方便排查崩潰前狀態。", "How often to write a [heartbeat] status line while recording."),
    "settings.system.auto_heal": ("录制崩溃自愈", "錄製崩潰自癒", "Auto-heal crashed recorders"),
    "settings.system.auto_heal_desc": ("单个房间录制线程意外退出后自动重启该房间；用户主动关闭监听不会触发。", "單一房間錄製執行緒意外退出後自動重啟該房間；使用者主動關閉監聽不會觸發。", "Restart a room after unexpected recorder exit. Manual stop does not trigger heal."),
    "settings.system.auto_heal_max": ("自愈最大次数", "自癒最大次數", "Max auto-heal restarts"),
    "settings.system.auto_heal_max_desc": ("时间窗口内同一房间最多自动重启次数，超过后熔断。", "時間窗口內同一房間最多自動重啟次數，超過後熔斷。", "Maximum automatic restarts per room inside the time window before circuit-break."),
    "settings.system.auto_heal_window": ("自愈统计窗口（秒）", "自癒統計視窗（秒）", "Auto-heal window (sec)"),
    "settings.system.auto_heal_window_desc": ("统计自动重启次数的时间窗口长度。", "統計自動重啟次數的時間視窗長度。", "Time window used to count automatic restarts."),

    # tray
    "tray.show": ("显示", "顯示", "Show"),
    "tray.quit": ("退出", "結束", "Quit"),
    "tray.tooltip": ("DD录播机", "DD錄播機", "DD Rec"),

    # startup
    "startup.init": ("正在初始化…", "正在初始化…", "Initializing…"),
    "startup.loading_channels": ("正在加载频道…", "正在載入頻道…", "Loading channels…"),
    "startup.system": ("正在初始化系统服务…", "正在初始化系統服務…", "Starting services…"),
    "startup.finishing": ("即将完成…", "即將完成…", "Finishing…"),
    "startup.starting": ("正在启动…", "正在啟動…", "Starting…"),

    # notify
    "notify.monitor_on_title": ("开始监控", "開始監控", "Monitoring started"),
    "notify.monitor_on_body": ("{name} 已开始监控", "{name} 已開始監控", "{name} is now monitored"),
    "notify.monitor_off_title": ("关闭监控", "關閉監控", "Monitoring stopped"),
    "notify.monitor_off_body": ("{name} 已关闭监控", "{name} 已關閉監控", "{name} monitoring stopped"),
    "notify.cut_disabled_title": ("切割不可用", "切割不可用", "Split unavailable"),
    "notify.cut_disabled_body": ("当前未处于录制中，无法切割", "目前未在錄製中，無法切割", "Not recording, cannot split"),
    "notify.cut_progress_title": ("切割中", "切割中", "Splitting"),
    "notify.cut_progress_body": ("{name} 正在切割当前录播文件", "{name} 正在切割目前錄播檔案", "{name} is splitting the recording"),
    "notify.cut_ok_title": ("切割成功", "切割成功", "Split complete"),
    "notify.cut_ok_body": ("{name} 切割完成：{file}", "{name} 切割完成：{file}", "{name} split complete: {file}"),
    "notify.cut_fail_title": ("切割失败", "切割失敗", "Split failed"),
    "notify.cut_fail_body": ("{name} 切割失败：{error}", "{name} 切割失敗：{error}", "{name} split failed: {error}"),
    "notify.delete_title": ("删除成功", "刪除成功", "Deleted"),
    "notify.delete_body": ("已删除 {name}", "已刪除 {name}", "Deleted {name}"),
    "notify.open_folder_fail_title": ("错误", "錯誤", "Error"),
    "notify.open_folder_fail_body": ("打开文件夹失败", "開啟資料夾失敗", "Failed to open folder"),
    "notify.add_ok_title": ("添加成功", "新增成功", "Added"),
    "notify.add_ok_body": ("已添加 {name}", "已新增 {name}", "Added {name}"),

    # add room
    "add_room.title": ("添加直播间", "新增直播間", "Add Channel"),
    "add_room.tip": ("请输入直播间号或完整链接", "請輸入直播間號或完整連結", "Enter a room ID or full URL"),
    "add_room.placeholder": (
        "例如：23058 或 https://live.bilibili.com/23058",
        "例如：23058 或 https://live.bilibili.com/23058",
        "e.g. 23058 or https://live.bilibili.com/23058",
    ),
    "add_room.confirm": ("添加", "新增", "Add"),
    "add_room.loading": ("获取中", "取得中", "Loading"),
    "add_room.error": (
        "获取直播间信息失败，请检查输入",
        "取得直播間資訊失敗，請檢查輸入",
        "Failed to fetch room info, check your input",
    ),

    # settings shell
    "settings.window_title": ("全局设置", "全域設定", "Settings"),
    "settings.badge": ("所有更改实时保存，立即生效", "所有變更即時儲存，立即生效", "All changes save instantly"),
    "settings.appearance": ("外观", "外觀", "Appearance"),
    "settings.appearance.language": ("语言", "語言", "Language"),
    "settings.appearance.language_desc": ("界面显示语言", "介面顯示語言", "Interface display language"),
    "settings.appearance.theme": ("主题", "主題", "Theme"),
    "settings.appearance.theme_desc": ("应用主题模式", "應用主題模式", "Application theme mode"),
    "settings.group.file_split": ("文件分割", "檔案分割", "File split"),
    "settings.group.network": ("网络", "網路", "Network"),
    "settings.group.stream": ("直播流录制", "直播流錄製", "Stream recording"),
    "settings.group.chat": ("聊天记录", "聊天紀錄", "Chat recording"),
    "settings.group.schedule": ("计划任务", "排程任務", "Schedule"),
    "settings.group.automation": ("自动化", "自動化", "Automation"),
    "settings.group.location": ("文件位置", "檔案位置", "File location"),
    "settings.group.convert": ("格式转换", "格式轉換", "Convert"),
    "settings.group.monitor": ("监控", "監控", "Monitor"),
    "settings.group.cover": ("封面", "封面", "Cover"),
    "settings.group.conditions": ("录制条件", "錄製條件", "Conditions"),
    "settings.group.notify": ("通知", "通知", "Notifications"),
    "settings.group.system": ("系统", "系統", "System"),

    # about
    "about.title": ("关于", "關於", "About"),
    "about.app_name": ("DD录播机", "DD錄播機", "DD Rec"),
    "about.version": ("当前版本:  v{version}", "目前版本:  v{version}", "Version:  v{version}"),
    "about.mode": ("运行模式:  {mode}", "執行模式:  {mode}", "Mode:  {mode}"),
    "about.mode_portable": ("Portable", "Portable", "Portable"),
    "about.mode_source": ("源码模式", "原始碼模式", "Source mode"),
    "about.mode_installed": ("非 Portable", "非 Portable", "Installed"),
    "about.logs": ("日志与诊断", "日誌與診斷", "Logs & diagnostics"),
    "about.logs_desc": (
        "后台日志用于排查录制、转换、弹幕和更新问题。",
        "後台日誌用於排查錄製、轉換、彈幕和更新問題。",
        "App logs help diagnose recording, convert, danmaku, and update issues.",
    ),
    "about.open_logs": ("打开日志", "開啟日誌", "Open logs"),
    "about.open_log_dir": ("打开日志文件夹", "開啟日誌資料夾", "Open log folder"),
    "about.open_log_dir_failed": (
        "无法打开日志文件夹: {error}",
        "無法開啟日誌資料夾: {error}",
        "Could not open log folder: {error}",
    ),
    "about.check_update": ("检查更新", "檢查更新", "Check for updates"),
    "about.checking": ("检查中...", "檢查中...", "Checking..."),

    # log viewer
    "log.title": ("日志查看器", "日誌檢視器", "Log viewer"),
    "log.search_placeholder": ("搜索日志…", "搜尋日誌…", "Search logs…"),
    "log.find_next": ("查找下一个", "尋找下一個", "Find next"),
    "log.refresh": ("刷新", "重新整理", "Refresh"),
    "log.copy_all": ("复制全部", "複製全部", "Copy all"),
    "log.close": ("关闭", "關閉", "Close"),
    "log.status_ready": ("就绪", "就緒", "Ready"),
    "log.status_lines": ("{count} 行", "{count} 行", "{count} lines"),
    "log.status_failed": ("读取失败", "讀取失敗", "Failed to read"),
    "log.filter_all": ("全部", "全部", "All"),
    "log.filter_info": ("INFO", "INFO", "INFO"),
    "log.filter_warning": ("WARNING", "WARNING", "WARNING"),
    "log.filter_error": ("ERROR", "ERROR", "ERROR"),
    "log.filter_crash": ("崩溃", "崩潰", "Crash"),
    "log.status_filtered": ("{level} · {count} 行", "{level} · {count} 行", "{level} · {count} lines"),
    "log.status_filtered_truncated": (
        "{level} · {count} 行（截断自 2MB 尾部）",
        "{level} · {count} 行（截斷自 2MB 尾部）",
        "{level} · {count} lines (from last 2MB)",
    ),

    # update dialog
    "update.title": ("发现新版本", "發現新版本", "Update available"),
    "update.current": ("当前版本", "目前版本", "Current version"),
    "update.latest": ("最新版本", "最新版本", "Latest version"),
    "update.notes": ("📋  更新内容", "📋  更新內容", "📋  Release notes"),
    "update.no_notes": ("(此版本没有提供更新说明)", "（此版本沒有提供更新說明）", "(No release notes for this version)"),
    "update.channel": ("下载通道", "下載通道", "Download channel"),
    "update.later": ("稍后再说", "稍後再說", "Later"),
    "update.now": ("立即更新", "立即更新", "Update now"),
    "update.launching": ("正在启动安装器…", "正在啟動安裝程式…", "Launching installer…"),
    "update.retry": ("重试", "重試", "Retry"),
    # settings full page
    "settings.action.confirm_reset": ("确认重置", "確認重設", "Confirm"),
    "settings.action.edit": ("编辑", "編輯", "Edit"),
    "settings.action.edit_template": ("编辑路径模板", "編輯路徑模板", "Edit path template"),
    "settings.action.reset": ("重置", "重設", "Reset"),
    "settings.automation.webhooks": ("Webhooks", "Webhooks", "Webhooks"),
    "settings.automation.webhooks_desc": ("录制事件通知的 Webhook 地址（每行一个）", "錄製事件通知的 Webhook 位址（每行一個）", "Webhook URLs for recording events (one per line)"),
    "settings.chat.credential": ("凭据", "憑證", "Credential"),
    "settings.chat.credential_desc": ("下载聊天消息使用的 SESSDATA（B站 cookie）", "下載聊天訊息使用的 SESSDATA（B站 cookie）", "SESSDATA cookie used for chat download"),
    "settings.chat.enabled": ("启用", "啟用", "Enable"),
    "settings.chat.enabled_desc": ("录制直播间弹幕和聊天消息", "錄製直播間彈幕和聊天訊息", "Record danmaku and chat messages"),
    "settings.chat.format": ("输出格式", "輸出格式", "Output format"),
    "settings.chat.format_desc": ("聊天记录保存格式", "聊天紀錄儲存格式", "Chat log save format"),
    "settings.common.disabled": ("禁用", "停用", "Disabled"),
    "settings.common.set": ("已设置", "已設定", "Set"),
    "settings.common.unset": ("未设置", "未設定", "Not set"),
    "settings.conditions.category": ("直播类别", "直播類別", "Category keywords"),
    "settings.conditions.category_desc": ("仅录制分区包含以下关键词的直播（多个用英文逗号分隔）", "僅錄製分區包含以下關鍵字的直播（多個以英文逗號分隔）", "Only record if category contains these keywords (comma-separated)"),
    "settings.conditions.hours": ("直播时段", "直播時段", "Time window"),
    "settings.conditions.hours_desc": ("仅在指定时段录制（格式: 08:00-23:00，留空不限）", "僅在指定時段錄製（格式: 08:00-23:00，留空不限）", "Only record in this window (08:00-23:00; empty = any)"),
    "settings.conditions.title": ("直播标题", "直播標題", "Title keywords"),
    "settings.conditions.title_desc": ("仅录制标题包含以下关键词的直播（多个用英文逗号分隔）", "僅錄製標題包含以下關鍵字的直播（多個以英文逗號分隔）", "Only record if title contains these keywords (comma-separated)"),
    "settings.convert.delete_source": ("删除原文件（遗留）", "刪除原檔（舊版）", "Delete source (legacy)"),
    "settings.convert.delete_source_desc": ("请改用「保留源文件」；此项仅兼容旧配置，新逻辑读 artifact_keep_source", "請改用「保留來源檔」；此項僅相容舊設定，新邏輯讀 artifact_keep_source", "Use Keep source instead; legacy only"),
    "settings.convert.enabled": ("启用转换（遗留）", "啟用轉換（舊版）", "Enable convert (legacy)"),
    "settings.convert.enabled_desc": ("已由「关闭文件后处理」接管；保留仅兼容旧配置", "已由「關閉檔案後處理」接管；保留僅相容舊設定", "Superseded by close-file post-processing; kept for old configs"),
    "settings.convert.format": ("目标格式", "目標格式", "Target format"),
    "settings.convert.format_desc": ("交付格式（当前固定 remux 为 mp4）", "交付格式（目前固定 remux 為 mp4）", "Delivery format (currently remux to mp4)"),
    "settings.cover.enabled": ("启用", "啟用", "Enable"),
    "settings.cover.enabled_desc": ("开播时自动下载直播封面图片", "開播時自動下載直播封面圖片", "Download cover image when live starts"),
    "settings.group.cookie": ("🔑 Cookie 凭据", "🔑 Cookie 憑證", "🔑 Cookie credentials"),
    "settings.location.dir": ("保存目录", "儲存目錄", "Save directory"),
    "settings.location.dir_desc": ("所有录制文件的根目录", "所有錄製檔案的根目錄", "Root directory for all recordings"),
    "settings.location.template": ("路径模板", "路徑模板", "Path template"),
    "settings.location.template_desc": ("点击按钮弹出编辑器修改 liquid 模板", "點擊按鈕彈出編輯器修改 liquid 模板", "Open editor to change the liquid path template"),
    "settings.monitor.concurrency": ("并发数", "並行數", "Concurrency"),
    "settings.monitor.concurrency_desc": ("同时轮询的房间数量上限", "同時輪詢的房間數量上限", "Max rooms polled at once"),
    "settings.monitor.debounce": ("防抖延迟", "防抖延遲", "Debounce"),
    "settings.monitor.debounce_desc": ("下播状态确认延迟，防止误触发", "下播狀態確認延遲，防止誤觸發", "Delay before confirming offline to avoid flicker"),
    "settings.monitor.delay": ("轮询延时", "輪詢延遲", "Poll delay"),
    "settings.monitor.delay_desc": ("每次轮询之间的等待时间", "每次輪詢之間的等待時間", "Wait between polling rounds"),
    "settings.monitor.interval": ("轮询间隔", "輪詢間隔", "Poll interval"),
    "settings.monitor.interval_desc": ("检查直播状态的时间间隔", "檢查直播狀態的時間間隔", "Interval for live-status checks"),
    "settings.monitor.proxy": ("监控代理", "監控代理", "Monitor proxy"),
    "settings.monitor.proxy_desc": ("专用于监控请求的代理地址", "專用於監控請求的代理位址", "Proxy used only for monitor requests"),
    "settings.network.bypass": ("绕过列表", "略過清單", "Bypass list"),
    "settings.network.bypass_desc": ("代理绕过规则，多个用逗号分隔", "代理略過規則，多個以逗號分隔", "Bypass rules, comma-separated"),
    "settings.network.proxy": ("全局代理", "全域代理", "Global proxy"),
    "settings.network.proxy_desc": ("HTTP/SOCKS5 代理地址与模式", "HTTP/SOCKS5 代理位址與模式", "HTTP/SOCKS5 proxy address and mode"),
    "settings.notify.body_tpl": ("正文模板", "正文模板", "Body template"),
    "settings.notify.body_tpl_desc": ("通知正文的模板（{uname}, {room_id}, {title}, {time}）", "通知正文的模板（{uname}, {room_id}, {title}, {time}）", "Notification body template ({uname}, {room_id}, {title}, {time})"),
    "settings.notify.enabled": ("启用通知", "啟用通知", "Enable notifications"),
    "settings.notify.enabled_desc": ("开播/下播/错误时发送通知", "開播/下播/錯誤時發送通知", "Notify on live start/end/errors"),
    "settings.notify.end": ("直播结束通知", "直播結束通知", "End-of-live notify"),
    "settings.notify.end_desc": ("直播结束时发送通知", "直播結束時發送通知", "Notify when live ends"),
    "settings.notify.error": ("错误通知", "錯誤通知", "Error notify"),
    "settings.notify.error_desc": ("发生录制错误时发送通知", "發生錄製錯誤時發送通知", "Notify on recording errors"),
    "settings.notify.title_tpl": ("标题模板", "標題模板", "Title template"),
    "settings.notify.title_tpl_desc": ("通知标题的模板（{uname}, {room_id}, {title}, {time}）", "通知標題的模板（{uname}, {room_id}, {title}, {time}）", "Notification title template ({uname}, {room_id}, {title}, {time})"),
    "settings.notify.url": ("通知地址", "通知位址", "Notify URL"),
    "settings.notify.url_desc": ("通知服务的 Webhook 地址", "通知服務的 Webhook 位址", "Webhook URL for notifications"),
    "settings.schedule.start": ("开始录制", "開始錄製", "Start after"),
    "settings.schedule.start_desc": ("仅在此时间后开始（HH:MM，留空不限）", "僅在此時間後開始（HH:MM，留空不限）", "Start only after this time (HH:MM, empty = any)"),
    "settings.schedule.stop": ("停止录制", "停止錄製", "Stop at"),
    "settings.schedule.stop_desc": ("到达此时间后停止（HH:MM，留空不限）", "到達此時間後停止（HH:MM，留空不限）", "Stop after this time (HH:MM, empty = any)"),
    "settings.schedule.timezone": ("时区", "時區", "Timezone"),
    "settings.schedule.timezone_desc": ("录制计划使用的时区", "錄製計劃使用的時區", "Timezone for recording schedule"),
    "settings.split.category": ("类别改变", "類別改變", "Category change"),
    "settings.split.category_desc": ("直播类别改变时自动切割", "直播類別改變時自動切割", "Split when category changes"),
    "settings.split.codec": ("编码改变", "編碼改變", "Codec change"),
    "settings.split.codec_desc": ("在编码改变处自动切割文件", "在編碼改變處自動切割檔案", "Split when codec changes"),
    "settings.split.discontinuity": ("流不连续", "串流不連續", "Stream discontinuity"),
    "settings.split.discontinuity_desc": ("在流不连续处自动切割文件", "在串流不連續處自動切割檔案", "Split on stream discontinuity"),
    "settings.split.duration": ("视频时长", "影片時長", "Duration"),
    "settings.split.duration_desc": ("留空或 00:00:00 表示不使用，默认 1 小时", "留空或 00:00:00 表示不使用，預設 1 小時", "Empty or 00:00:00 disables; default 1 hour"),
    "settings.split.size": ("文件大小", "檔案大小", "File size"),
    "settings.split.size_desc": ("输入字节数，留空或 0 表示不使用", "輸入位元組數，留空或 0 表示不使用", "Bytes; empty or 0 disables"),
    "settings.split.title": ("标题改变", "標題改變", "Title change"),
    "settings.split.title_desc": ("直播标题改变时自动切割", "直播標題改變時自動切割", "Split when title changes"),
    "settings.stream.audio_only": ("允许仅音频", "允許僅音訊", "Allow audio-only"),
    "settings.stream.audio_only_desc": ("允许录制仅音频的直播流", "允許錄製僅音訊的直播流", "Allow audio-only live streams"),
    "settings.stream.auto_switch": ("自动切换", "自動切換", "Auto switch"),
    "settings.stream.auto_switch_desc": ("有新画质或格式时自动切换流", "有新畫質或格式時自動切換串流", "Auto switch when better quality/format appears"),
    "settings.stream.bitrate": ("码率优先", "位元率優先", "Preferred bitrate"),
    "settings.stream.bitrate_desc": ("优先选择的码率", "優先選擇的位元率", "Preferred bitrate"),
    "settings.stream.codec": ("编码优先", "編碼優先", "Preferred codec"),
    "settings.stream.codec_desc": ("优先选择的编码（推荐 h264；av1 剪辑兼容较弱）", "優先選擇的編碼（建議 h264；av1 剪輯相容性較弱）", "Preferred codec (h264 recommended; av1 less editable)"),
    "settings.stream.enabled": ("启用录制", "啟用錄製", "Enable recording"),
    "settings.stream.enabled_desc": ("全局开关，关闭后所有房间停止录制", "全域開關，關閉後所有房間停止錄製", "Global switch; disables all rooms when off"),
    "settings.stream.fallback": ("流回退策略", "串流回退策略", "Fallback policy"),
    "settings.stream.fallback_desc": ("目标组合不存在时：compatible 同编码回退 / strict 不录 / best_effort 任意", "目標組合不存在時：compatible 同編碼回退 / strict 不錄 / best_effort 任意", "If target missing: compatible / strict / best_effort"),
    "settings.stream.format": ("格式优先", "格式優先", "Preferred format"),
    "settings.stream.format_desc": ("优先选择的封装格式（推荐 flv，配合原生录制管线）", "優先選擇的封裝格式（建議 flv，搭配原生錄製管線）", "Preferred container (flv recommended with native pipeline)"),
    "settings.stream.fps": ("帧率优先", "幀率優先", "Preferred FPS"),
    "settings.stream.fps_desc": ("优先选择的帧率", "優先選擇的幀率", "Preferred frame rate"),
    "settings.stream.keep_source": ("保留源文件", "保留來源檔", "Keep source file"),
    "settings.stream.keep_source_desc": ("后处理后保留 .capture 源文件（推荐）", "後處理後保留 .capture 來源檔（建議）", "Keep .capture source after post-processing"),
    "settings.stream.native_flv": ("原生 FLV 录制", "原生 FLV 錄製", "Native FLV capture"),
    "settings.stream.native_flv_desc": ("H.264+FLV 时使用关键帧/序列头管线（推荐开启）", "H.264+FLV 時使用關鍵幀/序列頭管線（建議開啟）", "Use keyframe/sequence-header pipeline for H.264+FLV"),
    "settings.stream.priority": ("流优先参数", "串流優先參數", "Priority field"),
    "settings.stream.priority_desc": ("优先级排序依据", "優先級排序依據", "What to prioritize when ranking streams"),
    "settings.stream.resolution": ("分辨率优先", "解析度優先", "Preferred resolution"),
    "settings.stream.resolution_desc": ("优先选择的分辨率", "優先選擇的解析度", "Preferred resolution"),
    "settings.stream.webhook_verified": ("仅验证后 Webhook", "僅驗證後 Webhook", "Webhook only if verified"),
    "settings.stream.webhook_verified_desc": ("FileClosed 仅在产物严格验证成功后发送", "FileClosed 僅在產物嚴格驗證成功後發送", "Send FileClosed only after strict artifact verification"),
    "settings.system.auto_start": ("开机自启", "開機自啟", "Launch at startup"),
    "settings.system.auto_start_desc": ("系统启动时自动运行本程序", "系統啟動時自動執行本程式", "Start this app with the system"),
    "settings.system.prevent_sleep": ("阻止休眠", "阻止休眠", "Prevent sleep"),
    "settings.system.prevent_sleep_desc": ("录制期间阻止系统进入休眠状态", "錄製期間阻止系統進入休眠狀態", "Prevent system sleep while recording"),

    # settings overlays
    "settings.overlay.size_title": ("按文件大小分割", "依檔案大小分割", "Split by file size"),
    "settings.overlay.enable": ("启用", "啟用", "Enable"),
    "settings.overlay.bytes_placeholder": ("输入字节数", "輸入位元組數", "Enter bytes"),
    "settings.overlay.bytes_unit": ("字节", "位元組", "bytes"),
    "settings.overlay.path_title": ("编辑路径模板", "編輯路徑模板", "Edit path template"),
    "settings.overlay.path_desc": ("修改 Liquid 路径模板后，点击保存立即生效。", "修改 Liquid 路徑模板後，點擊儲存立即生效。", "Edit the Liquid path template and save to apply."),
    "settings.overlay.path_placeholder": ("请输入路径模板", "請輸入路徑模板", "Enter path template"),
    "settings.overlay.show": ("显示", "顯示", "Show"),
    "settings.overlay.hide": ("隐藏", "隱藏", "Hide"),
    "settings.overlay.verify_cookie": ("验证 Cookie", "驗證 Cookie", "Verify cookie"),
    "settings.overlay.cookie_title": ("编辑 B站 Cookie 凭据", "編輯 B站 Cookie 憑證", "Edit Bilibili cookie"),
    "settings.overlay.cookie_hint": ("输入你的 SESSDATA（在浏览器 B站 cookie 中找到）", "輸入你的 SESSDATA（在瀏覽器 B站 cookie 中找到）", "Enter your SESSDATA from the Bilibili browser cookie"),
    "settings.overlay.cookie_placeholder": ("粘贴你的 SESSDATA …", "貼上你的 SESSDATA …", "Paste your SESSDATA…"),
    "settings.option.language.zh_cn": ("简体中文", "簡體中文", "Simplified Chinese"),
    "settings.option.language.zh_tw": ("繁體中文", "繁體中文", "Traditional Chinese"),
    "settings.option.language.en": ("English", "English", "English"),
    "settings.option.theme.dark": ("深色", "深色", "Dark"),
    "settings.option.theme.light": ("浅色", "淺色", "Light"),
    "settings.option.proxy.disabled": ("禁用", "停用", "Disabled"),
    "settings.option.proxy.system": ("系统", "系統", "System"),
    "settings.option.proxy.custom": ("自定义", "自訂", "Custom"),
    "settings.option.priority.resolution": ("分辨率", "解析度", "Resolution"),
    "settings.option.priority.fps": ("帧率", "幀率", "Frame rate"),
    "settings.option.priority.bitrate": ("码率", "位元率", "Bitrate"),
    "settings.option.priority.codec": ("编码", "編碼", "Codec"),
    "settings.option.priority.format": ("格式", "格式", "Format"),
    "settings.option.priority.url": ("网址", "網址", "URL"),
    "settings.option.resolution.original": ("原画", "原畫", "Original"),
    "settings.option.resolution.uhd": ("超清", "超清", "Ultra HD"),
    "settings.option.resolution.hd": ("高清", "高清", "HD"),
    "settings.option.resolution.smooth": ("流畅", "流暢", "Smooth"),
    "settings.option.fps.30": ("30 fps", "30 fps", "30 fps"),
    "settings.option.fps.60": ("60 fps", "60 fps", "60 fps"),
    "settings.option.fps.120": ("120 fps", "120 fps", "120 fps"),
    "settings.option.fps.25": ("25 fps", "25 fps", "25 fps"),
    "settings.option.fps.20": ("20 fps", "20 fps", "20 fps"),
    "settings.option.fps.15": ("15 fps", "15 fps", "15 fps"),
    "settings.option.codec.h264": ("h264", "h264", "h264"),
    "settings.option.codec.hevc": ("hevc", "hevc", "hevc"),
    "settings.option.codec.av1": ("av1", "av1", "av1"),
    "settings.option.format.mp4": ("mp4", "mp4", "mp4"),
    "settings.option.format.mkv": ("mkv", "mkv", "mkv"),
    "settings.option.format.ts": ("ts", "ts", "ts"),
    "settings.option.format.flv": ("flv", "flv", "flv"),
    "settings.option.format.fmp4": ("fmp4", "fmp4", "fmp4"),
    "settings.option.fallback.compatible": ("compatible", "compatible", "Compatible"),
    "settings.option.fallback.strict": ("strict", "strict", "Strict"),
    "settings.option.fallback.best_effort": ("best_effort", "best_effort", "Best effort"),
    "settings.option.chat.jsonl": ("jsonl 数据", "jsonl 資料", "JSONL data"),
    "settings.option.chat.xml": ("xml", "xml", "XML"),
    "settings.option.chat.ass": ("ass 弹幕", "ass 彈幕", "ASS danmaku"),
    "settings.option.timezone.utc": ("UTC", "UTC", "UTC"),
    "settings.option.timezone.shanghai": ("Asia/Shanghai", "Asia/Shanghai", "Asia/Shanghai"),
    "settings.option.timezone.tokyo": ("Asia/Tokyo", "Asia/Tokyo", "Asia/Tokyo"),
    "settings.option.timezone.new_york": ("America/New_York", "America/New_York", "America/New_York"),
    "settings.option.timezone.london": ("Europe/London", "Europe/London", "Europe/London"),
    "settings.option.duration.auto": ("自动", "自動", "Automatic"),
    "settings.option.duration.5_seconds": ("5 秒", "5 秒", "5 seconds"),
    "settings.option.duration.10_seconds": ("10 秒", "10 秒", "10 seconds"),
    "settings.option.duration.30_seconds": ("30 秒", "30 秒", "30 seconds"),
    "settings.option.duration.1_minute": ("1 分钟", "1 分鐘", "1 minute"),
    "settings.option.duration.3_minutes": ("3 分钟", "3 分鐘", "3 minutes"),
    "settings.option.duration.5_minutes": ("5 分钟", "5 分鐘", "5 minutes"),
    "settings.option.number.5": ("5", "5", "5"),
    "settings.option.number.10": ("10", "10", "10"),
    "settings.option.number.20": ("20", "20", "20"),
    "settings.option.number.50": ("50", "50", "50"),
    "settings.option.disabled": ("禁用", "停用", "Disabled"),
    "settings.option.inherit_global": ("继承全局", "繼承全域", "Inherit global"),
    "settings.overlay.not_verified": ("— 尚未验证 —", "— 尚未驗證 —", "— Not verified —"),
    "settings.overlay.verifying": ("验证中…", "驗證中…", "Verifying…"),
    "settings.overlay.cookie_empty": ("❌ 请先输入 SESSDATA", "❌ 請先輸入 SESSDATA", "❌ Enter SESSDATA first"),
    "settings.overlay.cookie_invalid": ("❌ Cookie 无效或已过期", "❌ Cookie 無效或已過期", "❌ Cookie invalid or expired"),
    "settings.overlay.network_error": ("❌ 网络错误：{error}", "❌ 網路錯誤：{error}", "❌ Network error: {error}"),
    "settings.room.cookie_desc": (
        "输入 B站 Cookie 以获取更高画质直播流",
        "輸入 B站 Cookie 以取得更高畫質直播流",
        "Enter Bilibili cookie for higher-quality streams",
    ),
    "settings.room.title": ("频道设置  —  {name}", "頻道設定  —  {name}", "Channel settings  —  {name}"),
    "settings.room.inherit_note": (
        "未修改的项目继承全局设定",
        "未修改的項目繼承全域設定",
        "Unchanged items inherit global settings",
    ),

}

STRINGS = {ZH_CN: {}, ZH_TW: {}, EN: {}}
for _key, (_zh_cn, _zh_tw, _en) in _ENTRIES.items():
    STRINGS[ZH_CN][_key] = _zh_cn
    STRINGS[ZH_TW][_key] = _zh_tw
    STRINGS[EN][_key] = _en


def normalize_language(value) -> str:
    """把配置里的语言值规范化为受支持的 code；未知/缺失一律回退简体中文。

    接受展示名（"简体中文"）或 code（"zh-CN"），未知值回退 DEFAULT_LANG。
    """
    if not isinstance(value, str):
        return DEFAULT_LANG
    if value in LANG_CODE_TO_DISPLAY:
        return value
    if value in LANG_DISPLAY:
        return LANG_DISPLAY[value]
    return DEFAULT_LANG


def t(key: str, **kwargs) -> str:
    """取当前语言的文案；缺失回退 zh-CN，再退化为 key。支持 {name} 占位。"""
    code = _current_code()
    text = STRINGS.get(code, {}).get(key)
    if text is None:
        text = STRINGS[ZH_CN].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


class LanguageManager(QObject):
    """进程级语言管理：保存当前 code，切换时发射 changed。"""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._code = DEFAULT_LANG

    @property
    def current_code(self) -> str:
        return self._code

    def set_language(self, value) -> str:
        """设置当前语言。接受展示名或 code；真实切换时发射 changed。"""
        code = normalize_language(value)
        if code == self._code:
            return code
        self._code = code
        self.changed.emit(code)
        return code


_manager = None


def _current_code() -> str:
    return _manager._code if _manager is not None else DEFAULT_LANG


def language_manager() -> LanguageManager:
    global _manager
    if _manager is None:
        from PySide6.QtWidgets import QApplication
        _manager = LanguageManager(parent=QApplication.instance())
    return _manager


def set_language(value) -> str:
    """便捷入口：设置进程语言。"""
    return language_manager().set_language(value)
