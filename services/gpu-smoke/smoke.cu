#include <cstdio>
#include <cstdlib>
#include <cuda_runtime.h>

static void check(cudaError_t status, const char* operation)
{
    if (status != cudaSuccess) {
        std::fprintf(stderr, "%s: %s\n", operation, cudaGetErrorString(status));
        std::exit(EXIT_FAILURE);
    }
}

__global__ void add(int* result)
{
    *result = 19 + 23;
}

int main()
{
    int count = 0;
    check(cudaGetDeviceCount(&count), "cudaGetDeviceCount");
    if (count != 1) {
        std::fprintf(stderr, "Expected one visible CUDA device, found %d\n", count);
        return EXIT_FAILURE;
    }

    cudaDeviceProp properties{};
    check(cudaGetDeviceProperties(&properties, 0), "cudaGetDeviceProperties");
    if (properties.major != 7 || properties.minor != 0) {
        std::fprintf(stderr, "Expected sm_70, found %d.%d (%s)\n",
                     properties.major, properties.minor, properties.name);
        return EXIT_FAILURE;
    }

    int* device_result = nullptr;
    int host_result = 0;
    check(cudaMalloc(&device_result, sizeof(host_result)), "cudaMalloc");
    add<<<1, 1>>>(device_result);
    check(cudaGetLastError(), "kernel launch");
    check(cudaDeviceSynchronize(), "kernel execution");
    check(cudaMemcpy(&host_result, device_result, sizeof(host_result),
                     cudaMemcpyDeviceToHost), "cudaMemcpy");
    check(cudaFree(device_result), "cudaFree");
    if (host_result != 42) {
        std::fprintf(stderr, "Expected result 42, found %d\n", host_result);
        return EXIT_FAILURE;
    }

    std::printf("CUDA PASS on %s (sm_70): 19 + 23 = %d\n", properties.name, host_result);
    return EXIT_SUCCESS;
}
