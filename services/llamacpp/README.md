# llama.cpp 로컬 API

`llama-server`는 단일 GGUF 모델을 CUDA로 실행하며 OpenAI 호환 API를 제공한다. 이미지에는 llama.cpp 커밋 `d12cc3d1ca6bba741cd77887ac9c9ee18c8415c7`과 CUDA 12.9.1 기반 이미지 다이제스트를 고정했다. CPU 빌드는 AVX를 사용하고 AVX2, AVX-512, FMA와 BMI2를 비활성화하며 CUDA 코드는 `sm_70`을 대상으로 한다. 이 구성은 아직 대상 VM에서 빌드하거나 추론을 검증하지 않았다. APT 패키지는 빌드 시점의 저장소를 사용한다.

기본 모델 경로는 `/srv/ai-data/models/llamacpp/model.gguf`다. 모델은 포함하거나 자동 다운로드하지 않는다. 라이선스와 제공자의 체크섬을 확인한 GGUF 파일을 해당 위치에 준비한다. 경로의 각 상위 디렉터리는 컨테이너 UID 65534가 탐색할 수 있어야 하고 파일은 읽을 수 있어야 한다. 모델 디렉터리는 읽기 전용으로만 연결된다. 파일 크기 외에도 KV 캐시, 컨텍스트 및 CUDA 작업 공간을 포함해 V100의 16GB VRAM과 VM RAM을 확인한다. 처음에는 작은 양자화 모델과 4096 컨텍스트, 동시 요청 1개로 시험한다. 모델에 따라 GPU 오프로딩 계층 수와 컨텍스트를 Compose의 `command`에서 조정하고 다시 시작한다. BF16·FP8 네이티브 연산은 전제하지 않는다.

다음은 **AI VM의 운영자 권한**으로, 영구 데이터 마운트와 모델 체크섬 및 읽기 권한을 확인한 후 실행하는 절차다. 디렉터리 생성 및 모델 배치는 운영자가 수행한다. GPU 전환은 다른 작업을 중단할 수 있으므로 현재 작업을 먼저 확인한다.

```sh
cd /opt/gnuvil-ai
sudo make build SERVICE=llamacpp
sudo make switch SERVICE=llamacpp
sudo make status
curl --fail http://127.0.0.1:8081/health
curl --fail http://127.0.0.1:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"model.gguf","messages":[{"role":"user","content":"안녕하세요"}],"max_tokens":64}'
sudo docker compose logs --tail 100 llamacpp
nvidia-smi
```

로그에서 CUDA 장치와 모델 계층의 GPU 오프로딩을 확인하고, 응답과 VRAM 사용량을 기록한다. `/health`는 모델 로딩 완료를 확인하지만 추론 성공이나 작업 종료 안전성을 보장하지 않는다. 모델 로딩이 정책의 준비 제한 시간 120초를 넘으면 `switch`가 실패할 수 있으므로 로그와 실제 컨테이너 상태를 먼저 확인한다.

API는 인증 없이 VM의 `127.0.0.1:8081`에만 노출된다. 원격 접속은 신뢰할 수 있는 사용자가 SSH 터널을 사용한다. 예를 들어 클라이언트에서 `ssh -L 8081:127.0.0.1:8081 <vm-계정>@<vm-주소>`를 실행한다. 포털에는 상태 및 시작 제어만 등록하며 직접 접속 링크는 제공하지 않는다. 안전한 drain 어댑터가 없어서 포털에서 실행 중 서비스 종료·전환은 제한된다. 작업을 모두 마친 뒤 AI VM에서 운영자가 `sudo make gpu-off CONFIRM=1`로 종료한다. 다른 GPU 서비스로 전환할 때도 작업 종료를 직접 확인한 뒤 필요하면 `sudo make switch SERVICE=<서비스> CONFIRM=1`을 사용한다.

[llama.cpp 빌드 문서](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) · [서버 API와 health](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
