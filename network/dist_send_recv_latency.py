#!/usr/bin/env python

# this is derived from the all_reduce_bench.py
# but adjusted to show how 1x 4GB reduction is much faster than 1000x 4MB reduction
#
# to run on 8 gpus:
# python -u -m torch.distributed.run --nproc_per_node=8 all_reduce_latency_comp.py

import os
import math
import socket
import torch
import torch.distributed as dist

from utils import calculate_dimensions, bytes_to_nice_format


def timed_send_recv(data, id, start_event, end_event):
    rank = dist.get_rank()
    
    start_event.record()
    for i in range(1):
        if rank == 0:
            dist.send(tensor=data, dst=1)
        elif rank == 1:
            dist.recv(tensor=data, src=0)
        
    end_event.record()

    torch.cuda.synchronize()
    duration = start_event.elapsed_time(end_event) / 1000

    size = data.numel() * 4 # 4 is fp32
    algbw = (size / duration) * 8 # 8 is bytes to bits
    n = dist.get_world_size()
    # the 2*(n-1)/n busbw correction factor specific to all-reduce is explained here:
    # https://github.com/NVIDIA/nccl-tests/blob/master/doc/PERFORMANCE.md#allreduce
    # busbw reflects how optimally the hardware is used
    busbw = algbw * (2*(n - 1) / n)
    
    if dist.get_rank() == 0:
        print(f"{id}:\n",
                f"duration: {duration:.3f} sec\n",
                f"algbw: {algbw/1e9:.3f} Gbps\n",
                f"busbw: {busbw / 1e9:.3f} Gbps"
        )



def run(local_rank):
    TRIALS = 2
    input_sizes = [(1, "MB"), (5, "MB"), (10, "MB"), (50, "MB"), (100, "MB"), (1, "GB"), (5, "GB"), (20, "GB")]

    hostname = socket.gethostname()
    id = f"{hostname}:{local_rank}"
    global_rank = dist.get_rank()

    sizes = calculate_dimensions(input_sizes)
    
    dist.barrier()
    for M, N in sizes:
        dist.barrier()
        
        # NOTE: these emulate the payload which will become a M * N * 4-sized tensor below
        if global_rank == 0:
            data = torch.rand(N, M, dtype=torch.float32).cuda(local_rank)
        elif global_rank == 1:
            data = torch.empty(N, M, dtype=torch.float32).cuda(local_rank)
                
        data_size = bytes_to_nice_format(data.numel() * 4)

        start_event = torch.cuda.Event(enable_timing=True)
        end_event = torch.cuda.Event(enable_timing=True)
        
        for trial in range(TRIALS):
            dist.barrier()
            print(f"\n\n\n----------- [Trial {trial}][M={M}, N={N}, size={data_size} GB] ----------------")
            
            if global_rank == 0:
                timed_send_recv(data, id, start_event, end_event)
            elif global_rank == 1:
                timed_send_recv(data, id, start_event, end_event)

def init_processes(local_rank, fn, backend='nccl'):
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend)
    fn(local_rank)


if __name__ == "__main__":
    local_rank = int(os.environ["LOCAL_RANK"])
    print("local_rank: %d" % local_rank)
    init_processes(local_rank=local_rank, fn=run)
