# Gnuvil AI Lab 운영 및 인수인계

동아리원이 브라우저에서 서비스를 시작하고 기존 AI 웹 화면에 접속하는 공통 진입점이다. 자체 회원가입, 사용자 DB, 관리자 웹 화면은 없다. 개별 AI 서비스의 인증과 사용자 데이터 격리는 별도다.

초기 구현 이후 실제 VM을 조사해 기존 ComfyUI 구성·모델 매니페스트를 보존했다. 현재 포털에는 `gpu-smoke`와 ComfyUI를 표시한다. ComfyUI는 기존 준비 상태 API로 접속 가능 여부를 확인하지만 drain adapter가 없어 웹 종료·전환은 거부한다. 기존 실행 컨테이너를 재생성하지 않고 준비 상태를 확인하기 위해 정책의 선택적 `ready_url`을 사용한다. 향후 새 컨테이너에는 Compose Health Check도 적용된다.

실제 VM의 LAN 공용 암호·TLS 배포는 [배포 기록](portal-deployment.md)을 따른다. `portal_lan_enabled=true`, `portal_lan_address`로 선택적으로 활성화한다.

## 구조와 수정 위치

```text
인증된 Reverse Proxy → FastAPI/Jinja2/HTMX 포털 (CPU, UID 10001)
                    → /run/gnuvil-ai/controller.sock
                    → 호스트 Controller → 기존 GPU 제어 함수 → Docker Compose
CLI ────────────────────────────────────┘
```

| 수정할 내용 | 파일 |
| --- | --- |
| 이름, 서비스 설명·주소·아이콘 | `configs/portal.yaml` |
| GPU 독점 정책, 수명 주기, 대기 시간 | `configs/gpu-policy.yaml` |
| CLI와 웹이 공유하는 잠금·실행·종료 | `scripts/gpu-switch.py` |
| Health Check 해석, 안전한 종료 프로토콜 | `scripts/service_runtime.py` |
| YAML 검증·서비스 연결 규칙 | `scripts/service_config.py` |
| 제한된 호스트 API와 자원 수집 | `scripts/service-controller.py` |
| 화면·웹 요청·Controller 연결 | `services/portal/app.py`, `templates/`, `static/` |
| Controller 실행 계정·권한·systemd | `ansible/roles/portal/` |

별도 프론트엔드 빌드, DB, 메시지 브로커, 영구 작업 큐는 사용하지 않는다. Controller에는 한 개의 제어 worker와 주기적 상태 수집 thread가 있다. 제어 요청은 접수 후 반환하며 대기열을 쌓지 않는다. 같은 진행 중 요청은 중복 접수하지 않고 다른 요청은 재시도를 안내한다. CLI와 Controller는 `/run/gnuvil-ai-gpu.lock`의 비차단 파일 잠금을 공유한다.

브라우저는 3초마다 HTML 일부를 갱신한다. 웹은 2초 캐시를 공유하고 Controller는 별도 thread에서 약 2초 간격으로 호스트 상태를 수집한다. 느린 Docker·GPU 명령은 상태 API를 막지 않는다. 10초 넘게 갱신되지 않은 호스트 표본은 `unknown`과 빈 수치로 바뀐다. 서버 현황에는 Controller의 실제 수집 시각을 KST로 표시하고, 표본이 오래되면 마지막 수집 시각과 갱신 지연을 함께 표시한다. CPU 사용률은 두 `/proc/stat` 표본의 차이로 계산하므로 첫 조회에는 수치가 없다.

## 개발 머신에서 정적 검증

실행 위치는 저장소 루트, 일반 사용자 권한이다. 소규모 Python 패키지 외에 모델·컨테이너 이미지는 받지 않는다.

```bash
uv sync --project services/portal --locked
make validate test PYTHON=services/portal/.venv/bin/python
```

`make validate`는 YAML 중복 키, Python·Shell 문법, 서비스 연결, Compose 모델·기본 Profile·포털 권한을 검사한다. Docker CLI의 Compose 플러그인은 필요하지만 데몬·GPU는 필요 없다. 도구가 없으면 해당 검사는 `SKIP`으로 표시된다. Ansible이 있으면 기존 syntax check와 선택적 lint도 실행한다. 단위 테스트는 호스트 명령과 Controller를 대체하여 실행하므로 실제 VM 동작 검증을 대신하지 않는다.

## 배포 전 준비

아래 명령은 **AI VM의 운영자가 검토 후 실행할 절차**다. 이번 개발 작업에서는 수행하지 않는다.

1. `/opt/gnuvil-ai`에 검토한 저장소 revision을 배치한다. 프로젝트와 상위 경로, `scripts/`, `configs/`, `services/`, Compose·환경 파일은 root 소유이고 group/world 쓰기 권한이 없어야 한다. 링크로 다른 사용자 소유 파일을 가리키지 않는다. `/srv/ai-data`의 서비스 데이터 권한은 기존 정책으로 별도 관리한다.
2. 기존 인증·Reverse Proxy의 위치와 접근 범위를 결정한다. 초기 구성은 **호스트의 인증된 Reverse Proxy**를 전제로 한다. 포털 포트는 `127.0.0.1:8080`에만 연결된다. Proxy를 컨테이너나 다른 서버에 둘 경우 네트워크·방화벽을 별도로 검토해야 한다.
3. `configs/portal.yaml`의 `public_origin`을 실제 HTTPS origin으로, `allowed_hosts`를 실제 호스트명으로 설정한다. origin에는 경로나 마지막 `/`를 넣지 않는다. 포털은 도메인 루트(`/`) 배포를 기준으로 한다.
4. 기존 부트스트랩·OS·스토리지 준비를 마친 뒤 다음 명령을 실행한다.

```bash
cd /opt/gnuvil-ai
make validate
sudo make plan
sudo make configure
```

`portal` Role은 전용 UID/GID 10001, root 실행 Controller, 소켓 디렉터리, GPU 잠금 파일, systemd unit을 관리한다. 전용 계정은 Docker 그룹에 들어가지 않는다. 소켓 디렉터리는 tmpfiles로 부팅 시 준비하며 systemd 종료·재시작 때도 보존하여 포털의 디렉터리 bind mount가 새 소켓을 계속 볼 수 있게 한다. 기존 UID/GID와 충돌하면 먼저 환경에 맞는 배포 계획을 세운다. GID 변경 시 Ansible의 `portal_socket_gid`와 `/etc/gnuvil-ai/portal.env`의 `PORTAL_SOCKET_GID`를 함께 맞춘다. 기존 환경 파일은 비밀을 보존하기 위해 덮어쓰지 않는다.

Controller를 시작하기 전 root 소유권 검사에 실패하면 자동으로 `chown`하지 않고 중단한다. 예를 들어 운영자가 배포할 checkout을 확인한 뒤 `sudo chown -R root:root /opt/gnuvil-ai` 및 쓰기 권한 정리를 수행한다. 개발자 작업 폴더에 이 명령을 적용하지 않는다. 이후 코드·정책·Compose 변경 시 검증 후 `sudo systemctl restart gnuvil-ai-controller`로 새 설정을 읽는다. 웹 설정·애플리케이션 변경 시 포털도 재시작하거나 이미지를 다시 빌드한다.

## 계정 없이 인증된 Proxy 연결

`/etc/gnuvil-ai/portal.env`는 root 전용 `0600` 파일로 생성되고, 초기 토큰은 비어 있다. 이 상태에서는 포털 화면과 제어 API가 모두 403으로 닫힌다. `/healthz`만 최소 생존 상태를 반환한다. GPU·Controller가 고장 나도 포털 자체 생존 검사와 오류 안내는 유지된다.

운영자는 충분한 무작위 토큰을 생성해 `PORTAL_PROXY_TOKEN`에 넣고, **인증을 통과한 요청에만** Proxy가 `X-Portal-Token` 헤더를 덮어써 전달하도록 한다. 32자 미만 토큰은 거부한다. 이 토큰은 사용자의 비밀번호나 API 키가 아닌 Proxy→포털 경로 확인용 비밀이다. 브라우저에 전달하거나 저장소에 기록하지 않는다. 명령줄 인자로 토큰을 넘기지 않는다.

동아리 인증이 이미 있으면 해당 Proxy 인증을 재사용한다. 아직 없다면 초기에는 Proxy의 HTTP Basic 인증을 이용할 수 있다. 아래 예시는 nginx의 **이미 TLS가 설정된 서버 블록 내부**에 넣는 참고 구성이다. htpasswd 생성·계정/공유 암호 정책과 인증 인프라 선택은 운영자가 결정한다.

```nginx
location / {
    auth_basic "Gnuvil AI Lab";
    auth_basic_user_file /etc/nginx/gnuvil-ai.htpasswd;
    # Root-only snippet containing: proxy_set_header X-Portal-Token "<same token>";
    include /etc/nginx/snippets/gnuvil-ai-proxy-token.conf;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_pass http://127.0.0.1:8080;
    client_max_body_size 1k;
}
```

기존 SSO의 `auth_request`로 대체해도 되지만 인증 실패 요청이 upstream에 도달하지 않아야 한다. 클라이언트가 보낸 `X-Portal-Token`을 그대로 전달하지 않는다. 인증되지 않은 별도 location이 같은 upstream으로 우회하지 않게 한다. 포털은 전달된 사용자 이름을 권한 근거로 사용하지 않고 사용자별 정보를 저장하지 않는다.

변경 요청은 POST이며 정확한 Origin, SameSite 쿠키와 CSRF 토큰을 검증한다. Host 허용 목록(컨테이너 생존 검사에 필요한 `127.0.0.1`은 항상 허용), CSP, 템플릿 escaping을 적용하고 외부 서비스 링크는 HTTP(S)만 허용한다. 기본 localhost origin은 개발용이며 외부 공개 전에 HTTPS로 바꾼다.

```bash
# AI VM, root 권한. 인증·토큰·도메인 설정 이후 실행한다.
cd /opt/gnuvil-ai
sudo make portal-build
sudo make portal-up
sudo make portal-logs
```

기본 Profile에는 CPU 포털만 포함된다. GPU 서비스는 여전히 각자의 명시적 Profile로만 실행되며 자동 재시작하지 않는다. `portal-up`은 `--no-deps --no-build --pull never`로 포털만 시작한다. `portal-down`도 포털만 정지한다. Controller는 이미 존재하는 등록 서비스만 시작·종료하며 이미지 빌드·pull·모델 설치·파일 변경 API를 제공하지 않는다.

## 새 GPU 서비스 등록

다음은 미래 서비스용 예시이며 현재 배포된 서비스가 아니다.

1. 기존 방식으로 `services/<name>/compose.yaml`과 고정된 이미지·빌드·의존성을 추가하고 루트 `include`에 연결한다.
2. GPU 컨테이너는 자기 이름의 Profile, `restart: "no"`, `pull_policy: never`를 사용한다. 장기 실행 서비스에는 Health Check를 반드시 정의한다.
3. `configs/gpu-policy.yaml`에 수명 주기와 필요한 동반 CPU 컨테이너를 등록한다.

```yaml
services:
  gpu-smoke: job
  llama-cpp:
    mode: service
    companions: [chat-ui]
    # Only if an adapter implementing the contract below actually exists.
    probe_url: http://127.0.0.1:9101
```

동반 컨테이너도 같은 GPU 그룹의 Profile, `restart: "no"`, 로컬 이미지, Health Check를 사용한다. GPU 장치를 가진 컨테이너는 정책의 GPU 서비스 키와 정확히 일치해야 한다. 그룹끼리 컨테이너를 공유하지 않는다. 한 사용자용 서비스가 이 그룹 전체를 제어한다. CLI는 GPU 그룹의 모든 이미지가 미리 준비되었는지 확인한다. 동반 이미지 빌드도 운영자가 등록 전에 준비해야 한다.

```yaml
# configs/portal.yaml → services 아래
chat:
  name: AI 채팅
  description: 채팅과 코딩을 위한 AI 서비스
  category: 채팅
  icon: message-square
  url: https://chat.example.org
  gpu_service: llama-cpp
```

화면 코드 수정 없이 카드가 생성된다. `icon`은 현재 `chip`, `image`, `message-square`, `audio`를 제공하고 다른 값은 기본 심벌로 표시한다. 접속 UI가 없는 작업은 `url: null`로 둔다. 일회성 작업은 완료까지 잠금을 유지하며 정상 종료와 VRAM 회수를 확인한다. 실행 중에는 웹으로 중단할 수 없다.

CPU 전용 서비스는 `gpu_service` 대신 아래 형태를 사용한다. GPU 정책에 CPU 서비스를 넣지 않는다. CPU 대상도 로컬 이미지·명시적 Profile·Health Check·`restart: "no"`를 사용하고 `portal` 자체나 GPU 그룹 컨테이너를 참조할 수 없다.

```yaml
cpu_service:
  service: cpu-ui
  companions: []
  # probe_url is optional; without it, web shutdown is refused.
  probe_url: http://127.0.0.1:9102
```

## 작업 상태와 안전한 종료 계약

컨테이너 `running`은 준비 완료가 아니다. 그룹 전체 Docker Health Check가 `healthy`이거나, 모두 실행 중이고 등록된 호스트 loopback `ready_url`이 HTTP 200을 반환해야 접속할 수 있다. 명시적으로 unhealthy인 컨테이너는 ready_url로 우회하지 않는다. ready_url은 작업 상태나 종료 안전성을 보장하지 않는다. 등록된 작업 상태 API가 있으면 그 API의 `ready`도 확인한다. GPU 사용률로 작업 유무를 추측하지 않는다.

`probe_url`은 호스트 loopback HTTP의 고정 포트만 허용하며 redirect·외부 Proxy는 사용하지 않는다. 해당 포트의 adapter는 서비스별로 구현·검증해야 한다. 기존 AI 서비스 API가 아래 계약과 다르면 URL만 등록해서 사용할 수 없다. **이번 변경에는 아직 존재하지 않는 AI 서비스용 adapter를 만들지 않았다.**

| API | 필수 동작 |
| --- | --- |
| `GET /state` | 실제 작업 상태를 반환한다. |
| `POST /drain` | 신규 작업을 원자적으로 차단하고 실제 작업 상태를 반환한다. 작업 중이면 종료를 허가하지 않는다. |
| `POST /resume` | 종료 준비가 취소된 경우 신규 작업을 다시 허용한다. |

응답은 `{"ready": true, "busy": false, "drained": true}`처럼 세 필드 모두 실제 boolean이어야 한다. 응답은 최대 4 KiB, 요청 제한 시간은 3초다. `drained: true`를 반환한 뒤 `/resume` 또는 서비스 재시작 전까지 **모든 경로의 새 작업 유입을 계속 차단**해야 한다. 직접 AI UI 접속·대기 중 작업·내부 재시도·백그라운드 실행도 포함된다. 단순한 queue 길이 0 조회로 이 계약을 대신할 수 없다.

작업 상태 조회 실패, busy, drain 불확실, 실행 중 job에서는 종료하지 않는다. 여러 그룹을 종료 준비하다 실패하면 시도한 adapter에 resume을 요청한다. resume까지 실패하면 자동으로 강제 복구하지 않고 운영자가 확인한다. 종료 신호는 검증된 Compose `stop_signal`(SIGTERM 또는 SIGINT)만 보내고, 시간 내 종료하지 않으면 SIGKILL 없이 중단한다. SIGTERM 실패나 종료 제한 시간 초과 후에도 서비스가 drained로 남을 수 있으므로 운영자가 작업·컨테이너 상태를 확인하고 adapter resume 여부를 결정한다.

현재 종료 adapter가 없는 장기 서비스는 Health Check를 통과하면 접속할 수 있지만 웹 종료·전환은 제한된다. 이 제한은 등록만으로 사라지지 않는다. CLI의 `CONFIRM=1`은 운영자가 직접 작업을 비웠다고 확인하는 기존 수동 경로이며 웹에는 노출하지 않는다.

## 장애 대응과 실제 VM 검증

실행 위치는 AI VM, 조회/제어는 root 권한이다.

```bash
sudo make status
sudo journalctl -u gnuvil-ai-controller --since '-15 min'
sudo systemctl status gnuvil-ai-controller
sudo make portal-logs
# 작업을 확인하고 비워도 되는 경우에만 운영자가 직접 수행:
sudo make gpu-off CONFIRM=1
```

Controller는 root로 Docker를 제어하므로 실질적으로 높은 권한을 가진 신뢰 경계다. 고정 명령 인자·서비스 허용 목록·root 소유 입력·소켓 0660/디렉터리 0750으로 기능을 제한한다. 포털에는 소켓 디렉터리와 표시 설정만 읽기 전용으로 연결하며 Docker 소켓·GPU·호스트 root 권한을 주지 않는다. Unix 소켓의 read-only bind는 요청 송신 자체를 차단하지 않으므로 Controller 검증을 생략할 수 없다. root 권한이나 소켓 그룹 권한을 가진 로컬 사용자는 별도로 신뢰해야 한다.

확인 순서:

1. 비인증·직접 포트 요청이 거부되고 인증된 Proxy 요청만 성공하는지 확인한다. 잘못된 Origin·CSRF 토큰으로 POST가 거부되어야 한다.
2. Controller를 중지해도 포털이 살아 있고 `확인 불가`를 표시하는지 확인한다. GPU 정보 실패 시에도 포털에 접근할 수 있어야 한다.
3. 승인된 시간에 GPU 검증 이미지를 준비하고 웹에서 `gpu-smoke`를 시작한다. 실제 연산 성공, 상태 변화, VRAM 회수와 CLI 결과를 비교한다.
4. CLI와 웹 동시 요청·중복 요청·Controller 재시작 중 실제 컨테이너 상태 복구를 확인한다. 재시작 시 중단된 제어 작업을 자동 재실행하지 않는다. 기존 job이 실행 중이면 busy로 재구성하고 웹 종료를 막는다.
5. 장기 서비스 추가 시 실제 작업 중 전환 거부, 신규 작업 유입 차단, 종료 제한 시간, 외부 GPU 점유, VRAM 미회수, Health Check 실패를 검증한다.
6. 대상 Ivy Bridge CPU에서 포털 이미지 빌드·시작과 Python 전이 Wheel의 CPU 호환성을 확인한다. 다이제스트/의존성 고정은 실제 CPU 실행 검증을 대신하지 않는다.
7. `sudo make plan` 재실행으로 Ansible 멱등성과 기존 서비스에 불필요한 변경이 없는지 확인한다.

작업·오류는 메모리에만 보관한다. Controller 재시작 시 과거 오류·접수 기록은 사라지며 Docker 상태·Health Check·작업 상태를 다시 수집한다. 실패한 작업은 journald의 서비스 ID·action·오류 종류를 확인하고 필요하면 `docker compose ... ps` 등 기존 CLI로 조사한다. 웹에는 subprocess stderr, 경로, 자격 증명을 반환하지 않는다.

예약·사용자별 할당·자동 유휴 종료·추론 게이트웨이·모델 관리·장기 통계는 초기 범위에 포함하지 않는다.

## 이번 변경의 로컬 검증 기록 — 2026-11-26

- 기준 checkout: `61c82d9`에서 작업했으며 Git 커밋·푸시는 하지 않았다.
- `make test PYTHON=services/portal/.venv/bin/python`: **48개 성공**, 최종 실행에서는 skip 없음. 로컬 Unix 소켓 서버와 웹 클라이언트 왕복 통신을 포함한다. Docker·GPU 호출은 테스트 대역으로 대체했다.
- `make validate`: YAML 18개, Python·Shell 문법, 실제 Compose include 해석·기본 Profile·GPU 정책·포털 권한 검사 성공. 임시 환경의 기존 고정 버전 Ansible과 `ansible.posix`로 Playbook syntax check도 성공했다.
- `ansible-lint`: 도구가 없어 실행하지 않았다.
- `uv lock --check --offline`, 자체 JavaScript와 HTMX의 `node --check`, `git diff --check`: 성공.
- 로컬 브라우저: Controller 미연결 오류 화면, 로고, 라이트·다크 테마 전환, 좁은 화면 줄바꿈·가로 넘침 없음, HTMX 상태 영역 교체 확인. 확인한 브라우저 console에는 error/warn이 없었다. 넓은 viewport의 스크린샷 캡처는 브라우저 도구에서 실패해 해당 크기의 시각 검증은 완료하지 않았다.
- Python 테스트 실행 시 Starlette가 테스트용 `httpx`에 대한 향후 교체 권고 경고를 출력한다. 현재 잠금 버전의 테스트는 성공했다. 이는 제품 화면의 브라우저 오류와는 별개다.
- 임시 화면 확인 서버는 검증 후 종료했다. 화면 확인용 인증 주입 코드는 `/private/tmp`의 임시 파일에서만 사용했으며 저장소 애플리케이션에는 개발용 인증 우회 기능을 넣지 않았다.
- 실제 VM 접속·Ansible 적용·컨테이너 이미지 빌드/실행·GPU 연산·Linux systemd/소켓 권한·대상 CPU 호환성 검증은 **미수행**이다.
