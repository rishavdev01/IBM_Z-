# Aegis Exchange — IBM LinuxONE (`s390x`) Deployment Guide

This guide details the deployment of the Aegis Exchange platform on **IBM LinuxONE** and **IBM zSystems** running Linux on `s390x` architecture.

---

## 1. System Architecture Verification

On your target IBM LinuxONE LPAR or z/VM guest (Ubuntu 22.04 LTS or 24.04 LTS recommended):

```bash
uname -m
# Expected output: s390x
```

Verify Docker and Docker Compose plugin availability:

```bash
docker version
docker compose version
```

---

## 2. Multi-Architecture Container Builds (via Docker Buildx)

If building images on an x86_64 development machine to push to an s390x deployment registry:

### 2.1 Enable QEMU Multi-Architecture Emulation

```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

> [!WARNING]
> Building `s390x` images on x86_64 machines uses QEMU software emulation. Native package compilation (such as C++ extensions) may take significantly longer than native compilation.

### 2.2 Create a Buildx Builder Instance

```bash
docker buildx create --name linuxone-builder --use
docker buildx inspect --bootstrap
```

### 2.3 Build and Push Images for `linux/s390x`

```bash
docker buildx build --platform linux/s390x -t aegis-api:s390x -f Dockerfile.api --load .
docker buildx build --platform linux/s390x -t aegis-dashboard:s390x -f Dockerfile.dashboard --load .
docker buildx build --platform linux/s390x -t aegis-simulator:s390x -f Dockerfile.simulator --load .
```

---

## 3. Dependency & Ecosystem Compatibility on `s390x`

Aegis Exchange has been designed to avoid exotic native dependencies:

| Component | Package / Tool | `s390x` Status | Notes |
| :--- | :--- | :--- | :--- |
| **Runtime Base** | `python:3.11-slim` | ✅ Official | Official Debian-based multi-arch image provided by Docker Hub. |
| **Web Framework** | `FastAPI`, `Uvicorn` | ✅ Pure-Python / Wheel | Installs cleanly via pip. |
| **Database** | `SQLAlchemy 2.0`, `sqlite3` | ✅ Native / Standard Lib | SQLite engine is bundled with Python standard library. |
| **Graph Analysis**| `NetworkX` | ✅ Pure-Python | Full graph manipulation support on all architectures. |
| **ML Inference** | `scikit-learn`, `joblib` | ✅ Wheel / Build | `scikit-learn` provides pre-built wheels or compiles cleanly with `gcc`/`gfortran`. |
| **Gradient Boost**| `LightGBM` | ⚠️ Build Constraint | Pre-compiled wheels may not be published on PyPI for `s390x`. If `pip install lightgbm` fails during build, compile from source with CMake or use the fallback backend. |
| **Visualization** | `Streamlit`, `Plotly` | ✅ Verified | Pure-Python and HTML/JS frontend assets. |

### LightGBM Fallback Configuration
If compiling LightGBM on a minimal `s390x` image encounters toolchain limitations, set the environment variable:

```bash
export AEGIS_MODEL_BACKEND=sklearn
```

The ML scoring engine (Part 3) automatically selects the pure Scikit-Learn pipeline without requiring native OpenMP compilation.

---

## 4. Native LinuxONE Deployment (Quick Start)

Clone the repository on the IBM LinuxONE instance:

```bash
git clone https://github.com/rishavdev01/IBM_Z-.git aegis-exchange
cd aegis-exchange
```

### 4.1 Launch Production Services

Start the API and Analyst Dashboard:

```bash
docker compose up -d api dashboard
```

Check service status and container health:

```bash
docker compose ps
docker compose logs -f api
```

### 4.2 Validate Endpoints

Check the API health check:

```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

Access the Streamlit Analyst Dashboard:
- Open your browser at `http://<LINUXONE_HOST_IP>:8501`.

---

## 5. Running Real-Time Demo Traffic

To inject synthetic transactions and simulated scam typologies:

```bash
docker compose --profile demo up simulator
```

Or trigger a specific scam burst scenario manually:

```bash
python3 -m simulator.main burst --typology mule_fan_in --api-url http://localhost:8000
```

---

## 6. Verification Status & Disclaimer

- **What was tested:**
  - Python 3.11/3.13 syntax, SQLAlchemy 2.0 WAL persistence, FastAPI route registration, and Streamlit dashboard components were fully tested and validated locally.
  - Multi-architecture Dockerfile syntax and container entrypoints have been structured according to LinuxONE best practices (`python:3.11-slim`, non-root `aegis` user, `/app/data` volume permissions).
- **What remains to be verified on physical hardware:**
  - Native compilation speed of Scikit-Learn / LightGBM on physical IBM z16 / LinuxONE hardware.
  - Direct integration with IBM z/OS or Enterprise SAF security modules (if extended beyond containerized prototype).
