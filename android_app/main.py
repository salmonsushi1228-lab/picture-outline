"""
android_app/main.py
--------------------
PoseFrame 안드로이드 클라이언트.

이 파일은 mediapipe/opencv를 전혀 사용하지 않습니다 (안드로이드 wheel 미지원).
대신 Flet의 네이티브 Camera 컨트롤(flet-camera, camera Flutter 패키지 기반)로
실제 카메라 프리뷰/촬영만 담당하고, 촬영한 사진을 분석 서버(server/app.py)로
전송해 유사도/피드백을 받아와 화면에 표시합니다.

설치:
    pip install -r requirements.txt

로컬 확인 (에뮬레이터/실기기, USB 디버깅 등):
    flet run main.py

APK 빌드:
    flet build apk --permissions camera
    (pyproject.toml 에도 동일 권한이 명시되어 있습니다)

주의:
    - "설정" 화면에서 분석 서버 주소(server/app.py 를 실행 중인 컴퓨터의
      사설 IP:포트)를 입력해야 정상 동작합니다. 예) http://192.168.0.12:8000
    - 진짜 실시간 스트리밍이 아니라, 약 1~1.5초 간격으로 사진을 찍어 서버에
      보내는 "주기적 스냅샷" 방식입니다. camera 플러그인의 raw 이미지 스트림
      (start_image_stream)을 이용하면 더 매끄럽게 만들 수 있지만, 기기별로
      포맷(YUV420 등) 디코딩이 달라 안정성이 떨어져 우선 스냅샷 방식으로
      구현했습니다.
"""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import Optional

import flet as ft
import flet_camera as fc
import httpx

from theme import Colors, Radius, Spacing, app_theme, badge_color_for_score


DEFAULT_SERVER_URL = "http://192.168.0.10:8000"
FRAME_INTERVAL_SEC = 1.3


@dataclass
class SessionRecord:
    timestamp: datetime.datetime
    similarity: float


@dataclass
class AppState:
    server_url: str = DEFAULT_SERVER_URL
    session_id: Optional[str] = None
    guide_opacity: float = 0.55
    grid_enabled: bool = False
    similarity: float = 0.0
    feedback: str = "먼저 설정에서 서버 주소를 입력하고, 레퍼런스 포즈를 업로드하세요."
    streaming: bool = False
    camera_ready: bool = False
    history: list = field(default_factory=list)


async def main(page: ft.Page) -> None:
    page.title = "PoseFrame — AI 포즈 가이드 카메라"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = app_theme()
    page.bgcolor = Colors.BG_PRIMARY
    page.padding = 0

    state = AppState()
    http_client = httpx.AsyncClient(timeout=15.0)
    capture_task: Optional[asyncio.Task] = None

    # ==================================================================
    # 오버레이 위젯 (카메라 프리뷰 위에 얹히는 것들)
    # ==================================================================
    silhouette_image = ft.Image(
        fit=ft.ImageFit.CONTAIN,
        opacity=state.guide_opacity,
        visible=False,
    )

    def build_grid_lines() -> ft.Stack:
        lines = []
        for i in (1, 2):
            lines.append(ft.Container(left=0, right=0, top=f"{i * 33.33}%", height=1, bgcolor="#FFFFFF33"))
            lines.append(ft.Container(top=0, bottom=0, left=f"{i * 33.33}%", width=1, bgcolor="#FFFFFF33"))
        return ft.Stack(lines, expand=True)

    grid_overlay = ft.Container(content=build_grid_lines(), visible=state.grid_enabled, expand=True)

    match_badge_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor=Colors.TEXT_DISABLED)
    match_badge_text = ft.Text("0% Match", size=13, weight=ft.FontWeight.W_600, color=Colors.TEXT_SECONDARY)
    match_badge = ft.Container(
        content=ft.Row([match_badge_dot, match_badge_text], spacing=6, tight=True),
        padding=ft.padding.symmetric(horizontal=14, vertical=8),
        border_radius=Radius.CHIP,
        bgcolor=Colors.CHIP_BG,
        top=Spacing.MD,
        right=Spacing.MD,
    )

    feedback_chip_text = ft.Text(state.feedback, size=13, color=Colors.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER)
    feedback_chip = ft.Container(
        content=feedback_chip_text,
        padding=ft.padding.symmetric(horizontal=18, vertical=12),
        border_radius=Radius.CHIP,
        bgcolor=Colors.CHIP_BG,
        bottom=Spacing.LG,
        left=Spacing.LG,
        right=Spacing.LG,
        alignment=ft.alignment.center,
    )

    camera_placeholder = ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.PHOTO_CAMERA_OUTLINED, size=48, color=Colors.TEXT_DISABLED),
                ft.Text("셔터 버튼을 눌러 카메라를 시작하세요", color=Colors.TEXT_DISABLED, size=13),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=Spacing.SM,
        ),
        alignment=ft.alignment.center,
        expand=True,
    )

    overlay_stack = ft.Stack(
        [camera_placeholder, silhouette_image, grid_overlay, match_badge, feedback_chip],
        expand=True,
    )

    camera = fc.Camera(
        expand=True,
        preview_enabled=True,
        content=overlay_stack,
    )

    viewfinder = ft.Container(
        content=camera,
        expand=True,
        margin=ft.margin.symmetric(horizontal=Spacing.MD),
        border_radius=Radius.VIEWFINDER,
        border=ft.border.all(1, Colors.BORDER),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        bgcolor=Colors.BG_VIEWFINDER,
    )

    # ==================================================================
    # 상단 바
    # ==================================================================
    def on_opacity_change(e: ft.ControlEvent) -> None:
        state.guide_opacity = e.control.value
        silhouette_image.opacity = state.guide_opacity
        page.update()

    opacity_slider = ft.Slider(
        min=0, max=1, value=state.guide_opacity,
        active_color=Colors.ACCENT, inactive_color=Colors.BORDER,
        on_change=on_opacity_change, expand=True,
    )

    def toggle_grid(e: ft.ControlEvent) -> None:
        state.grid_enabled = not state.grid_enabled
        grid_overlay.visible = state.grid_enabled
        grid_button.bgcolor = Colors.ACCENT_SOFT if state.grid_enabled else Colors.BG_SURFACE
        grid_button.content.color = Colors.ACCENT if state.grid_enabled else Colors.TEXT_SECONDARY
        page.update()

    grid_button = ft.Container(
        content=ft.Icon(ft.Icons.GRID_3X3, size=20, color=Colors.TEXT_SECONDARY),
        width=40, height=40, border_radius=12, bgcolor=Colors.BG_SURFACE,
        alignment=ft.alignment.center, on_click=toggle_grid,
        tooltip="구도 그리드 (삼등분의 법칙)",
    )

    def open_settings(e: ft.ControlEvent | None = None) -> None:
        page.open(settings_dialog)

    settings_button = ft.Container(
        content=ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=20, color=Colors.TEXT_SECONDARY),
        width=40, height=40, border_radius=12, bgcolor=Colors.BG_SURFACE,
        alignment=ft.alignment.center, on_click=open_settings,
        tooltip="서버 주소 설정 / 사용법",
    )

    top_bar = ft.Container(
        content=ft.Row(
            [ft.Icon(ft.Icons.OPACITY, size=18, color=Colors.TEXT_SECONDARY), opacity_slider, grid_button, settings_button],
            spacing=Spacing.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
    )

    # ==================================================================
    # 설정 다이얼로그 (서버 주소 입력 + 사용법)
    # ==================================================================
    server_url_field = ft.TextField(
        label="분석 서버 주소",
        value=state.server_url,
        hint_text="예: http://192.168.0.12:8000",
        border_color=Colors.BORDER,
        color=Colors.TEXT_PRIMARY,
    )

    def save_settings(e: ft.ControlEvent) -> None:
        new_url = (server_url_field.value or "").strip().rstrip("/")
        if new_url:
            state.server_url = new_url
        page.close(settings_dialog)
        page.open(ft.SnackBar(ft.Text("서버 주소가 저장되었습니다.")))
        page.update()

    settings_dialog = ft.AlertDialog(
        modal=True,
        bgcolor=Colors.BG_SECONDARY,
        title=ft.Text("설정 & 사용법", color=Colors.TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "이 앱은 촬영한 사진을 분석 서버로 보내 포즈를 비교합니다. "
                        "같은 Wi-Fi에 연결된 컴퓨터에서 서버(server/app.py)를 먼저 실행한 뒤, "
                        "그 컴퓨터의 사설 IP 주소를 아래에 입력하세요.",
                        size=12, color=Colors.TEXT_SECONDARY,
                    ),
                    server_url_field,
                    ft.Divider(color=Colors.BORDER),
                    ft.Text("1. 왼쪽 하단 버튼으로 레퍼런스 사진 업로드", size=12, color=Colors.TEXT_SECONDARY),
                    ft.Text("2. 셔터 버튼으로 카메라 시작", size=12, color=Colors.TEXT_SECONDARY),
                    ft.Text("3. 약 1.3초마다 자동으로 사진을 찍어 매치율을 갱신", size=12, color=Colors.TEXT_SECONDARY),
                    ft.Text("4. 다시 셔터 버튼을 누르면 정지하고 기록에 저장", size=12, color=Colors.TEXT_SECONDARY),
                ],
                spacing=Spacing.SM, tight=True,
            ),
            width=320,
        ),
        actions=[ft.TextButton("저장", style=ft.ButtonStyle(color=Colors.ACCENT), on_click=save_settings)],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    # ==================================================================
    # 레퍼런스 업로드
    # ==================================================================
    def _read_file_bytes(path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    async def on_reference_picked(e: ft.FilePickerResultEvent) -> None:
        if not e.files or not e.files[0].path:
            return
        raw = _read_file_bytes(e.files[0].path)
        if raw is None:
            page.open(ft.SnackBar(ft.Text("이미지 파일을 읽지 못했습니다.")))
            page.update()
            return

        try:
            resp = await http_client.post(
                f"{state.server_url}/reference",
                files={"file": ("reference.jpg", raw, "image/jpeg")},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as ex:
            detail = ex.response.json().get("detail", "알 수 없는 오류") if ex.response is not None else str(ex)
            page.open(ft.SnackBar(ft.Text(f"업로드 실패: {detail}")))
            page.update()
            return
        except httpx.HTTPError:
            page.open(ft.SnackBar(ft.Text("서버에 연결할 수 없습니다. 설정에서 서버 주소를 확인해주세요.")))
            page.update()
            return

        state.session_id = data["session_id"]
        silhouette_b64 = data.get("silhouette_png_base64", "")
        if silhouette_b64:
            silhouette_image.src_base64 = silhouette_b64
            silhouette_image.visible = True

        reference_thumb.src = e.files[0].path
        reference_thumb.visible = True
        upload_icon.visible = False

        feedback_chip_text.value = "레퍼런스 등록 완료! 카메라를 시작해보세요."
        page.update()

    file_picker = ft.FilePicker(on_result=on_reference_picked)
    page.overlay.append(file_picker)

    def on_upload_click(e: ft.ControlEvent) -> None:
        file_picker.pick_files(
            dialog_title="레퍼런스 포즈 사진 선택",
            file_type=ft.FilePickerFileType.IMAGE,
            allow_multiple=False,
        )

    reference_thumb = ft.Image(width=44, height=44, fit=ft.ImageFit.COVER, border_radius=12, visible=False)
    upload_icon = ft.Icon(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, size=22, color=Colors.TEXT_SECONDARY)

    upload_button = ft.Container(
        content=ft.Stack([upload_icon, reference_thumb], alignment=ft.alignment.center),
        width=52, height=52, border_radius=16, bgcolor=Colors.BG_SURFACE,
        border=ft.border.all(1, Colors.BORDER), alignment=ft.alignment.center,
        on_click=on_upload_click, tooltip="레퍼런스 포즈 업로드",
    )

    # ==================================================================
    # 카메라 시작/정지 + 주기적 촬영 루프
    # ==================================================================
    async def ensure_camera_initialized() -> bool:
        if state.camera_ready:
            return True
        try:
            cameras = await camera.get_available_cameras()
            if not cameras:
                page.open(ft.SnackBar(ft.Text("사용 가능한 카메라가 없습니다.")))
                page.update()
                return False
            front = next((c for c in cameras if c.lens_direction == fc.CameraLensDirection.FRONT), cameras[0])
            await camera.initialize(
                description=front,
                resolution_preset=fc.ResolutionPreset.MEDIUM,
                enable_audio=False,
            )
            state.camera_ready = True
            camera_placeholder.visible = False
            return True
        except Exception:
            page.open(
                ft.SnackBar(
                    ft.Text("카메라를 초기화하지 못했습니다. 카메라 권한이 허용되어 있는지 확인해주세요.")
                )
            )
            page.update()
            return False

    async def capture_loop() -> None:
        while state.streaming:
            if state.session_id is None:
                feedback_chip_text.value = "레퍼런스 포즈를 먼저 업로드해주세요."
                page.update()
                await asyncio.sleep(FRAME_INTERVAL_SEC)
                continue
            try:
                photo_bytes = await camera.take_picture()
                resp = await http_client.post(
                    f"{state.server_url}/frame",
                    data={"session_id": state.session_id},
                    files={"file": ("frame.jpg", photo_bytes, "image/jpeg")},
                )
                resp.raise_for_status()
                result = resp.json()
                state.similarity = float(result.get("similarity", 0.0))
                state.feedback = result.get("feedback", "")

                score_color = badge_color_for_score(state.similarity)
                match_badge_text.value = f"{state.similarity:.0f}% Match"
                match_badge_dot.bgcolor = score_color
                match_badge_text.color = score_color
                feedback_chip_text.value = state.feedback
                page.update()
            except httpx.HTTPError:
                feedback_chip_text.value = "서버 연결에 실패했습니다. 설정에서 주소를 확인해주세요."
                page.update()
            except Exception:
                pass
            await asyncio.sleep(FRAME_INTERVAL_SEC)

    async def on_shutter_click(e: ft.ControlEvent) -> None:
        nonlocal capture_task
        if not state.streaming:
            ready = await ensure_camera_initialized()
            if not ready:
                return
            state.streaming = True
            shutter_icon.name = ft.Icons.STOP_ROUNDED
            page.update()
            capture_task = page.run_task(capture_loop)
        else:
            state.streaming = False
            shutter_icon.name = ft.Icons.CIRCLE
            if state.history or state.similarity > 0:
                state.history.append(SessionRecord(datetime.datetime.now(), state.similarity))
                refresh_dashboard()
            page.open(ft.SnackBar(ft.Text(f"정지! 마지막 매치율 {state.similarity:.0f}% 로 기록되었습니다.")))
            page.update()

    shutter_icon = ft.Icon(ft.Icons.CIRCLE, size=30, color=Colors.BG_PRIMARY)
    shutter_button = ft.Container(
        content=shutter_icon,
        width=76, height=76, border_radius=Radius.SHUTTER, bgcolor=Colors.ACCENT,
        alignment=ft.alignment.center, on_click=on_shutter_click,
        border=ft.border.all(4, Colors.BG_SURFACE),
        tooltip="카메라 시작 / 정지",
    )

    async def on_switch_camera(e: ft.ControlEvent) -> None:
        if not state.camera_ready:
            page.open(ft.SnackBar(ft.Text("먼저 카메라를 시작해주세요.")))
            page.update()
            return
        cameras = await camera.get_available_cameras()
        if len(cameras) < 2:
            page.open(ft.SnackBar(ft.Text("전환 가능한 다른 카메라가 없습니다.")))
            page.update()
            return
        current = cameras[0]
        other = next((c for c in cameras if c.lens_direction != current.lens_direction), cameras[-1])
        await camera.set_description(other)
        page.open(ft.SnackBar(ft.Text("카메라를 전환했습니다.")))
        page.update()

    switch_camera_button = ft.Container(
        content=ft.Icon(ft.Icons.CAMERASWITCH_OUTLINED, size=22, color=Colors.TEXT_SECONDARY),
        width=52, height=52, border_radius=16, bgcolor=Colors.BG_SURFACE,
        border=ft.border.all(1, Colors.BORDER), alignment=ft.alignment.center,
        on_click=on_switch_camera, tooltip="카메라 전환",
    )

    # ==================================================================
    # 포즈 라이브러리 바텀시트
    # ==================================================================
    POSE_LIBRARY = [
        {"name": "스탠다드 정면", "desc": "어깨 넓이로 서서 정면을 바라보는 기본 포즈", "icon": ft.Icons.ACCESSIBILITY_NEW},
        {"name": "다이나믹 워킹", "desc": "한 발을 내딛는 자연스러운 걷는 포즈", "icon": ft.Icons.DIRECTIONS_WALK},
        {"name": "캐주얼 사이드", "desc": "몸을 살짝 틀어 옆모습을 강조하는 포즈", "icon": ft.Icons.PERSON_OUTLINE},
        {"name": "파워 포즈", "desc": "허리에 손을 올려 당당함을 강조하는 포즈", "icon": ft.Icons.BOLT},
    ]

    def select_library_pose(pose: dict) -> None:
        page.close(pose_library_sheet)
        page.open(ft.SnackBar(ft.Text(f"'{pose['name']}' 스타일을 참고해 레퍼런스 사진을 업로드해보세요.")))
        page.update()

    def build_pose_library_items() -> list:
        items = []
        for pose in POSE_LIBRARY:
            items.append(
                ft.ListTile(
                    leading=ft.Container(
                        content=ft.Icon(pose["icon"], color=Colors.ACCENT, size=22),
                        width=44, height=44, border_radius=12, bgcolor=Colors.ACCENT_SOFT,
                        alignment=ft.alignment.center,
                    ),
                    title=ft.Text(pose["name"], color=Colors.TEXT_PRIMARY, weight=ft.FontWeight.W_600),
                    subtitle=ft.Text(pose["desc"], color=Colors.TEXT_SECONDARY, size=12),
                    on_click=lambda e, p=pose: select_library_pose(p),
                )
            )
        return items

    def open_pose_library(e: ft.ControlEvent) -> None:
        page.open(pose_library_sheet)

    pose_library_button = ft.Container(
        content=ft.Icon(ft.Icons.STYLE_OUTLINED, size=22, color=Colors.TEXT_SECONDARY),
        width=52, height=52, border_radius=16, bgcolor=Colors.BG_SURFACE,
        border=ft.border.all(1, Colors.BORDER), alignment=ft.alignment.center,
        on_click=open_pose_library, tooltip="포즈 라이브러리",
    )

    pose_library_sheet = ft.BottomSheet(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Container(width=40, height=4, border_radius=2, bgcolor=Colors.BORDER,
                                 alignment=ft.alignment.center, margin=ft.margin.only(bottom=Spacing.SM)),
                    ft.Text("포즈 라이브러리", size=16, weight=ft.FontWeight.BOLD, color=Colors.TEXT_PRIMARY),
                    ft.Text("추천 포즈 스타일을 선택해 참고하세요", size=12, color=Colors.TEXT_SECONDARY),
                    ft.Divider(color=Colors.BORDER, height=Spacing.MD),
                    ft.Column(build_pose_library_items(), spacing=0),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=Spacing.XS,
            ),
            padding=Spacing.LG, bgcolor=Colors.BG_SECONDARY,
            border_radius=ft.border_radius.only(top_left=Radius.CARD, top_right=Radius.CARD),
        ),
        open=False,
    )

    bottom_bar = ft.Container(
        content=ft.Row(
            [upload_button, ft.Container(expand=True), shutter_button, ft.Container(expand=True),
             switch_camera_button, pose_library_button],
            alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.MD),
    )

    # ==================================================================
    # 성장 기록 대시보드
    # ==================================================================
    dashboard_chart = ft.Column(spacing=6)
    dashboard_summary = ft.Text("아직 기록이 없습니다. 촬영을 해보세요!", size=12, color=Colors.TEXT_SECONDARY)

    def refresh_dashboard() -> None:
        dashboard_chart.controls.clear()
        recent = state.history[-8:]
        if recent:
            best = max(r.similarity for r in state.history)
            avg = sum(r.similarity for r in state.history) / len(state.history)
            dashboard_summary.value = f"총 {len(state.history)}회 기록 · 평균 {avg:.0f}% · 최고 {best:.0f}%"
            for r in recent:
                dashboard_chart.controls.append(
                    ft.Row(
                        [
                            ft.Text(r.timestamp.strftime("%H:%M"), size=11, color=Colors.TEXT_SECONDARY, width=44),
                            ft.Container(
                                content=ft.Container(
                                    width=max(4, r.similarity * 1.8), height=10, border_radius=5,
                                    bgcolor=badge_color_for_score(r.similarity),
                                ),
                                bgcolor=Colors.BG_SURFACE, width=180, border_radius=5, padding=0,
                            ),
                            ft.Text(f"{r.similarity:.0f}%", size=11, color=Colors.TEXT_PRIMARY, width=36),
                        ],
                        spacing=Spacing.SM,
                    )
                )
        page.update()

    dashboard_card = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [ft.Icon(ft.Icons.SHOW_CHART_ROUNDED, color=Colors.ACCENT, size=18),
                     ft.Text("나의 포즈 성장 기록", size=14, weight=ft.FontWeight.W_600, color=Colors.TEXT_PRIMARY)],
                    spacing=Spacing.SM,
                ),
                dashboard_summary,
                ft.Container(height=Spacing.SM),
                dashboard_chart,
            ],
            spacing=Spacing.SM,
        ),
        padding=Spacing.MD,
        margin=ft.margin.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
        bgcolor=Colors.BG_SECONDARY, border_radius=Radius.CARD, border=ft.border.all(1, Colors.BORDER),
    )

    # ==================================================================
    # 전체 레이아웃
    # ==================================================================
    header = ft.Container(
        content=ft.Row(
            [
                ft.Text("PoseFrame", size=20, weight=ft.FontWeight.BOLD, color=Colors.TEXT_PRIMARY),
                ft.Container(
                    content=ft.Text("AI GUIDE", size=10, weight=ft.FontWeight.BOLD, color=Colors.ACCENT),
                    padding=ft.padding.symmetric(horizontal=8, vertical=3),
                    bgcolor=Colors.ACCENT_SOFT, border_radius=8,
                ),
            ],
            spacing=Spacing.SM,
        ),
        padding=ft.padding.only(left=Spacing.MD, top=Spacing.LG, bottom=Spacing.XS),
    )

    page.add(
        ft.SafeArea(
            content=ft.Column(
                [header, top_bar, viewfinder, bottom_bar, ft.Divider(color=Colors.BORDER, height=1), dashboard_card],
                spacing=Spacing.XS, expand=True,
            ),
            expand=True,
        )
    )

    page.open(settings_dialog)

    async def on_disconnect(e: ft.ControlEvent) -> None:
        state.streaming = False
        await http_client.aclose()

    page.on_disconnect = on_disconnect


if __name__ == "__main__":
    ft.run(main)
