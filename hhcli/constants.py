from enum import Enum


class SearchMode(Enum):
    """Режимы поиска вакансий."""
    AUTO = "auto"
    MANUAL = "manual"


class AppStateKeys:
    """Ключи для таблицы состояния приложения app_state."""
    ACTIVE_PROFILE = "active_profile"
    AREAS_HASH = "areas_hash"
    AREAS_UPDATED_AT = "areas_updated_at"
    PROFESSIONAL_ROLES_HASH = "professional_roles_hash"
    PROFESSIONAL_ROLES_UPDATED_AT = "professional_roles_updated_at"
    LAST_NEGOTIATION_SYNC_PREFIX = "last_negotiation_sync_"


class ConfigKeys:
    """Ключи для конфигурации профиля."""
    TEXT_INCLUDE = "text_include"
    NEGATIVE = "negative"
    WORK_FORMAT = "work_format"
    AREA_ID = "area_id"
    SEARCH_FIELD = "search_field"
    PERIOD = "period"
    ROLE_IDS_CONFIG = "role_ids_config"
    COVER_LETTER = "cover_letter"
    SKIP_APPLIED_IN_SAME_COMPANY = "skip_applied_in_same_company"
    DEDUPLICATE_BY_NAME_AND_COMPANY = "deduplicate_by_name_and_company"
    STRIKETHROUGH_APPLIED_VAC = "strikethrough_applied_vac"
    STRIKETHROUGH_APPLIED_VAC_NAME = "strikethrough_applied_vac_name"
    THEME = "theme"
    VACANCY_LEFT_PANE_PERCENT = "vacancy_left_pane_percent"
    VACANCY_COL_INDEX_WIDTH = "vacancy_col_index_width"
    VACANCY_COL_TITLE_WIDTH = "vacancy_col_title_width"
    VACANCY_COL_COMPANY_WIDTH = "vacancy_col_company_width"
    VACANCY_COL_PREVIOUS_WIDTH = "vacancy_col_previous_width"
    HISTORY_LEFT_PANE_PERCENT = "history_left_pane_percent"
    HISTORY_COL_INDEX_WIDTH = "history_col_index_width"
    HISTORY_COL_TITLE_WIDTH = "history_col_title_width"
    HISTORY_COL_COMPANY_WIDTH = "history_col_company_width"
    HISTORY_COL_STATUS_WIDTH = "history_col_status_width"
    HISTORY_COL_SENT_WIDTH = "history_col_sent_width"
    HISTORY_COL_DATE_WIDTH = "history_col_date_width"

LAYOUT_WIDTH_KEYS: tuple[str, ...] = (
    ConfigKeys.VACANCY_LEFT_PANE_PERCENT,
    ConfigKeys.VACANCY_COL_INDEX_WIDTH,
    ConfigKeys.VACANCY_COL_TITLE_WIDTH,
    ConfigKeys.VACANCY_COL_COMPANY_WIDTH,
    ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH,
    ConfigKeys.HISTORY_LEFT_PANE_PERCENT,
    ConfigKeys.HISTORY_COL_INDEX_WIDTH,
    ConfigKeys.HISTORY_COL_TITLE_WIDTH,
    ConfigKeys.HISTORY_COL_COMPANY_WIDTH,
    ConfigKeys.HISTORY_COL_STATUS_WIDTH,
    ConfigKeys.HISTORY_COL_SENT_WIDTH,
    ConfigKeys.HISTORY_COL_DATE_WIDTH,
)

class ApiErrorReason:
    """
    Строковые идентификаторы причин ответа API при отклике на вакансию.
    """
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    TEST_REQUIRED = "test_required"
    QUESTIONS_REQUIRED = "questions_required"
    NEGOTIATIONS_FORBIDDEN = "negotiations_forbidden"
    RESUME_NOT_PUBLISHED = "resume_not_published"
    CONDITIONS_NOT_MET = "conditions_not_met"
    NOT_FOUND = "not_found"
    BAD_ARGUMENT = "bad_argument"
    UNKNOWN_API_ERROR = "unknown_api_error"
    NETWORK_ERROR = "network_error"


ERROR_REASON_LABELS: dict[str, str] = {
    ApiErrorReason.APPLIED: "Отклик отправлен",
    ApiErrorReason.ALREADY_APPLIED: "Вы уже откликались",
    ApiErrorReason.TEST_REQUIRED: "Требуется пройти тест",
    ApiErrorReason.QUESTIONS_REQUIRED: "Требуются ответы на вопросы",
    ApiErrorReason.NEGOTIATIONS_FORBIDDEN: "Работодатель запретил отклики",
    ApiErrorReason.RESUME_NOT_PUBLISHED: "Резюме не опубликовано",
    ApiErrorReason.CONDITIONS_NOT_MET: "Не выполнены условия",
    ApiErrorReason.NOT_FOUND: "Вакансия в архиве",
    ApiErrorReason.BAD_ARGUMENT: "Некорректные параметры",
    ApiErrorReason.UNKNOWN_API_ERROR: "Неизвестная ошибка API",
    ApiErrorReason.NETWORK_ERROR: "Ошибка сети",
}

DELIVERED_STATUS_CODES: frozenset[str] = frozenset({
    "applied",
    "responded",
    "response",
    "invited",
    "interview",
    "interview_assigned",
    "interview_scheduled",
    "offer",
    "offer_made",
    "rejected",
    "declined",
    "canceled",
    "cancelled",
    "discard",
    "employer_viewed",
    "viewed",
    "seen",
    "in_progress",
    "considering",
    "processing",
    "accepted",
    "hired",
})

FAILED_STATUS_CODES: frozenset[str] = frozenset({"failed"})


class LogSource:
    """Источники для логирования в базу данных."""
    API_CLIENT = "APIClient"
    OAUTH = "OAuth"
    SYNC_ENGINE = "SyncEngine"
    CONFIG_SCREEN = "ConfigScreen"
    MAIN = "Main"
    REFERENCE_DATA = "ReferenceData"
    VACANCY_LIST_FETCH = "VacancyListFetch"
    VACANCY_LIST_SCREEN = "VacancyListScreen"
    CACHE = "Cache"
    RESUME_SCREEN = "ResumeScreen"
    SEARCH_MODE_SCREEN = "SearchModeScreen"
    PROFILE_SCREEN = "ProfileScreen"
    TUI = "TUI"
