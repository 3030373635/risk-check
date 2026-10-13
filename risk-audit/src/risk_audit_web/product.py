"""重庆单地区审核器的产品配置边界。"""

from __future__ import annotations

from risk_audit_web.region_profiles import RegionCatalog, RegionProfile, RegionProfileError


PRODUCT_REGION_CODE = "chongqing"
PRODUCT_REGION_NAME = "重庆"


def require_product_region(catalog: RegionCatalog) -> RegionProfile:
    """返回重庆产品地区。

    Args:
        catalog: 已经完成路径和规则包校验的地区目录。

    Returns:
        唯一的重庆地区配置。

    Raises:
        RegionProfileError: 目录包含多个地区或不是重庆地区。
    """
    if tuple(catalog.profiles) != (PRODUCT_REGION_CODE,):
        raise RegionProfileError(
            f"{PRODUCT_REGION_NAME}审核器只能包含{PRODUCT_REGION_NAME}地区配置"
        )
    profile = catalog.require(PRODUCT_REGION_CODE)
    if profile.region_name != PRODUCT_REGION_NAME:
        raise RegionProfileError(
            f"{PRODUCT_REGION_NAME}审核器地区名称必须为{PRODUCT_REGION_NAME}"
        )
    return profile
