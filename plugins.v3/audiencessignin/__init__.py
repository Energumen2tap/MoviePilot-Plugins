"""
观众（Audiences）PT 站自动签到插件 —— MoviePilot V3

=== v1.5.2 变更（修复「把未签到误判成已签到」）===
v1.5.1 实跑日志报「✅ 今日已签到」，但用户去站点点签到却显示**刚签到成功**
→ 证明插件**根本没签到**，是被自身的兜底逻辑骗了。

本地实跑（Edge + 带登录态 profile，抓取真实页面）确诊两处根因：

根因 1 —— 兜底逻辑有害（v1.5.1 第 5 条）：
  原规则「登录态在 + 无未签到标记 → 视为已签到」的前提**是错的**。
  站点改版后人机验证改成「**点击签到按钮之后才弹出**」，因此未签到态页面上
  根本没有任何验证文案，而登录信息（收件箱/发件箱）却一应俱全
  → 兜底条件被满足 → 未签到被静默判成已签到，直接跳过签到动作。
  **本版删除该兜底**，改为明确返回 ``unsigned``（可观测、会去签到），
  无法识别时保守返回 ``unknown``。教训：兜底若长期命中就不再是兜底。

根因 2 —— 结构标记查错了地方：
  v1.5.1 打算「改用 CSS class 判定」，但把 class 串也塞进 ``_to_text()`` 的
  **纯文本**里查 —— 标签已被剥掉，所以那些串**永远匹配不到**。
  本版新增 ``_match_signed()`` 分三路判定：
    ① 结构标记（class 名）→ 查 **HTML 原文**
    ② 文案标记            → 查 **纯文本**
    ③ 正则模式（``已签到 +32``）→ 查纯文本

站点三态实测（2026-09-14 本地实跑定为基线）：
  ============  ==========================================================
  已签到         attendance-card--done / site-userbar__compact-tool--attended
                文案：今天已签到 / 今日已签到，明天再来吧 / 请勿重复刷新
  签到成功       attendance-card--success / attendance-page--success
                文案：签到成功 / 本次获得爆米花
  未签到        只有 attendance-page（**无修饰符**）→ 应执行签到
  ============  ==========================================================
  ⚠️ ``attendance-page`` 是基类，**三态都有**，不能单独用它判定。

新增状态 ``unsigned``：唯一需要真正执行签到动作的分支，走浏览器
（点击按钮 + 完成验证），因为纯 HTTP 无法产生按钮交互与验证 widget。

附带修正：``cdp.py cookies`` 取 HttpOnly Cookie 失败的问题
  ``Storage.getCookies`` 在 page target 上报
  ``browserContextId is only allowed for Browser target``，
  而原回退判断 ``if "error" in result`` 写法不对（CDP 错误在**顶层**，
  不在 ``result`` 里）→ 回退未触发 → 拿到空 Cookie → 纯 HTTP 403。
  正确做法：直接用 ``Network.getAllCookies``（实测可稳定取到
  ``cf_clearance``，len=597）。

=== v1.5.1 变更（修复 unknown 解析缺陷）===
v1.5.0 实跑出现「HTTP 200 但四组判定词全未命中」→ 状态 unknown，
且日志正文片段以 ``--> --> -->`` 一堆 HTML 注释残余符开头
（而此前实跑的正文片段开头是干净的「首 页 论 坛 影 视…」）。
根因：``_to_text()`` 用的 ``<[^>]+>`` 在遇到 HTML 注释时会**在第一个 ``>``
处提前截断**（注释正文可能含 ``>``，例如 ``-->``、``=>``），于是
① 注释残余符留在文本里污染判定；② 注释**内部的文字**被当成正文保留。
修复与增强：
1. ``_to_text()`` 改为**先删注释**再剥标签，并清理界面箭头等实体；
2. 探测未识别时打印「正文长度 / HTML 长度 / 前段 / 中段 / 尾段」，
   不再只给前 200 字（站点把签到区放在页面中后部时看不到关键内容）；
3. 状态探测 API 支持 ``?full=1`` 返回页面纯文本全文，便于排障；
4. 防御性兜底：登录态正常且页面无任何未签到标记 → 判为已签到
   （把「误报失败」降级为「多发一次浏览器确认」）。

=== v1.5.0 变更（修复判定词误报）===
v1.4.0 实跑把**已经签到的成功页**判成了 need_verify，
白等 60 秒后报失败。根因：``NEED_VERIFY_MARKERS`` 里的词是站点页面的
**静态说明文案**（签到奖励规则区块附近），只要页面渲染出来就会被读到。
修复：新增 ``_looks_like_need_verify()``，判定需要「登录态反证」——
必须同时「不含已签到标记」且「不含登录用户信息」才算真的需要验证。
另修正页面文本读取顺序（优先整份 HTML 而非可见文本，避免无头模式下
窗口尺寸导致读取不稳定），并在证据日志中打印命中词前后文。

=== v1.2.0 变更 ===
1. 新增「立即运行一次」按钮（插件设置页），点一下当场执行，
   不必再跳到「设定 -> 服务」页手动触发。
2. 日志改为「分节 + 结论先行」结构，一眼即可判断执行是否正常：
   * 执行头：触发方式 / 开始时间 / 站点 / 浏览器配置；
   * 每站按 [步骤 1/3] [步骤 2/3] [步骤 3/3] 打印，每步都带明确结果；
   * 每站一行「结论：✅ 成功 / ✅ 今日已签到 / ❌ 失败 —— 原因」；
   * 结尾统一打印「执行结果总览」+ 总结论；
   * 浏览器等待期间每 15 秒输出心跳，避免静默让人以为卡死。
3. 作者名由占位值「本地自建」改为 GitHub 名 Energumen2tap。

=== v1.1.0 重要变更（基于真实站点实测）===
本站签到已改为「人机验证门控」：未签到时 attendance.php 会渲染
「完成人机验证即可领取今日爆米花奖励 / 验证通过后将自动完成签到」，
必须由真实浏览器执行验证后站点才记账。纯 HTTP 请求（即使带 cf_clearance
能拿到 HTTP 200）也无法完成签到。

实测数据（2026-09-13，本机带登录态）：
  * 带完整 Cookie（含 cf_clearance）纯 HTTP GET attendance.php → HTTP 200，
    能正确读到「今日已签到 / 今天已签到 / 您今天已经签到过了，请勿重复刷新」；
    不带 Cookie → HTTP 403 且 cf-mitigated: challenge。
  * 未签到状态下同一请求只能拿到「人机验证」页面，不会产生签到记录。
  * 真实浏览器加载 attendance.php 后会自行通过验证并完成签到，
    签到后页面头部由「签到」变为「已签到 +<奖励>」。

因此本插件采用两段式策略：
  第 1 段 HTTP 轻量探测：判断今天是否已签到、Cookie 是否失效、是否被 Cloudflare 拦住。
  第 2 段 真实浏览器：仅在「需要人机验证」或「被 Cloudflare 拦住」时启用，
      通过宿主稳定 SDK app.sdk.browser.launch_browser_context 驱动 CloakBrowser 完成验证。

=== v1.1.0 修正的缺陷（v1.0.0）===
v1.0.0 的 SUCCESS_KEYWORDS 含「签到获得」，而签到页**常驻**文案
「首次签到获得 20 粒爆米花」恒在，导致任何情况下都误判为签到成功。
v1.1.0 改为基于实测文案的精确判定，并把「已签到」判定置于成功判定之前。

合规性说明（对照官方《插件开发指南（V3）》）：
- 只从 app.sdk.*、app.schemas.*、app.db.oper.* 等文档明确许可的入口导入宿主能力。
  浏览器能力走 app.sdk.browser（官方指南第 248 行列入稳定 SDK）。
- 不在模块导入期或类定义期访问网络 / 数据库 / 启动浏览器；
  浏览器等重资源在方法内部延迟导入，并在 finally 中释放。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger

# ---------------------------------------------------------------- 常量定义

#: 默认签到站点（观众）
DEFAULT_SITES = "audiences.me"

#: 默认签到页相对路径（NexusPHP 标准打卡页）
DEFAULT_ATTENDANCE_PATH = "attendance.php"

#: 判定「今天已经签过」的**结构标记**（HTML class 名，实测原文）。
#:
#: ⚠️ v1.5.2 关键修正：类名只存在于 **HTML 原文**，`_to_text()` 会把标签剥掉，
#: 所以在**纯文本**里查这些串**永远失败** —— 必须拿 HTML 原文查。
#: 这是 v1.5.1「改用类名判定」失败的原因。
#:
#: 站点三态实测对照（2026-09-14 本地实跑）：
#:   已签到   → attendance-card--done / site-userbar__compact-tool--attended
#:   签到成功 → attendance-card--success / attendance-page--success
#:   未签到   → 只有 attendance-page（无修饰符）
#: 因此 **attendance-page 这个基类三态都有，绝不能用它单独判定**。
SIGNED_STRUCTURE_MARKERS = (
    "attendance-page--success",              # 成功态的页面级修饰符
    "attendance-card--success",              # 成功态的卡片修饰符
    "attendance-card--done",                 # 已签到态的卡片修饰符
    "attendance-card__icon--done",           # 已签到态的图标修饰符
    "site-userbar__compact-tool--attended",  # 顶部栏「已签到」小标签
)

#: 判定「未签到」的**结构标记**（HTML class 名，实测原文）。
#: 只要出现其中之一，就说明页面是签到页的**初始态**，应当去执行签到。
ATTENDANCE_PAGE_MARKER = "attendance-page"

#: 判定「今天已经签过」的**文案**（实测原文，来自真实页面）。
#:
#: ⚠️ 只能拿 `_to_text()` 的**纯文本**结果查这些串（不是 HTML 原文）。
#: 已签到态实测命中的组合：
#:   顶部栏        已签到 +32
#:   hero 副标题   今日已签到，明天再来吧
#:   卡片标题      今天已签到
#:   卡片说明      您今天已经签到过了，请勿重复刷新。
#: 签到成功态实测命中：
#:   卡片标题      签到成功
#:   统计标签      本次获得爆米花
SIGNED_MARKERS = (
    "今日已签到",
    "今天已签到",
    "您今天已经签到过了",
    "已经签到过了",
    "已经签到过",
    "请勿重复刷新",
    "签到成功",
    "打卡成功",
)

#: 判定「今天已经签过」的**正则模式**（在纯文本上匹配）。
#:
#: 顶部栏渲染为 `<strong>已签到</strong><span>+32</span>`，转纯文本后是
#: 「已签到 +32」。单看「已签到」二字过于宽泛（可能出现在按钮上），
#: 所以要求后面紧跟 `+数字` 才认定为「已签到奖励」。
SIGNED_REGEX_MARKERS = (
    r"已签到\s*\+\s*\d+",
    r"本次获得\s*\+\s*\d+",
)

#: 判定「需要人机验证」的文案（实测原文）
#:
#: ⚠️ 注意（v1.5.0 实测教训）：这三个词是站点**页面上的静态说明文案**
#: （签到奖励规则区块附近），只要页面渲染出来就可能被 `inner_text` 读到。
#: 因此**不能**单独作为 need_verify 的依据 —— 必须配合「页面不含登录用户信息」
#: 才成立（见 `_looks_like_need_verify`）。
NEED_VERIFY_MARKERS = (
    "人机验证",
    "验证通过后将自动完成签到",
    "完成人机验证即可",
)

#: 登录用户已登录的**强特征**：页面头部渲染了用户名/分享率等个人信息。
#: 这些内容只有通过鉴权的会话才会出现，是「登录态确实生效」的最可靠证据。
#: 一旦命中，即可断定当前不是「未登录 → 被要求验证」的场景。
LOGGED_IN_MARKERS = (
    "分享率",
    "收件箱",
    "发件箱",
    "控制面板",
    "我的用户名",
)

#: 判定 Cookie 失效的强特征
LOGIN_MARKERS = (
    "takelogin.php",
    "请先登录",
    "请登录后",
    "您尚未登录",
    "你尚未登录",
)

#: 判定被 Cloudflare 质询的特征（小写比较）
CF_MARKERS = (
    "just a moment",
    "cf-challenge",
    "challenge-platform",
    "__cf_chl",
    "cf_chl_opt",
    "checking your browser",
    "enable javascript and cookies",
)

#: 历史记录最多保留条数
MAX_HISTORY = 20

#: 浏览器模式
BROWSER_MODE_HEADLESS = "headless"
BROWSER_MODE_HEADED = "headed"
BROWSER_MODE_AUTO = "auto"


class AudiencesSignIn(_PluginBase):
    """观众站点自动签到插件。"""

    plugin_name = "观众站点签到"
    plugin_desc = (
        "自动完成观众（Audiences）等 NexusPHP 站点的每日签到。"
        "先以 HTTP 探测状态，遇到人机验证时自动调用宿主浏览器通过验证，"
        "支持定时执行、手动触发、结果通知与历史记录。"
    )
    plugin_icon = "audiencessignin.png"
    plugin_version = "1.5.2"
    plugin_author = "Energumen2tap"
    author_url = "https://github.com/Energumen2tap"
    plugin_config_prefix = "audiencessignin_"
    plugin_order = 50
    auth_level = 1

    # ------------------------------------------------------------ 运行状态
    _enabled: bool = False
    _notify: bool = True
    _cron: str = ""
    _sites: List[str] = []
    _attendance_path: str = DEFAULT_ATTENDANCE_PATH
    _cookie_override: str = ""
    _ua_override: str = ""
    _retry: int = 1
    _skip_if_done: bool = True
    _use_browser: bool = True
    _browser_mode: str = BROWSER_MODE_AUTO
    _browser_wait: int = 60
    _forward_ua: bool = True

    def __init__(self) -> None:
        """初始化，保持与宿主基类一致。"""
        super().__init__()

    # ------------------------------------------------------------ 生命周期

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """读取配置并建立本次运行状态，允许重复调用。"""
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._notify = bool(config.get("notify", True))
        self._cron = str(config.get("cron") or "").strip()
        self._sites = self._parse_sites(config.get("sites"))
        self._attendance_path = (
            str(config.get("attendance_path") or "").strip() or DEFAULT_ATTENDANCE_PATH
        )
        self._cookie_override = str(config.get("cookie") or "").strip()
        self._ua_override = str(config.get("ua") or "").strip()
        self._use_browser = bool(config.get("use_browser", True))
        self._forward_ua = bool(config.get("forward_ua", True))
        mode = str(config.get("browser_mode") or "").strip().lower()
        self._browser_mode = (
            mode
            if mode in (BROWSER_MODE_HEADLESS, BROWSER_MODE_HEADED, BROWSER_MODE_AUTO)
            else BROWSER_MODE_AUTO
        )
        try:
            self._retry = max(1, int(config.get("retry") or 1))
        except (TypeError, ValueError):
            self._retry = 1
        try:
            self._browser_wait = max(15, int(config.get("browser_wait") or 60))
        except (TypeError, ValueError):
            self._browser_wait = 60
        self._skip_if_done = bool(config.get("skip_if_done", True))

    def get_state(self) -> bool:
        """返回插件是否启用。"""
        return self._enabled

    def stop_service(self) -> None:
        """释放插件资源。本插件不持有常驻线程，仅复位运行标记。"""
        self._enabled = False

    # ------------------------------------------------------------ 定时服务

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时签到服务；cron 非法时不注册，避免影响宿主调度器。"""
        if not self.get_state():
            return []
        if not self._cron:
            logger.warning("【观众签到】未配置签到时间（cron），已跳过定时任务注册")
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except Exception as err:  # noqa: BLE001 - cron 解析失败属于用户配置问题
            logger.error(f"【观众签到】cron 表达式无效：{self._cron}，{err}")
            return []
        return [
            {
                "id": "AudiencesSignIn.SignIn",
                "name": "观众站点签到",
                "trigger": trigger,
                "func": self.sign_in_all,
                "kwargs": {},
            }
        ]

    # ------------------------------------------------------------ 远程命令

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """注册远程命令，可通过消息渠道发送 /audiences_signin 触发签到。"""
        return [
            {
                "cmd": "/audiences_signin",
                "event": EventType.PluginAction,
                "desc": "观众站点签到",
                "category": "插件命令",
                "data": {"action": "audiences_signin"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def on_plugin_action(self, event: Event) -> None:
        """响应属于本插件的远程命令。"""
        payload = getattr(event, "event_data", None) or {}
        if payload.get("action") != "audiences_signin":
            return
        self.sign_in_all()

    # ------------------------------------------------------------ 插件 API

    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 API：立即签到、状态探测与历史查询。"""
        return [
            {
                "path": "/run",
                "endpoint": self.run_now,
                "methods": ["GET", "POST"],
                "auth": "bear",
                "summary": "立即执行签到",
                "description": "对所有已配置站点立即执行一次签到，并返回本次结果摘要。",
            },
            {
                "path": "/state",
                "endpoint": self.query_state,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "探测签到状态",
                "description": ("仅用 HTTP 探测各站点当前签到状态，不执行签到、不启动浏览器。"
                                "追加 ?full=1 可额外返回页面纯文本全文，用于排查判定词是否失效。"),
            },
            {
                "path": "/history",
                "endpoint": self.get_history,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "查询签到历史",
                "description": "返回最近若干次签到记录。",
            },
        ]

    def run_now(self) -> Dict[str, Any]:
        """插件 API：立即签到。

        供前端「立即运行一次」按钮与外部脚本调用。
        无论成功失败都返回结构化结果，便于前端直接弹窗展示；
        异常也在此处兜住，避免前端收到 500 而无法给出提示。
        """
        try:
            results = self.sign_in_all(trigger="api")
        except Exception as err:  # noqa: BLE001 - 保证前端总能拿到可读结果
            logger.error(f"【观众签到】立即运行异常：{err}")
            return {"success": False, "results": [], "message": f"执行异常：{err}"}

        if not results:
            return {
                "success": False,
                "results": [],
                "message": "未配置签到站点，未执行任何操作",
            }

        failed = [item for item in results if not item.get("success")]
        return {
            "success": not failed,
            "results": results,
            "message": (
                f"存在 {len(failed)} 个失败项" if failed
                else "全部站点执行成功"
            ),
        }

    def query_state(self, full: str = "") -> Dict[str, Any]:
        """插件 API：只探测状态，不做任何写操作。

        参数 ``full``（v1.5.1 新增）：传任意真值（如 ``?full=1``）时，
        额外返回**页面纯文本全文**，用于诊断判定词是否与站点当前文案对得上。
        返回的 ``body`` 可能较长，仅在排障时使用。
        """
        want_full = str(full or "").strip().lower() not in ("", "0", "false", "no")
        states: List[Dict[str, Any]] = []
        for domain in self._sites:
            site = self._get_site(domain)
            cookie = self._cookie_override or self._attr(site, "cookie")
            ua = self._ua_override or self._attr(site, "ua")
            if not cookie:
                states.append({"site": domain, "state": "no_cookie"})
                continue
            target_url = f"{self._site_url(site, domain)}/{self._attendance_path.lstrip('/')}"
            state, detail, body = self._probe_state(target_url, cookie, ua, site)
            item: Dict[str, Any] = {
                "site": domain,
                "state": state,
                "detail": detail,
                "body_length": len(body or ""),
            }
            if want_full:
                item["body"] = body or ""
            states.append(item)
        return {"states": states}

    def get_history(self) -> Dict[str, Any]:
        """插件 API：返回历史记录。"""
        return {"history": self._history()}

    # ------------------------------------------------------------ 配置页面

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回 Vuetify 配置页面与默认配置。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "notify", "label": "发送签到通知"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "skip_if_done",
                                            "label": "当天已成功则跳过",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "retry",
                                            "label": "失败重试次数",
                                            "placeholder": "1",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "sites",
                                            "label": "签到站点域名",
                                            "placeholder": "audiences.me，多个用英文逗号分隔",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "签到时间（cron）",
                                            "placeholder": "0 8 * * * 表示每天 08:00",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "attendance_path",
                                            "label": "签到页路径",
                                            "placeholder": "attendance.php",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "use_browser",
                                            "label": "允许调用浏览器过人机验证",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "browser_mode",
                                            "label": "浏览器模式",
                                            "items": [
                                                {"title": "自动（先无头，失败改有头）",
                                                 "value": "auto"},
                                                {"title": "仅无头", "value": "headless"},
                                                {"title": "仅有头", "value": "headed"},
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "browser_wait",
                                            "label": "等待验证完成秒数",
                                            "placeholder": "60",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "forward_ua",
                                            "label": "强制用站点登记的 UA 打开浏览器",
                                            "hint": (
                                                "推荐开启。NexusPHP 会话 Cookie 与 "
                                                "cf_clearance 都绑定 UA，浏览器自带指纹 UA "
                                                "与登记 UA 不一致时，会话会被判无效，"
                                                "表现为一直卡在人机验证页。"
                                            ),
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "density": "compact",
                                            "text": (
                                                "签到日志中的「浏览器 UA 与站点登记 UA 不一致」"
                                                "告警出现时，务必开启此项。"
                                            ),
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cookie",
                                            "label": "Cookie 覆盖（可选）",
                                            "placeholder": "留空则使用 MoviePilot 站点管理中已配置的 Cookie",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "ua",
                                            "label": "User-Agent 覆盖（可选）",
                                            "placeholder": "留空则使用站点管理中记录的 UA",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "color": "primary",
                                            "variant": "tonal",
                                            "block": True,
                                            "prepend-icon": "mdi-play-circle-outline",
                                            "onclick": (
                                                "function(e) {"
                                                "  var m = '（日志中查看执行详情）';"
                                                "  window.MoviePilotAPI.post('plugin/AudiencesSignIn/run', {})"
                                                "    .then(function(r) {"
                                                "      var res = (r && r.results) || [];"
                                                "      var lines = res.map(function(i) {"
                                                "        return (i.success ? '[OK] ' : '[FAIL] ') + i.site + ': ' + i.message;"
                                                "      });"
                                                "      alert('已执行一次签到\\n\\n' + (lines.length ? lines.join('\\n') : m)"
                                                "        + '\\n\\n详细过程见日志（筛选：观众签到）');"
                                                "    })"
                                                "    .catch(function(err) {"
                                                "      console.error(err);"
                                                "      alert('执行失败，详见日志\\n' + err);"
                                                "    });"
                                                "}"
                                            ),
                                        },
                                        "text": "立即运行一次",
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "density": "compact",
                                            "text": "点击上方按钮立即执行一次签到，结果会弹出提示，"
                                                    "并写入日志（设定 -> 日志，筛选「观众签到」）"
                                                    "——日志中有分步进度与总结论，可据此判断是否正常。",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "本站签到需要人机验证，纯 HTTP 请求无法完成签到。"
                                                    "插件先用 HTTP 探测状态，仅在未签到时启动浏览器完成验证。"
                                                    "签到使用 MoviePilot「站点管理」中观众站点的 Cookie 与 User-Agent。",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "warning",
                                            "variant": "tonal",
                                            "text": "若提示「Cookie 已失效」，请到「站点管理」更新观众站 Cookie；"
                                                    "若提示浏览器不可用，请确认宿主已启用站点浏览器仿真"
                                                    "（设定 -> 系统 -> 站点设置 -> 浏览器仿真）。",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "skip_if_done": True,
            "retry": 1,
            "sites": DEFAULT_SITES,
            "cron": "0 8 * * *",
            "attendance_path": DEFAULT_ATTENDANCE_PATH,
            "use_browser": True,
            "browser_mode": BROWSER_MODE_AUTO,
            "browser_wait": 60,
            "forward_ua": True,
            "cookie": "",
            "ua": "",
        }

    def get_page(self) -> List[dict]:
        """返回插件详情页：当前配置概览与最近签到记录。"""
        history = self._history()
        mode_text = {
            BROWSER_MODE_AUTO: "自动（先无头，失败改有头）",
            BROWSER_MODE_HEADLESS: "仅无头",
            BROWSER_MODE_HEADED: "仅有头",
        }.get(self._browser_mode, self._browser_mode)
        content: List[dict] = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info" if self._enabled else "warning",
                    "variant": "tonal",
                    "text": f"插件状态：{'已启用' if self._enabled else '未启用'}；"
                            f"签到站点：{'、'.join(self._sites) if self._sites else '未配置'}；"
                            f"签到时间：{self._cron or '未配置'}；"
                            f"浏览器：{'允许' if self._use_browser else '禁用'}（{mode_text}）",
                },
            }
        ]
        if not history:
            content.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "text": "暂无签到记录。可在插件设置页点击「立即运行一次」，"
                                "或到「设定 -> 服务」手动执行，也可等待定时任务触发。",
                    },
                }
            )
        else:
            for record in history[:10]:
                content.append(
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "success" if record.get("success") else "error",
                            "variant": "tonal",
                            "text": f"[{record.get('time', '')}] {record.get('site', '')}："
                                    f"{record.get('message', '')}",
                        },
                    }
                )
        return content

    # ------------------------------------------------------------ 核心逻辑

    def sign_in_all(self, trigger: str = "schedule") -> List[Dict[str, Any]]:
        """对所有配置站点执行签到，返回本次结果列表。

        日志采用「分节 + 结论先行」结构，便于从日志一眼判断执行是否正常：
        开头打印执行头，每站打印分节与结论行，结束打印总览结论。
        """
        if not self._sites:
            logger.warning("【观众签到】未配置签到站点，已跳过执行")
            return []

        trigger_text = {
            "schedule": "定时任务",
            "api": "手动触发（立即运行）",
            "command": "远程命令",
        }.get(trigger, trigger)

        logger.info("=" * 52)
        logger.info("【观众签到】开始执行")
        logger.info(f"【观众签到】触发方式：{trigger_text}")
        logger.info(f"【观众签到】开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"【观众签到】签到站点：{'、'.join(self._sites)}")
        logger.info(f"【观众签到】浏览器调用：{'允许' if self._use_browser else '禁用'}"
                    f"（模式 {self._browser_mode}，等待 {self._browser_wait}s，"
                    f"UA 跟随站点：{'开' if self._forward_ua else '关'}）")
        logger.info("=" * 52)

        results: List[Dict[str, Any]] = []
        for index, domain in enumerate(self._sites, start=1):
            logger.info(f"----- 第 {index}/{len(self._sites)} 个站点：{domain} -----")
            results.append(self.sign_in_one(domain))

        # ------- 总览结论：这是"一眼判断"的核心 -------
        logger.info("=" * 52)
        logger.info("【观众签到】执行结果总览")
        for item in results:
            mark = "✅ 成功" if item.get("success") else "❌ 失败"
            logger.info(
                f"【观众签到】  {mark} | {item.get('site')} | "
                f"{item.get('status')} | {item.get('message')}"
            )

        failed = [item for item in results if not item.get("success")]
        signed = [item for item in results if item.get("status") == "success"]
        already = [item for item in results if item.get("status") == "already"]

        logger.info("-" * 52)
        if failed:
            logger.error(
                f"【观众签到】结论：本次执行存在 {len(failed)} 个失败项，"
                f"共处理 {len(results)} 个站点"
            )
        elif signed:
            logger.info(
                f"【观众签到】结论：✅ 签到成功 {len(signed)} 个站点"
                + (f"，另有 {len(already)} 个站点此前已签到" if already else "")
            )
        elif already:
            logger.info(
                f"【观众签到】结论：✅ 今日已签到（{len(already)} 个站点），无需重复签到"
            )
        else:
            logger.info(f"【观众签到】结论：本次执行完成，共处理 {len(results)} 个站点")
        logger.info(f"【观众签到】结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 52)

        if self._notify:
            self._post_result(results)

        if results:
            self._append_history(results)
        return results

    def sign_in_one(self, domain: str) -> Dict[str, Any]:
        """对单个站点执行签到，返回结果字典。

        日志按 [1/3] [2/3] [3/3] 分步打印，每步都带明确结果，便于定位卡在哪一环。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self._skip_if_done and self._signed_today(domain):
            message = "今天已签到成功，已跳过"
            logger.info(f"【观众签到】[{domain}] 步骤 1/3：检查本地历史 → 今日已有成功记录")
            logger.info(f"【观众签到】[{domain}] 结论：⏭️ {message}")
            return {"site": domain, "success": True, "status": "skipped",
                    "message": message, "time": now}

        site = self._get_site(domain)
        cookie = self._cookie_override or self._attr(site, "cookie")
        ua = self._ua_override or self._attr(site, "ua")
        site_url = self._site_url(site, domain)

        logger.info(f"【观众签到】[{domain}] 步骤 1/3：读取站点配置")
        if site is None:
            logger.warning(
                f"【观众签到】[{domain}] 步骤 1/3 提示："
                f"未能从「站点管理」读取到该站点记录（站点可能未添加或域名不匹配）"
            )
        if not cookie:
            message = ("未取到站点 Cookie：请在 MoviePilot「站点管理」中添加/更新该站点，"
                       "或在插件中填写 Cookie 覆盖")
            if site is None:
                message = ("未能读取站点配置：请确认已在 MoviePilot「站点管理」中添加 "
                           f"{domain}，且域名与该站点登记的域名一致")
            logger.error(f"【观众签到】[{domain}] 步骤 1/3 失败：{message}")
            logger.error(f"【观众签到】[{domain}] 结论：❌ {message}")
            return {"site": domain, "success": False, "status": "no_cookie",
                    "message": message, "time": now}
        logger.info(
            f"【观众签到】[{domain}] 步骤 1/3 完成：Cookie 已取到（{len(cookie)} 字符）"
            f"，UA {'已' if ua else '未'}提供"
        )

        target_url = f"{site_url}/{self._attendance_path.lstrip('/')}"

        last_message = "签到未成功"
        for attempt in range(1, self._retry + 1):
            if self._retry > 1:
                logger.info(f"【观众签到】[{domain}] 步骤 2/3：执行签到（第 {attempt}/{self._retry} 次尝试）")
            else:
                logger.info(f"【观众签到】[{domain}] 步骤 2/3：执行签到")

            status, message = self._attempt_once(target_url, cookie, ua, site)

            if status in ("success", "already"):
                logger.info(f"【观众签到】[{domain}] 步骤 2/3 完成：{message}")
                logger.info(f"【观众签到】[{domain}] 步骤 3/3：写入历史记录")
                if status == "success":
                    logger.info(f"【观众签到】[{domain}] 结论：✅ 签到成功 —— {message}")
                else:
                    logger.info(f"【观众签到】[{domain}] 结论：✅ 今日已签到 —— {message}")
                return {"site": domain, "success": True, "status": status,
                        "message": message, "time": now}

            last_message = message
            logger.warning(f"【观众签到】[{domain}] 步骤 2/3 第 {attempt} 次尝试失败：{message}")

        logger.error(f"【观众签到】[{domain}] 结论：❌ 签到未成功 —— {last_message}")
        return {"site": domain, "success": False, "status": "failed",
                "message": last_message, "time": now}

    def _attempt_once(
        self,
        target_url: str,
        cookie: str,
        ua: str,
        site: Any,
    ) -> Tuple[str, str]:
        """执行一次完整签到尝试：先 HTTP 探测，必要时再动用浏览器。"""
        logger.info(f"【观众签到】  HTTP 探测签到状态：{target_url}")
        state, detail, body = self._probe_state(target_url, cookie, ua, site)
        logger.info(f"【观众签到】  HTTP 探测结果：{state}（{detail}）")

        if state == "signed":
            reward = self._extract_reward(body)
            logger.info("【观众签到】  判定：页面显示已签到，无需重复操作")
            return "already", f"今天已经签到过了{reward}"

        if state == "login":
            logger.warning("【观众签到】  判定：Cookie 已失效，页面要求登录")
            return "failed", "Cookie 已失效（页面要求登录），请在站点管理中更新 Cookie"

        if state == "need_verify":
            logger.info("【观众签到】  判定：站点要求人机验证，纯 HTTP 无法完成，需要浏览器")
            if not self._use_browser:
                logger.error("【观众签到】  但「允许调用浏览器过人机验证」为关闭状态，无法继续")
                return ("failed",
                        "站点要求人机验证，但插件已禁用浏览器调用；请在插件配置中开启"
                        "「允许调用浏览器过人机验证」")
            return self._sign_in_with_browser(target_url, cookie, ua, site)

        if state == "cloudflare":
            logger.info("【观众签到】  判定：被 Cloudflare 质询拦截，改用浏览器处理")
            if not self._use_browser:
                logger.error("【观众签到】  但「允许调用浏览器过人机验证」为关闭状态，无法继续")
                return ("failed",
                        "被 Cloudflare 质询拦截且插件已禁用浏览器；"
                        "请更新站点 Cookie（建议包含 cf_clearance）或开启浏览器调用")
            return self._sign_in_with_browser(target_url, cookie, ua, site)

        if state == "unsigned":
            # v1.5.2 新增：明确识别出「签到页初始态，尚未签到」。
            # 这是**唯一需要真正执行签到动作**的分支。
            #
            # 为什么必须走浏览器：站点的人机验证是「点击签到按钮后才弹出」的，
            # 纯 HTTP 请求拿不到按钮交互，也无法完成验证 widget。
            # （用户实测确认：「他是点签到，然后自动开始验证。验证完之后才会显示签到成功。」）
            logger.info("【观众签到】  判定：签到页初始态，今日尚未签到，需要执行签到动作")
            if not self._use_browser:
                logger.error("【观众签到】  但「允许调用浏览器过人机验证」为关闭状态，无法继续")
                return ("failed",
                        "检测到今日尚未签到，但插件已禁用浏览器调用；"
                        "签到需点击按钮并完成人机验证，请开启「允许调用浏览器过人机验证」")
            return self._sign_in_with_browser(target_url, cookie, ua, site)

        logger.warning(f"【观众签到】  判定：状态无法识别 —— {detail}")
        return "failed", detail

    # ------------------------------------------------------------ HTTP 探测

    def _probe_state(
        self,
        target_url: str,
        cookie: str,
        ua: str,
        site: Any,
    ) -> Tuple[str, str, str]:
        """用宿主 HTTP 栈读取签到页并判定状态，返回 (状态, 说明, 页面文本)。

        状态取值：
          signed      —— 今日已签到（结构标记或文案命中）
          unsigned    —— 签到页初始态、今日尚未签到，**需要执行签到动作**（v1.5.2 新增）
          need_verify —— 页面明确要求人机验证
          login       —— Cookie 失效
          cloudflare  —— 被 Cloudflare 质询
          unknown     —— 结果无法识别（保守返回，**绝不假装成功**）
        """
        try:
            from app.sdk.network import RequestUtils, SiteUtils
        except ImportError as err:  # pragma: no cover - 仅用于提示宿主版本不匹配
            return "unknown", f"宿主 SDK 不可用（{err}），请确认 MoviePilot 为 V3 版本", ""

        timeout = 20
        try:
            timeout = int(self._attr(site, "timeout") or 20) or 20
        except (TypeError, ValueError):
            timeout = 20

        proxies = self._proxies(bool(self._attr(site, "proxy", raw=True)))

        try:
            request = RequestUtils(
                cookies=cookie,
                ua=ua or "",
                headers={"Referer": f"{target_url.rsplit('/', 1)[0]}/index.php"},
                proxies=proxies,
                timeout=timeout,
            )
            response = request.get_res(target_url)
        except TypeError:
            try:
                response = RequestUtils().get_res(target_url)
            except Exception as err:  # noqa: BLE001 - 网络异常统一归入失败
                return "unknown", f"请求异常：{err}", ""
        except Exception as err:  # noqa: BLE001 - 网络异常统一归入失败
            return "unknown", f"请求异常：{err}", ""

        if not response:
            return "unknown", "站点无响应（网络不可达或请求被中断）", ""

        status_code = getattr(response, "status_code", 0)
        html = getattr(response, "text", "") or ""
        if not html:
            return "unknown", f"站点返回空内容（HTTP {status_code}）", ""

        body = self._to_text(html)

        # 1) Cloudflare 质询（通常是 Cookie 缺少 cf_clearance）
        if self._looks_like_cloudflare(body):
            return ("cloudflare",
                    f"被 Cloudflare 质询拦截（HTTP {status_code}），Cookie 可能缺少 cf_clearance",
                    body)

        # 2) 未登录
        if self._looks_like_login(body, html):
            return "login", "Cookie 已失效（页面为登录页）", body

        # 3) 已签到（必须先于任何「成功」判定，避免被页面常驻文案误导）
        #
        #    v1.5.2 关键修正（本地实跑确诊）：
        #    判定必须**分两路**做 ——
        #      结构标记（class 名）→ 查 **HTML 原文**（纯文本里已被剥掉，查不到）
        #      文案标记            → 查 **纯文本**
        #    v1.5.1 把两者都塞进 `body`（纯文本）里查，导致所有 class 类判定恒假。
        signed_evidence = self._match_signed(html, body)
        if signed_evidence:
            return "signed", f"页面显示今日已签到（命中：{signed_evidence}）", body

        # 4) 未签到 —— 确认是签到页初始态后，交给调用方去执行签到动作。
        #
        #    ⚠️ v1.5.1 的「第 5 条防御性兜底」在此被**删除**。
        #    它原意是「登录态在 + 无验证文案 → 视为已签到」，但本地实跑证明该
        #    前提是**错的**：站点改版后人机验证改成「点击签到按钮后才弹出」，
        #    未签到态页面上根本没有任何验证文案，同时登录信息（收件箱/发件箱）
        #    却一应俱全 → 兜底条件被满足 → **把未签到误判成已签到**，
        #    直接跳过签到动作，成为静默失败的根源。
        #    教训：兜底若长期命中，就不再是兜底，而是主逻辑；一旦前提被站点改版
        #    破坏，它会安静地把错误结论报成成功。宁可 unknown（可观测），
        #    不要 signed（静默放过）。
        if ATTENDANCE_PAGE_MARKER in (html or ""):
            return ("unsigned",
                    "签到页初始态（有签到区、无已签到标记），需要执行签到",
                    body)

        # 5) 需要人机验证
        #    ⚠️ 不能只看关键词：这三个词是站点页面上的**静态说明文案**，
        #    只要页面渲染出来就可能被读到。必须配合「页面不含登录用户信息」才成立。
        if self._looks_like_need_verify(body):
            return "need_verify", "站点要求人机验证，需由浏览器完成", body

        snippet = body[:200].replace("\n", " ").strip()
        # v1.5.1：unknown 时补充「文本长度 + 中段 + 尾段」证据。
        # 只给前 200 字常常看不到关键内容（站点把签到区放在页面中后部），
        # 导致无法判断是「站点改文案」还是「解析把内容丢了」。
        logger.warning(
            f"【观众签到】  探测未识别：正文长度={len(body)}"
            f"｜HTML 长度={len(html)}"
            f"｜正文前段={snippet or '(空)'}"
        )
        if len(body) > 200:
            mid_start = max(0, len(body) // 2 - 100)
            middle = body[mid_start:mid_start + 200].replace("\n", " ").strip()
            logger.warning(f"【观众签到】  正文中段：{middle}")
            tail = body[-200:].replace("\n", " ").strip()
            logger.warning(f"【观众签到】  正文尾段：{tail}")
        return "unknown", f"结果无法识别（HTTP {status_code}），页面片段：{snippet}", body

    @staticmethod
    def _match_signed(html: str, body: str) -> str:
        """判断页面是否处于「今日已签到」状态，返回命中的证据名（未命中返回空串）。

        **必须分两路查**（v1.5.2 本地实跑确诊的要点）：

        * 结构标记（CSS class 名）只在 **HTML 原文**里存在 ——
          `_to_text()` 会把 `<div class="attendance-card--done">` 整体剥成
          空白，所以在纯文本里搜这些串**永远匹配不到**。
        * 文案标记则相反，必须拿 **纯文本** 查 ——
          在 HTML 原文里查也能命中，但容易撞上注释、属性值（如 `title="(签到已得32)"`
          或 `<!-- ...已签到... -->`），不如纯文本干净。

        站点三态实测（2026-09-14）：
        ============  ==========================================
        已签到         attendance-card--done / --attended
                       + 文案「今天已签到」「请勿重复刷新」
        签到成功        attendance-card--success / attendance-page--success
                       + 文案「签到成功」「本次获得爆米花」
        未签到         只有 attendance-page（无修饰符），应去签到
        ============  ==========================================
        """
        raw_html = html or ""
        text = body or ""

        # --- 第一路：结构标记，查 HTML 原文 ---
        for marker in SIGNED_STRUCTURE_MARKERS:
            if marker in raw_html:
                return f"结构标记 {marker}"

        # --- 第二路：文案标记，查纯文本 ---
        for marker in SIGNED_MARKERS:
            if marker in text:
                return f"文案「{marker}」"

        # --- 第三路：正则模式（覆盖「已签到 +32」这类带数值的渲染）---
        for pattern in SIGNED_REGEX_MARKERS:
            try:
                if re.search(pattern, text):
                    return f"模式 /{pattern}/"
            except re.error:  # pragma: no cover - 常量写错时保护
                continue

        return ""

    @staticmethod
    def _looks_like_need_verify(body: str) -> bool:
        """判断页面是否**真的**在要求人机验证。

        v1.5.0 关键修正：``NEED_VERIFY_MARKERS`` 里的三个词是站点页面的**静态文案**，
        在任何状态下都可能出现在 DOM / 可见文本里（实测：已登录且已签到的页面上
        也会被 ``inner_text`` 读到）。若拿它单独判定，就会把**已签到的成功页面**
        误判成「需要人机验证」，进而白等 60 秒并报失败。

        因此这里加一道**登录态反证**：页面若渲染出了只有登录用户才看得到的信息
        （分享率 / 收件箱 / 控制面板 等），说明会话有效，就不可能是「未登录 → 被要求验证」。
        """
        if not any(marker in (body or "") for marker in NEED_VERIFY_MARKERS):
            return False
        # 已被任何一条「已签到」或「登录用户信息」特征证伪 → 不是 need_verify
        if any(marker in (body or "") for marker in SIGNED_MARKERS):
            return False
        if any(marker in (body or "") for marker in LOGGED_IN_MARKERS):
            return False
        return True

    @staticmethod
    def _looks_like_cloudflare(body: str) -> bool:
        """判断页面是否为 Cloudflare 质询页。"""
        lowered = (body or "").lower()
        return any(marker in lowered for marker in CF_MARKERS)

    @staticmethod
    def _looks_like_login(body: str, html: str) -> bool:
        """判断页面是否为登录页；要求「签到」字样缺席，避免误判签到页。

        v1.5.0 增加登录态反证：页面若已渲染出登录用户信息（分享率 / 收件箱等），
        即使正文里出现「请先登录」之类的通用提示文案，也不认定为登录页。
        """
        if "takelogin.php" in (html or ""):
            return True
        # 已登录的反证：能看到登录用户信息 → 肯定不是登录页
        if any(marker in (body or "") for marker in LOGGED_IN_MARKERS):
            return False
        if any(marker in body for marker in LOGIN_MARKERS):
            return True
        if "签到" in body:
            return False
        return "密码" in body and ("登录" in body or "登錄" in body)

    # ------------------------------------------------------------ 浏览器签到

    def _sign_in_with_browser(
        self,
        target_url: str,
        cookie: str,
        ua: str,
        site: Any,
    ) -> Tuple[str, str]:
        """用宿主 CloakBrowser 打开签到页并等待人机验证自动完成。"""
        try:
            from app.sdk.browser import launch_browser_context
        except ImportError as err:
            logger.error(f"【观众签到】  宿主浏览器 SDK 不可用：{err}")
            return ("failed",
                    f"宿主浏览器 SDK 不可用（{err}）；请确认 MoviePilot 为 V3 且已启用浏览器能力")

        timeout = 60
        try:
            timeout = int(self._attr(site, "timeout") or 60) or 60
        except (TypeError, ValueError):
            timeout = 60

        proxies = self._proxies(bool(self._attr(site, "proxy", raw=True)))

        modes: List[bool] = []
        if self._browser_mode == BROWSER_MODE_HEADLESS:
            modes = [True]
        elif self._browser_mode == BROWSER_MODE_HEADED:
            modes = [False]
        else:
            modes = [True, False]

        logger.info(
            f"【观众签到】  启动浏览器过验证，模式序列："
            f"{' → '.join(('无头' if m else '有头') for m in modes)}"
            f"，等待上限 {self._browser_wait}s"
        )

        last_message = "浏览器未能完成签到"
        for headless in modes:
            mode_label = "无头" if headless else "有头"
            logger.info(f"【观众签到】  以「{mode_label}」模式启动浏览器加载签到页 ……")
            status, message = self._run_browser_once(
                launch_browser_context, target_url, cookie, ua, proxies, headless, timeout
            )
            if status in ("success", "already"):
                logger.info(f"【观众签到】  「{mode_label}」模式成功：{message}")
                return status, f"{message}（{mode_label}浏览器）"
            last_message = message
            logger.warning(f"【观众签到】  「{mode_label}」模式未完成：{message}")
            if len(modes) > 1 and headless:
                logger.info("【观众签到】  自动回退到「有头」模式重试 ……")

        return "failed", last_message

    def _run_browser_once(
        self,
        launcher: Any,
        target_url: str,
        cookie: str,
        ua: str,
        proxies: Dict[str, str],
        headless: bool,
        timeout: int,
    ) -> Tuple[str, str]:
        """启动一次浏览器，加载签到页并轮询等待签到完成。"""
        context = None
        page = None
        try:
            context = self._launch_context(launcher, headless, cookie, ua, proxies)
            pages = getattr(context, "pages", None) or []
            page = pages[0] if pages else context.new_page()

            # Cookie 必须在导航前写入浏览器 cookie 罐（Turnstile 与站点会话依赖它，
            # 仅设请求头对已发起的挑战无效），页面级请求头作为补充。
            self._install_cookies(context, target_url, cookie)
            self._inject_cookie(page, cookie)
            self._log_ua_consistency(context, page, ua)

            try:
                page.set_default_timeout(timeout * 1000)
            except Exception:  # noqa: BLE001 - 非关键能力，缺失可忽略
                pass

            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            self._log_browser_page(page, "首屏已加载")

            # ---- 关键：主动点击「签到」按钮（v1.5.2 行为修正）----
            #
            # 站点现在的人机验证是**点击后才触发**的，不会随页面加载自动开始。
            # 旧版本只加载页面然后干等，永远等不到「签到成功」。
            # 顺序：先读一次状态（已签到就直接返回）→ 未签到则点击 → 再轮询验证结果。
            pre_html = self._page_html(page)
            pre_body = self._to_text(pre_html)
            pre_evidence = self._match_signed(pre_html, pre_body)
            if pre_evidence:
                reward = self._extract_reward(pre_body)
                logger.info(f"【观众签到】  首屏即为已签到态（{pre_evidence}），无需点击")
                return "success", f"今天已经签到过了{reward}"

            logger.info("【观众签到】  首屏为未签到态，尝试点击「签到」按钮 ……")
            click_msg = self._click_sign_button(page)
            logger.info(f"【观众签到】  {click_msg}")
            if "已点击" in click_msg:
                logger.info("【观众签到】  已触发签到，等待人机验证自动完成 ……")
            else:
                logger.warning(
                    "【观众签到】  ⚠️ 未能点到签到按钮，将只做被动等待；"
                    "若站点改版请反馈页面结构以便适配"
                )

            deadline = time.time() + self._browser_wait
            last_state = "unknown"
            last_html = ""
            last_body = ""
            last_url = ""
            last_title = ""
            first_state = ""
            tick = 0
            heartbeat = 0
            evidence_logged = False
            clicked = "已点击" in click_msg
            reclick_at = time.time() + 12 if clicked else 0
            while time.time() < deadline:
                tick += 1
                last_html = self._page_html(page)
                last_body = self._to_text(last_html)
                last_url = self._safe_attr(page, "url")
                last_title = self._safe_call(page, "title")

                # 判定已签到：结构标记查 HTML、文案查纯文本（v1.5.2 两路并查）
                hit = self._match_signed(last_html, last_body)
                if hit:
                    reward = self._extract_reward(last_body)
                    logger.info(f"【观众签到】  第 {tick} 次轮询检测到已签到（{hit}）")
                    return "success", f"签到成功{reward}"

                if self._looks_like_login(last_body, ""):
                    logger.warning(f"【观众签到】  第 {tick} 次轮询检测到跳转登录页")
                    self._log_browser_page(page, "跳转登录页")
                    return "failed", "浏览器加载后跳转到登录页，Cookie 已失效"

                # 已点击但过了一段时间仍未成功 → 补点一次，应对验证弹层遮挡/首次点击落空
                if clicked and reclick_at and time.time() >= reclick_at:
                    reclick_at = 0
                    retry_msg = self._click_sign_button(page)
                    logger.info(f"【观众签到】  第 {tick} 次轮询补点签到：{retry_msg}")

                if self._looks_like_need_verify(last_body):
                    last_state = "need_verify"
                elif self._looks_like_cloudflare(last_body):
                    last_state = "cloudflare"
                else:
                    # 注意：这里**不再**把「登录信息在」当作已签到的迹象。
                    # v1.5.1 的兜底正是栽在这个推断上 —— 未签到态同样有登录信息。
                    last_state = "unknown"
                if not first_state:
                    first_state = last_state
                    # 首次判定即留证据：URL / 标题 / 文本片段，便于定位到底看到了什么
                    self._log_browser_page(page, f"首轮判定 {last_state}")
                    evidence_logged = True
                # 每 15 秒输出一次心跳，避免长时间静默让人以为卡死
                elapsed = int(time.time() - (deadline - self._browser_wait))
                if elapsed - heartbeat >= 15:
                    heartbeat = elapsed
                    logger.info(
                        f"【观众签到】  等待中 …… 已用 {elapsed}s / 上限 {self._browser_wait}s"
                        f"（当前页面状态：{last_state}，地址：{last_url}）"
                    )
                time.sleep(3)

            if not evidence_logged:
                self._log_browser_page(page, f"超时终态 {last_state}")
            logger.error(
                f"【观众签到】  超时诊断：状态={last_state}｜地址={last_url}｜标题={last_title}"
            )
            if last_state == "need_verify":
                logger.error(
                    f"【观众签到】  ⏱️ {self._browser_wait}s 内人机验证未通过"
                    f"（已轮询 {tick} 次）"
                )
                return ("failed",
                        f"已加载签到页但 {self._browser_wait}s 内人机验证未通过"
                        f"（地址：{last_url or target_url}）；"
                        "可尝试切换为「仅有头」模式或延长等待时间")
            if last_state == "cloudflare":
                logger.error(f"【观众签到】  ⏱️ {self._browser_wait}s 内未通过 Cloudflare 质询")
                return "failed", f"{self._browser_wait}s 内未通过 Cloudflare 质询"
            snippet = (last_body or "")[:200].replace("\n", " ").strip()
            logger.error(f"【观众签到】  ⏱️ {self._browser_wait}s 内未检测到签到结果")
            logger.error(f"【观众签到】  页面片段：{snippet}")
            return "failed", f"浏览器未能在 {self._browser_wait}s 内完成签到，页面片段：{snippet}"
        except Exception as err:  # noqa: BLE001 - 浏览器异常统一归入失败
            logger.error(f"【观众签到】  浏览器执行异常：{err}")
            return "failed", f"浏览器执行异常：{err}"
        finally:
            for closer in (page, context):
                if closer is None:
                    continue
                try:
                    closer.close()
                except Exception:  # noqa: BLE001 - 释放失败不影响结果
                    pass

    def _launch_context(
        self,
        launcher: Any,
        headless: bool,
        cookie: str,
        ua: str,
        proxies: Dict[str, str],
    ) -> Any:
        """按宿主稳定 SDK 启动浏览器上下文，并兼容不同版本的参数签名。

        官方指南示例：``with launch_browser_context(cookies=..., browser_type="chromium") as ctx``
        但不同版本接受的参数集合可能不同，故此处逐级回退。

        **关键顺序**：宿主 ``launch_browser_context`` 把其余参数**原样透传**给 CloakBrowser，
        被拒绝时抛 ``TypeError``。实测该版本**不接受 ``cookies``**（详见下方告警），
        因此 cookie 改由 ``_install_cookies`` 在导航前写入浏览器 cookie 罐 —— 这里仍尝试传参，
        传得进去最好，传不进也不影响登录态。``browser_type`` 经源码核实**不被消费**，放最后试。
        """
        # 顺序即优先级：先试能力最强的组合，逐级降级时优先保留 cookie 相关参数。
        candidates: List[Dict[str, Any]] = []
        base: Dict[str, Any] = {"headless": headless}
        if cookie:
            base["cookies"] = cookie
        # UA 决定会话 Cookie 是否被判有效：默认跟随站点登记的 UA（可用开关关闭）
        if ua and self._forward_ua:
            base["user_agent"] = ua
        if proxies:
            base["proxy"] = proxies
        candidates.append(dict(base))
        # 去掉 cookies（由 cookie 罐注入兜底）
        no_cookie = {k: v for k, v in base.items() if k != "cookies"}
        if no_cookie != base:
            candidates.append(no_cookie)
        # 再去掉 UA 之外的附加项
        candidates.append({"headless": headless})
        # 最后才试 browser_type（源码核实 launch_browser_context 不消费它）
        candidates.append({"headless": headless, "browser_type": "chromium"})

        last_error: Optional[Exception] = None
        tried: List[List[str]] = []
        for kwargs in candidates:
            keys = sorted(kwargs)
            if keys in tried:
                continue
            tried.append(keys)
            try:
                context = launcher(**kwargs)
            except TypeError as err:
                last_error = err
                logger.debug(
                    f"【观众签到】launch_browser_context 不接受参数组合 {keys}：{err}"
                )
                continue
            if cookie and "cookies" not in kwargs:
                logger.warning(
                    "【观众签到】  ⚠️ 宿主浏览器拒绝了 cookies 启动参数，"
                    "改由浏览器 cookie 罐注入登录态（若不生效页面会停在验证页）"
                )
            return context
        raise last_error if last_error else RuntimeError("无法启动浏览器上下文")

    @staticmethod
    def _log_ua_consistency(context: Any, page: Any, ua: str) -> None:
        """核对浏览器实际 UA 与站点登记的 UA 是否一致。

        NexusPHP 的会话 Cookie（``c_secure_uid`` 等）与 Cloudflare 的 ``cf_clearance``
        **都绑定 User-Agent**。浏览器用自己的指纹 UA 打开、而 Cookie 是用站点登记的 UA
        申请的话，服务端会判为会话无效 → 表现就是"一直停在人机验证/登录页"。
        这里把差异显式打出来，避免此类问题再次被误判为"验证码过不去"。
        """
        if not ua:
            return
        try:
            actual = ""
            try:
                actual = str(page.evaluate("() => window.navigator.userAgent") or "")
            except Exception:  # noqa: BLE001 - 取不到就算了
                actual = ""
            if not actual:
                return
            if actual.strip() == ua.strip():
                logger.info("【观众签到】  浏览器 UA 与站点登记 UA 一致（会话 Cookie 可正常生效）")
            else:
                logger.warning(
                    "【观众签到】  ⚠️ 浏览器 UA 与站点登记 UA **不一致**，"
                    "会话 Cookie / cf_clearance 可能被判无效（这会直接导致卡在人机验证页）\n"
                    f"【观众签到】     站点登记：{ua[:120]}\n"
                    f"【观众签到】     浏览器实际：{actual[:120]}"
                )
        except Exception as err:  # noqa: BLE001 - 诊断失败不影响主流程
            logger.debug(f"【观众签到】核对 UA 一致性失败：{err}")

    def _install_cookies(self, context: Any, target_url: str, cookie: str) -> None:
        """把站点 Cookie 写入浏览器 cookie 罐（导航前调用）。

        这是让浏览器**处于登录态**的关键一步：
        ``page.set_extra_http_headers`` 只影响后续请求头，而 Turnstile 与站点会话
        依赖的是浏览器 cookie 罐，且对**已发起**的挑战无效 —— 必须在 goto 之前写入。

        先用 Playwright 原生 ``context.add_cookies`` 逐条添加；失败后退回
        ``context.add_cookies`` 的 dict 形式，再失败退回页面级请求头（由调用方兜底）。
        """
        if not cookie:
            return
        parsed = self._parse_cookie_header(cookie)
        if not parsed:
            logger.warning("【观众签到】  ⚠️ 站点 Cookie 无法解析，浏览器可能处于未登录态")
            return
        try:
            from urllib.parse import urlparse

            host = urlparse(target_url).hostname or ""
            domain = f".{host}" if host and "." in host else host
            jar = [
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "secure": target_url.startswith("https://"),
                }
                for name, value in parsed.items()
            ]
            try:
                context.add_cookies(jar)
                logger.info(
                    f"【观众签到】  已将 {len(jar)} 条 Cookie 写入浏览器 cookie 罐"
                    f"（域名 {domain}），浏览器将以登录态访问"
                )
                return
            except TypeError:
                # 部分实现只接受无 domain 的简表
                context.add_cookies(
                    [{"name": n, "value": v} for n, v in parsed.items()]
                )
                logger.info(
                    f"【观众签到】  已将 {len(parsed)} 条 Cookie 写入浏览器 cookie 罐（简表形式）"
                )
                return
        except Exception as err:  # noqa: BLE001 - 失败仍可依赖页面级请求头
            logger.warning(
                f"【观众签到】  ⚠️ 写入浏览器 cookie 罐失败：{err}；"
                "退回页面级请求头注入（对验证挑战可能无效）"
            )

    @staticmethod
    def _parse_cookie_header(cookie: str) -> Dict[str, str]:
        """把 ``a=1; b=2`` 形式的 Cookie 请求头解析成字典。

        值中可能含 ``=``（如 base64 填充），故只按第一个 ``=`` 切分。
        """
        parsed: Dict[str, str] = {}
        for chunk in (cookie or "").split(";"):
            item = chunk.strip()
            if not item or "=" not in item:
                continue
            name, _, value = item.partition("=")
            name = name.strip()
            if name:
                parsed[name] = value.strip()
        return parsed

    @staticmethod
    def _inject_cookie(page: Any, cookie: str) -> None:
        """把站点 Cookie 注入页面请求头（CloakBrowser 已注入时重复设置亦无害）。"""
        if not cookie:
            return
        try:
            page.set_extra_http_headers({"cookie": cookie})
        except Exception as err:  # noqa: BLE001 - 注入失败仍可依赖启动参数
            logger.debug(f"【观众签到】注入 Cookie 请求头失败：{err}")

    @staticmethod
    def _page_text(page: Any) -> str:
        """读取页面文本，失败时返回空字符串。

        v1.5.0 调整顺序：**优先用 `content()`（整份 HTML）转文本**，再退回 `inner_text`。
        原因：`inner_text` 只返回**可见**文本，受浏览器窗口尺寸与 CSS 影响，
        无头模式下底部内容可能被判为不可见 —— 会导致同一页面在不同模式下
        读到不同文本，判定结果不稳定（v1.4.0 的误判即与此有关）。
        """
        for action in ("content", "inner_text"):
            try:
                if action == "content":
                    html = page.content() or ""
                    if html:
                        return AudiencesSignIn._to_text(html)
                    continue
                return page.inner_text("body") or ""
            except Exception:  # noqa: BLE001 - 页面切换中读取失败属常见情况
                continue
        return ""

    @staticmethod
    def _page_html(page: Any) -> str:
        """读取页面**HTML 原文**，失败返回空字符串。

        v1.5.2 新增：``_page_text()`` 返回的是剥完标签的纯文本，class 类判定
        （``attendance-card--done`` 等）在里面查不到。判定已签到需要**两路并查**，
        因此这里单独提供 HTML 原文读取。
        """
        try:
            return page.content() or ""
        except Exception:  # noqa: BLE001 - 页面切换中读取失败属常见情况
            return ""

    @staticmethod
    def _click_sign_button(page: Any) -> str:
        """在当前页面上寻找并点击「签到」按钮，返回结果说明。

        v1.5.2 新增 —— 这是本版**最关键的行为修正**。

        站点改版后，人机验证不再随页面加载自动触发，而是
        「**点击签到按钮 → 自动开始验证 → 验证通过 → 显示签到成功**」
        （用户实测确认）。旧版本只加载页面然后干等，因此永远等不到结果。

        按钮识别策略按优先级依次尝试，覆盖多种可能的实现方式：
          1. ``attendance-card`` 等签到区内的 ``button`` / ``a`` / ``input[submit]``
          2. 文本含「签到 / 打卡 / 领取」且**不含**「已签到」的可点元素
          3. 含 ``attend`` 的 ``href`` / ``class`` / ``id`` 的可点元素

        为避免误点「已签到」标签（它也是链接），统一排除文本含「已签到」的元素。
        """
        script = r"""
(() => {
  const out = {clicked: false, how: '', text: '', err: ''};
  try {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const txt = el => ((el.innerText || el.value || '') + '').trim();
    const all = () => [...document.querySelectorAll('button, a, input[type=submit], input[type=button]')]
                        .filter(visible);

    const isAttended = el => /已签到|已打卡/.test(txt(el)) ||
                            /--attended/.test((el.className || '').toString());

    let target = null, how = '';

    // 策略 1：签到卡片/页面区块内的可点元素
    if (!target) {
      for (const sel of ['.attendance-card', '.attendance-page', '.attendance-hero']) {
        const box = document.querySelector(sel);
        if (!box) continue;
        const cand = [...box.querySelectorAll('button, a, input[type=submit], input[type=button]')]
                       .filter(visible).filter(el => !isAttended(el));
        if (cand.length) { target = cand[0]; how = sel + ' 内首个可点元素'; break; }
      }
    }

    // 策略 2：文本含 签到/打卡/领取 且不是「已签到」
    if (!target) {
      const cand = all().filter(el => !isAttended(el) &&
                    /签到|打卡|领取|领取奖励|点击签到/.test(txt(el)));
      if (cand.length) { target = cand[0]; how = '文本匹配「签到」'; }
    }

    // 策略 3：href / class / id 含 attend
    if (!target) {
      const cand = all().filter(el => !isAttended(el) &&
                    /attend/i.test((el.getAttribute('href') || '') + ' ' +
                                   (el.className || '') + ' ' + (el.id || '')));
      if (cand.length) { target = cand[0]; how = 'href/class/id 匹配 attend'; }
    }

    if (!target) { out.err = '未找到可点的签到按钮'; return out; }

    out.text = txt(target).slice(0, 80);
    target.scrollIntoView({block: 'center'});
    target.click();
    out.clicked = true;
    out.how = how;
  } catch (e) { out.err = String(e); }
  return out;
})()
"""
        try:
            result = page.evaluate(script)
        except Exception as err:  # noqa: BLE001 - 浏览器能力差异
            return f"点击签到按钮失败：{err}"

        if not isinstance(result, dict):
            return f"点击签到按钮返回异常结果：{result!r}"
        if result.get("clicked"):
            return f"已点击签到按钮（{result.get('how')}，按钮文本：{result.get('text') or '(空)'}）"
        return f"未找到签到按钮（{result.get('err') or '未知原因'}）"

    @staticmethod
    def _safe_attr(page: Any, name: str) -> str:
        """安全读取页面属性（当前地址等），失败返回空字符串。"""
        try:
            value = getattr(page, name, "")
            return str(value() if callable(value) else value or "")
        except Exception:  # noqa: BLE001 - 页面切换中读取失败属常见情况
            return ""

    @staticmethod
    def _safe_call(page: Any, name: str) -> str:
        """安全调用页面无参方法并返回字符串结果。"""
        try:
            return str(getattr(page, name)() or "")
        except Exception:  # noqa: BLE001 - 非关键能力，缺失可忽略
            return ""

    def _log_browser_page(self, page: Any, label: str) -> None:
        """把浏览器当前页面的关键证据写入日志，用于定位「浏览器到底看到了什么」。

        记录：地址、标题、判定命中的关键词、正文片段。
        v1.5.0 增强：对每个命中词额外打印**上下游 80 字上下文**，
        这样能立刻分辨「关键词是静态说明文案」还是「真的在要求验证」。
        """
        try:
            url = self._safe_attr(page, "url")
            title = self._safe_call(page, "title")
            body = self._page_text(page)
            hits = [
                marker
                for group in (SIGNED_MARKERS, NEED_VERIFY_MARKERS, LOGIN_MARKERS,
                              CF_MARKERS, LOGGED_IN_MARKERS)
                for marker in group
                if marker.lower() in body.lower()
            ]
            snippet = (body or "")[:180].replace("\n", " ").strip()
            logger.info(
                f"【观众签到】  [{label}] 地址={url or '(未知)'}｜标题={title or '(空)'}"
                f"｜命中关键词={hits or '无'}｜正文长度={len(body)}"
            )
            logger.info(f"【观众签到】  [{label}] 正文片段：{snippet or '(空)'}")
            # 命中上下文：只在「可疑词」命中时打印，避免日志刷屏
            for marker in NEED_VERIFY_MARKERS:
                ctx = self._marker_context(body, marker)
                if ctx:
                    logger.info(
                        f"【观众签到】  [{label}] 「{marker}」上下文：…{ctx}…"
                    )
        except Exception as err:  # noqa: BLE001 - 诊断失败不影响主流程
            logger.debug(f"【观众签到】记录浏览器页面证据失败：{err}")

    @staticmethod
    def _marker_context(body: str, marker: str, span: int = 80) -> str:
        """返回关键词在正文中的上下游片段（用于判断它是静态文案还是真实提示）。"""
        if not body or marker not in body:
            return ""
        idx = body.find(marker)
        start = max(0, idx - span)
        end = min(len(body), idx + len(marker) + span)
        return body[start:end].replace("\n", " ").strip()

    # ------------------------------------------------------------ 工具方法

    @staticmethod
    def _parse_sites(value: Any) -> List[str]:
        """解析站点配置，支持逗号 / 分号 / 换行分隔，并做域名归一化。"""
        if not value:
            return [DEFAULT_SITES]
        raw = str(value).replace(";", ",").replace("\n", ",").replace(" ", ",")
        sites: List[str] = []
        for item in raw.split(","):
            domain = AudiencesSignIn._normalize_domain(item)
            if domain and domain not in sites:
                sites.append(domain)
        return sites or [DEFAULT_SITES]

    @staticmethod
    def _normalize_domain(value: Any) -> str:
        """把 url 或域名统一为注册域名。"""
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
        text = text.split("/")[0].split("?")[0].strip()
        return text.lower().replace("www.", "")

    def _get_site(self, domain: str) -> Optional[Any]:
        """从宿主站点配置读取站点对象（含 Cookie / UA / url / timeout / proxy）。"""
        try:
            from app.db.oper.site import SiteOper
        except ImportError as err:
            logger.error(f"【观众签到】无法导入站点 Oper：{err}")
            return None
        try:
            return SiteOper().get_by_domain(domain)
        except Exception as err:  # noqa: BLE001 - 站点读取失败不应中断插件执行
            logger.error(f"【观众签到】读取站点 {domain} 失败：{err}")
            return None

    @staticmethod
    def _attr(site: Any, name: str, raw: bool = False) -> Any:
        """安全读取站点属性；raw 为 False 时把值统一为字符串。"""
        value = getattr(site, name, None) if site is not None else None
        if raw:
            return value
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _site_url(site: Any, domain: str) -> str:
        """确定站点访问地址，优先使用站点配置中的 url。"""
        url = AudiencesSignIn._attr(site, "url")
        if url and not url.startswith("http"):
            url = f"https://{url}"
        if not url:
            url = f"https://{domain}"
        return url.rstrip("/")

    @staticmethod
    def _proxies(use_proxy: bool) -> Dict[str, str]:
        """站点启用代理时，按宿主配置构造代理字典。"""
        if not use_proxy:
            return {}
        try:
            from app.sdk.config import settings
            host = str(getattr(settings, "PROXY_HOST", "") or "").strip()
            if host:
                return {"http": host, "https": host}
        except Exception:  # noqa: BLE001 - 代理配置缺失时按直连处理
            pass
        return {}

    @staticmethod
    def _to_text(html: str) -> str:
        """把 HTML 转成用于关键词匹配的纯文本。

        v1.5.1 修正：**必须先删除 HTML 注释**，再剥标签。
        原因：``<[^>]+>`` 在遇到注释时会**提前在第一个 ``>`` 处截断**
        （注释正文里可能含 ``>``，例如 ``-->`` 或 ``=>``），导致
        ① 注释残余符 (``-->``) 留在文本里污染判定；
        ② 注释**内部的文字**被当成正文保留。
        实测观众站页面顶部有大量 ``-->`` 残留，正是这个缺陷所致。
        """
        text = re.sub(r"(?is)<!--.*?-->", " ", html)          # 先删注释（含内部文字）
        text = re.sub(r"(?is)<script.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]*>", " ", text)              # 再剥标签
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&#8249;", " ").replace("&#8250;", " ")   # 界面箭头
        text = re.sub(r"(?m)&[a-zA-Z#0-9]{1,8};", " ", text)  # 其余实体统一清掉
        text = re.sub(r"(?:\s*-->\s*)+", " ", text)           # 兜底：清理残留的注释尾
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_reward(body: str) -> str:
        """从页面文本中提取签到奖励。

        只接受**状态相关**的奖励上下文，避免命中页面常驻的奖励规则说明
        （例如本站签到页永远带着「首次签到获得 20 粒爆米花」，那是规则而非奖励）：

        1. 签到后页面头部形如「已签到 +30」——本站实测格式，最可靠；
        2. 带时间限定的句式，如「今日签到获得 30 魔力值」——要求出现
           本次/此次/今日/今天，因此不会匹配「首次签到获得」这类规则文案。
        """
        text = body or ""
        header = re.search(r"已签到\s*[+＋]?\s*(\d{1,4})", text)
        if header:
            return f"，获得 {header.group(1)} 爆米花"
        temporal = re.search(
            r"(?:本次|此次|今日|今天)[^。；;\n]{0,12}?(?:获得|得到|奖励)\s*(\d{1,4})",
            text,
        )
        if temporal:
            return f"，获得 {temporal.group(1)}"
        return ""

    # ------------------------------------------------------------ 数据与通知

    def _history(self) -> List[dict]:
        """读取签到历史。"""
        try:
            data = self.get_data("history")
        except Exception as err:  # noqa: BLE001 - 数据读取失败按空历史处理
            logger.error(f"【观众签到】读取历史记录失败：{err}")
            return []
        return data if isinstance(data, list) else []

    def _append_history(self, results: List[Dict[str, Any]]) -> None:
        """追加历史记录并裁剪长度。"""
        history = self._history()
        history = list(results) + history
        try:
            self.save_data("history", history[:MAX_HISTORY])
        except Exception as err:  # noqa: BLE001 - 数据写入失败不影响签到结果
            logger.error(f"【观众签到】保存历史记录失败：{err}")

    def _signed_today(self, domain: str) -> bool:
        """判断某站点今天是否已经签到成功（用于当天跳过）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        for record in self._history():
            if record.get("site") != domain:
                continue
            if not record.get("success"):
                continue
            if str(record.get("time", "")).startswith(today):
                return True
        return False

    def _post_result(self, results: List[Dict[str, Any]]) -> None:
        """发送签到结果通知。"""
        if not results:
            return
        failed = [item for item in results if not item.get("success")]
        title = "【观众签到】存在失败" if failed else "【观众签到】签到完成"
        text = "\n".join(f"{item['site']}：{item['message']}" for item in results)
        try:
            self.post_message(mtype=self._notification_type(), title=title, text=text)
        except Exception as err:  # noqa: BLE001 - 通知失败不影响签到本身
            logger.error(f"【观众签到】发送通知失败：{err}")

    @staticmethod
    def _notification_type(name: str = "PluginAction") -> Any:
        """获取通知类型枚举，取不到时返回 None 由宿主使用默认类型。"""
        try:
            from app.schemas import NotificationType
            return getattr(NotificationType, name, None)
        except Exception:  # noqa: BLE001 - 枚举缺失时退回默认
            return None
