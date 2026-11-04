"""
内置权限门控单元测试（当前构建：Pro 版）

验证 core/licensing/community_stub.py 的行为契约：
- require_pro / require_feature（FastAPI 依赖）：放行透传
- has_feature：Pro 功能集全部放行；授权无效时拦截
- is_pro：授权有效即为 True
- 配额：Pro 档（分析师 6、工作流 50、5 并发、API 2000/日，允许自定义 Agent/并行/记忆持久化）
"""

import asyncio

import pytest

from core.licensing.community_stub import (
    COMMUNITY_TIER_FEATURES,
    CommunityLicenseManager,
    CommunityLicenseService,
    FREE_ACCESS_FEATURES,
    LicenseInfo,
    PRO_FEATURES,
    PRO_TIER_FEATURES,
    get_license_service,
    require_feature,
    require_pro,
    _community_license_info,
)


@pytest.fixture
def service() -> CommunityLicenseService:
    return get_license_service()


@pytest.fixture
def pro_info() -> LicenseInfo:
    return _community_license_info("user@example.com")


class TestLicenseInfo:
    def test_plan_is_pro(self, pro_info):
        assert pro_info.plan == "pro"

    def test_is_valid(self, pro_info):
        assert pro_info.is_valid is True

    def test_no_error(self, pro_info):
        assert pro_info.error_message is None

    def test_features_cover_pro_set(self, pro_info):
        assert set(pro_info.features) == set(PRO_FEATURES)


class TestServiceSemantics:
    def test_is_pro_when_valid(self, service, pro_info):
        assert service.is_pro(pro_info) is True

    def test_is_pro_false_when_invalid(self, service):
        invalid = LicenseInfo(email="u@e.com", plan="pro", is_valid=False)
        assert service.is_pro(invalid) is False

    def test_has_feature_all_pro_features(self, service, pro_info):
        for feature in PRO_FEATURES:
            assert service.has_feature(pro_info, feature) is True

    def test_has_feature_pro_features_allowed(self, service, pro_info):
        # Pro 版：这些功能全部放行（review.py 的 trading_plan_check 等）
        for feature in ("trading_plan_check", "advanced_courses", "export_reports", "workflow"):
            assert service.has_feature(pro_info, feature) is True

    def test_has_feature_invalid_license(self, service):
        invalid = LicenseInfo(email="u@e.com", plan="pro", is_valid=False)
        assert service.has_feature(invalid, "portfolio_analysis") is False

    def test_get_available_servers_empty(self, service):
        assert service.get_available_servers() == []

    @pytest.mark.asyncio
    async def test_verify_app_token_returns_pro(self, service):
        info = await service.verify_app_token("any-token")
        assert info.plan == "pro"
        assert info.is_valid is True

    def test_get_license_service_singleton(self):
        assert get_license_service() is get_license_service()


class TestFastAPIDependencies:
    @pytest.mark.asyncio
    async def test_require_pro_passthrough(self, pro_info):
        result = await require_pro(license_info=pro_info)
        assert result is pro_info

    @pytest.mark.asyncio
    async def test_require_feature_passthrough(self, pro_info):
        check = require_feature("advanced_courses")
        result = await check(license_info=pro_info)
        assert result is pro_info

    def test_pro_features_is_full_set(self):
        # Pro 版功能集 = 全部放行的功能
        assert PRO_FEATURES == sorted(FREE_ACCESS_FEATURES)
        assert len(PRO_FEATURES) > 0


class TestProQuotas:
    def test_legacy_alias_is_pro_tier(self):
        assert COMMUNITY_TIER_FEATURES is PRO_TIER_FEATURES

    def test_analyst_quota(self):
        assert PRO_TIER_FEATURES.max_analysts == 6

    def test_researcher_quota(self):
        assert PRO_TIER_FEATURES.max_researchers == 2

    def test_concurrency(self):
        assert PRO_TIER_FEATURES.max_concurrent_executions == 5

    def test_daily_api_calls(self):
        assert PRO_TIER_FEATURES.daily_api_calls == 2000

    def test_workflow_quota(self):
        assert PRO_TIER_FEATURES.max_workflows == 50

    def test_custom_agents_allowed(self):
        assert PRO_TIER_FEATURES.allow_custom_agents is True


class TestLicenseManager:
    @pytest.fixture
    def manager(self) -> CommunityLicenseManager:
        return CommunityLicenseManager()

    def test_tier_pro(self, manager):
        from core.licensing.models import LicenseTier
        assert manager.tier == LicenseTier.PRO

    def test_singleton(self):
        assert CommunityLicenseManager() is CommunityLicenseManager()

    def test_check_limit_within(self, manager):
        # 分析师 6 名额：已有 5 个，可再加
        assert manager.check_limit("max_analysts", 5) is True

    def test_check_limit_exceeded(self, manager):
        # 分析师 6 名额：已有 6 个，不可再加
        assert manager.check_limit("max_analysts", 6) is False

    def test_get_remaining(self, manager):
        assert manager.get_remaining("max_analysts", 1) == 5
        assert manager.get_remaining("daily_api_calls", 60) == 1940

    def test_activate_not_supported(self, manager):
        ok, err = manager.activate("any-key")
        assert ok is False
        assert "不支持" in err

    def test_can_use_feature_respects_quota_flags(self, manager):
        # 布尔型功能按 Pro 档：行业/大盘分析师、并行执行均开启
        assert manager.can_use_feature("sector_analyst") is True
        assert manager.can_use_feature("index_analyst") is True
        assert manager.can_use_feature("parallel_execution") is True
