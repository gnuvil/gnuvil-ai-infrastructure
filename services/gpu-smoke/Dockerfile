FROM nvidia/cuda:12.9.1-devel-ubuntu24.04@sha256:e542739fcaa4f45da5add8c4cf5769783a61628b2518304a5bbe4ace468b8c8f AS build
WORKDIR /src
COPY smoke.cu .
RUN nvcc -O2 -gencode arch=compute_70,code=sm_70 \
    -Xcompiler=-march=ivybridge,-mno-avx2,-mno-avx512f \
    --cudart=static -o /gpu-smoke smoke.cu

FROM nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df
COPY --from=build /gpu-smoke /usr/local/bin/gpu-smoke
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/gpu-smoke"]
