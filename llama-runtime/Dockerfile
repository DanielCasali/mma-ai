FROM registry.access.redhat.com/ubi9/ubi as builder
ENV PATH=/opt/atX.X/bin:/opt/at17.0/sbin:$PATH
ENV BLA_VENDOR=OpenBLAS
ENV CMAKE_PREFIX_PATH=/opt/at17.0/
RUN dnf update -y && dnf install -y \
  cmake git ninja-build \
  && dnf install -y https://public.dhe.ibm.com/software/server/POWER/Linux/toolchain/at/redhat/RHEL9/at17.0/advance-toolchain-at17.0-runtime-17.0-2.ppc64le.rpm \
  && dnf install -y https://public.dhe.ibm.com/software/server/POWER/Linux/toolchain/at/redhat/RHEL9/at17.0/advance-toolchain-at17.0-devel-17.0-2.ppc64le.rpm \
  && dnf install -y https://public.dhe.ibm.com/software/server/POWER/Linux/toolchain/at/redhat/RHEL9/at17.0/advance-toolchain-at17.0-perf-17.0-2.ppc64le.rpm \
  && dnf install -y https://public.dhe.ibm.com/software/server/POWER/Linux/toolchain/at/redhat/RHEL9/at17.0/advance-toolchain-at17.0-mcore-libs-17.0-2.ppc64le.rpm

RUN dnf install -y curl-devel
RUN git clone https://github.com/ggml-org/llama.cpp.git  && cd llama.cpp && sed -i "s/powerpc64le/native -mcpu=power10 -mvsx -mtune=native -D__POWER10_VECTOR__/g" ggml/src/CMakeLists.txt && \
    mkdir build; \
    cd build; \
    cmake -DCMAKE_C_FLAGS="-mcpu=power10" -DCMAKE_CXX_FLAGS="-mcpu=power10" -G Ninja ..; \
    cmake --build . --config Release

CMD bash

FROM registry.access.redhat.com/ubi9/ubi

RUN dnf install -y https://public.dhe.ibm.com/software/server/POWER/Linux/toolchain/at/redhat/RHEL9/at17.0/advance-toolchain-at17.0-runtime-17.0-2.ppc64le.rpm && dnf clean all
RUN mkdir -p /opt/llama/lib

ENV  LD_LIBRARY_PATH=/lib
COPY --from=builder --chmod=755 /llama.cpp/build/bin/llama-server /usr/local/bin
COPY --from=builder --chmod=644 /llama.cpp/build/bin/*.so /lib/

EXPOSE 8080

ENTRYPOINT [ "/usr/local/bin/llama-server", "--host", "0.0.0.0"]
