# Image AI Studio (Local Image AI Studio 서버판)

채팅형 이미지·영상 생성 UI(React)와 FastAPI 백엔드를 한 컨테이너로 묶은 CPU 전용 서비스다. 생성은 같은 Compose 프로젝트의 `comfyui` 서비스(GPU)에 HTTP/WebSocket으로 맡긴다. GPU·Docker 소켓은 연결하지 않는다.

- 소스: `app/`은 Local Image AI Studio 커밋 `333b4c2`의 스냅숏이다(LLM 에이전트·스토리보드·자동 마스크 제외). 이미지 태그도 이 커밋으로 고정한다.
- 접속: http://192.168.50.14:8190 (동아리방 LAN, **ComfyUI와 같이 로그인 없음**). GPU 정책에서 `comfyui` 그룹의 동반 서비스(`companions: [studio]`)라 ComfyUI와 **항상 같이 켜지고 꺼진다**. 포털의 ComfyUI 카드와 Image AI Studio 카드는 따로 보이지만 둘 다 같은 그룹을 제어한다(`scripts/service_config.py`가 같은 GPU 그룹을 여러 카드가 공유하는 것만 허용).
- ComfyUI가 꺼져 있으면 생성 요청은 "포털에서 ComfyUI를 켠 뒤 다시 시도" 오류로 끝난다. 백엔드는 ComfyUI를 직접 켜거나 GPU를 중재하지 않는다(서버의 GPU 컨트롤러와 `gnuvil_lifecycle` 계약을 그대로 따른다). 백엔드 큐에 작업이 남아 있는 동안 포털에서 ComfyUI를 종료하면 남은 작업은 실패로 기록된다.

## 데이터 경로

| 호스트 | 컨테이너 | 용도 |
| --- | --- | --- |
| `/srv/ai-data/state/studio` | `/data/state` | `app.db`(생성 기록·캐릭터), `uploads/`, `danbooru_tags.csv`(태그 자동완성, 없으면 기능만 꺼짐) |
| `/srv/ai-data/outputs/studio` | `/data/outputs` | 결과 이미지·영상 (ComfyUI `/view`로 받아 저장) |
| `/srv/ai-data/models/comfyui/loras` | `/models/loras` (ro) | LoRA 정보(트리거 후보) |
| `/srv/ai-data/outputs/comfyui` | `/comfyui-output` (ro) | 누락 결과 복구용 |

## ComfyUI 쪽 변경 (같은 브랜치)

- 커스텀 노드 5개를 커밋 고정으로 이미지에 포함하고 허용 목록에 추가: ComfyUI-GGUF(Qwen 20B·Wan 14B GGUF), ComfyUI_IPAdapter_plus, ComfyUI-Impact-Pack/Subpack(얼굴·손 디테일러), comfyui_controlnet_aux(DWPose, TorchScript 모델 사용 → onnxruntime 불필요). 이미지 태그 `0d0e105-cu126-studio1`.
- Python 의존성은 `services/comfyui/uv.lock`에 추가. ultralytics가 끌어오는 polars는 AVX2 런타임 대신 `polars-runtime-compat`로 교체(Ivy Bridge 대응).
- 모델 폴더 9개를 읽기 전용으로 마운트: loras, vae, controlnet, ipadapter, clip_vision, ultralytics, diffusion_models, text_encoders, frame_interpolation.
- `gnuvil_lifecycle`, GPU 정책, 포트, 프로필은 그대로다.

## 배포 (AI VM, root)

```
cd /opt/gnuvil-ai && git pull
sudo sh services/studio/setup-data.sh
# 모델 복사 (root 소유, 0644): /srv/ai-data/models/comfyui/<폴더>/
#   얼굴·손 YOLO(.pt)는 ultralytics/bbox/ 에 넣고, 파일 이름을
#   /srv/ai-data/state/comfyui/user/default/ComfyUI-Impact-Subpack/model-whitelist.txt 에 한 줄씩 추가
# (선택) 태그 CSV: /srv/ai-data/state/studio/danbooru_tags.csv (소유자 1000)
sudo make build SERVICE=comfyui
sudo docker compose --profile comfyui build studio
sudo systemctl restart gnuvil-ai-controller   # portal.yaml의 새 카드 반영
sudo make switch SERVICE=comfyui
# ComfyUI를 켜면 studio도 같이 켜진다 (포털 카드 둘 중 아무거나)
sudo make status
```

## 검증 순서 (아직 실제 VM에서 실행하지 않음)

1. ComfyUI 로그에서 커스텀 노드 5개가 `Import failed` 없이 로드되는지 확인한다. 특히 ultralytics/polars/opencv가 Ivy Bridge에서 `Illegal instruction` 없이 import되는지 본다.
2. http://192.168.50.14:8190 → 왼쪽 GPU 칸에 Tesla V100이 표시되는지(ComfyUI `/system_stats` 경유).
3. txt2img 1장 → 얼굴·손 디테일러 1건 → Qwen Image 1장(20B GGUF, V100에서 FP16 경로 확인) → Wan2.2 5B 영상 1건 → 프레임 보간 1건.
4. ControlNet openpose 미리보기 1회(첫 실행 시 DWPose 가중치가 `state/comfyui/controlnet_aux`로 내려받아짐 — 컨테이너 외부 접속 필요).
5. 생성 중 포털의 ComfyUI 종료가 거부되고, 유휴 상태에서 종료한 뒤 Studio에서 생성하면 안내 오류가 나오는지 확인한다.
