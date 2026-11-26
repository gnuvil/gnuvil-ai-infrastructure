# 하드웨어 및 버전 호환성

대상은 Ivy Bridge Xeon E5-2680 v2 두 개와 Tesla V100 PCIe 16GB 한 개를 탑재한 Lenovo System x3500 M4다. VM에는 표준 amd64 Server ISO를 사용하고 AVX가 노출되는지 확인한다. Ubuntu의 `amd64v3` 변형에는 AVX2가 필요하다. 자체 빌드는 AVX2·AVX-512 없이 실행되는 CPU 코드를 생성해야 한다. 컨테이너에서도 미리 빌드한 Python Wheel과 시스템 라이브러리가 x86-64-v3를 요구할 수 있다. [Ubuntu CPU 아키텍처](https://ubuntu.com/architecture)

V100은 Compute Capability 7.0의 Volta GPU다. NVIDIA의 지원 표에 따르면 Volta를 지원하는 마지막 컴파일 도구 계열은 CUDA 12.x이고, 마지막 드라이버 계열은 R580이다. CUDA 12.9 Update 1의 Linux 동반 드라이버 버전은 575.57.08이다. R580 독점 모듈과 CUDA 12.9.1을 이 근거로 선정했다. V100은 네이티브 BF16·FP8 경로를 지원하지 않으므로 서비스 추가 시 FP16·FP32 및 메모리 요구량을 검토한다. [NVIDIA 아키텍처 매트릭스](https://docs.nvidia.com/datacenter/tesla/drivers/latest/cuda-toolkit-driver-and-architecture-matrix.html) · [CUDA 12.9.1 릴리스 노트](https://docs.nvidia.com/cuda/archive/12.9.1/cuda-toolkit-release-notes/index.html)

| 구성 요소 | 선택한 버전 | 근거 |
| --- | --- | --- |
| 게스트 OS | Ubuntu Server 26.04 LTS 표준 amd64 | Ubuntu Resolute 커널과 패키지 사용 |
| Ansible | `ansible-core 2.20.11`, `ansible.posix 2.2.2` | Ubuntu의 기본 Python 3.14 지원, 컬렉션은 `ansible-core >= 2.16` 지원 |
| NVIDIA 드라이버 | `nvidia-driver-580-server`, `nvidia-dkms-580-server` | Resolute `restricted`의 R580 독점 패키지. `580.178.04-0ubuntu0.26.04.1` 가용성 확인, 기본은 보안 패치가 제공되는 R580 브랜치 |
| Docker Engine 및 CLI | `29.10.0` | Docker 공식 Resolute amd64 APT 인덱스 |
| containerd · Buildx · Compose | `2.4.2-4` · `0.39.0` · `5.7.2` | 동일 APT 인덱스. Compose의 `include`와 `gpus` 사용 |
| NVIDIA Container Toolkit | `1.20.2-1` | NVIDIA의 Ubuntu 26.04 amd64 지원 표와 stable 목록에서 확인 |
| CUDA 이미지 | `12.9.1-devel-ubuntu24.04`, `12.9.1-base-ubuntu24.04` | NVIDIA Docker Hub의 linux/amd64 manifest digest 고정 |

[Ansible 지원 표](https://docs.ansible.com/projects/ansible/latest/reference_appendices/release_and_maintenance.html) · [ansible.posix 컬렉션](https://docs.ansible.com/projects/ansible/14/collections/ansible/posix/index.html) · [Ubuntu NVIDIA 서버 드라이버](https://packages.ubuntu.com/resolute-updates/all/libs/) · [Docker APT 인덱스](https://download.docker.com/linux/ubuntu/dists/resolute/pool/stable/amd64/) · [Compose GPU 설정](https://docs.docker.com/reference/compose-file/services/#gpus) · [Toolkit 지원 플랫폼](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.20.2/supported-platforms.html) · [NVIDIA CUDA 이미지](https://hub.docker.com/r/nvidia/cuda/tags)

검증 컨테이너는 Ubuntu 24.04 기반 CUDA 12.9.1 이미지를 사용하며, GPU 드라이버는 Ubuntu 26.04 게스트에서 로드한다. 컨테이너 빌드는 `nvcc`로 `sm_70` 기계어를 생성하고 호스트 코드는 `-march=ivybridge -mno-avx2 -mno-avx512f`로 빌드한다. 컨테이너에서 GPU 장치 1개와 Compute Capability를 확인한 뒤 정수 덧셈 커널을 실행한다. GPU 결과를 호스트로 복사하고 42가 아니면 실패한다. [NVIDIA CUDA 12.9.1 컴파일러 문서](https://docs.nvidia.com/cuda/archive/12.9.1/cuda-compiler-driver-nvcc/index.html)

패키지 가용성과 이미지 다이제스트는 공급자 메타데이터에서 확인했다. Compose 모델은 개발 머신에 있던 Compose v2.38.1-desktop.1에서 해석했다. Ubuntu 26.04의 실제 게스트 커널에서 R580 독점 DKMS 모듈 로딩, Compose 5.7.2 동작, 이미지 빌드 및 GPU 커널 실행을 확인해야 한다.

Ubuntu 커널·일반 패키지·R580 보안 패치와 bootstrap의 Python 전이 의존성은 현재 저장소의 가용 버전을 사용한다. 패키지 저장소는 변경될 수 있다. 정확한 Ubuntu 드라이버 패치 버전이 필요한 배포에서는 `nvidia_driver_apt_version`을 지정하고 커널과 패키지 가용성도 함께 기록한다. 오래된 패키지를 계속 재현해야 하면 내부 APT 스냅샷 또는 검증된 패키지 묶음을 보관한다. 서명키가 바뀌었을 때는 원본 키와 새 SHA-256을 확인하여 저장소와 함께 갱신한다.

PyTorch, ComfyUI, Whisper와 기타 AI 앱은 추가 시 해당 Wheel과 CUDA 확장의 `sm_70` 포함 여부, CPU ISA 및 실 GPU에서의 메모리 요구량을 각각 검증한다. 현재 앱 버전 기준선은 없다.

## Gnuvil AI Lab 포털 기준선 (2026-11-26)

Python `3.12-slim-bookworm`과 uv `0.10.9`의 공개 manifest 다이제스트를 Dockerfile에 고정했다. Python manifest는 Docker Hub 태그 API, uv manifest는 GHCR에서 확인했다. 이미지 레이어를 다운로드하거나 대상 VM에서 실행하지 않았다. FastAPI `0.165.0`, Uvicorn `0.54.0`, Jinja2 `3.1.6`, PyYAML `6.0.4` 및 전이 의존성은 PyPI 메타데이터로 확인하고 `uv.lock`에 버전·배포물 해시를 고정했다. 로컬 테스트는 CPython 3.12.12에서 실행했다. HTMX `2.0.8`은 공식 GitHub 릴리스 파일을 자체 호스팅하며 SHA-256은 `22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313`이다.

포털에는 GPU 및 CUDA 의존성이 없다. Ivy Bridge에서 Python 이미지와 Pydantic 등의 전이 Wheel이 실제로 실행되는지, Ubuntu 26.04에서 systemd 소켓 권한과 Compose bind mount가 동작하는지는 운영자 검증이 필요하다. 실제 VM·GPU 검증 완료로 간주하지 않는다. [uv Docker 가이드](https://docs.astral.sh/uv/guides/integration/docker/) · [FastAPI 템플릿](https://fastapi.tiangolo.com/advanced/templates/) · [HTMX 문서](https://htmx.org/docs/)

2026-11-26 실제 VM 배포에서는 동일 다이제스트의 Python 3.12.16 이미지와 잠금 의존성을 Ivy Bridge 게스트에서 빌드·실행했고 포털·인증 Proxy 상태 확인을 통과했다. nginx stable-alpine 다이제스트는 `8eb04fe3b385090e9aa02961f77398ad09b6bf33ab07f6669a5bb7acce22c0d3`로 고정했다. GPU 전환·추론·VM 재부팅은 이번 포털 배포 검증에서 실행하지 않았다. [배포 기록](portal-deployment.md)
