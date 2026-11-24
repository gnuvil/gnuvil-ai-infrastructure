# 설치 및 실제 VM 검증

이 문서의 설치·빌드·실행 명령은 운영자가 **AI VM 안에서** 실행한다.

## 사전 조건

- Proxmox에 Ubuntu Server 24.04 amd64 VM, V100 PCIe 패스스루 및 QEMU Guest Agent 채널이 준비되어 있어야 한다. VM에서 `lspci -nn`으로 GPU를 확인한다.
- Ubuntu APT 소스에 Noble `restricted`와 `universe`, 보안 및 업데이트 포켓이 있어야 한다. 실행 중인 커널의 헤더 패키지 후보가 필요하다. Secure Boot가 켜져 있으면 DKMS 모듈 서명과 MOK 등록 절차를 준비한다. [Ubuntu NVIDIA 드라이버 안내](https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/)
- Proxmox와 ZFS를 위한 물리 RAM을 남긴 다음 VM 메모리를 지정한다. 데이터용 디스크는 이미 준비된 파일시스템을 연결하거나 외부에서 마운트한다.
- VM에 Git, sudo, Bash, GNU Make가 필요하다. 공식 Ubuntu, Docker 및 NVIDIA APT, PyPI, Ansible Galaxy, Docker Hub 접근이 필요하다.

## 설치 순서

```sh
sudo install -d -m 0750 -o "$(id -u)" -g "$(id -g)" /opt/gnuvil-ai
git clone git@github.com:gnuvil/gnuvil-ai-infrastructure.git /opt/gnuvil-ai
cd /opt/gnuvil-ai
```

승인된 Git 커밋을 체크아웃한다. 저장소가 비공개이므로 읽기 권한이 있는 SSH 자격 증명이 필요하다. 신뢰하는 관리자가 저장소를 소유해야 한다. 아래 명령은 해당 체크아웃의 스크립트와 Role을 root 권한으로 실행한다.

`ansible/group_vars/all.yaml`을 검토한 다음 스토리지 방식을 정한다. 별도 변수 파일을 사용할 경우 Git에서 제외하는 `*.local.yaml`로 관리하고 필요한 Playbook 실행에 `--extra-vars @경로`로 전달한다. `make plan`과 `make configure`는 저장소 변수를 기본으로 사용한다.

- 기존 포맷된 게스트 블록 장치를 마운트하려면 `lsblk -f`에서 확인한 `ai_data_mount_uuid`와 `ai_data_mount_fstype`를 지정한다. 해당 파일시스템을 `ai_data_root`에 마운트하고 fstab에 기록한다.
- 운영자가 별도로 마운트를 관리한다면 `ai_data_mount_uuid`를 비워 두고 `ai_data_require_mount: true`로 설정한다.
- 기본값은 VM의 현재 파일시스템에 디렉터리를 만든다. 데이터 디스크를 사용할 예정이라면 최초 구성 전에 설정한다. 스토리지 Role은 기존 데이터 위에 새 파일시스템을 마운트하려는 경우 중단한다.

```sh
sudo make bootstrap
sudo make plan
sudo make configure
```

새 NVIDIA 드라이버 또는 커널 헤더 설치로 재부팅이 필요하면 Playbook이 안내와 함께 종료한다. 작업을 확인하고 **AI VM을** 재부팅한 후 다음 명령으로 이어간다.

```sh
sudo reboot
```

```sh
cd /opt/gnuvil-ai
sudo make configure
```

`make plan`은 설치된 시스템과 캐시를 바탕으로 가능한 변경을 표시한다. 새 APT 저장소의 패키지 설치 예측, 새 마운트 아래의 권한, 서비스 시작 및 런타임 검증은 실제 적용 후 확인한다. 첫 `plan`에서 Ubuntu 패키지 인덱스가 불충분하면 VM의 사전 조건과 APT 상태를 확인한다. Role 태그는 `base`, `storage`, `nvidia`, `docker`, `nvidia_container`다. 개별 태그를 실행하기 전에 해당 Role의 선행 구성을 완료한다.

## 실제 VM 확인

1. 구성 완료 후 `sudo make configure`를 다시 실행해 `changed=0`을 확인한다. 새 패키지나 변수가 추가되었다면 달라진 작업과 이유를 기록한다.
2. OS와 하드웨어를 확인한다.

   ```sh
   lscpu
   lspci -nn -d 10de:
   nvidia-smi --query-gpu=name,compute_cap,driver_version,memory.total --format=csv
   modinfo -F license nvidia
   systemctl is-active qemu-guest-agent docker
   sudo docker info --format '{{json .Runtimes}}'
   sudo docker compose version
   ```

   AVX가 노출되고 GPU 목록에 Tesla V100 한 개와 Compute Capability 7.0이 표시되어야 한다. 드라이버는 580.x, 로드된 커널 모듈은 독점 모듈, Docker 런타임 목록에는 `nvidia`가 있어야 한다.

3. 영구 데이터 파일시스템과 각 경로의 소유권을 확인한다.

   ```sh
   findmnt --target /srv/ai-data
   sudo stat -c '%A %U:%G %n' /srv/ai-data /srv/ai-data/{models,datasets,outputs,cache,state,logs}
   ```

   별도 디스크를 사용한다면 `findmnt --mountpoint /srv/ai-data`의 UUID와 종류를 설정값과 대조한다.

4. 검증 이미지를 빌드하고 프로필 및 CUDA 연산 결과를 확인한다. 첫 빌드에서 CUDA 개발 이미지를 내려받으므로 시간과 디스크 공간을 확보한다.

   ```sh
   sudo docker compose config --services
   sudo docker compose --profile gpu-smoke config --services
   sudo make build SERVICE=gpu-smoke
   sudo make switch SERVICE=gpu-smoke
   sudo make status
   sudo make gpu-off
   ```

   기본 서비스 목록은 비어 있고, 프로필을 켜면 `gpu-smoke`가 표시되어야 한다. 실행 출력에 `CUDA PASS`와 종료 코드 0이 확인되어야 한다. 테스트 이후 컨테이너는 종료 상태로 남으며 GPU 연산 프로세스는 없어야 한다. 같은 테스트는 이미지 빌드 후 Ansible Role을 통해서도 실행할 수 있다.

   ```sh
   cd /opt/gnuvil-ai/ansible
   sudo /opt/gnuvil-ansible/bin/ansible-playbook playbook.yaml --tags gpu_verify
   ```

5. 정상 종료 후 VM을 한 번 더 재부팅하고 GPU, 마운트, Docker 및 CUDA 스모크 테스트를 다시 확인한다. 새 VM에서 이 순서를 처음부터 반복하고 Git SHA, 커널, 실제 패키지 버전, 로그를 운영 기록에 남긴다.

Docker 구성이 이미 있는 VM에서 APT 소스가 중복되거나 Docker 배포판 패키지 충돌이 발생하면 적용을 멈추고 기존 설정과 서비스를 확인한다. GPU가 나타나지 않으면 패스스루, `dkms status`, 현재 커널 헤더와 Secure Boot를 확인한다. GPU 테스트가 실패했을 때는 `sudo docker compose logs gpu-smoke`, `sudo journalctl -u docker -b`, `nvidia-smi`를 대조한다.
