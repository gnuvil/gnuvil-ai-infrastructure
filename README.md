# Gnuvil AI Infrastructure

Lenovo System x3500 M4의 Proxmox VE에서 실행하는 Gnuvil AI VM의 구성을 관리한다. 초기 구성은 Ubuntu Server 26.04 LTS 표준 amd64, Tesla V100 PCIe 패스스루, Docker, 영구 데이터 디렉터리, 실제 CUDA 연산을 확인하는 `gpu-smoke`와 경량 서비스 포털 **Gnuvil AI Lab**을 제공한다.

## 구성

| 경로 | 역할 |
| --- | --- |
| `AGENTS.md` | 하드웨어 제약과 장기 개발 원칙 |
| `bootstrap.sh`, `ansible/` | VM 내부 Ansible 설치와 공통 시스템 설정 |
| `compose.yaml`, `services/gpu-smoke/` | Compose 모듈과 Volta용 CUDA 검증 이미지 |
| `configs/gpu-policy.yaml`, `scripts/gpu-switch.py` | GPU 서비스 정책, 전환 및 상태 관리 |
| `scripts/validate.py`, `Makefile` | 정적 검사와 운영 명령 |
| `services/portal/`, `configs/portal.yaml` | FastAPI·Jinja2·HTMX 대시보드와 선언적 서비스 목록 |
| `scripts/service-controller.py`, `ansible/roles/portal/` | 제한된 호스트 제어 API와 systemd·소켓 권한 |

## 정적 검증

개발 머신에서 Bash, GNU Make, Python 3와 PyYAML, Docker CLI의 Compose 플러그인이 준비되어 있으면 `make validate`를 실행한다. YAML 중복 키, Python·Shell 문법, Compose 모델과 기본 Profile, GPU 정책 일치 여부를 검사한다. Ansible 도구가 있으면 Playbook syntax check와 오프라인 lint도 실행한다. Ansible syntax check에는 `ansible/requirements.yaml`의 컬렉션이 필요하다. 도구가 없을 때는 해당 검사를 `SKIP`으로 표시한다. 이 명령은 Compose 모델을 해석하며 Docker 데몬과 GPU 없이 실행할 수 있다.

## AI VM에서 사용하는 명령

VM의 사전 조건, 스토리지 선택과 재부팅 절차는 [설치 및 검증](docs/installation.md)에 있다.

| 명령 | 실행 위치 및 동작 |
| --- | --- |
| `make validate` | 개발 머신 또는 VM에서 정적 구성 검사. 일반 사용자 권한 |
| `sudo make bootstrap` | VM에 Ansible 전용 venv와 고정 버전 컬렉션 설치 |
| `sudo make plan` | VM에서 Ansible `--check --diff` 실행 |
| `sudo make configure` | VM의 기본 설정, 마운트, 드라이버, Docker, Toolkit 적용 |
| `sudo make build SERVICE=gpu-smoke` | VM에서 다이제스트로 고정된 CUDA 기반 이미지로 로컬 검증 이미지 빌드 |
| `sudo make switch SERVICE=gpu-smoke` | VM에서 GPU가 비었는지 확인하고 CUDA 검증 실행 |
| `sudo make gpu-off` | 관리 대상 컨테이너 종료와 GPU 유휴 상태 확인 |
| `sudo make status` | VM에서 Compose 컨테이너, GPU, VRAM, 연산 프로세스 조회 |

실행 중인 컨테이너가 있으면 먼저 작업을 확인하고 완료를 기다린다. 정지해도 되는 작업에 한해 `switch` 또는 `gpu-off`에 `CONFIRM=1`을 추가한다. 기본 Compose Profile에는 CPU 포털만 포함되며 GPU 서비스는 자동 실행되지 않는다. `gpu-smoke`는 명령이 끝나면 GPU를 반환한다.

## Gnuvil AI Lab

동아리 로고를 사용하는 한국어 대시보드에서 서비스·GPU·CPU·RAM 상태를 확인하고 등록 서비스를 제어한다. 자체 계정 DB는 없으며 인증된 Reverse Proxy가 접근을 제한한다. 초기 토큰은 비어 있어 인증 경로를 설정하기 전에는 화면·제어 요청이 거부된다. Docker 소켓은 웹 컨테이너에 연결하지 않는다. 현재 등록 서비스는 GPU 진단용 `gpu-smoke`, ComfyUI와 llama.cpp다. ComfyUI에는 안전한 drain adapter가 없어 웹 종료·전환을 제한하고 기존 CLI 운영을 유지한다.

개발 머신의 일반 사용자 권한으로 다음을 실행한다. Docker 데몬이나 GPU는 필요 없다.

```bash
uv sync --project services/portal --locked
make validate test PYTHON=services/portal/.venv/bin/python
```

AI VM 운영 명령은 `sudo make portal-build`, `portal-up`, `portal-down`, `portal-logs`다. 실제 실행 전에 [포털 설치·인증·서비스 등록·장애 대응](docs/portal.md)을 따른다.

실제 AI VM 배포 주소와 인증서·공용 암호 조회, 운영 명령은 [배포 기록](docs/portal-deployment.md)에 있다.

## 운영 문서

- [포털 설치 및 인수인계](docs/portal.md)
- [아키텍처와 GPU 운영 정책](docs/architecture.md)
- [설치 및 실제 VM 검증](docs/installation.md)
- [하드웨어 호환성 및 버전 근거](docs/compatibility.md)
- [재구축 및 백업 복구](docs/recovery.md)
- [ComfyUI와 NoobAI XL V-Pred 설치 및 사용](services/comfyui/README.md)
- [llama.cpp 설치 및 사용](services/llamacpp/README.md)

ComfyUI와 NoobAI 모델 매니페스트는 기존 VM의 운영 구성을 보존해 포함했다. 새 VM에서는 구성과 재부팅, CUDA 테스트 및 서비스별 검증을 수행하고 기준 버전을 기록한다. RVC, 음원 분리, Whisper, CPU 기반 Open WebUI는 향후 개별 서비스로 추가할 수 있다.
