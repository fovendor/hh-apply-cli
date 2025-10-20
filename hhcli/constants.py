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