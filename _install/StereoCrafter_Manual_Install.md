# 🛠️ Manual Installation Guide for StereoCrafter  for Windows (UV Edition)

This guide walks you through manually installing the **StereoCrafter** using the uv package manager.

---

## 📋 Prerequisites

Ensure the following tools are installed and available in your system's PATH:

- [Git](https://git-scm.com/)
- [FFmpeg](https://techtactician.com/how-to-install-ffmpeg-and-add-it-to-path-on-windows/) -- a full build, with `ffprobe`
- An NVIDIA RTX graphics card (RTX 20 series or newer) with **driver 580 or newer**

The CUDA Toolkit is **not** needed: PyTorch brings its own CUDA.

---

## 🚀 Installation Steps

### 1. Check the NVIDIA driver

```bash

nvidia-smi

```

The top line shows `Driver Version`. It must be **580 or newer** (Studio's PyTorch is built for CUDA 13); if
it's older, update it from [nvidia.com](https://www.nvidia.com/Download/index.aspx) or the NVIDIA app.

### 2. Install uv Package Manager

```bash

powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

```


### 3. Clone the Repository with Submodules & Change Directory Path

```bash

git clone --recursive https://github.com/NowIAmBecomeDepth/StereoCrafterStudio.git
cd StereoCrafterStudio

```

> If the folder `StereoCrafterStudio` already exists, delete or rename it before proceeding.



---

### 4. Setup and Install

```bash

uv sync

```


## 📦 Model Weights Installation

**Easiest: skip this section.** Start Studio and open **Settings > Studio > Models**: it downloads each model with a
button, signs you in to Hugging Face, and fetches only the files Studio uses (about 7.5 GB for the three below instead
of 22). The commands here are the by-hand alternative, using the Hugging Face CLI (provided by the huggingface-hub
package in your environment).

### 1. Prerequisite: Accept Model Terms

The SVD model is "Gated." You must manually accept the terms of use before you can download it:

- 1. Log in to [Hugging Face.](https://huggingface.co/)
- 2. Visit the [SVD XT 1.1 Page.](https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt-1-1)
- 3. Click **"Agree and access repository"**.

### 2. Authenticate your PC
You need a "Read" Token from your [Hugging Face Settings.](https://huggingface.co/settings/tokens) Run this command and paste your token when prompted:

```bash

uv run hf auth login

```

### 3. Download the Models
Run these commands from the `StereoCrafter` root folder. The CLI will automatically create the `weights` folder if it doesn't exist:

```bash
# 1. Download SVD img2vid (Gated - Requires Login)
uv run hf download stabilityai/stable-video-diffusion-img2vid-xt-1-1 --local-dir weights/stable-video-diffusion-img2vid-xt-1-1

# 2. Download DepthCrafter (Open)
uv run hf download tencent/DepthCrafter --local-dir weights/DepthCrafter

# 3. Download StereoCrafter (Open)
uv run hf download TencentARC/StereoCrafter --local-dir weights/StereoCrafter

```

**Note:** Total download size is approximately 22GB. Ensure you have enough disk space.
Your downloaded weights should contain files matching the visual hiearchy below.

```text

..\Stereocrafter\weights\
   │ 
   ├───DepthCrafter/
   │       config.json                                  
   │       diffusion_pytorch_model.safetensors          
   │                                                    
   ├───stable-video-diffusion-img2vid-xt-1-1/  
   │   │   model_index.json                            
   │   │   svd_xt_1_1.safetensors                       
   │   │                                                
   │   ├───feature_extractor/
   │   │       preprocessor_config.json                 
   │   │                                                
   │   ├───image_encoder/
   │   │       config.json                              
   │   │       model.fp16.safetensors                   
   │   │       model.safetensors                        
   │   │                                                
   │   ├───scheduler/
   │   │       scheduler_config.json                    
   │   │                                                
   │   ├───unet/
   │   │       config.json                              
   │   │       diffusion_pytorch_model.fp16.safetensors 
   │   │       diffusion_pytorch_model.safetensors      
   │   │                                                
   │   └───vae/
   │           config.json                              
   │           diffusion_pytorch_model.fp16.safetensors 
   │           diffusion_pytorch_model.safetensors      
   │                                                    
   └───StereoCrafter/                        
           config.json                                  
           diffusion_pytorch_model.safetensors         

```

## ▶️ Running It

With the environment installed and the weights in place, start everything from
one window:

```bash
"StereoCrafter Studio.bat"
```

or double-click `StereoCrafter Studio.exe`, or, from the repository folder:

```bash
uv run python -m studio.app
```

Studio holds the four pipeline stages as tabs --- DepthCrafter, Splatting,
Inpainting, Merging --- plus an Extra tab for the other tools (Scene Cut, the
Depth Map Editor, Edge Fill). Press F1 in Studio for the manual.

## ✅ Final Notes

- If any step fails, check your environment variables and permissions.
- Refer to `install_log.txt` (if generated during script-based install) for troubleshooting.
- CUDA support is critical for GPU acceleration. Ensure your drivers and toolkit are correctly installed.

