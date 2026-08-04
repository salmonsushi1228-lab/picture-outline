# picture-outline

# PoseFrame — 안드로이드 전용 AI 포즈 가이드 카메라

## 왜 앱과 서버로 나뉘었나요?

`mediapipe`와 `opencv-python`은 C++로 컴파일된 바이너리 패키지라서 안드로이드용
사전 빌드 wheel이 없습니다. `flet build apk`에 그대로 넣으면 빌드가 실패하거나,
설령 빌드돼도 실행 시 임포트에 실패합니다. 그래서:

- **`android_app/`** — 폰에 설치되는 실제 APK. `flet-camera`로 카메라 프리뷰/촬영만
  담당하고, mediapipe/opencv는 전혀 포함하지 않습니다.
- **`server/`** — 같은 Wi-Fi의 PC(또는 클라우드)에서 돌아가는 FastAPI 서버.
  실제 AI 연산(포즈 추출, 유사도 계산, 피드백 생성)은 전부 여기서 수행합니다.

폰 앱이 사진을 찍어 서버로 보내면, 서버가 분석 결과(매치율 %, 코칭 문구)만
JSON으로 돌려주고 앱은 그걸 화면에 표시하는 구조입니다.

```
[안드로이드 폰]                    [PC / 서버]
 flet-camera로 촬영  --사진 업로드-->  FastAPI + MediaPipe + OpenCV
 결과 HUD 표시        <--JSON 응답---  유사도 % + 피드백 문구
```

---

## 1. 서버 실행 (PC에서 먼저)

```bash
cd server
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

PC의 사설(로컬) IP 주소를 확인해두세요.
- Windows: `ipconfig` (IPv4 주소)
- macOS/Linux: `ifconfig` 또는 `ip a`

예: `192.168.0.12`

> 방화벽이 8000번 포트를 막고 있다면 허용해주세요. 폰이 PC와 같은 Wi-Fi에
> 있어야 접속됩니다. 외부망(카페, LTE 등)에서 쓰려면 ngrok 등으로 터널링하거나
> 서버를 클라우드에 배포해야 합니다.

## 2. 앱 로컬 테스트 (APK 빌드 전에 먼저 확인)

```bash
cd android_app
pip install -r requirements.txt
flet run main.py
```

`flet run`은 데스크톱 창으로도 뜨지만, **카메라 컨트롤은 Windows/macOS/Linux를
지원하지 않으므로** 실제 카메라 동작 확인은 안드로이드 기기나 에뮬레이터에서
해야 합니다. 실기기에서 확인하려면 [Flet 앱(Flet Studio)](https://flet.app)을
설치하고 QR/코드로 연결하거나, USB 디버깅 상태에서 관련 명령을 사용하세요
(정확한 플래그는 실행 중인 Flet 버전의 `flet run --help`로 확인해주세요).

앱을 열면 먼저 **설정(⚙️) 다이얼로그**가 뜹니다. 여기에 1번에서 확인한
`http://<PC의 사설 IP>:8000`을 입력하고 저장하세요.

## 3. APK 빌드

```bash
cd android_app
flet build apk --permissions camera
```

`pyproject.toml`에 이미 `android.permission.CAMERA`, `android.permission.INTERNET`이
명시되어 있으므로 `--permissions camera` 플래그는 중복 안전장치입니다.
처음 실행 시 Flutter SDK / Android SDK / JDK 17이 없으면 Flet이 자동으로
설치를 시도합니다 (인터넷 필요, 시간이 꽤 걸릴 수 있음).

빌드가 끝나면 `build/apk/` 아래에 `.apk` 파일이 생성됩니다. 이 파일을 폰에
직접 설치(사이드로드)하거나 Google Play Console에 업로드하면 됩니다.

---

## 사용 흐름

1. 앱 실행 → 설정에서 서버 주소 입력
2. 왼쪽 하단 버튼으로 레퍼런스 포즈 사진 업로드 → 서버가 분석 후 실루엣 오버레이 전송
3. 셔터 버튼으로 카메라 시작 → 약 1.3초 간격으로 자동 촬영, 서버에 전송, 매치율/피드백 HUD 갱신
4. 다시 셔터 버튼을 누르면 정지하고 마지막 매치율이 하단 "성장 기록"에 저장

## 알려진 제한사항

- **진짜 실시간 스트리밍이 아닙니다.** `camera` 플러그인의 raw 이미지 스트림
  (`start_image_stream`)을 쓰면 더 매끄럽게 만들 수 있지만, 기기마다 프레임
  포맷(YUV420 등)이 달라 디코딩 안정성이 떨어져서, 우선 `take_picture()`로
  주기적 스냅샷을 찍는 방식으로 구현했습니다. 더 빠른 반응이 필요하면
  `FRAME_INTERVAL_SEC` 값을 줄이거나(카메라 하드웨어 셔터 지연이 있어 한계는
  있습니다), 이미지 스트리밍 기반으로 후속 개선이 가능합니다.
- **네트워크 지연에 영향을 받습니다.** 서버 응답이 느리면 매치율 갱신도
  늦어집니다. 같은 Wi-Fi 내에서는 보통 200~500ms 수준입니다.
- **서버가 항상 켜져 있어야 합니다.** PC를 끄면 앱에서 "서버에 연결할 수
  없습니다" 메시지가 뜹니다. 상시 운영하려면 클라우드(VM, 컨테이너 등)에
  `server/`를 배포하세요.
- `flet-camera`, `Camera` 컨트롤의 정확한 메서드/이벤트 시그니처는 Flet
  버전에 따라 조금씩 달라질 수 있으니, 빌드 전에
  [공식 Camera 컨트롤 문서](https://flet.dev/docs/controls/camera/)와
  실제 설치된 `flet`/`flet-camera` 버전을 한 번 대조해보시길 권합니다.

## 파일 구조

```
android_app/
  main.py          # Flet UI + flet-camera 연동 + 서버 API 호출
  theme.py         # 다크 모드 컬러/스타일 상수 (기존과 동일)
  pyproject.toml   # flet build 설정 (안드로이드 카메라 권한)
  requirements.txt

server/
  app.py           # FastAPI: /reference, /frame 엔드포인트
  pose_engine.py   # MediaPipe 포즈 추출 / 유사도 계산 (기존 로직 재사용)
  requirements.txt

desktop_legacy/    # 이전에 만든 데스크톱(OpenCV 웹캠) 단일 앱 버전 — 참고용 보관
```
