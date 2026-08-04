"""
theme.py
--------
앱 전역에서 사용하는 다크 모드 컬러 팔레트, 폰트, 라운딩 값 등을 정의합니다.
상용 카메라 앱(Instagram, SNOW, NOMO 스타일)을 참고한 다크 테마입니다.
"""

import flet as ft


class Colors:
    # 배경
    BG_PRIMARY = "#0A0A0C"          # 최상위 배경 (거의 블랙)
    BG_SECONDARY = "#141417"        # 카드/패널 배경
    BG_SURFACE = "#1C1C21"          # 컨트롤 바 배경
    BG_VIEWFINDER = "#000000"       # 뷰파인더 배경

    # 강조색 (포인트 컬러 - 민트/네온 그린 계열, 매치율 표시에 사용)
    ACCENT = "#3DDC97"
    ACCENT_SOFT = "#3DDC9733"
    WARNING = "#FFB020"
    DANGER = "#FF5A5F"

    # 텍스트
    TEXT_PRIMARY = "#F5F5F7"
    TEXT_SECONDARY = "#A0A0A8"
    TEXT_DISABLED = "#5C5C63"

    # 테두리 / 구분선
    BORDER = "#2A2A30"
    BORDER_SOFT = "#2A2A3055"

    # 오버레이
    OVERLAY_DARK = "#00000099"
    CHIP_BG = "#1C1C21CC"


class Radius:
    VIEWFINDER = 28
    CARD = 20
    BUTTON = 18
    CHIP = 30
    SHUTTER = 100  # 완전한 원


class Spacing:
    XS = 4
    SM = 8
    MD = 16
    LG = 24
    XL = 32


def app_theme() -> ft.Theme:
    """Flet Theme 객체를 생성해서 반환합니다."""
    return ft.Theme(
        color_scheme_seed=Colors.ACCENT,
        color_scheme=ft.ColorScheme(
            primary=Colors.ACCENT,
            background=Colors.BG_PRIMARY,
            surface=Colors.BG_SECONDARY,
        ),
        font_family="SF Pro Display, Segoe UI, Roboto",
    )


def badge_color_for_score(score: float) -> str:
    """포즈 일치율(0~100)에 따라 뱃지 색상을 반환합니다."""
    if score >= 80:
        return Colors.ACCENT
    if score >= 50:
        return Colors.WARNING
    return Colors.DANGER
