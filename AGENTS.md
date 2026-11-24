# Gnuvil AI Infrastructure 개발 원칙

## 작업 범위와 운영 승인

- 저장소 변경과 정적 검증을 기본 작업 범위로 삼는다. VM 접속, 실제 Playbook 적용, 시스템 구성 변경, 컨테이너 실행, 대용량 이미지나 모델 다운로드에는 운영자의 별도 요청이 필요하다.
- 검증 명령에는 실행 위치와 필요한 권한을 명시한다. 실제 실행 결과와 문서로 제공한 검증 절차를 구분한다.
- Git 커밋과 푸시는 요청받은 작업에 한해 수행한다. 모델, 데이터셋, 출력물, DB, 로그, 캐시, 자격 증명과 실제 `.env`를 커밋하지 않는다.
- Ubuntu VM 생성, ZFS와 PCIe 패스스루는 Proxmox 운영 영역이다. Playbook은 이미 준비된 Ubuntu 게스트를 관리한다.

## 대상 하드웨어

- Lenovo System x3500 M4, Proxmox VE, Xeon E5-2680 v2 × 2, Ubuntu Server 24.04 LTS amd64 게스트를 기준으로 한다.
- CPU 기준은 Ivy Bridge와 AVX다. AVX2, AVX-512 및 x86-64-v3를 요구하는 바이너리나 Wheel을 배포하기 전에 대체 빌드를 검토한다. VM의 CPU 플래그와 전이 의존성도 확인한다.
- Tesla V100 PCIe 16GB × 1은 Volta `sm_70`이다. NVIDIA R580 독점 커널 모듈과 CUDA 12.x를 기준선으로 유지한다. 이미지, Wheel, 커스텀 CUDA 확장의 `sm_70` 지원을 각각 확인한다.
- V100에서 네이티브 BF16·FP8을 전제하지 않는다. 모델의 VRAM 예산과 FP16·FP32 경로를 확인한다. GPU 서비스는 기본적으로 한 번에 하나를 운영한다.
- 물리 RAM은 현재 32GB이고 64GB 증설 예정이며 AI VM 초기 목표는 32GB다. 실제 VM 할당 전 Proxmox와 ZFS에 필요한 메모리를 확보한다.

## 구성과 재현성

- Ansible은 VM 공통 환경, Compose는 서비스 실행 환경, Dockerfile과 서비스의 `uv.lock`은 애플리케이션 의존성을 정의한다. 호스트에는 시스템 Python과 Ansible, NVIDIA 드라이버와 Container Toolkit을 사용한다. CUDA Toolkit과 앱의 Python·PyTorch 환경은 컨테이너에 둔다.
- 이미지는 버전 태그와 가능한 경우 다이제스트로 고정한다. 애플리케이션 소스는 검증한 커밋 SHA, Python 의존성은 서비스별 `uv.lock`과 `uv sync --locked`로 고정한다. `latest`와 변경 가능한 브랜치를 배포 입력으로 사용하지 않는다.
- 공급자 메타데이터로 확인한 버전과 실제 VM에서 검증한 버전을 호환성 문서에 기록한다. 보안 업데이트와 패키지 가용성 때문에 달라질 수 있는 버전도 기록한다.
- VM 재부팅은 운영자가 수행한다. Role은 멱등 모듈을 우선 사용하고, 변경에 따른 서비스 재시작은 Handler로 처리한다. 디스크 포맷·파티셔닝과 강제 서비스 종료는 자동화에 포함하지 않는다.
- 새 GPU 서비스에는 독립 Compose Profile, 자동 재시작 비활성화, GPU 정책 등록과 서비스별 검증 절차를 추가한다. GPU를 사용하지 않는 서비스에는 GPU나 Docker 소켓을 노출하지 않는다. 외부 포트 공개는 서비스별로 검토한다.
- 프로젝트 기본 경로는 `/opt/gnuvil-ai`, 영구 데이터는 `/srv/ai-data`다. 배포 전에 마운트와 소유권을 확인하고 컨테이너마다 필요한 디렉터리만 연결한다.
