# ComfyUI와 NoobAI XL V-Pred

현재 구성은 V100(sm_70), CUDA 12.6 PyTorch wheel, FP16을 대상으로 한다. 커밋과 의존성을 고정했으며, 실제 V100 VM에서 이미지 빌드와 서비스 시작, 모델 인식을 확인했다. 이미지 생성은 사용자 환경에서 확인했다. 새 VM에서는 다시 검증한다. ComfyUI 0d0e105와 Python 3.12, PyTorch 2.10.0+cu126을 사용한다. 커스텀 노드와 dynamic VRAM은 기본 비활성화한다.

AI VM에서 운영자 권한으로 저장소의 `services/comfyui/setup-data.sh`를 실행해 디렉터리 권한을 준비하고 모델을 체크섬 확인 후 설치한다. 스크립트의 인자로 검증할 모델 파일 경로를 전달한다. 기존 데이터의 소유권을 재귀 변경하지 않는다.

```
cd /opt/gnuvil-ai
sudo sh services/comfyui/setup-data.sh /opt/gnuvil-ai/.model-download/NoobAI-XL-Vpred-v1.0.safetensors
sudo make build SERVICE=comfyui
sudo make switch SERVICE=comfyui
sudo make status
```

동아리방 네트워크의 브라우저에서 http://192.168.50.14:8188 에 접속한다. 이 주소로 라우팅할 수 있는 다른 네트워크도 접속할 수 있다. ComfyUI에는 별도 로그인이나 사용자별 격리가 없으므로 접속 가능한 사람은 공유 큐와 결과물에 접근할 수 있다. 기존 SSH 터널도 사용할 수 있다. 큐가 비었는지 확인한 후 `sudo make gpu-off CONFIRM=1`로 종료한다.

기본 SDXL 워크플로에서 Checkpoint를 선택하고 `ModelSamplingDiscrete`를 MODEL과 KSampler 사이에 넣어 `v_prediction`, `zsnr=true`를 설정한다. Euler, normal, 28 steps, CFG 5, 832x1216, 배치 1로 시작한다. 최초 실행 시 VRAM과 시스템 메모리를 확인한다. 서비스 준비는 API 응답까지 확인하며, 실제 이미지 생성 성공을 보장하지 않는다.
