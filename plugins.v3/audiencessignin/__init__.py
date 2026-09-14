"""
观众（Audiences）PT 站自动签到插件 —— MoviePilot V3

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

#: 判定「今天已经签过」的文案（实测原文，来自真实页面）
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

#: 判定「需要人机验证」的文案（实测原文）
NEED_VERIFY_MARKERS = (
    "人机验证",
    "验证通过后将自动完成签到",
    "完成人机验证即可",
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
    plugin_version = "1.1.0"
    plugin_author = "本地自建"
    author_url = "https://wiki.movie-pilot.org"
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
                "methods": ["GET"],
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
                "description": "仅用 HTTP 探测各站点当前签到状态，不执行签到、不启动浏览器。",
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
        """插件 API：立即签到。"""
        results = self.sign_in_all(trigger="api")
        return {
            "success": all(item["success"] for item in results) if results else False,
            "results": results,
        }

    def query_state(self) -> Dict[str, Any]:
        """插件 API：只探测状态，不做任何写操作。"""
        states: List[Dict[str, Any]] = []
        for domain in self._sites:
            site = self._get_site(domain)
            cookie = self._cookie_override or self._attr(site, "cookie")
            ua = self._ua_override or self._attr(site, "ua")
            if not cookie:
                states.append({"site": domain, "state": "no_cookie"})
                continue
            target_url = f"{self._site_url(site, domain)}/{self._attendance_path.lstrip('/')}"
            state, detail, _ = self._probe_state(target_url, cookie, ua, site)
            states.append({"site": domain, "state": state, "detail": detail})
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
                        "text": "暂无签到记录。可在「设定 -> 服务」页手动执行一次，或等待定时任务触发。",
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
        """对所有配置站点执行签到，返回本次结果列表。"""
        if not self._sites:
            logger.warning("【观众签到】未配置签到站点，已跳过执行")
            return []

        logger.info(
            f"【观众签到】开始执行签到（触发方式：{trigger}），站点：{'、'.join(self._sites)}"
        )
        results: List[Dict[str, Any]] = []
        for domain in self._sites:
            results.append(self.sign_in_one(domain))

        if self._notify:
            self._post_result(results)

        if results:
            self._append_history(results)
        return results

    def sign_in_one(self, domain: str) -> Dict[str, Any]:
        """对单个站点执行签到，返回结果字典。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self._skip_if_done and self._signed_today(domain):
            message = "今天已签到成功，已跳过"
            logger.info(f"【观众签到】{domain} {message}")
            return {"site": domain, "success": True, "status": "skipped",
                    "message": message, "time": now}

        site = self._get_site(domain)
        cookie = self._cookie_override or self._attr(site, "cookie")
        ua = self._ua_override or self._attr(site, "ua")
        site_url = self._site_url(site, domain)

        if not cookie:
            message = ("未取到站点 Cookie：请在 MoviePilot「站点管理」中添加/更新该站点，"
                       "或在插件中填写 Cookie 覆盖")
            logger.error(f"【观众签到】{domain} {message}")
            return {"site": domain, "success": False, "status": "no_cookie",
                    "message": message, "time": now}

        target_url = f"{site_url}/{self._attendance_path.lstrip('/')}"

        last_message = "签到未成功"
        for attempt in range(1, self._retry + 1):
            status, message = self._attempt_once(target_url, cookie, ua, site)
            if status in ("success", "already"):
                logger.info(f"【观众签到】{domain} {message}")
                return {"site": domain, "success": True, "status": status,
                        "message": message, "time": now}
            last_message = message
            logger.warning(f"【观众签到】{domain} 第 {attempt} 次尝试失败：{message}")

        logger.error(f"【观众签到】{domain} 签到未成功：{last_message}")
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
        state, detail, body = self._probe_state(target_url, cookie, ua, site)

        if state == "signed":
            reward = self._extract_reward(body)
            return "already", f"今天已经签到过了{reward}"

        if state == "login":
            return "failed", "Cookie 已失效（页面要求登录），请在站点管理中更新 Cookie"

        if state == "need_verify":
            if not self._use_browser:
                return ("failed",
                        "站点要求人机验证，但插件已禁用浏览器调用；请在插件配置中开启"
                        "「允许调用浏览器过人机验证」")
            return self._sign_in_with_browser(target_url, cookie, ua, site)

        if state == "cloudflare":
            if not self._use_browser:
                return ("failed",
                        "被 Cloudflare 质询拦截且插件已禁用浏览器；"
                        "请更新站点 Cookie（建议包含 cf_clearance）或开启浏览器调用")
            logger.info("【观众签到】HTTP 被 Cloudflare 质询拦截，改用浏览器处理")
            return self._sign_in_with_browser(target_url, cookie, ua, site)

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

        状态取值：signed / need_verify / login / cloudflare / unknown
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
        if any(marker in body for marker in SIGNED_MARKERS):
            return "signed", "页面显示今日已签到", body

        # 4) 需要人机验证
        if any(marker in body for marker in NEED_VERIFY_MARKERS):
            return "need_verify", "站点要求人机验证，需由浏览器完成", body

        snippet = body[:200].replace("\n", " ").strip()
        return "unknown", f"结果无法识别（HTTP {status_code}），页面片段：{snippet}", body

    @staticmethod
    def _looks_like_cloudflare(body: str) -> bool:
        """判断页面是否为 Cloudflare 质询页。"""
        lowered = (body or "").lower()
        return any(marker in lowered for marker in CF_MARKERS)

    @staticmethod
    def _looks_like_login(body: str, html: str) -> bool:
        """判断页面是否为登录页；要求「签到」字样缺席，避免误判签到页。"""
        if "takelogin.php" in (html or ""):
            return True
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

        last_message = "浏览器未能完成签到"
        for headless in modes:
            mode_label = "无头" if headless else "有头"
            logger.info(f"【观众签到】以{mode_label}模式启动浏览器处理人机验证：{target_url}")
            status, message = self._run_browser_once(
                launch_browser_context, target_url, cookie, ua, proxies, headless, timeout
            )
            if status in ("success", "already"):
                return status, f"{message}（{mode_label}浏览器）"
            last_message = message
            logger.warning(f"【观众签到】{mode_label}模式未完成签到：{message}")

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

            self._inject_cookie(page, cookie)

            try:
                page.set_default_timeout(timeout * 1000)
            except Exception:  # noqa: BLE001 - 非关键能力，缺失可忽略
                pass

            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout * 1000)

            deadline = time.time() + self._browser_wait
            last_state = "unknown"
            last_body = ""
            while time.time() < deadline:
                last_body = self._page_text(page)
                if any(marker in last_body for marker in SIGNED_MARKERS):
                    reward = self._extract_reward(last_body)
                    return "success", f"签到成功{reward}"
                if self._looks_like_login(last_body, ""):
                    return "failed", "浏览器加载后跳转到登录页，Cookie 已失效"
                if any(marker in last_body for marker in NEED_VERIFY_MARKERS):
                    last_state = "need_verify"
                elif self._looks_like_cloudflare(last_body):
                    last_state = "cloudflare"
                time.sleep(3)

            if last_state == "need_verify":
                return ("failed",
                        f"已加载签到页但 {self._browser_wait}s 内人机验证未通过；"
                        "可尝试切换为「仅有头」模式或延长等待时间")
            if last_state == "cloudflare":
                return "failed", f"{self._browser_wait}s 内未通过 Cloudflare 质询"
            snippet = (last_body or "")[:200].replace("\n", " ").strip()
            return "failed", f"浏览器未能在 {self._browser_wait}s 内完成签到，页面片段：{snippet}"
        except Exception as err:  # noqa: BLE001 - 浏览器异常统一归入失败
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
        但不同版本接受的参数集合可能不同，故此处按「从全到简」逐级回退。
        """
        optional: Dict[str, Any] = {}
        if cookie:
            optional["cookies"] = cookie
        if ua:
            optional["user_agent"] = ua
        if proxies:
            optional["proxy"] = proxies
        optional["browser_type"] = "chromium"

        keys = list(optional.keys())
        last_error: Optional[Exception] = None
        for drop in range(len(keys) + 1):
            kwargs = {k: optional[k] for k in keys[: len(keys) - drop]}
            kwargs["headless"] = headless
            try:
                return launcher(**kwargs)
            except TypeError as err:
                last_error = err
                logger.debug(f"【观众签到】launch_browser_context 不接受参数组合 {list(kwargs)}：{err}")
        raise last_error if last_error else RuntimeError("无法启动浏览器上下文")

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
        """读取页面可见文本，失败时返回空字符串。"""
        for action in ("inner_text", "content"):
            try:
                if action == "inner_text":
                    return page.inner_text("body") or ""
                return AudiencesSignIn._to_text(page.content() or "")
            except Exception:  # noqa: BLE001 - 页面切换中读取失败属常见情况
                continue
        return ""

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
        """去掉脚本、样式与标签，得到用于关键词匹配的纯文本。"""
        text = re.sub(r"(?is)<script.*?</script>", " ", html)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
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
