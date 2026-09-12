# GPU on this laptop: what actually works

Measured on 2026-09-12, on `VadaLinux.lan` (openSUSE Leap 16, i7-6820HQ, Quadro M3000M).

## The premise was wrong

An earlier note claimed the Quadro M3000M had no driver and that `nvidia-smi` was
unavailable. That was false. The proprietary driver is installed and working:

```
NVIDIA-SMI 580.178.04    CUDA 13.0    compute capability 5.2 (Maxwell)   3.9 GiB VRAM
```

What was actually missing was the **NVIDIA Container Toolkit**, so Docker could not pass
the GPU to any container — the stack was configured CPU-only on the strength of the wrong
note (`DOCMIND_ENABLE_GPU_ACCELERATION=false`, `CUDA_VISIBLE_DEVICES=""`).

`nvidia-smi` showing a process is also why the GPU looks permanently busy: **Xorg drives
the desktop with it** (~24% util, 10 W, P8). That is normal compositing, not a fault.

## Enabling it

```bash
sudo zypper addrepo --refresh \
  https://nvidia.github.io/libnvidia-container/stable/rpm/x86_64 nvidia-container-toolkit
sudo rpm --import https://nvidia.github.io/libnvidia-container/gpgkey
sudo zypper install nvidia-container-toolkit          # 1.20.0
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Restarting Docker drops every container that has no restart policy. On this host that is
the whole FirecREST demo stack plus `metamcp`; `scripts/workbench-up.sh` brings them back
in ~8s.

## Result: it works, but the win is small

Ollama refuses the GPU in one code path and accepts it in another, which is worth
knowing precisely:

```
skipping CUDA device — compute capability not in compiled architectures
  device="Quadro M3000M"  cc=520
  archs="[750 800 860 870 890 900 1000 1030 1100 1200 1210]"
  libDirs="[/usr/lib/ollama /usr/lib/ollama/cuda_v13]"
```

`cuda_v13` starts at sm_75 (Turing) and skips Maxwell. **`cuda_v12` supports sm_52**, and
Ollama falls back to it successfully:

```
load_tensors: offloaded 32/37 layers to GPU
CUDA0 (Quadro M3000M): 32 layers, 2900 MiB used, 1027 MiB free
```

`nvidia-smi` confirms the real thing — `/usr/lib/ollama/llama-server` holding ~3 GB on
the GPU. So the GPU genuinely computes; it is not merely detected.

Benchmark, same model and prompt, `qwen3:4b-instruct`:

| | eval rate | model load | total |
|---|---|---|---|
| **GPU** | **12.08 tok/s** | 0.39 s | 13.1 s |
| CPU | 7.41 tok/s | 4.31 s | 26.1 s |

**~1.6x on generation, ~11x on model load.** Real, but far from the order-of-magnitude
one hopes for, for two reasons: only 32 of 37 layers fit in 3.9 GiB, so five layers stay
on the CPU and throttle the rest; and the M3000M is a 2015 low-power Maxwell.

## Why it is not currently worth switching on

The synthesis LLM was moved to OpenRouter, so the local Ollama is now only a **fallback**.
Accelerating the fallback path buys almost nothing.

The workload that would actually benefit is the **embedding** (BGE-M3), which is what made
the corpus ingestion take three hours. Two obstacles:

1. `torch` in the DocMind image is CPU-only (`2.11.0+cpu`), so enabling GPU embeddings
   means rebuilding that image against a GPU torch — a multi-gigabyte change.
2. **VRAM is the hard limit.** The Ollama model wants 2.9 GB and BGE-M3 wants ~2.3 GB
   against 3.9 GB total. They cannot both be resident. It is one or the other.

## Current state

- Toolkit installed, `nvidia` runtime registered with Docker, nothing switched on.
- Leaving the runtime configured costs nothing: it is only used when a container asks.
- Revisit only if re-ingestion speed becomes the binding constraint, and then expect to
  choose between the LLM and the embedding, not both.
