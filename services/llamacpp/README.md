# llama.cpp 모델 라우터

`llama-server`를 라우터 모드로 실행한다. `/srv/ai-data/models/llamacpp`의 GGUF 모델을 검색하고 요청의 `model` 이름으로 모델을 선택한다. 첫 요청에 모델을 로드하며, 기본 동시 로드 한도는 V100의 16GB VRAM을 고려해 1개다. 모델과 이미지는 포함하지 않는다. 라우터 자체는 실험적 기능이다.

고정된 llama.cpp 커밋 `d12cc3d1ca6bba741cd77887ac9c9ee18c8415c7`에 [안전 패치](router-safety.py)를 적용한다. 다른 모델을 로드하려 할 때 기존 모델을 자동으로 내리는 동작을 제거하고, 자식 프로세스 종료가 지연되어도 강제 종료하지 않는다. 이미 모델이 로드되어 있으면 두 번째 모델 로드는 거부된다. 전환 전 모든 요청이 끝났는지 운영자가 확인하고 기존 모델을 명시적으로 언로드한다. 이 패치는 고정 소스와 정확히 일치하지 않으면 빌드를 중단한다.

이미지는 CUDA 12.9.1 다이제스트를 사용하고 CPU 빌드에서 AVX2·AVX-512·FMA·BMI2를 끄며 CUDA `sm_70`을 대상으로 한다. 실제 VM 빌드와 라우터, 추론 및 CPU/GPU 호환성은 아직 검증하지 않았다. APT 패키지는 빌드 시점의 저장소를 사용한다.

## 모델과 프리셋

영구 데이터 마운트를 확인하고, 라이선스와 제공자의 체크섬을 검증한 GGUF 파일을 `/srv/ai-data/models/llamacpp`에 둔다. 각 상위 디렉터리는 컨테이너 UID 65534가 탐색할 수 있고 모델은 읽을 수 있어야 한다. 모델 디렉터리는 읽기 전용이다. 모델 추가 후 라우터를 재시작한다.

[models.ini](models.ini)의 `[*]`는 기본 GPU 계층 99개, 컨텍스트 4096, 동시 요청 1개다. 모델별 설정은 예시 주석을 참고해 섹션을 추가한다. 예를 들어 `[example]`에 `model = /models/example.gguf`, `n-gpu-layers = 20`, `threads = 8`을 지정하면 API의 모델 이름 `example`로 선택할 수 있다. 해당 모델의 나머지 계층은 CPU에서 처리한다. GPU 계층 수가 작아질수록 시스템 RAM 사용량과 CPU 작업이 늘 수 있다. 모델 파일 크기에 KV 캐시와 CUDA 작업 공간을 더해 VRAM 및 RAM 예산을 확인한다. BF16·FP8 네이티브 연산은 전제하지 않는다.

프리셋은 root가 소유하고 다른 사용자가 쓰지 못하도록 유지한다. 변경 전 작업을 끝내고 저장소에서 수정한 뒤 라우터 컨테이너를 재생성한다. 모델별 값이 적용되도록 `n-gpu-layers`, `ctx-size`, `parallel`을 라우터 명령줄에 중복 지정하지 않는다. llama.cpp에서는 명령줄 인자가 프리셋보다 우선한다.

## 운영자 검증 절차

아래는 **AI VM에서 운영자 권한**으로 실행하는 절차다. GPU 전환 전 다른 작업을 확인한다.

```sh
cd /opt/gnuvil-ai
sudo make build SERVICE=llamacpp
sudo make switch SERVICE=llamacpp
sudo make status
curl --fail http://127.0.0.1:8081/health
curl --fail http://127.0.0.1:8081/v1/models
```

모델 목록의 `data[].id`를 확인하고 아래 `MODEL_ID`를 실제 ID로 바꿔 요청한다.

```sh
curl --fail http://127.0.0.1:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"MODEL_ID","messages":[{"role":"user","content":"안녕하세요"}],"max_tokens":64}'
sudo docker compose logs --tail 100 llamacpp
nvidia-smi
```

다른 모델로 전환할 때는 모든 클라이언트의 요청이 끝났는지 확인한 뒤 기존 모델 ID를 넣어 언로드하고, 목록에서 `unloaded`를 확인한다. 그 다음 새 모델로 요청한다.

```sh
curl --fail http://127.0.0.1:8081/models/unload \
  -H 'Content-Type: application/json' -d '{"model":"MODEL_ID"}'
curl --fail http://127.0.0.1:8081/v1/models
```

로그에서 CUDA 장치, GPU 오프로딩 계층, 응답과 VRAM 사용량을 확인한다. 라우터의 `/health`는 라우터 준비 상태이며 개별 모델의 로딩이나 추론 성공을 보장하지 않는다. 모델 로딩 실패 또는 언로드 지연 시 로그와 `nvidia-smi`를 확인한다.

API는 인증 없이 VM의 `127.0.0.1:8081`에만 노출된다. 원격 접속은 신뢰할 수 있는 사용자가 `ssh -L 8081:127.0.0.1:8081 <vm-계정>@<vm-주소>`로 터널을 사용한다.

Gnuvil AI Lab 포털에서 llama.cpp를 시작·종료하고 다른 GPU 서비스로 전환할 수 있다. 종료 요청 시 서비스가 새 작업을 잠시 차단하고 전달 중인 API 요청, 모델 로딩, 각 모델의 실행·대기 작업을 확인한다. 작업이 있거나 상태를 확인할 수 없으면 요청 접수를 재개하고 전환을 취소한다. 호스트 Controller는 `/run/gnuvil-llamacpp/lifecycle.sock`을 사용한다. 이 디렉터리는 `portal` Ansible 역할에서 준비한다.

[고정 소스의 라우터와 API 문서](https://github.com/ggml-org/llama.cpp/blob/d12cc3d1ca6bba741cd77887ac9c9ee18c8415c7/tools/server/README.md)
