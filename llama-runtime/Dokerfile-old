FROM registry.access.redhat.com/ubi9/ubi as builder
ENV PATH=/opt/rh/gcc-toolset-13/root/usr/bin/:$PATH
ENV BLA_VENDOR=OpenBLAS
RUN dnf update -y && dnf install -y \
  cmake git ninja-build gcc-c++ curl-devel gcc-toolset-13 \
  && scl enable gcc-toolset-13 bash \
  && source scl_source enable gcc-toolset-13 \
  && dnf clean all


RUN git clone https://github.com/ggml-org/llama.cpp.git  && cd llama.cpp && sed -i "s/powerpc64le/native -mvsx -mtune=native -D__POWER10_VECTOR__/g" ggml/src/CMakeLists.txt && \
    mkdir build; \
    cd build; \
    cmake -G Ninja ..; \
    cmake --build . --config Release

CMD bash

FROM registry.access.redhat.com/ubi9/ubi

ENV  LD_LIBRARY_PATH=/lib
COPY --from=builder --chmod=755 /llama.cpp/build/bin/llama-server /usr/local/bin
COPY --from=builder --chmod=644 /llama.cpp/build/bin/*.so /lib/


ENTRYPOINT [ "/usr/local/bin/llama-server", "--host", "0.0.0.0"]
