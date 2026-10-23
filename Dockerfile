# Multi-stage build: compile Rust, then copy binary to minimal runtime.
FROM rust:1.98-slim AS builder

WORKDIR /build
COPY rust-dashboard/Cargo.toml rust-dashboard/Cargo.lock ./
COPY rust-dashboard/src ./src
COPY rust-dashboard/static ./static

RUN cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release/tmm /usr/local/bin/tmm

RUN mkdir -p /app/data
WORKDIR /app

EXPOSE 8501

# Run worker + dashboard in one process
CMD ["tmm", "all"]
