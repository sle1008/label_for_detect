# Install ONNX inference support for the annotation tool (CPU/GPU agnostic).
# TensorRT (.engine / .trt) requires an NVIDIA GPU — see notes at end.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host '=== Annotation tool inference setup ===' -ForegroundColor Cyan

python -c @"
import torch
print('Python torch:', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
"@

Write-Host "`nInstalling onnxruntime (for .onnx pre-annotation)..." -ForegroundColor Yellow
python -m pip install -U onnxruntime

Write-Host "`nVerifying ONNX stack..." -ForegroundColor Yellow
python -c @"
import onnxruntime as ort
print('onnxruntime', ort.__version__)
print('providers', ort.get_available_providers())
"@

Write-Host "`n=== TensorRT status ===" -ForegroundColor Cyan
python -c @"
import torch
if not torch.cuda.is_available():
    print('SKIP TensorRT: no CUDA GPU detected (need NVIDIA GPU + CUDA PyTorch).')
    print('Use .pt or .onnx models on this machine.')
else:
    print('CUDA GPU detected. To enable .engine/.trt:')
    print('  pip install tensorrt')
    print('  Export: yolo export model=best.pt format=engine device=0')
"@

Write-Host "`nDone." -ForegroundColor Green
