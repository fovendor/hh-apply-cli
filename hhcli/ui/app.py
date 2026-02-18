from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from requests import exceptions as req_exc
from textual.app import App
from textual.binding import Binding
from textual.events import Key
from textual.scrollbar import ScrollBar
from textual.widgets import Input, TextArea

from ..client import AuthorizationPending
from ..constants import ConfigKeys, LogSource
from ..database import (
    get_active_profile_name,
    get_all_profiles,
    get_default_config,
    load_profile_config,
    log_to_db,
    set_active_profile,
)
from .css_manager import CssManager
from .scrollbars import ThinScrollBarRender
from .modules.dictionaries import cache_dictionaries as cache_dictionaries_service
from .screens.profile_select import ProfileSelectionScreen
from .dialogs import ResumeSelectDialog
from .screens.search_mode import SearchModeScreen

CSS_MANAGER = CssManager()
ScrollBar.renderer = ThinScrollBarRender


class HHCliApp(App):
    """Основное TUI-приложение hhcli"""

    CSS_PATH = CSS_MANAGER.css_file
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("q", "quit", "Выход", show=True, priority=True),
        Binding("й", "quit", "Выход (RU)", show=False, priority=True),
    ]

    def __init__(self, client) -> None:
        super().__init__(watch_css=True)
        self.client = client
        self.client.set_captcha_flow_runner(self._run_captcha_in_ui_thread)
        self.dictionaries = {}
        self.css_manager = CSS_MANAGER
        self.title = "hhcli"
        self._ctrl_c_armed: bool = False
        self._ctrl_c_reset_timer = None
        self._auto_raise_timer = None
        self._auto_raise_remaining: int = 0
        self._auto_raise_resume_id: Optional[str] = None
        self._auto_raise_resume_title: str = ""
        self._auto_raise_can_publish: bool = True
        self._auto_raise_enabled: bool = False
        self._auto_raise_in_progress: bool = False

    def _run_captcha_in_ui_thread(self, captcha_url: str) -> bool:
        """Открывает webview-капчу строго из main thread приложения."""
        if not self.is_running:
            return bool(self.client.start_captcha_flow(captcha_url))
        app_thread_id = getattr(self, "_thread_id", None)
        if app_thread_id == threading.get_ident():
            return bool(self.client.start_captcha_flow(captcha_url))
        return bool(self.call_from_thread(self.client.start_captcha_flow, captcha_url))

    def apply_theme_from_profile(self, profile_name: Optional[str] = None) -> None:
        """Применяет тему, указанную в конфигурации профиля"""
        theme_name: Optional[str] = None
        if profile_name:
            try:
                profile_config = load_profile_config(profile_name)
                theme_name = profile_config.get(ConfigKeys.THEME)
            except Exception as exc:  # pragma: no cover
                log_to_db(
                    "WARN",
                    LogSource.TUI,
                    f"Не удалось загрузить тему профиля '{profile_name}': {exc}",
                )
        if not theme_name:
            defaults = get_default_config()
            theme_name = defaults.get(ConfigKeys.THEME, "hhcli-base")
        try:
            self.css_manager.set_theme(theme_name or "hhcli-base")
        except ValueError:
            self.css_manager.set_theme("hhcli-base")

    async def on_mount(self) -> None:
        log_to_db("INFO", LogSource.TUI, "Приложение смонтировано")
        all_profiles = get_all_profiles()
        active_profile = get_active_profile_name()
        theme_profile = active_profile or (all_profiles[0]["profile_name"] if all_profiles else None)
        self.apply_theme_from_profile(theme_profile)

        log_to_db("INFO", LogSource.TUI, "Открываю экран выбора профиля.")
        self.push_screen(ProfileSelectionScreen(all_profiles), self.on_profile_selected)

    async def on_profile_selected(self, selected_profile: Optional[str]) -> None:
        if not selected_profile:
            log_to_db("INFO", LogSource.TUI, "Выбор профиля отменён.")
            self.exit()
            return
        log_to_db("INFO", LogSource.TUI, f"Выбран профиль '{selected_profile}' из списка.")
        await self.proceed_with_profile(selected_profile)

    async def proceed_with_profile(self, profile_name: str) -> None:
        try:
            self.client.load_profile_data(profile_name)
            self.sub_title = f"Профиль: {profile_name}"
            self.apply_theme_from_profile(profile_name)
            self.client.ensure_active_token()

            self.run_worker(self.cache_dictionaries, thread=True, name="DictCacheWorker")

            self.notify(
                "Синхронизация истории откликов...",
                title="Синхронизация",
                timeout=2,
            )
            self.run_worker(self._sync_history_worker, thread=True, name="SyncWorker")

            log_to_db("INFO", LogSource.TUI, f"Загрузка резюме для '{profile_name}'")
            resumes = self.client.get_my_resumes()
            items = (resumes or {}).get("items") or []
            if not items:
                self.notify("У вас нет ни одного резюме. Создайте резюме на hh.ru и попробуйте снова.",
                            title="Резюме", severity="warning", timeout=5)
                self.push_screen(ProfileSelectionScreen(get_all_profiles()), self.on_profile_selected)
                return

            if len(items) == 1:
                r = items[0]
                self._open_search_mode(r["id"], r.get("title") or "", is_root=True)
            else:
                self.push_screen(ResumeSelectDialog(items), lambda result: self._on_resume_selected(profile_name, result))
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.TUI,
                f"Профиль '{profile_name}' требует повторной авторизации: {auth_exc}",
            )
            self.sub_title = f"Профиль: {profile_name} (ожидание авторизации)"
            # Повторно проверяем токен после завершения окна авторизации.
            try:
                self.client.ensure_active_token()
                self.notify(
                    "Авторизация завершена, продолжаю работу с профилем.",
                    title="Авторизация",
                    timeout=4,
                )
                await self.proceed_with_profile(profile_name)
            except AuthorizationPending:
                self.notify(
                    "Требуется повторная авторизация. "
                    "Завершите вход в браузере и повторите выбор профиля.",
                    title="Авторизация",
                    severity="warning",
                    timeout=6,
                )
                all_profiles = get_all_profiles()
                self.push_screen(ProfileSelectionScreen(all_profiles), self.on_profile_selected)
        except Exception as exc:
            log_to_db("ERROR", LogSource.TUI, f"Критическая ошибка профиля/резюме: {exc}")
            self.exit(result=exc)

    def _on_resume_selected(self, profile_name: str, result: tuple[str, str] | None) -> None:
        if not result:
            # Возвращаемся к выбору профиля, если пользователь отменил модалку
            self.push_screen(ProfileSelectionScreen(get_all_profiles()), self.on_profile_selected)
            return
        resume_id, resume_title = result
        log_to_db("INFO", LogSource.RESUME_SCREEN, f"Выбрано резюме: {resume_id} '{resume_title}'")
        self._open_search_mode(resume_id, resume_title, is_root=True)

    def _open_search_mode(self, resume_id: str, resume_title: str, *, is_root: bool) -> None:
        self._start_auto_raise_service(resume_id, resume_title)
        self.push_screen(
            SearchModeScreen(
                resume_id=resume_id,
                resume_title=resume_title,
                is_root_screen=is_root,
            )
        )

    def _sync_history_worker(self) -> None:
        """Синхронизирует историю откликов и обрабатывает запрос повторной авторизации"""
        try:
            self.client.sync_negotiation_history()
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Синхронизация истории остановлена: {auth_exc}",
            )
            self.call_from_thread(
                self.notify,
                "Авторизация требуется для синхронизации истории откликов.",
                title="Авторизация",
                severity="warning",
                timeout=4,
            )
        except (req_exc.RequestException, ConnectionError) as conn_exc:
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Синхронизация истории остановлена из-за сетевой ошибки: {conn_exc}",
            )
            self.call_from_thread(
                self.notify,
                "Не удалось синхронизировать историю (проблема с подключением/SSL). "
                "Попробуйте позже.",
                title="Синхронизация",
                severity="warning",
                timeout=5,
            )
        except Exception as exc:  # pragma: no cover
            log_to_db(
                "ERROR",
                LogSource.SYNC_ENGINE,
                f"Неожиданная ошибка синхронизации: {exc}",
            )
            self.call_from_thread(
                self.notify,
                "Синхронизация истории прервана из-за неизвестной ошибки.",
                title="Синхронизация",
                severity="error",
                timeout=5,
            )

    async def cache_dictionaries(self) -> None:
        """Загружает словари и обновляет справочные данные"""
        self.dictionaries = cache_dictionaries_service(self.client, notify=self.notify)

    def action_quit(self) -> None:
        focused = self.focused
        if isinstance(focused, (Input, TextArea)):
            return
        log_to_db("INFO", LogSource.TUI, "Пользователь запросил выход.")
        self.css_manager.cleanup()
        self.exit()

    def on_key(self, event: Key) -> None:
        is_ctrl = getattr(event, "ctrl", False) or getattr(event, "ctrl_key", False)
        if is_ctrl and event.key == "c":
            event.stop()
            event.prevent_default()
            if self._ctrl_c_armed:
                self.css_manager.cleanup()
                self.exit()
                return
            self._ctrl_c_armed = True
            if self._ctrl_c_reset_timer:
                self._ctrl_c_reset_timer.stop()
            self._ctrl_c_reset_timer = self.set_timer(2.5, self._reset_ctrl_c)
            self.notify(
                "Нажмите Ctrl+C ещё раз, чтобы выйти.",
                title="Выход",
                severity="warning",
                timeout=2.5,
            )
            return
        # App базово не реализует on_key; если не обработали сами — пропускаем
        return None

    def _reset_ctrl_c(self) -> None:
        self._ctrl_c_armed = False


# --- Автоподнятие резюме при старте профиля ---

    def _stop_auto_raise_service(self) -> None:
        if self._auto_raise_timer:
            self._auto_raise_timer.stop()
            self._auto_raise_timer = None
        self._auto_raise_remaining = 0
        self._auto_raise_can_publish = True
        self._auto_raise_in_progress = False

    def _start_auto_raise_service(self, resume_id: str, resume_title: str) -> None:
        """Запускает фоновую проверку автоподнятия после выбора резюме."""
        self._stop_auto_raise_service()
        self._auto_raise_resume_id = resume_id
        self._auto_raise_resume_title = resume_title or ""
        try:
            profile_config = load_profile_config(self.client.profile_name)
        except Exception as exc:  # pragma: no cover
            log_to_db("WARN", LogSource.TUI, f"Не удалось загрузить конфиг для автоподнятия: {exc}")
            return
        self._auto_raise_enabled = bool(profile_config.get(ConfigKeys.AUTO_RAISE_RESUME))
        if not self._auto_raise_enabled:
            return
        self.run_worker(self._auto_raise_worker(resume_id, resume_title), thread=True, exclusive=False)

    def _parse_iso(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def _format_remaining(self, seconds: int | None) -> str:
        if seconds is None or seconds < 0:
            return "--:--"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:02d}:{minutes:02d}"

    def _format_remaining_human(self, seconds: int | None) -> str:
        """Форматирует остаток в человеко-читаемый текст с падежами."""
        if seconds is None or seconds < 0:
            return "неизвестно"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60

        def hours_text(h: int) -> str:
            forms = {
                1: "1 час",
                2: "2 часа",
                3: "3 часа",
                4: "4 часа",
            }
            return forms.get(h, "")

        parts: list[str] = []
        if hours:
            parts.append(hours_text(hours))
        if minutes:
            parts.append(f"{minutes} минут")
        if not parts:
            parts.append("менее минуты")
        return " ".join(parts)

    async def _auto_raise_worker(self, resume_id: str, resume_title: str):
        try:
            data = self.client.get_resume_details(resume_id)
            next_raw = data.get("next_publish_at")
            can_publish = data.get("can_publish_or_update", True)
            next_dt = self._parse_iso(next_raw)
            remaining = 0
            if next_dt:
                delta = next_dt - datetime.now(tz=next_dt.tzinfo)
                remaining = max(0, int(delta.total_seconds()))
        except AuthorizationPending:
            self.call_from_thread(
                self.notify,
                "Требуется авторизация для автоподнятия.",
                title="Автоподнятие",
                severity="warning",
                timeout=4,
            )
            return
        except Exception as exc:
            log_to_db("WARN", LogSource.TUI, f"Не удалось получить статус резюме для автоподнятия: {exc}")
            return

        self._auto_raise_remaining = remaining
        self._auto_raise_can_publish = bool(can_publish)
        if remaining > 0:
            human = self._format_remaining_human(remaining)
            self.call_from_thread(
                self.notify,
                f"Следующее автоподнятие через {human}",
                title=resume_title or "Автоподнятие",
                timeout=4,
            )
            self.call_from_thread(self._start_auto_raise_timer)
            return

        if can_publish:
            self.call_from_thread(self._auto_publish_resume_background, resume_id, resume_title)
        else:
            self.call_from_thread(
                self.notify,
                "Поднятие недоступно для выбранного резюме.",
                title=resume_title or "Автоподнятие",
                severity="warning",
                timeout=4,
            )

    def _start_auto_raise_timer(self) -> None:
        if self._auto_raise_timer:
            self._auto_raise_timer.stop()
            self._auto_raise_timer = None
        if self._auto_raise_remaining <= 0 or not self._auto_raise_resume_id:
            return
        self._auto_raise_timer = self.set_interval(1.0, self._on_auto_raise_tick, pause=False)

    def _on_auto_raise_tick(self) -> None:
        if self._auto_raise_remaining <= 0:
            self._stop_auto_raise_service()
            if self._auto_raise_resume_id:
                self.run_worker(
                    self._auto_raise_worker(self._auto_raise_resume_id, ""),
                    thread=True,
                    exclusive=False,
                )
            return
        self._auto_raise_remaining = max(0, self._auto_raise_remaining - 1)

    def get_auto_raise_state(self) -> dict:
        """Возвращает текущее состояние автоподнятия для UI."""
        return {
            "enabled": self._auto_raise_enabled,
            "resume_id": self._auto_raise_resume_id,
            "resume_title": self._auto_raise_resume_title,
            "remaining": self._auto_raise_remaining,
            "can_publish": self._auto_raise_can_publish,
            "in_progress": self._auto_raise_in_progress,
        }

    def _auto_publish_resume_background(self, resume_id: str, resume_title: str) -> None:
        def _worker():
            self._auto_raise_in_progress = True
            try:
                self.client.publish_resume(resume_id)
                self.call_from_thread(
                    self.notify,
                    "Резюме поднято автоматически",
                    title=resume_title or "Автоподнятие",
                    timeout=3,
                )
            except AuthorizationPending:
                self.call_from_thread(
                    self.notify,
                    "Требуется авторизация для поднятия резюме.",
                    title="Автоподнятие",
                    severity="warning",
                    timeout=4,
                )
            except Exception as exc:  # pragma: no cover
                log_to_db("ERROR", LogSource.TUI, f"Не удалось поднять резюме автоматически: {exc}")
                self.call_from_thread(
                    self.notify,
                    f"Не удалось поднять резюме: {exc}",
                    title="Автоподнятие",
                    severity="error",
                    timeout=4,
                )
            finally:
                self._auto_raise_in_progress = False
                self.call_from_thread(self._stop_auto_raise_service)
                # После публикации обновляем статус для следующего таймера
                self.run_worker(self._auto_raise_worker(resume_id, resume_title), thread=True, exclusive=False)

        self.run_worker(_worker, thread=True, exclusive=False)

__all__ = ["HHCliApp", "CSS_MANAGER"]
