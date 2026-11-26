# Gnuvil AI Lab — AI VM 배포 기록

2026-11-26에 `gnuvil@192.168.50.14`의 기존 `/opt/gnuvil-ai`에 배포했다. 운영체제는 Ubuntu 26.04.1 amd64, Docker Engine 29.10.0, Compose 5.7.2다. 기존 ComfyUI 설정·모델 매니페스트·스크립트의 SIGINT 종료 정책을 로컬 작업본에 병합한 뒤 배포했다. 모델이나 AI 이미지는 새로 받지 않았으며 기존 ComfyUI 컨테이너를 중단·재생성하지 않았다.

## 접속

- 포털: **https://192.168.50.14**
- LAN Proxy 공용 사용자 이름: `lab`
- 공용 암호: VM의 root 전용 `/etc/gnuvil-ai/portal-access.json`에서 조회한다. 저장소·빌드 입력·이 문서에는 저장하지 않는다.
- 기존 ComfyUI: http://192.168.50.14:8188 — 기존 공개 방식 그대로다. 포털의 공용 암호가 ComfyUI 자체 인증까지 제공하지는 않는다.

운영자는 다음 명령으로 로그인 정보를 확인하고 승인된 동아리원에게 전달한다.

```bash
ssh gnuvil@192.168.50.14 'sudo cat /etc/gnuvil-ai/portal-access.json'
```

도메인이 없어 IP용 자체 서명 TLS 인증서를 사용한다. 사용자 기기에서 인증서를 신뢰하도록 등록해야 브라우저 인증서 오류 없이 접속할 수 있다. 기기의 신뢰 목록은 자동으로 변경하지 않았다. 공개 인증서는 VM의 `/etc/gnuvil-ai/portal.crt`이며 개인 키는 `/etc/gnuvil-ai/portal.key`에만 있다. 인증서 만료는 **2027-11-26 09:23:32 KST**다. 도메인과 공인 인증서가 준비되면 교체한다.

SHA-256 인증서 지문:

```text
D8:E0:FE:EF:B2:E5:CD:49:D2:63:27:BC:7E:43:89:2E:FD:18:6B:91:18:23:80:EF:8E:DC:A9:0A:49:8B:C8:10
```

## 실행 구성

| 구성 | 실행 방식 | 접근 |
| --- | --- | --- |
| `gnuvil-ai-portal-1` | Docker, UID 10001, 읽기 전용 rootfs, RAM 상한 256 MiB | 호스트 `127.0.0.1:8080` |
| `gnuvil-ai-portal-proxy-1` | 고정 다이제스트 nginx Docker, UID 10001, 읽기 전용 rootfs, RAM 상한 64 MiB | `192.168.50.14:443` → 컨테이너 8443 |
| `gnuvil-ai-controller.service` | 호스트 systemd, 제한된 Unix 소켓 API | `/run/gnuvil-ai/controller.sock` |
| 기존 ComfyUI | 기존 Docker 컨테이너 유지 | 기존 8188 포트 유지 |

포털과 Proxy에는 GPU·Docker 소켓·privileged 권한이 없다. Proxy는 모든 경로에서 공용 암호를 확인한 뒤 포털용 비밀 헤더를 덮어쓴다. 포털은 비밀 헤더 없는 직접 접근을 거부한다. nginx의 임시 경로는 모두 `/tmp` tmpfs로 지정한다. [nginx Basic Auth 공식 문서](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html) · [nginx HTTPS 공식 문서](https://nginx.org/en/docs/http/configuring_https_servers.html)

Proxy는 `portal-access` Profile에만 포함되며 기본 Profile에는 포털만 있다. LAN 주소·토큰·소켓 GID는 root 전용 `/etc/gnuvil-ai/portal.env`에 있다. 사용자 이름·암호, htpasswd, 토큰 snippet과 개인 키는 모두 저장소 밖에서 생성했다.

ComfyUI 준비 상태는 Compose Health Check와 `ready_url: http://127.0.0.1:8188/system_stats`로 확인한다. 현재 Compose 구성은 저장소의 종료 제어 확장을 읽기 전용으로 연결한다. 확장이 등록된 컨테이너에서는 포털이 전용 Unix 소켓을 통해 작업 상태를 확인하고, 실행·대기 작업이 모두 없을 때 종료 버튼을 활성화한다. 기존 이미지로 실행하며, 확장이 연결된 새 컨테이너가 필요하다.

## 재현 및 변경

기존 소스 백업은 `/root/gnuvil-ai-backups/portal-source-20261126T002102Z/source.tar.gz`에 있다. 모델 임시 폴더를 제외한 코드·설정 백업이다. 런타임 입력은 root 소유, group/world 쓰기 금지로 보호했다. 모델 staging 디렉터리 `.model-download`는 기존 사용자 소유로 보존했다. `/srv/ai-data`의 데이터와 권한은 변경하지 않았다.

포털 역할만 적용하여 드라이버·Docker 데몬·기존 AI 서비스를 건드리지 않는다. AI VM에서 root 권한으로 실행한다.

```bash
cd /opt/gnuvil-ai/ansible
sudo /opt/gnuvil-ansible/bin/ansible-playbook playbook.yaml --tags portal --check --diff \
  -e portal_lan_enabled=true -e portal_lan_address=192.168.50.14
sudo /opt/gnuvil-ansible/bin/ansible-playbook playbook.yaml --tags portal \
  -e portal_lan_enabled=true -e portal_lan_address=192.168.50.14
cd /opt/gnuvil-ai
sudo make portal-build
sudo make portal-access-pull
sudo make portal-up
sudo make portal-access-up
```

LAN 설정은 opt-in이다. 위 Ansible 변수로 실제 origin과 허용 호스트를 설정하며 서비스 카드는 보존한다. `portal-access.py`는 비밀 파일이 이미 있으면 새로 만들지 않는다. 공용 암호와 키를 보존하므로 원래 파일을 지우고 다시 초기화하는 방식으로 일상 업데이트하지 않는다.

인증서 교체 시 동일 IP SAN과 새 키·인증서 일치를 확인하고, 컨테이너 UID/GID가 파일을 읽을 수 있게 root:10001/0440을 유지한다. 공용 암호 변경 시 htpasswd와 운영자용 JSON을 함께 갱신한다. 비밀 파일이나 nginx 설정을 원자적으로 교체한 후에는 파일 bind mount를 새로 연결하기 위해 Proxy만 재생성한다.

```bash
cd /opt/gnuvil-ai
sudo docker compose --env-file /etc/gnuvil-ai/portal.env --profile portal-access \
  up --detach --no-deps --no-build --pull never --force-recreate portal-proxy
```

## 실제 확인 결과

- 기존 ComfyUI 컨테이너 ID `4336ea9c7942`와 시작 시각이 유지되었다. 재시작·추론·모델 로딩/언로딩은 수행하지 않았다.
- 대상 Ivy Bridge VM에서 포털 이미지를 빌드하고 CPython 3.12.16·잠금 Python 의존성으로 컨테이너를 실행했다.
- 포털·Proxy Health Check 정상, Controller active 상태를 확인했다.
- 실제 V100 모델·GPU 사용량·온도·소비 전력, CPU·RAM, ComfyUI ready 상태를 Unix 소켓과 HTTPS 화면에서 확인했다.
- LAN 개발 머신에서 인증서를 검증한 HTTPS 요청이 401로 거부됨을 확인했다. 암호 없는 요청과 잘못된 암호는 거부되고, 올바른 공용 암호는 화면·정적 리소스·상태 영역에 HTTP 200을 반환했다.
- 포털 직접 접근은 403, CSRF 없는 변경 요청은 403이었다. 실제 인증·CSRF를 통과한 ComfyUI 중복 시작은 접수되었고 기존 컨테이너를 재생성하지 않았다.
- 적용 후 같은 Ansible Role의 `--check --diff` 결과는 `changed=0`, 실패 0이었다.
- 로컬 단위·Unix 소켓 테스트 **50개 성공**, 최종 실행에서 skip 없음. VM에서 YAML 21개·Compose·Ansible syntax check가 성공했다. `ansible-lint`는 설치되지 않아 생략했다.
- 측정 시점 포털 메모리는 약 42 MiB, nginx Proxy는 약 3 MiB였다. 접속자·작업량에 따라 달라진다.

실제 CUDA 진단 job 실행, ComfyUI 종료·GPU 전환, 추론 결과, VM 재부팅 후 복구는 이번 배포에서 시험하지 않았다. 기존 작업을 유지하기 위해 GPU 수명 주기를 변경하는 검증은 생략했다. 브라우저의 인증서 신뢰 설정과 공용 암호 입력은 사용자 기기에서 마무리한다.

## 조회 및 포털만 내리기

```bash
ssh gnuvil@192.168.50.14
cd /opt/gnuvil-ai
sudo make portal-logs
sudo docker compose --env-file /etc/gnuvil-ai/portal.env --profile portal-access logs --tail 50 portal-proxy
sudo systemctl status gnuvil-ai-controller
sudo journalctl -u gnuvil-ai-controller --since '-15 min'
# 제어 작업이 없는지 확인한 뒤 포털 구성만 중지한다. ComfyUI에는 영향이 없다.
sudo make portal-access-down portal-down
sudo systemctl disable --now gnuvil-ai-controller
```

`docker compose down`으로 프로젝트 전체를 내리지 않는다. 기존 AI 서비스를 유지하려면 서비스명을 지정한 명령만 사용한다. 운영 코드는 GitHub main에서 관리하며 공용 암호·Proxy 토큰·개인 키·실제 사용자 데이터는 포함하지 않는다.

## 포털 수정 기록

- 화면의 홍보 문구를 제거하고 `대시보드`, `서버 현황`, `서비스`처럼 기능 중심의 표현으로 정리했다.
- 첨부 로고의 원본 파일 모드 0600 때문에 비특권 컨테이너 프로세스가 파일을 읽지 못해 HTTP 헤더 이후 전송이 끊겼다. 빌드 시 정적 자산의 읽기 권한을 보장하고 인증 middleware도 스트림을 감싸지 않는 순수 ASGI 방식으로 정리했다. 로고는 상태 코드뿐 아니라 전체 바이트와 해시가 원본과 같은지 검증한다.
- 서버 현황에 Controller의 실제 정보 수집 시각을 KST로 표시한다. 2초 캐시를 다시 읽거나 페이지를 갱신해도 새 수집 전에는 시각이 바뀌지 않는다. 오래된 표본은 마지막 수집 시각과 `갱신 지연`을 표시하고 수치를 비운다.
- ComfyUI 등 실행 중인 GPU 서비스의 안전한 종료를 확인할 수 없으면 `전환 불가`와 구체적인 사유를 표시하고 시작 버튼을 비활성화한다. 직접 요청이나 상태 변화가 있어도 기존 호스트 잠금·drain 검증은 유지한다.
- 안전 정책에 따른 요청 거부는 컨테이너 실행 오류와 구분한다. 거부된 GPU 동작 확인 요청 때문에 정상 중지된 진단 서비스가 `error`로 남지 않는다.
- 로컬 회귀 검사: **57개 테스트 성공**, 실제 HTTP 로고 전송과 Unix 소켓 왕복 포함, skip 없음.
