# Keep this terminal open while the local worker uses the GPU model endpoint.
while ($true) {
    ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 gpu-8-vm
    Start-Sleep -Seconds 5
}
