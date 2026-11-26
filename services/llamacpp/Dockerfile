FROM nvidia/cuda:12.9.1-devel-ubuntu24.04@sha256:e542739fcaa4f45da5add8c4cf5769783a61628b2518304a5bbe4ace468b8c8f

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake curl git build-essential python3 \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/llama.cpp && git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git \
    && git -C /opt/llama.cpp fetch --depth 1 origin d12cc3d1ca6bba741cd77887ac9c9ee18c8415c7 \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD
COPY router-safety.py /tmp/router-safety.py
RUN python3 /tmp/router-safety.py && rm /tmp/router-safety.py
RUN cmake -S /opt/llama.cpp -B /opt/llama.cpp/build \
    -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 \
    -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=OFF -DGGML_AVX512=OFF \
    -DGGML_FMA=OFF -DGGML_F16C=ON -DGGML_BMI2=OFF \
    -DGGML_CPU_ALL_VARIANTS=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_WEBUI=OFF -DLLAMA_BUILD_UI=OFF \
    && cmake --build /opt/llama.cpp/build --config Release --target llama-server -j 4
ENV LD_LIBRARY_PATH=/opt/llama.cpp/build/bin
USER 65534:65534
EXPOSE 8080
ENTRYPOINT ["/opt/llama.cpp/build/bin/llama-server"]
