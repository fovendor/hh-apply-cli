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
    VACANCY_COL_INDEX_PERCENT = "vacancy_col_index_percent"
    VACANCY_COL_TITLE_PERCENT = "vacancy_col_title_percent"
    VACANCY_COL_COMPANY_PERCENT = "vacancy_col_company_percent"
    VACANCY_COL_PREVIOUS_PERCENT = "vacancy_col_previous_percent"
    HISTORY_LEFT_PANE_PERCENT = "history_left_pane_percent"
    HISTORY_COL_INDEX_PERCENT = "history_col_index_percent"
    HISTORY_COL_TITLE_PERCENT = "history_col_title_percent"
    HISTORY_COL_COMPANY_PERCENT = "history_col_company_percent"
    HISTORY_COL_STATUS_PERCENT = "history_col_status_percent"
    HISTORY_COL_DATE_PERCENT = "history_col_date_percent"

LAYOUT_PERCENT_KEYS: tuple[str, ...] = (
    ConfigKeys.VACANCY_LEFT_PANE_PERCENT,
    ConfigKeys.VACANCY_COL_INDEX_PERCENT,
    ConfigKeys.VACANCY_COL_TITLE_PERCENT,
    ConfigKeys.VACANCY_COL_COMPANY_PERCENT,
    ConfigKeys.VACANCY_COL_PREVIOUS_PERCENT,
    ConfigKeys.HISTORY_LEFT_PANE_PERCENT,
    ConfigKeys.HISTORY_COL_INDEX_PERCENT,
    ConfigKeys.HISTORY_COL_TITLE_PERCENT,
    ConfigKeys.HISTORY_COL_COMPANY_PERCENT,
    ConfigKeys.HISTORY_COL_STATUS_PERCENT,
    ConfigKeys.HISTORY_COL_DATE_PERCENT,
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
