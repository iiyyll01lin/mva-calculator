import pytest

import main


def test_p_modifier_example_from_spec():
    """EX: 組(一種方向)+對準+較難處理---->TMU=16+8+8"""
    total, addon, wi = main.calculate_p_with_modifiers(
        base_p_index=16,
        modifiers=["对准", "较难处理"],
    )
    assert total == 32
    assert addon == 16
    # WI should show 對準 but not 較難處理
    assert "对准" in wi
    assert "较难处理" not in wi


def test_m_max_rule_from_spec_example():
    """EX: 理(<=4(10)) TMU=6; 手度(<=180) TMU=10; 無腳步移動 → max=10"""
    assert main.calculate_m_tmu_max(distance_cm=10, hand_angle_deg=180, foot_cm=None) == 10


@pytest.mark.parametrize(
    "seconds, expected_tmu",
    [
        (0.216, 6),
        (1.0, 28),
        (2.0, 56),
    ],
)
def test_x_time_to_tmu_conversion(seconds, expected_tmu):
    assert int(round(seconds / main.TMU_FACTOR)) == expected_tmu


def test_fixed_x_like_scan_is_6_tmu():
    """Spec: 0.216S == 6 TMU, these are fixed-time actions in mapping."""
    for verb in ["锁附固定", "刷PPID", "刷工单二维码", "刷条形码", "扫描"]:
        profile = main.ACTION_SKILL_MAPPING.get(verb)
        assert profile is not None
        assert profile.get("tmu_default") == 6
        assert profile.get("fixed") is True


def test_tool_distance_lookup_table_edges():
    """理/穿/推/拉/贴附/去除/撕除/撕开/折/擦拭: distance lookup table."""
    assert main.lookup_tool_distance_tmu(2.5) == 3
    assert main.lookup_tool_distance_tmu(10) == 6
    assert main.lookup_tool_distance_tmu(25) == 10
    assert main.lookup_tool_distance_tmu(45) == 16
    assert main.lookup_tool_distance_tmu(75) == 24


def test_derive_most_tmu_for_tool_action_distance_and_angle_max():
    """Tool actions: distance table + angle table + foot table → take max; no extra scaling."""
    action_profile = main.ACTION_SKILL_MAPPING["理"]
    params = {
        "M_distance_cm": 10,  # tool table => 6
        "M_hand_angle": 180,  # hand angle => 10
        # foot not provided
    }
    tmu = main.derive_most_tmu_from_params(params, action_profile)
    assert tmu == 10
